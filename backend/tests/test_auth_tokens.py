from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

from northforge.auth.principal import Principal
from northforge.auth.tokens import ClerkTokenVerifier
from northforge.core.errors import AuthenticationError

ISSUER = "https://example.clerk.accounts.dev"
AUTHORIZED_PARTY = "https://app.example.com"


class StubJWKClient:
    """Stands in for ``jwt.PyJWKClient``: always returns the same test key."""

    def __init__(self, public_key: RSAPublicKey) -> None:
        self._public_key = public_key

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        return SimpleNamespace(key=self._public_key)


@pytest.fixture(scope="module")
def keypair() -> tuple[RSAPrivateKey, RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def verifier(keypair: tuple[RSAPrivateKey, RSAPublicKey]) -> ClerkTokenVerifier:
    _private_key, public_key = keypair
    return ClerkTokenVerifier(
        jwks_url="https://unused.example/.well-known/jwks.json",
        issuer=ISSUER,
        authorized_parties=[AUTHORIZED_PARTY],
        jwk_client=StubJWKClient(public_key),  # type: ignore[arg-type]
    )


def _make_token(private_key: RSAPrivateKey, /, **claim_overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": "user_123",
        "iss": ISSUER,
        "azp": AUTHORIZED_PARTY,
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, private_key, algorithm="RS256")


def test_valid_token_maps_to_principal(
    verifier: ClerkTokenVerifier, keypair: tuple[RSAPrivateKey, RSAPublicKey]
) -> None:
    private_key, _public_key = keypair
    token = _make_token(private_key, email="jane@example.com", name="Jane Doe")

    principal = verifier.verify(token)

    assert principal == Principal(
        subject="user_123", email="jane@example.com", display_name="Jane Doe"
    )


def test_expired_token_is_rejected(
    verifier: ClerkTokenVerifier, keypair: tuple[RSAPrivateKey, RSAPublicKey]
) -> None:
    private_key, _public_key = keypair
    now = int(time.time())
    token = _make_token(private_key, iat=now - 7200, exp=now - 3600)

    with pytest.raises(AuthenticationError):
        verifier.verify(token)


def test_wrong_issuer_is_rejected(
    verifier: ClerkTokenVerifier, keypair: tuple[RSAPrivateKey, RSAPublicKey]
) -> None:
    private_key, _public_key = keypair
    token = _make_token(private_key, iss="https://not-the-issuer.example")

    with pytest.raises(AuthenticationError):
        verifier.verify(token)


def test_wrong_authorized_party_is_rejected(
    verifier: ClerkTokenVerifier, keypair: tuple[RSAPrivateKey, RSAPublicKey]
) -> None:
    private_key, _public_key = keypair
    token = _make_token(private_key, azp="https://not-authorized.example")

    with pytest.raises(AuthenticationError):
        verifier.verify(token)


def test_missing_sub_is_rejected(
    verifier: ClerkTokenVerifier, keypair: tuple[RSAPrivateKey, RSAPublicKey]
) -> None:
    private_key, _public_key = keypair
    now = int(time.time())
    token = jwt.encode(
        {"iss": ISSUER, "azp": AUTHORIZED_PARTY, "iat": now, "exp": now + 3600},
        private_key,
        algorithm="RS256",
    )

    with pytest.raises(AuthenticationError):
        verifier.verify(token)


def test_tampered_signature_is_rejected(
    verifier: ClerkTokenVerifier, keypair: tuple[RSAPrivateKey, RSAPublicKey]
) -> None:
    private_key, _public_key = keypair
    token = _make_token(private_key)
    header, payload, signature = token.split(".")
    tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    tampered_token = ".".join([header, payload, tampered_signature])

    with pytest.raises(AuthenticationError):
        verifier.verify(tampered_token)


def test_alg_none_is_rejected(verifier: ClerkTokenVerifier) -> None:
    now = int(time.time())
    token = jwt.encode(
        {"sub": "user_123", "iss": ISSUER, "iat": now, "exp": now + 3600},
        key="",
        algorithm="none",
    )

    with pytest.raises(AuthenticationError):
        verifier.verify(token)


def test_display_name_falls_back_to_first_and_last_name(
    verifier: ClerkTokenVerifier, keypair: tuple[RSAPrivateKey, RSAPublicKey]
) -> None:
    private_key, _public_key = keypair
    token = _make_token(private_key, first_name="Jane", last_name="Doe")

    principal = verifier.verify(token)

    assert principal.display_name == "Jane Doe"


def test_display_name_is_none_when_no_name_claims_present(
    verifier: ClerkTokenVerifier, keypair: tuple[RSAPrivateKey, RSAPublicKey]
) -> None:
    private_key, _public_key = keypair
    token = _make_token(private_key)

    principal = verifier.verify(token)

    assert principal.display_name is None


def test_no_authorized_parties_configured_skips_azp_check(
    keypair: tuple[RSAPrivateKey, RSAPublicKey],
) -> None:
    private_key, public_key = keypair
    open_verifier = ClerkTokenVerifier(
        jwks_url="https://unused.example/.well-known/jwks.json",
        issuer=ISSUER,
        jwk_client=StubJWKClient(public_key),  # type: ignore[arg-type]
    )
    token = _make_token(private_key, azp="https://anything.example")

    principal = open_verifier.verify(token)

    assert principal.subject == "user_123"
