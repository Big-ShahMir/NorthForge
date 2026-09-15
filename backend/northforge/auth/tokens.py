"""Session token verification.

``ClerkTokenVerifier`` verifies Clerk-issued RS256 session tokens against
Clerk's published JWKS. Token contents are never logged: failures surface a
single user-safe message and nothing about the token itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import jwt

from northforge.auth.principal import Principal
from northforge.core.errors import AuthenticationError

_INVALID_TOKEN_MESSAGE = "Invalid or expired session token."  # noqa: S105 - not a secret


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Principal: ...


class ClerkTokenVerifier:
    """Verifies Clerk session tokens using Clerk's JSON Web Key Set."""

    def __init__(
        self,
        jwks_url: str,
        issuer: str,
        authorized_parties: Sequence[str] = (),
        leeway_seconds: int = 10,
        jwk_client: jwt.PyJWKClient | None = None,
    ) -> None:
        self._issuer = issuer
        self._authorized_parties = tuple(authorized_parties)
        self._leeway_seconds = leeway_seconds
        self._jwk_client = jwk_client or jwt.PyJWKClient(jwks_url)

    def verify(self, token: str) -> Principal:
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=self._issuer,
                leeway=self._leeway_seconds,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError(_INVALID_TOKEN_MESSAGE) from exc

        if self._authorized_parties and claims.get("azp") not in self._authorized_parties:
            raise AuthenticationError(_INVALID_TOKEN_MESSAGE)

        subject = claims.get("sub")
        if not subject:
            raise AuthenticationError(_INVALID_TOKEN_MESSAGE)

        return Principal(
            subject=subject,
            email=claims.get("email"),
            display_name=_display_name_of(claims),
        )


def _display_name_of(claims: dict[str, object]) -> str | None:
    name = claims.get("name")
    if isinstance(name, str) and name:
        return name
    first_name = claims.get("first_name")
    last_name = claims.get("last_name")
    parts = [part for part in (first_name, last_name) if isinstance(part, str) and part]
    if parts:
        return " ".join(parts)
    return None
