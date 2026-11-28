"""
SSO Service - Provider-agnostic SSO functionality.

Handles SSO connection management, login initiation, callback processing,
and user provisioning across different SSO providers.
"""

import hmac
import json
import logging
import os
import random
import secrets
import time
from datetime import datetime
from typing import Optional

from fastapi import HTTPException, Request
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.redis import get_redis_client
from src.db.organizations import Organization
from src.db.roles import Role
from src.db.sso import (
    SSOAuthorizationResponse,
    SSOConnection,
    SSOConnectionCreate,
    SSOConnectionUpdate,
)
from src.db.user_organizations import UserOrganization
from src.db.users import AnonymousUser, User, UserCreate
from src.security.features_utils.resolve import resolve_feature
from src.security.session_context import AUTH_METHOD_SSO
from src.services.orgs.org_config_lookup import get_session_org_config

from .providers import (
    SSOAuthenticationError,
    SSOConfigurationError,
    SSOUserProfile,
    get_available_providers as get_providers,
    get_sso_provider,
    is_provider_available,
)

logger = logging.getLogger(__name__)

SSO_STATE_TTL_SECONDS = 600
_STATE_KEY = "sso_state:{}"
# Without Redis (local dev, tests) the flow only survives on a single worker.
_local_states: dict[str, tuple[float, dict]] = {}

DEFAULT_SSO_ROLE_ID = 4


def _sso_error(status_code: int, error: str, error_code: str, description: str, **extra) -> HTTPException:
    """The structured error the SSO callback page renders (SSOErrorDetail)."""
    return HTTPException(
        status_code=status_code,
        detail={
            "error": error,
            "error_code": error_code,
            "error_description": description,
            "message": description,
            **extra,
        },
    )


def _save_state(state: str, data: dict) -> None:
    client = get_redis_client()
    if client is None:
        _local_states[state] = (time.monotonic() + SSO_STATE_TTL_SECONDS, data)
        return
    try:
        client.setex(_STATE_KEY.format(state), SSO_STATE_TTL_SECONDS, json.dumps(data))
    except Exception:
        logger.exception("Could not store the SSO state")
        raise HTTPException(status_code=503, detail="SSO is temporarily unavailable, please try again.")


def _pop_state(state: str) -> Optional[dict]:
    """The state's data, once: a replayed callback finds nothing."""
    client = get_redis_client()
    if client is None:
        expires, data = _local_states.pop(state, (0.0, None))
        return data if data is not None and time.monotonic() < expires else None
    try:
        pipe = client.pipeline(transaction=True)
        pipe.get(_STATE_KEY.format(state))
        pipe.delete(_STATE_KEY.format(state))
        raw, _ = pipe.execute()
    except Exception:
        logger.exception("Could not read the SSO state")
        raise HTTPException(status_code=503, detail="SSO is temporarily unavailable, please try again.")
    return json.loads(raw) if raw else None


async def is_sso_feature_enabled(org_id: int, db_session: AsyncSession) -> bool:
    """Whether the org may use SSO, through the same resolution as every feature."""
    org_config = await get_session_org_config(db_session, org_id)
    if org_config is None:
        return False
    return bool(resolve_feature("sso", org_config.config or {}, org_id).get("enabled"))


async def get_sso_redirect_uri(org: Organization, db_session: AsyncSession) -> str:
    """Where the IdP sends the browser back: the web app's SSO callback page.

    ``SSO_REDIRECT_URI`` overrides it with a full URL; ``SSO_REDIRECT_URI_BASE``
    replaces the org's public base URL.
    """
    full_uri = os.getenv("SSO_REDIRECT_URI")
    if full_uri:
        return full_uri
    base = os.getenv("SSO_REDIRECT_URI_BASE")
    if not base:
        from src.services.nudges.links import org_base_url

        base = await org_base_url(org.slug, db_session, org.id)
    return f"{base.rstrip('/')}/auth/sso/callback"


async def _org_by_slug(org_slug: str, db_session: AsyncSession) -> Optional[Organization]:
    return (await db_session.execute(
        select(Organization).where(Organization.slug == org_slug)
    )).scalars().first()


async def get_sso_connection(
    org_id: int,
    db_session: AsyncSession
) -> Optional[SSOConnection]:
    """SSO connection of an organization, if one is configured."""
    return (await db_session.execute(
        select(SSOConnection).where(SSOConnection.org_id == org_id)
    )).scalars().first()


async def get_sso_connection_by_org_slug(
    org_slug: str,
    db_session: AsyncSession
) -> Optional[SSOConnection]:
    """SSO connection of the organization with this slug, if one is configured."""
    org = await _org_by_slug(org_slug, db_session)
    if not org:
        return None
    return await get_sso_connection(org.id, db_session)


async def _validate_role(role_id: int, org_id: int, db_session: AsyncSession) -> None:
    role = (await db_session.execute(
        select(Role).where(
            Role.id == role_id,
            (Role.org_id == org_id) | (Role.org_id.is_(None))  # type: ignore[union-attr]
        )
    )).scalars().first()
    if not role:
        raise HTTPException(status_code=400, detail="Invalid default role ID")


async def create_sso_connection(
    org_id: int,
    data: SSOConnectionCreate,
    db_session: AsyncSession
) -> SSOConnection:
    """Create the SSO connection of an organization."""
    if await get_sso_connection(org_id, db_session):
        raise HTTPException(
            status_code=400,
            detail="SSO connection already exists for this organization. Use update instead."
        )

    if not is_provider_available(data.provider):
        raise HTTPException(
            status_code=400,
            detail=f"SSO provider '{data.provider}' is not available or not configured."
        )

    try:
        provider = get_sso_provider(data.provider)
        if data.provider_config:
            provider.validate_config(data.provider_config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if data.default_role_id:
        await _validate_role(data.default_role_id, org_id, db_session)

    now = datetime.utcnow()
    connection = SSOConnection(
        org_id=org_id,
        provider=data.provider,
        enabled=data.enabled,
        domains=_normalize_domains(data.domains or []),
        auto_provision_users=data.auto_provision_users,
        default_role_id=data.default_role_id,
        provider_config=data.provider_config or {},
        created_at=now,
        updated_at=now,
    )
    db_session.add(connection)
    await db_session.commit()
    await db_session.refresh(connection)
    return connection


async def update_sso_connection(
    org_id: int,
    data: SSOConnectionUpdate,
    db_session: AsyncSession
) -> SSOConnection:
    """Update the SSO connection of an organization."""
    connection = await get_sso_connection(org_id, db_session)
    if not connection:
        raise HTTPException(
            status_code=404,
            detail="SSO connection not found for this organization"
        )

    if data.provider is not None:
        if not is_provider_available(data.provider):
            raise HTTPException(
                status_code=400,
                detail=f"SSO provider '{data.provider}' is not available."
            )
        connection.provider = data.provider

    if data.enabled is not None:
        connection.enabled = data.enabled

    if data.domains is not None:
        connection.domains = _normalize_domains(data.domains)

    if data.auto_provision_users is not None:
        connection.auto_provision_users = data.auto_provision_users

    if data.default_role_id is not None:
        await _validate_role(data.default_role_id, org_id, db_session)
        connection.default_role_id = data.default_role_id

    if data.provider_config is not None:
        config = dict(data.provider_config)
        # The admin form never receives the stored secret back: an empty one
        # keeps it.
        stored = connection.provider_config or {}
        if not config.get("client_secret") and stored.get("client_secret"):
            config["client_secret"] = stored["client_secret"]
        try:
            get_sso_provider(connection.provider).validate_config(config)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        connection.provider_config = config

    connection.updated_at = datetime.utcnow()
    db_session.add(connection)
    await db_session.commit()
    await db_session.refresh(connection)
    return connection


async def delete_sso_connection(
    org_id: int,
    db_session: AsyncSession
) -> bool:
    """Delete the SSO connection of an organization; False if there was none."""
    connection = await get_sso_connection(org_id, db_session)
    if not connection:
        return False
    await db_session.delete(connection)
    await db_session.commit()
    return True


async def check_sso_enabled(
    org_slug: str,
    db_session: AsyncSession
) -> dict:
    """Whether the login page should offer SSO for this organization."""
    connection = await get_sso_connection_by_org_slug(org_slug, db_session)
    if (
        not connection
        or not connection.enabled
        or not await is_sso_feature_enabled(connection.org_id, db_session)
    ):
        return {"sso_enabled": False, "provider": None}
    return {"sso_enabled": True, "provider": connection.provider}


def safe_next_path(next_path: Optional[str]) -> Optional[str]:
    """A same-origin path to land on after sign-in, or None."""
    if not next_path or len(next_path) > 512:
        return None
    if not next_path.startswith("/") or next_path.startswith("//") or "\\" in next_path:
        return None
    return next_path


async def initiate_sso_login(
    org_slug: str,
    db_session: AsyncSession,
    next_path: Optional[str] = None,
    binding: Optional[str] = None,
) -> SSOAuthorizationResponse:
    """Start an SSO sign-in: the IdP URL to send the browser to."""
    org = await _org_by_slug(org_slug, db_session)
    connection = await get_sso_connection(org.id, db_session) if org else None
    if not org or not connection:
        raise HTTPException(
            status_code=404,
            detail="SSO is not configured for this organization"
        )
    if not connection.enabled or not await is_sso_feature_enabled(org.id, db_session):
        raise HTTPException(
            status_code=400,
            detail="SSO is not enabled for this organization"
        )

    from src.services.orgs.auth_policy import enforce_login_auth_method

    await enforce_login_auth_method(db_session, org.id, AUTH_METHOD_SSO)

    state = secrets.token_urlsafe(32)
    redirect_uri = await get_sso_redirect_uri(org, db_session)
    provider = get_sso_provider(connection.provider)
    try:
        authorization_url = await provider.get_authorization_url(
            connection_config=connection.provider_config or {},
            redirect_uri=redirect_uri,
            state=state,
        )
    except SSOConfigurationError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except SSOAuthenticationError as e:
        raise HTTPException(status_code=502, detail=e.message)

    _save_state(state, {
        "org_id": org.id,
        "org_slug": org.slug,
        "provider": connection.provider,
        "redirect_uri": redirect_uri,
        "next": safe_next_path(next_path),
        "binding": binding,
    })
    return SSOAuthorizationResponse(authorization_url=authorization_url, state=state)


def _email_verified_claim(profile: SSOUserProfile) -> Optional[bool]:
    """The IdP's ``email_verified``: True, False, or None when it says nothing.
    Some IdPs send it as the string "true"/"false"."""
    value = (profile.raw_attributes or {}).get("email_verified")
    if isinstance(value, str):
        value = {"true": True, "false": False}.get(value.strip().lower())
    return value if isinstance(value, bool) else None


def _email_marked_unverified(profile: SSOUserProfile) -> bool:
    return _email_verified_claim(profile) is False


def _email_marked_verified(profile: SSOUserProfile) -> bool:
    return _email_verified_claim(profile) is True


def _normalize_domains(domains: list[str]) -> list[str]:
    return sorted({d.strip().lower().lstrip("@") for d in domains if d and d.strip()})


def _email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


async def handle_sso_callback(
    request: Request,
    code: str,
    state: str,
    db_session: AsyncSession,
    binding: Optional[str] = None,
) -> dict:
    """Finish an SSO sign-in: the account it lands on and its organization."""
    state_data = _pop_state(state)
    if not state_data:
        raise _sso_error(
            400, "invalid_state", "state_invalid_or_expired",
            "The SSO session has expired or is invalid. Please try logging in again.",
            provider=None,
        )

    # The state only finishes a sign-in in the browser that started it, so a
    # callback link with someone else's code cannot sign the reader in as them.
    expected = state_data.get("binding")
    if expected and not (binding and hmac.compare_digest(binding, expected)):
        raise _sso_error(
            400, "invalid_state", "state_not_bound",
            "This sign-in was started in another browser. Please try logging in again.",
            provider=None,
        )

    org_id = state_data["org_id"]
    provider_type = state_data["provider"]

    connection = await get_sso_connection(org_id, db_session)
    if (
        not connection
        or not connection.enabled
        or connection.provider != provider_type
        or not await is_sso_feature_enabled(org_id, db_session)
    ):
        raise _sso_error(
            400, "sso_disabled", "sso_not_enabled",
            "SSO is no longer enabled for this organization. Please contact your administrator.",
            provider=provider_type,
        )

    callback_config = dict(connection.provider_config or {})
    callback_config["redirect_uri"] = state_data["redirect_uri"]

    provider = get_sso_provider(provider_type)
    try:
        profile: SSOUserProfile = await provider.handle_callback(
            code=code,
            connection_config=callback_config,
        )
    except SSOAuthenticationError as e:
        raise _sso_error(
            401, "authentication_failed",
            (e.details or {}).get("error", "auth_error"),
            f"SSO authentication failed: {e.message}",
            provider=e.provider,
        )
    except SSOConfigurationError as e:
        raise _sso_error(
            400, "configuration_error", "sso_misconfigured",
            f"SSO configuration error: {e.message}",
            provider=e.provider, field=e.field,
        )

    email = (profile.email or "").strip().lower()
    if "@" not in email or _email_marked_unverified(profile):
        raise _sso_error(
            401, "authentication_failed", "email_not_verified",
            "Your identity provider did not return a verified email address.",
            provider=provider_type,
        )
    profile.email = email

    if connection.domains and _email_domain(email) not in _normalize_domains(connection.domains):
        raise _sso_error(
            403, "domain_not_allowed", "email_domain_rejected",
            f"Email domain '{_email_domain(email)}' is not allowed for SSO login.",
            provider=provider_type,
        )

    user = await find_or_create_sso_user(request, profile, connection, db_session)
    return {
        "user": user,
        "org_id": org_id,
        "org_slug": state_data["org_slug"],
        "next": safe_next_path(state_data.get("next")),
    }


async def find_or_create_sso_user(
    request: Request,
    profile: SSOUserProfile,
    connection: SSOConnection,
    db_session: AsyncSession,
) -> User:
    """The account an SSO sign-in lands on: a new one, or an existing one the
    org's IdP may speak for.

    A matching email alone never takes over an account. An existing account is
    accepted when SSO created it and it never signed in any other way, or when
    it belongs to this organization alone, its email is in a domain the org
    claims for SSO and the IdP says the address is verified.
    """
    from src.security.backoffice import is_user_backoffice_staff

    user = (await db_session.execute(
        select(User).where(func.lower(User.email) == profile.email)
    )).scalars().first()

    if user is None:
        if not connection.auto_provision_users:
            raise _sso_error(
                403, "user_not_found", "auto_provision_disabled",
                "Your user account does not exist. Please contact your administrator for access.",
                provider=connection.provider,
            )
        return await create_sso_user(request, profile, connection, db_session)

    # On a multi-org instance one org's IdP must not mint a platform admin's session.
    # Single tenancy has one org, whose first admin the installer makes backoffice staff.
    if (
        _multi_tenant()
        and user.id is not None
        and await is_user_backoffice_staff(user.id, db_session)
    ):
        raise _sso_error(
            403, "platform_sign_in_required", "platform_sign_in_required",
            "Platform administrators must sign in on the platform's own sign-in page.",
            provider=connection.provider,
        )

    if not await _only_member_of(user, connection.org_id, db_session):
        raise _sso_error(
            403, "account_exists", "account_not_linked",
            "An account with this email already exists. Sign in with your usual method.",
            provider=connection.provider,
        )

    claimed_domain = _email_domain(profile.email) in _normalize_domains(connection.domains or [])
    if claimed_domain and _email_marked_verified(profile):
        from src.services.auth.utils import claim_account_on_inbox_proof

        await claim_account_on_inbox_proof(user, db_session)
    elif not await _is_sso_owned_account(user, db_session):
        raise _sso_error(
            403, "account_exists", "account_not_linked",
            "An account with this email already exists. Sign in with your usual method.",
            provider=connection.provider,
        )

    await _record_sso_login(request, user, connection.org_id, db_session)
    return user


def _multi_tenant() -> bool:
    from config.config import get_learnhouse_config

    return get_learnhouse_config().hosting_config.tenancy == "multi"


async def _only_member_of(user: User, org_id: int, db_session: AsyncSession) -> bool:
    memberships = (await db_session.execute(
        select(UserOrganization.org_id).where(UserOrganization.user_id == user.id)
    )).scalars().all()
    return list(memberships) == [org_id]


async def _is_sso_owned_account(user: User, db_session: AsyncSession) -> bool:
    """SSO created the account and every recorded sign-in went through SSO."""
    from src.db.user_audit_events import UserAuditEvent, UserAuditEventType

    if user.id is None or user.signup_method != AUTH_METHOD_SSO:
        return False
    logins = (await db_session.execute(
        select(UserAuditEvent.audit_metadata).where(
            UserAuditEvent.user_id == user.id,
            UserAuditEvent.event_type == UserAuditEventType.LOGIN,
        )
    )).scalars().all()
    return all((meta or {}).get("method") == AUTH_METHOD_SSO for meta in logins)


async def _org_vouches_for_email(org_id: int, email: str, db_session: AsyncSession) -> bool:
    """Whether the org has a verified custom domain under the email's domain."""
    from src.db.custom_domains import CustomDomain

    email_domain = _email_domain(email)
    domains = (await db_session.execute(
        select(CustomDomain.domain).where(
            CustomDomain.org_id == org_id,
            CustomDomain.status.in_(("verified", "active")),  # type: ignore[attr-defined]
        )
    )).scalars().all()
    return any(d.lower() == email_domain or d.lower().endswith(f".{email_domain}") for d in domains)


async def create_sso_user(
    request: Request,
    profile: SSOUserProfile,
    connection: SSOConnection,
    db_session: AsyncSession,
) -> User:
    """A new account for a first-time SSO sign-in, already in the organization."""
    from src.services.users.users import create_user

    username_parts = [p for p in (profile.first_name, profile.last_name) if p]
    if not username_parts:
        username_parts.append(profile.email.split("@")[0] or "user")
    username = "".join(username_parts) + str(random.randint(100000, 999999))

    created = await create_user(
        request,
        db_session,
        AnonymousUser(),
        UserCreate(
            email=profile.email,
            username=username,
            password="",
            first_name=profile.first_name or "",
            last_name=profile.last_name or "",
            avatar_image=profile.avatar_url or "",
        ),
        connection.org_id,
        is_oauth=True,
        signup_provider=AUTH_METHOD_SSO,
    )
    user = (await db_session.execute(
        select(User).where(User.id == created.id)
    )).scalars().one()

    if not await _org_vouches_for_email(connection.org_id, profile.email, db_session):
        from src.services.users.users import _INBOX_UNPROVEN

        user.email_verified_at = _INBOX_UNPROVEN
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

    if connection.default_role_id and connection.default_role_id != DEFAULT_SSO_ROLE_ID:
        membership = (await db_session.execute(
            select(UserOrganization).where(
                UserOrganization.user_id == user.id,
                UserOrganization.org_id == connection.org_id,
            )
        )).scalars().first()
        if membership is not None:
            membership.role_id = connection.default_role_id
            db_session.add(membership)
            await db_session.commit()
            from src.routers.users import _invalidate_session_cache

            _invalidate_session_cache(user.id)

    await _record_sso_login(request, user, connection.org_id, db_session)
    return user


async def _record_sso_login(request: Request, user: User, org_id: int, db_session: AsyncSession) -> None:
    from src.db.user_audit_events import UserAuditEventType
    from src.services.audit.audit import record_audit_event
    from src.services.security.account_lockout import update_login_info
    from src.services.security.rate_limiting import get_client_ip

    client_ip = get_client_ip(request)
    await update_login_info(user, client_ip, db_session)
    if user.id is not None:
        await record_audit_event(
            event_type=UserAuditEventType.LOGIN,
            user_id=user.id,
            org_id=org_id,
            ip=client_ip,
            user_agent=request.headers.get("user-agent"),
            metadata={"method": AUTH_METHOD_SSO},
        )


async def get_provider_setup_url(
    org_id: int,
    return_url: str,
    db_session: AsyncSession
) -> Optional[str]:
    """The provider's admin portal URL, for providers that have one."""
    connection = await get_sso_connection(org_id, db_session)
    if not connection:
        return None
    try:
        return await get_sso_provider(connection.provider).get_setup_url(
            connection_config=connection.provider_config or {},
            return_url=return_url,
        )
    except Exception:
        return None


def get_available_providers() -> list[dict]:
    """Supported SSO providers and whether each is configured."""
    return get_providers()
