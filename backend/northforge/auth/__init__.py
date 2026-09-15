"""Authentication: the ``Principal`` identity, token verification, and the
``get_principal`` FastAPI dependency.
"""

from __future__ import annotations

from northforge.auth.dependencies import get_principal
from northforge.auth.principal import Principal
from northforge.auth.tokens import ClerkTokenVerifier, TokenVerifier

__all__ = ["ClerkTokenVerifier", "Principal", "TokenVerifier", "get_principal"]
