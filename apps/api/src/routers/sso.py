"""
SSO (Single Sign-On) API endpoints.

Provides endpoints for SSO configuration (admin) and authentication (public).
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.organizations import Organization
from src.db.sso import (
    SSOAuthorizationResponse,
    SSOConnection,
    SSOConnectionCreate,
    SSOConnectionRead,
    SSOConnectionUpdate,
    SSOLoginCheckResponse,
    SSOProviderInfo,
)
from src.db.users import PublicUser, UserRead
from src.security.auth import get_current_user
from src.security.session_context import AUTH_METHOD_SSO
from src.services.orgs.orgs import rbac_check
from src.services.sso import (
    check_sso_enabled,
    create_sso_connection,
    delete_sso_connection,
    get_available_providers,
    get_provider_setup_url,
    get_sso_connection,
    handle_sso_callback,
    initiate_sso_login,
    update_sso_connection,
)
from src.services.sso.sso import is_sso_feature_enabled


router = APIRouter()

SECRET_CONFIG_KEYS = ("client_secret",)


async def verify_org_admin_and_sso_feature(
    org_id: int,
    request: Request,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> None:
    """The caller administers the org, and the org may use SSO."""
    org = (await db_session.execute(
        select(Organization).where(Organization.id == org_id)
    )).scalars().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    await rbac_check(request, org.org_uuid, current_user, "update", db_session)
    if not await is_sso_feature_enabled(org.id, db_session):
        raise HTTPException(status_code=403, detail="SSO is not enabled for this organization")


def _read(connection: SSOConnection) -> SSOConnectionRead:
    """The connection as the admin UI sees it: secrets are never sent back."""
    read = SSOConnectionRead.model_validate(connection)
    config = dict(read.provider_config or {})
    for key in SECRET_CONFIG_KEYS:
        if config.get(key):
            config[key] = ""
    read.provider_config = config
    return read


def _get_idp_error_message(error_code: str) -> str:
    """Human-readable text for the standard OAuth 2.0 / OIDC error codes."""
    error_messages = {
        "access_denied": "Access was denied. You may have declined the login request or don't have permission to access this application.",
        "invalid_request": "The authentication request was invalid. Please try again.",
        "unauthorized_client": "This application is not authorized to request authentication.",
        "unsupported_response_type": "The authentication method is not supported.",
        "invalid_scope": "The requested permissions are invalid.",
        "server_error": "The identity provider encountered an internal error. Please try again later.",
        "temporarily_unavailable": "The identity provider is temporarily unavailable. Please try again later.",
        "interaction_required": "User interaction is required. Please try logging in again.",
        "login_required": "You need to log in to your identity provider first.",
        "account_selection_required": "Please select an account to continue.",
        "consent_required": "You need to grant consent to access this application.",
        "invalid_grant": "The authorization code has expired or is invalid. Please try again.",
        "invalid_client": "Client authentication failed. Please contact your administrator.",
        "user_canceled": "You canceled the login process.",
    }
    return error_messages.get(error_code, f"Authentication error: {error_code}")


# ============================================================================
# Admin endpoints (require org admin)
# ============================================================================

_ADMIN_RESPONSES = {
    401: {"description": "Authentication required"},
    403: {"description": "Caller lacks admin rights, or SSO is not enabled for the organization"},
    404: {"description": "Organization not found"},
}


@router.get(
    "/providers",
    response_model=list[SSOProviderInfo],
    summary="List available SSO providers",
    description="List the supported SSO providers with their configuration fields. Requires org admin.",
    responses={200: {"description": "Supported SSO providers with their metadata."}, **_ADMIN_RESPONSES},
)
async def list_sso_providers(
    *,
    request: Request,
    org_id: int,
    current_user: PublicUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    await verify_org_admin_and_sso_feature(org_id, request, current_user, db_session)
    return get_available_providers()


@router.get(
    "/{org_id}/config",
    response_model=Optional[SSOConnectionRead],
    summary="Get SSO configuration",
    description=(
        "The organization's SSO configuration, or null if none is set up. "
        "Secrets come back empty. Requires org admin."
    ),
    responses={200: {"description": "SSO connection if configured, otherwise null."}, **_ADMIN_RESPONSES},
)
async def get_sso_config(
    *,
    request: Request,
    org_id: int,
    current_user: PublicUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    await verify_org_admin_and_sso_feature(org_id, request, current_user, db_session)
    connection = await get_sso_connection(org_id, db_session)
    return _read(connection) if connection else None


@router.post(
    "/{org_id}/config",
    response_model=SSOConnectionRead,
    summary="Create SSO configuration",
    description="Set up SSO for an organization with the chosen provider and its settings. Requires org admin.",
    responses={200: {"description": "Newly created SSO connection."}, **_ADMIN_RESPONSES},
)
async def create_sso_config(
    *,
    request: Request,
    org_id: int,
    data: SSOConnectionCreate,
    current_user: PublicUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    await verify_org_admin_and_sso_feature(org_id, request, current_user, db_session)
    return _read(await create_sso_connection(org_id, data, db_session))


@router.put(
    "/{org_id}/config",
    response_model=SSOConnectionRead,
    summary="Update SSO configuration",
    description=(
        "Change an organization's SSO settings. An empty client secret keeps "
        "the stored one. Requires org admin."
    ),
    responses={200: {"description": "Updated SSO connection."}, **_ADMIN_RESPONSES},
)
async def update_sso_config(
    *,
    request: Request,
    org_id: int,
    data: SSOConnectionUpdate,
    current_user: PublicUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    await verify_org_admin_and_sso_feature(org_id, request, current_user, db_session)
    return _read(await update_sso_connection(org_id, data, db_session))


@router.delete(
    "/{org_id}/config",
    summary="Delete SSO configuration",
    description="Turn SSO off for an organization by removing its SSO connection. Requires org admin.",
    responses={200: {"description": "SSO configuration deleted."}, **_ADMIN_RESPONSES},
)
async def delete_sso_config(
    *,
    request: Request,
    org_id: int,
    current_user: PublicUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    await verify_org_admin_and_sso_feature(org_id, request, current_user, db_session)
    if not await delete_sso_connection(org_id, db_session):
        raise HTTPException(status_code=404, detail="SSO configuration not found")
    return {"message": "SSO configuration deleted successfully"}


@router.get(
    "/{org_id}/setup-url",
    summary="Get provider setup URL",
    description=(
        "The provider's admin portal URL for configuring the identity provider, "
        "for providers that have one (WorkOS). Requires org admin."
    ),
    responses={
        200: {"description": "Setup URL for the admin portal."},
        400: {"description": "No setup URL: SSO is not configured or the provider has no portal"},
        **_ADMIN_RESPONSES,
    },
)
async def get_setup_url(
    *,
    request: Request,
    org_id: int,
    return_url: str = Query(..., description="URL to return to after setup"),
    current_user: PublicUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    await verify_org_admin_and_sso_feature(org_id, request, current_user, db_session)
    setup_url = await get_provider_setup_url(org_id, return_url, db_session)
    if not setup_url:
        raise HTTPException(
            status_code=400,
            detail="Setup URL is not available. Please configure SSO first or provider does not support setup portal."
        )
    return {"setup_url": setup_url}


# ============================================================================
# Public authentication endpoints
# ============================================================================

@router.get(
    "/check",
    response_model=SSOLoginCheckResponse,
    summary="Check if SSO is enabled",
    description="Whether the login page should show the SSO button for an organization.",
    responses={200: {"description": "Whether SSO is enabled for the organization."}},
)
async def check_sso(
    *,
    org_slug: str = Query(..., description="Organization slug"),
    db_session: AsyncSession = Depends(get_db_session),
):
    return SSOLoginCheckResponse(**(await check_sso_enabled(org_slug, db_session)))


@router.get(
    "/authorize",
    response_model=SSOAuthorizationResponse,
    summary="Initiate SSO login flow",
    description="Start an SSO sign-in: the identity provider URL to send the browser to.",
    responses={
        200: {"description": "Authorization URL and state for the IdP redirect."},
        400: {"description": "SSO is not enabled for this organization"},
        403: {"description": "The organization does not allow signing in with SSO"},
        404: {"description": "SSO is not configured for this organization"},
    },
)
async def authorize_sso(
    *,
    org_slug: str = Query(..., description="Organization slug"),
    db_session: AsyncSession = Depends(get_db_session),
):
    return await initiate_sso_login(org_slug, db_session)


class SSOErrorResponse(BaseModel):
    """Error body of the SSO endpoints."""
    error: str
    error_code: str
    error_description: str
    provider: Optional[str] = None
    details: Optional[dict] = None


@router.get(
    "/callback",
    summary="Handle SSO callback",
    description=(
        "Finish an SSO sign-in: exchange the authorization code at the identity "
        "provider, resolve the account, set the session cookies and return the "
        "user with its tokens. With a confirmed second factor, no cookies are "
        "set and the body is {mfa_required: true, mfa_token} instead."
    ),
    responses={
        200: {"description": "Signed-in user with tokens, or a second-factor challenge."},
        400: {"description": "The IdP returned an error, parameters are missing, or the state is invalid"},
        401: {"description": "The identity provider did not authenticate the user"},
        403: {"description": "The account may not sign in through this organization's SSO"},
    },
)
async def sso_callback(
    *,
    request: Request,
    response: Response,
    code: Optional[str] = Query(None, description="Authorization code from IdP"),
    state: Optional[str] = Query(None, description="State parameter for CSRF verification"),
    error: Optional[str] = Query(None, description="Error code from IdP"),
    error_description: Optional[str] = Query(None, description="Error description from IdP"),
    db_session: AsyncSession = Depends(get_db_session),
):
    from src.routers.auth import _bind_session_to_custom_domain, get_token_expiry_ms, set_auth_cookies
    from src.services.auth.session import issue_session_or_challenge, mint_session_tokens, track_login
    from src.services.nudges.links import org_base_url
    from src.services.orgs.mfa_policy import get_org_mfa_policy

    if error:
        error_msg = error_description or _get_idp_error_message(error)
        raise HTTPException(
            status_code=400,
            detail={
                "error": "idp_error",
                "error_code": error,
                "error_description": error_msg,
                "message": f"Identity provider error: {error_msg}",
            },
        )
    if not code or not state:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "missing_parameters",
                "error_code": "missing_params",
                "error_description": "Missing required parameters: code and state",
                "message": "SSO callback is missing required parameters. Please try logging in again.",
            },
        )

    result = await handle_sso_callback(request, code, state, db_session)
    user = result["user"]
    org_id = result["org_id"]
    redirect_url = f"{await org_base_url(result['org_slug'], db_session, org_id)}/redirect_from_auth"

    session_org_id, on_custom_domain = await _bind_session_to_custom_domain(
        request, db_session, user.id, org_id, AUTH_METHOD_SSO
    )
    policy = await get_org_mfa_policy(db_session, org_id)
    if policy.exempt_external_auth:
        # The org trusts its IdP's second factor: no LearnHouse code on top of it.
        issue = mint_session_tokens(user.email, AUTH_METHOD_SSO, session_org_id, on_custom_domain)
        await track_login(user.id, AUTH_METHOD_SSO, session_org_id)
    else:
        issue = await issue_session_or_challenge(
            db_session, user, amr=AUTH_METHOD_SSO, org_id=session_org_id, custom_domain=on_custom_domain,
        )
    if issue.mfa_required:
        return {
            "mfa_required": True,
            "mfa_token": issue.mfa_token,
            "redirect_url": redirect_url,
            "org_slug": result["org_slug"],
        }

    set_auth_cookies(response, issue.access_token, issue.refresh_token, request)
    return {
        "user": UserRead.model_validate(user),
        "tokens": {
            "access_token": issue.access_token,
            "refresh_token": issue.refresh_token,
            "expiry": get_token_expiry_ms(),
        },
        "redirect_url": redirect_url,
        "org_slug": result["org_slug"],
    }
