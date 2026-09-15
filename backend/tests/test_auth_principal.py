from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi import Request

from northforge.auth.dependencies import get_principal
from northforge.auth.principal import Principal
from northforge.core.errors import AuthenticationError


class _FakeHeaders:
    def __init__(self, headers: dict[str, str]) -> None:
        self._headers = headers

    def get(self, key: str) -> str | None:
        return self._headers.get(key)


class _FakeSettings:
    def __init__(self, auth_mode: str) -> None:
        self.auth_mode = auth_mode


class _FakeState:
    def __init__(self, auth_mode: str, token_verifier: Any) -> None:
        self.settings = _FakeSettings(auth_mode)
        self.token_verifier = token_verifier


class _FakeApp:
    def __init__(self, auth_mode: str, token_verifier: Any) -> None:
        self.state = _FakeState(auth_mode, token_verifier)


class _FakeRequest:
    def __init__(self, headers: dict[str, str], auth_mode: str, token_verifier: Any = None) -> None:
        self.headers = _FakeHeaders(headers)
        self.app = _FakeApp(auth_mode, token_verifier)


class StubVerifier:
    def __init__(self, principal: Principal) -> None:
        self._principal = principal
        self.received_token: str | None = None

    def verify(self, token: str) -> Principal:
        self.received_token = token
        return self._principal


def _request(headers: dict[str, str], *, auth_mode: str, token_verifier: Any = None) -> Request:
    """Build a minimal stand-in for ``fastapi.Request`` with only the surface
    ``get_principal`` reads: ``headers.get`` and ``app.state``.
    """
    return cast(Request, _FakeRequest(headers, auth_mode, token_verifier))


def test_dev_mode_with_header_returns_dev_principal() -> None:
    request = _request({"X-Dev-User": "alice"}, auth_mode="dev")

    principal = get_principal(request)

    assert principal == Principal(subject="dev|alice")


def test_dev_mode_without_header_raises() -> None:
    request = _request({}, auth_mode="dev")

    with pytest.raises(AuthenticationError):
        get_principal(request)


def test_clerk_mode_missing_authorization_header_raises() -> None:
    request = _request({}, auth_mode="clerk")

    with pytest.raises(AuthenticationError):
        get_principal(request)


def test_clerk_mode_malformed_authorization_header_raises() -> None:
    request = _request({"Authorization": "Token abc"}, auth_mode="clerk")

    with pytest.raises(AuthenticationError):
        get_principal(request)


def test_clerk_mode_empty_bearer_token_raises() -> None:
    request = _request({"Authorization": "Bearer "}, auth_mode="clerk")

    with pytest.raises(AuthenticationError):
        get_principal(request)


def test_clerk_mode_valid_token_delegates_to_verifier() -> None:
    expected = Principal(subject="user_123", email="a@example.com")
    stub = StubVerifier(expected)
    request = _request({"Authorization": "Bearer a-token"}, auth_mode="clerk", token_verifier=stub)

    principal = get_principal(request)

    assert principal == expected
    assert stub.received_token == "a-token"  # noqa: S105 - test fixture value, not a secret


def test_clerk_mode_without_token_verifier_raises_runtime_error() -> None:
    request = _request({"Authorization": "Bearer a-token"}, auth_mode="clerk")

    with pytest.raises(RuntimeError):
        get_principal(request)
