"""The authenticated caller, independent of how the token was verified."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    """The verified identity of the caller.

    ``subject`` is stable and unique per identity provider: the Clerk user id
    in clerk mode, or ``dev|<X-Dev-User value>`` in dev mode.
    """

    subject: str
    email: str | None = None
    display_name: str | None = None
