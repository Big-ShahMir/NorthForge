"""FastAPI dependency for resolving the authenticated caller.

Only ``get_principal`` lives here. Mapping a ``Principal`` to a persisted
``User`` row (``get_current_user``) is added alongside the database session
dependency by other Phase 1 work.
"""

from __future__ import annotations

from fastapi import Request

from northforge.auth.principal import Principal
from northforge.core.errors import AuthenticationError

DEV_USER_HEADER = "X-Dev-User"


def get_principal(request: Request) -> Principal:
    """Resolve the authenticated caller from the request.

    Dev mode (``settings.auth_mode == "dev"``) trusts the ``X-Dev-User``
    header; this mode is rejected in production by ``Settings`` validation.
    Clerk mode reads a bearer token from ``Authorization`` and verifies it
    with ``request.app.state.token_verifier``.
    """
    settings = request.app.state.settings

    if settings.auth_mode == "dev":
        value = request.headers.get(DEV_USER_HEADER)
        if not value:
            raise AuthenticationError("Missing X-Dev-User header (dev auth mode).")
        return Principal(subject=f"dev|{value}")

    authorization = request.headers.get("Authorization")
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthenticationError("Missing bearer token.")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise AuthenticationError("Missing bearer token.")

    verifier = getattr(request.app.state, "token_verifier", None)
    if verifier is None:
        raise RuntimeError("token verifier not initialised; app lifespan did not run")
    return verifier.verify(token)  # type: ignore[no-any-return]
