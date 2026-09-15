"""Users repository: upsert-by-identity for the authenticated caller."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from northforge.auth.principal import Principal
from northforge.db.models import User


class UsersRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_from_principal(self, principal: Principal) -> User:
        """Insert or refresh the ``User`` row identified by ``principal.subject``.

        A new value for ``email``/``display_name`` overwrites the stored one;
        a missing (``None``) value never clobbers what is already stored,
        via ``COALESCE(EXCLUDED.column, users.column)``.
        """
        insert_stmt = pg_insert(User).values(
            clerk_user_id=principal.subject,
            email=principal.email,
            display_name=principal.display_name,
        )
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[User.clerk_user_id],
            set_={
                "email": func.coalesce(insert_stmt.excluded.email, User.email),
                "display_name": func.coalesce(insert_stmt.excluded.display_name, User.display_name),
                "updated_at": func.now(),
            },
        ).returning(User)
        result = await self._session.execute(upsert_stmt)
        user: User = result.scalar_one()
        return user
