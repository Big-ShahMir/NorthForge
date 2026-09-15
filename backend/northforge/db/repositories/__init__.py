"""Repository layer: typed, session-scoped data access for the domain model.

Repositories never commit; the caller (a route or a test) commits or rolls
back. They ``flush()`` where needed so generated ids are available before
the method returns. Authorization is enforced inside every ``*_for_owner``
read by joining through project ownership and returning ``None`` when a row
does not exist or belongs to another owner -- callers turn ``None`` into a
404 ``NotFoundError``, deliberately not a 403, to avoid leaking whether a
resource exists at all.
"""

from __future__ import annotations

from northforge.db.repositories.projects import ProjectsRepository
from northforge.db.repositories.runs import RunsRepository
from northforge.db.repositories.users import UsersRepository
from northforge.db.repositories.workflows import WorkflowsRepository

__all__ = [
    "ProjectsRepository",
    "RunsRepository",
    "UsersRepository",
    "WorkflowsRepository",
]
