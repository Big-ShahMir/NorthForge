"""FastAPI dependencies for resolving the authenticated caller.

``get_principal`` verifies the bearer token (or trusts the dev header) and
returns a ``Principal``. ``get_current_user`` maps that principal onto a
persisted ``User`` row via the users repository, upserting it on every
request so a caller seen for the first time is created automatically.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from northforge.api.deps import get_session
from northforge.auth.principal import Principal
from northforge.core.errors import AuthenticationError
from northforge.db.models import User
from northforge.db.repositories.users import UsersRepository

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


async def get_current_user(
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """The persisted ``User`` row for the current caller, upserted from ``principal``."""
    return await UsersRepository(session).upsert_from_principal(principal)
