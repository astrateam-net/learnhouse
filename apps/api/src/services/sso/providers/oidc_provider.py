"""
Custom OIDC SSO provider implementation.

Allows enterprise self-hosters to connect directly to any OpenID Connect
compatible identity provider (Azure AD, Okta, Google Workspace, Keycloak, etc.)
without requiring a third-party SSO broker like WorkOS.
"""

import os
import httpx
from typing import Optional
from urllib.parse import urlencode, urlsplit

from src.services.utils.ssrf_guard import SSRFBlockedError, pinned_transport

from .base import SSOProvider, SSOUserProfile, SSOAuthenticationError, SSOConfigurationError


class CustomOIDCProvider(SSOProvider):
    """
    Custom OIDC provider for direct IdP connections.

    Supports any OpenID Connect 1.0 compatible identity provider.
    Uses OIDC Discovery to automatically configure endpoints.
    """

    def __init__(self):
        """Initialize the Custom OIDC provider."""
        # Cache for OIDC discovery documents
        self._discovery_cache: dict[str, dict] = {}

    @property
    def provider_name(self) -> str:
        return "custom_oidc"

    @property
    def has_setup_portal(self) -> bool:
        # Custom OIDC doesn't have an admin portal - config is done in LearnHouse
        return False

    def is_configured(self) -> bool:
        """Custom OIDC is always available - config is per-organization."""
        return True

    @staticmethod
    def _trusted_hosts() -> set[str]:
        """``LEARNHOUSE_SSO_TRUSTED_HOSTS``: comma-separated IdP hostnames the
        instance may reach on private addresses (a self-hosted IdP on the LAN)."""
        raw = os.getenv("LEARNHOUSE_SSO_TRUSTED_HOSTS", "")
        return {h.strip().lower().rstrip(".") for h in raw.split(",") if h.strip()}

    def _client(self, url: str) -> httpx.AsyncClient:
        """IdP requests connect only to public addresses (SSRF guard), unless
        the host is one the operator trusts."""
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        if host and host in self._trusted_hosts():
            return httpx.AsyncClient(timeout=10.0, follow_redirects=False)
        return httpx.AsyncClient(transport=pinned_transport(), timeout=10.0, follow_redirects=False)

    def _require_https(self, url: str, field: str) -> None:
        if not isinstance(url, str) or not url.startswith("https://"):
            raise SSOConfigurationError(
                f"OIDC {field} must be an https:// URL",
                provider=self.provider_name,
                field="issuer_url",
            )

    async def _get_discovery_document(self, issuer_url: str) -> dict:
        """
        Fetch OIDC discovery document from the issuer.

        Args:
            issuer_url: The OIDC issuer URL

        Returns:
            Discovery document with endpoints

        Raises:
            SSOConfigurationError: If discovery fails
        """
        # Check cache first
        if issuer_url in self._discovery_cache:
            return self._discovery_cache[issuer_url]

        # Normalize issuer URL
        issuer = issuer_url.rstrip('/')
        discovery_url = f"{issuer}/.well-known/openid-configuration"

        # SECURITY: an admin-configured ``issuer_url`` must not reach
        # 127.0.0.1, 169.254.169.254 (cloud metadata) or internal ranges.
        self._require_https(discovery_url, "issuer_url")
        try:
            async with self._client(discovery_url) as client:
                response = await client.get(discovery_url)
                response.raise_for_status()
                discovery = response.json()

                required_fields = ['authorization_endpoint', 'token_endpoint']
                for field in required_fields:
                    if field not in discovery:
                        raise SSOConfigurationError(
                            f"OIDC discovery document missing required field: {field}",
                            provider=self.provider_name,
                            field="issuer_url"
                        )
                for field in ('token_endpoint', 'userinfo_endpoint'):
                    if discovery.get(field):
                        self._require_https(discovery[field], field)

                # Cache the discovery document
                self._discovery_cache[issuer_url] = discovery
                return discovery

        except (httpx.HTTPError, SSRFBlockedError, ValueError) as e:
            raise SSOConfigurationError(
                f"Failed to fetch OIDC discovery document from {discovery_url}: {str(e)}",
                provider=self.provider_name,
                field="issuer_url"
            )

    async def get_authorization_url(
        self,
        connection_config: dict,
        redirect_uri: str,
        state: str
    ) -> str:
        """
        Generate OIDC authorization URL.

        Args:
            connection_config: Must contain issuer_url, client_id
            redirect_uri: Callback URL after authentication
            state: State parameter for CSRF protection

        Returns:
            Authorization URL to redirect user to
        """
        issuer_url = connection_config.get("issuer_url")
        client_id = connection_config.get("client_id")
        scopes = connection_config.get("scopes", "openid email profile")

        if not issuer_url or not client_id:
            raise SSOConfigurationError(
                "OIDC configuration requires issuer_url and client_id",
                provider=self.provider_name
            )

        # Get discovery document
        discovery = await self._get_discovery_document(issuer_url)
        auth_endpoint = discovery['authorization_endpoint']

        # Build authorization URL
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scopes,
            "state": state,
        }

        # Add nonce for additional security
        import secrets
        params["nonce"] = secrets.token_urlsafe(32)

        return f"{auth_endpoint}?{urlencode(params)}"

    async def handle_callback(
        self,
        code: str,
        connection_config: dict
    ) -> SSOUserProfile:
        """
        Exchange authorization code for user profile.

        Args:
            code: Authorization code from callback
            connection_config: Must contain issuer_url, client_id, client_secret

        Returns:
            Normalized user profile
        """
        issuer_url = connection_config.get("issuer_url")
        client_id = connection_config.get("client_id")
        client_secret = connection_config.get("client_secret")
        redirect_uri = connection_config.get("redirect_uri")

        if not all([issuer_url, client_id, client_secret]):
            raise SSOConfigurationError(
                "OIDC configuration requires issuer_url, client_id, and client_secret",
                provider=self.provider_name
            )

        # Get discovery document
        discovery = await self._get_discovery_document(issuer_url)
        token_endpoint = discovery['token_endpoint']
        userinfo_endpoint = discovery.get('userinfo_endpoint')

        try:
            async with self._client(token_endpoint) as client:
                token_response = await client.post(
                    token_endpoint,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": redirect_uri,
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                )

                if token_response.status_code != 200:
                    error_data = token_response.json() if token_response.content else {}
                    raise SSOAuthenticationError(
                        f"Token exchange failed: {error_data.get('error_description', error_data.get('error', 'Unknown error'))}",
                        provider=self.provider_name,
                        details=error_data
                    )

                tokens = token_response.json()
                access_token = tokens.get("access_token")
                id_token = tokens.get("id_token")

                # Try to get user info from userinfo endpoint first
                user_info = {}
                if userinfo_endpoint and access_token:
                    async with self._client(userinfo_endpoint) as userinfo_client:
                        userinfo_response = await userinfo_client.get(
                            userinfo_endpoint,
                            headers={"Authorization": f"Bearer {access_token}"}
                        )
                    if userinfo_response.status_code == 200:
                        user_info = userinfo_response.json()

                # If no userinfo, try to decode id_token (basic decode, not verification)
                if not user_info and id_token:
                    user_info = self._decode_id_token_claims(id_token)

                if not user_info.get("email"):
                    raise SSOAuthenticationError(
                        "Could not retrieve user email from identity provider",
                        provider=self.provider_name,
                        details={"user_info": user_info}
                    )

                return SSOUserProfile(
                    email=user_info["email"],
                    first_name=user_info.get("given_name") or user_info.get("first_name"),
                    last_name=user_info.get("family_name") or user_info.get("last_name"),
                    avatar_url=user_info.get("picture"),
                    provider_user_id=user_info.get("sub"),
                    raw_attributes=user_info
                )

        except (httpx.HTTPError, SSRFBlockedError, ValueError) as e:
            raise SSOAuthenticationError(
                f"Failed to communicate with identity provider: {str(e)}",
                provider=self.provider_name,
                details={"error": str(e)}
            )

    def _decode_id_token_claims(self, id_token: str) -> dict:
        """
        Decode ID token claims without verification.

        Note: In production, you should verify the token signature.
        This is a simplified implementation for extracting claims.

        Args:
            id_token: JWT ID token

        Returns:
            Decoded claims dictionary
        """
        import base64
        import json

        try:
            # JWT has 3 parts: header.payload.signature
            parts = id_token.split('.')
            if len(parts) != 3:
                return {}

            # Decode payload (middle part)
            payload = parts[1]
            # Add padding if needed
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += '=' * padding

            decoded = base64.urlsafe_b64decode(payload)
            return json.loads(decoded)

        except Exception:
            return {}

    async def get_setup_url(
        self,
        connection_config: dict,
        return_url: str
    ) -> Optional[str]:
        """
        Custom OIDC doesn't have a setup portal.

        Returns:
            None - configuration is done directly in LearnHouse
        """
        return None

    def validate_config(self, config: dict) -> bool:
        """
        Validate OIDC configuration.

        Args:
            config: Configuration dictionary

        Returns:
            True if valid

        Raises:
            ValueError: If configuration is invalid
        """
        required = ["issuer_url", "client_id", "client_secret"]
        missing = [f for f in required if not config.get(f)]

        if missing:
            raise ValueError(f"Missing required OIDC configuration: {', '.join(missing)}")

        # Validate issuer_url format
        issuer_url = config.get("issuer_url", "")
        if not issuer_url.startswith("https://"):
            raise ValueError("issuer_url must be a valid URL starting with https://")

        return True

    def get_config_fields(self) -> list[dict]:
        """Return configuration fields for Custom OIDC."""
        return [
            {
                "name": "issuer_url",
                "type": "string",
                "required": True,
                "description": "OIDC Issuer URL (supports auto-discovery)",
                "placeholder": "https://login.microsoftonline.com/{tenant}/v2.0"
            },
            {
                "name": "client_id",
                "type": "string",
                "required": True,
                "description": "OAuth 2.0 Client ID from your identity provider",
                "placeholder": "your-client-id"
            },
            {
                "name": "client_secret",
                "type": "password",
                "required": True,
                "description": "OAuth 2.0 Client Secret from your identity provider",
                "placeholder": "your-client-secret"
            },
            {
                "name": "scopes",
                "type": "string",
                "required": False,
                "description": "OAuth scopes (space-separated)",
                "placeholder": "openid email profile"
            }
        ]
