"""Community SSO: the OIDC provider, which accounts an SSO sign-in may land on,
and the callback's session (cookies, or a second-factor challenge)."""
from datetime import datetime
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from src.core.events.database import get_db_session
from src.db.organization_config import OrganizationConfig
from src.db.organizations import Organization
from src.db.sso import SSOConnection, SSOConnectionCreate, SSOConnectionUpdate
from src.db.user_audit_events import UserAuditEvent, UserAuditEventType
from src.db.user_organizations import UserOrganization
from src.db.users import AnonymousUser, User
from src.routers.sso import router as sso_router
from src.security.auth import JWT_COOKIE_NAME, get_current_user
from src.services.sso import sso as svc
from src.services.sso.providers import SSOConfigurationError, SSOUserProfile, get_sso_provider
from src.services.sso.providers.oidc_provider import CustomOIDCProvider

ISSUER = "https://idp.example.test/application/o/learnhouse"
OIDC_CONFIG = {"issuer_url": ISSUER, "client_id": "lh", "client_secret": "s3cret"}


@pytest.fixture(autouse=True)
def no_redis():
    svc._local_states.clear()
    with patch("src.services.sso.sso.get_redis_client", return_value=None):
        yield
    svc._local_states.clear()


@pytest.fixture(autouse=True)
def quiet_side_effects():
    with patch("src.services.users.users.send_account_creation_email"), patch(
        "src.services.nudges.links.org_base_url", new=AsyncMock(return_value="https://lms.example.test"),
    ):
        yield


@pytest.fixture
async def sso_org(db, org):
    db.add(OrganizationConfig(org_id=org.id, config={"config_version": "2.0", "plan": "free"},
                              creation_date=str(datetime.now()), update_date=str(datetime.now())))
    await db.commit()
    return org


@pytest.fixture
async def connection(db, sso_org):
    return await svc.create_sso_connection(
        sso_org.id,
        SSOConnectionCreate(provider="custom_oidc", enabled=True, domains=["Example.com"], provider_config=OIDC_CONFIG),
        db,
    )


def profile(email="ada@example.com", verified=True, **attrs) -> SSOUserProfile:
    raw = {"email": email, "sub": "u-1", **attrs}
    if verified is not None:
        raw["email_verified"] = verified
    return SSOUserProfile(email=email, first_name="Ada", last_name="Lovelace", provider_user_id="u-1", raw_attributes=raw)


async def make_user(db, uid, email, signup_method="email", password="hash"):
    user = User(id=uid, username=f"u{uid}", first_name="A", last_name="B", email=email, password=password,
                user_uuid=f"user_{uid}", email_verified=True, signup_method=signup_method,
                creation_date=str(datetime.now()), update_date=str(datetime.now()))
    db.add(user)
    await db.commit()
    return user


async def join(db, user_id, org_id, role_id=4):
    db.add(UserOrganization(user_id=user_id, org_id=org_id, role_id=role_id,
                            creation_date=str(datetime.now()), update_date=str(datetime.now())))
    await db.commit()


async def sign_in(db, mock_request, prof):
    """A full service-side sign-in: authorize, then the callback with ``prof``."""
    auth = await svc.initiate_sso_login("test-org", db)
    with patch.object(CustomOIDCProvider, "handle_callback", new=AsyncMock(return_value=prof)):
        return await svc.handle_sso_callback(mock_request, "code", auth.state, db)


def discovery_doc() -> dict:
    return {
        "authorization_endpoint": f"{ISSUER}/authorize/",
        "token_endpoint": f"{ISSUER}/token/",
        "userinfo_endpoint": f"{ISSUER}/userinfo/",
    }


# Captured before the autouse fixture below replaces it in every test.
REAL_DISCOVERY = CustomOIDCProvider._get_discovery_document


@pytest.fixture(autouse=True)
def discovery():
    with patch.object(CustomOIDCProvider, "_get_discovery_document", new=AsyncMock(return_value=discovery_doc())):
        yield


# ── provider ────────────────────────────────────────────────────────────────


def mock_idp(handler):
    return patch.object(
        CustomOIDCProvider, "_client",
        lambda self, url: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_oidc_exchanges_the_code_and_reads_userinfo():
    seen = {}

    def handler(request: httpx.Request):
        if request.url.path.endswith("/token/"):
            seen["token"] = parse_qs(request.content.decode())
            return httpx.Response(200, json={"access_token": "at", "id_token": "x.y.z"})
        assert request.headers["authorization"] == "Bearer at"
        return httpx.Response(200, json={"email": "ada@example.com", "email_verified": True, "given_name": "Ada", "sub": "u-1"})

    with mock_idp(handler):
        prof = await CustomOIDCProvider().handle_callback("the-code", {**OIDC_CONFIG, "redirect_uri": "https://lms.example.test/auth/sso/callback"})
    assert (prof.email, prof.first_name, prof.provider_user_id) == ("ada@example.com", "Ada", "u-1")
    assert seen["token"]["code"] == ["the-code"]
    assert seen["token"]["redirect_uri"] == ["https://lms.example.test/auth/sso/callback"]


async def test_oidc_discovery_reads_the_well_known_document():
    seen = []

    def handler(request: httpx.Request):
        seen.append(str(request.url))
        return httpx.Response(200, json=discovery_doc())

    with mock_idp(handler):
        doc = await REAL_DISCOVERY(CustomOIDCProvider(), ISSUER)
    assert seen == [f"{ISSUER}/.well-known/openid-configuration"]
    assert doc["token_endpoint"] == f"{ISSUER}/token/"


async def test_oidc_refuses_a_plain_http_token_endpoint():
    doc = {**discovery_doc(), "token_endpoint": "http://idp.example.test/token/"}
    with mock_idp(lambda r: httpx.Response(200, json=doc)):
        with pytest.raises(SSOConfigurationError):
            await REAL_DISCOVERY(CustomOIDCProvider(), ISSUER)


def test_only_listed_hosts_skip_the_private_address_guard(monkeypatch):
    monkeypatch.setenv("LEARNHOUSE_SSO_TRUSTED_HOSTS", " Auth.Corp.Lan , idp.lan.")
    provider = CustomOIDCProvider()
    assert provider._trusted_hosts() == {"auth.corp.lan", "idp.lan"}
    with patch("src.services.sso.providers.oidc_provider.pinned_transport",
               return_value=httpx.AsyncHTTPTransport()) as pinned:
        provider._client("https://auth.corp.lan/application/o/lh/")
        assert pinned.call_count == 0
        provider._client("https://idp.example.test/token/")
        assert pinned.call_count == 1


def test_oidc_config_needs_https():
    with pytest.raises(ValueError):
        CustomOIDCProvider().validate_config({**OIDC_CONFIG, "issuer_url": "http://idp.example.test"})


# ── connection admin ────────────────────────────────────────────────────────


async def test_domains_are_normalized_and_the_secret_survives_an_empty_update(db, connection):
    assert connection.domains == ["example.com"]
    updated = await svc.update_sso_connection(
        connection.org_id, SSOConnectionUpdate(provider_config={**OIDC_CONFIG, "client_secret": ""}), db
    )
    assert updated.provider_config["client_secret"] == "s3cret"


# ── who may sign in ─────────────────────────────────────────────────────────


async def test_first_sign_in_creates_a_member(db, mock_request, connection):
    result = await sign_in(db, mock_request, profile())
    user = result["user"]
    assert user.email == "ada@example.com" and user.signup_method == "sso"
    roles = (await db.execute(select(UserOrganization.role_id).where(UserOrganization.user_id == user.id))).scalars().all()
    assert roles == [4]


async def test_authorize_sends_the_web_callback_page(db, connection):
    auth = await svc.initiate_sso_login("test-org", db)
    query = parse_qs(urlsplit(auth.authorization_url).query)
    assert query["redirect_uri"] == ["https://lms.example.test/auth/sso/callback"]
    assert query["state"] == [auth.state]


async def test_a_state_works_once(db, mock_request, connection):
    auth = await svc.initiate_sso_login("test-org", db)
    with patch.object(CustomOIDCProvider, "handle_callback", new=AsyncMock(return_value=profile())):
        await svc.handle_sso_callback(mock_request, "code", auth.state, db)
        with pytest.raises(HTTPException) as e:
            await svc.handle_sso_callback(mock_request, "code", auth.state, db)
    assert e.value.detail["error_code"] == "state_invalid_or_expired"


async def test_existing_member_in_a_claimed_domain_is_matched(db, mock_request, connection):
    await make_user(db, 501, "Ada@Example.com")
    await join(db, 501, connection.org_id, role_id=1)
    result = await sign_in(db, mock_request, profile())
    assert result["user"].id == 501


async def test_existing_member_needs_a_verified_email(db, mock_request, connection):
    await make_user(db, 502, "ada@example.com")
    await join(db, 502, connection.org_id)
    with pytest.raises(HTTPException) as e:
        await sign_in(db, mock_request, profile(verified=None))
    assert e.value.detail["error_code"] == "account_not_linked"


async def test_account_in_another_org_is_never_matched(db, mock_request, connection, other_org):
    await make_user(db, 503, "ada@example.com")
    await join(db, 503, connection.org_id)
    await join(db, 503, other_org.id)
    with pytest.raises(HTTPException) as e:
        await sign_in(db, mock_request, profile())
    assert e.value.status_code == 403


async def test_sso_owned_account_signs_in_without_a_claimed_domain(db, mock_request, connection):
    await svc.update_sso_connection(connection.org_id, SSOConnectionUpdate(domains=[]), db)
    await make_user(db, 504, "ada@example.com", signup_method="sso", password="")
    await join(db, 504, connection.org_id)
    result = await sign_in(db, mock_request, profile(verified=None))
    assert result["user"].id == 504


async def test_sso_owned_account_that_signed_in_otherwise_is_refused(db, mock_request, connection):
    await svc.update_sso_connection(connection.org_id, SSOConnectionUpdate(domains=[]), db)
    await make_user(db, 505, "ada@example.com", signup_method="sso")
    await join(db, 505, connection.org_id)
    db.add(UserAuditEvent(event_type=UserAuditEventType.LOGIN, user_id=505, org_id=connection.org_id,
                          audit_metadata={"method": "password"}))
    await db.commit()
    with pytest.raises(HTTPException) as e:
        await sign_in(db, mock_request, profile(verified=None))
    assert e.value.detail["error_code"] == "account_not_linked"


async def test_an_unverified_email_is_refused(db, mock_request, connection):
    with pytest.raises(HTTPException) as e:
        await sign_in(db, mock_request, profile(verified="false"))
    assert e.value.detail["error_code"] == "email_not_verified"


async def test_a_domain_outside_the_list_is_refused(db, mock_request, connection):
    with pytest.raises(HTTPException) as e:
        await sign_in(db, mock_request, profile(email="eve@elsewhere.org"))
    assert e.value.detail["error_code"] == "email_domain_rejected"


async def test_backoffice_staff_never_sign_in_through_an_org_of_a_multi_org_instance(db, mock_request, connection):
    await make_user(db, 506, "ada@example.com")
    await join(db, 506, connection.org_id)
    with patch("src.security.backoffice.is_user_backoffice_staff", new=AsyncMock(return_value=True)), patch(
        "src.services.sso.sso._multi_tenant", return_value=True
    ):
        with pytest.raises(HTTPException) as e:
            await sign_in(db, mock_request, profile())
    assert e.value.detail["error_code"] == "platform_sign_in_required"


async def test_single_tenancy_admin_signs_in_through_sso(db, mock_request, connection):
    await make_user(db, 507, "ada@example.com")
    await join(db, 507, connection.org_id, role_id=1)
    with patch("src.security.backoffice.is_user_backoffice_staff", new=AsyncMock(return_value=True)), patch(
        "src.services.sso.sso._multi_tenant", return_value=False
    ):
        result = await sign_in(db, mock_request, profile())
    assert result["user"].id == 507


async def test_disabled_connection_is_not_offered(db, connection):
    assert (await svc.check_sso_enabled("test-org", db))["sso_enabled"] is True
    await svc.update_sso_connection(connection.org_id, SSOConnectionUpdate(enabled=False), db)
    assert (await svc.check_sso_enabled("test-org", db))["sso_enabled"] is False


def test_custom_oidc_is_registered():
    assert get_sso_provider("custom_oidc").provider_name == "custom_oidc"


# ── the callback endpoint ───────────────────────────────────────────────────


@pytest.fixture
async def client(db):
    app = FastAPI()
    app.include_router(sso_router, prefix="/api/v1/auth/sso")
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: AnonymousUser()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://lms.example.test") as c:
        yield c


async def test_callback_sets_the_session(db, client, connection):
    auth = await svc.initiate_sso_login("test-org", db)
    with patch.object(CustomOIDCProvider, "handle_callback", new=AsyncMock(return_value=profile())):
        r = await client.get("/api/v1/auth/sso/callback", params={"code": "c", "state": auth.state})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["email"] == "ada@example.com" and body["tokens"]["access_token"]
    assert body["redirect_url"] == "https://lms.example.test/redirect_from_auth"
    assert JWT_COOKIE_NAME in r.cookies


async def test_idp_second_factor_counts_when_the_org_exempts_sso(db, client, connection):
    auth = await svc.initiate_sso_login("test-org", db)
    with patch.object(CustomOIDCProvider, "handle_callback", new=AsyncMock(return_value=profile())), patch(
        "src.services.auth.session.is_mfa_active", new=AsyncMock(return_value=True)
    ):
        r = await client.get("/api/v1/auth/sso/callback", params={"code": "c", "state": auth.state})
    assert r.status_code == 200, r.text
    assert "mfa_required" not in r.json() and r.json()["tokens"]["access_token"]
    assert JWT_COOKIE_NAME in r.cookies


async def test_callback_with_a_second_factor_sets_no_session(db, client, connection):
    from src.services.auth.session import SessionIssueResult
    from src.services.orgs.mfa_policy import OrgMFAPolicy

    auth = await svc.initiate_sso_login("test-org", db)
    with patch.object(CustomOIDCProvider, "handle_callback", new=AsyncMock(return_value=profile())), patch(
        "src.services.orgs.mfa_policy.get_org_mfa_policy",
        new=AsyncMock(return_value=OrgMFAPolicy(exempt_external_auth=False)),
    ), patch(
        "src.services.auth.session.issue_session_or_challenge",
        new=AsyncMock(return_value=SessionIssueResult(mfa_required=True, mfa_token="pending")),
    ):
        r = await client.get("/api/v1/auth/sso/callback", params={"code": "c", "state": auth.state})
    assert r.status_code == 200, r.text
    assert r.json()["mfa_required"] is True and r.json()["mfa_token"] == "pending"
    assert JWT_COOKIE_NAME not in r.cookies


async def test_callback_passes_idp_errors_on(client):
    r = await client.get("/api/v1/auth/sso/callback", params={"error": "access_denied"})
    assert r.status_code == 400 and r.json()["detail"]["error_code"] == "access_denied"


async def test_config_never_returns_the_secret(db, client, connection):
    org = (await db.execute(select(Organization).where(Organization.id == connection.org_id))).scalars().one()
    with patch("src.routers.sso.rbac_check", new=AsyncMock()):
        r = await client.get(f"/api/v1/auth/sso/{org.id}/config")
    assert r.status_code == 200, r.text
    assert r.json()["provider_config"]["client_secret"] == ""
    stored = (await db.execute(select(SSOConnection))).scalars().one()
    assert stored.provider_config["client_secret"] == "s3cret"


def test_community_build_mounts_sso():
    from src.router import v1_router

    mounted = [getattr(route, "original_router", None) for route in v1_router.routes]
    assert any(router is sso_router for router in mounted)
