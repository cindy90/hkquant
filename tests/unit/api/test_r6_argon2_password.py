"""R6-2 — passwords hash with Argon2id; existing sha256 hashes lazy-rehash on login.

Pre-R6-2 ``api.auth.dependencies._hash_password`` used SHA-256 with a
static salt — that's a single-iteration unsalted-per-user hash that GPU
rigs crack at billions/sec. CLAUDE.md §UI 集成约束 + PROJECT_SPEC.md §6
imply OWASP-grade password storage; SHA-256 trips an obvious audit.

Post-R6-2:
  * ``_hash_password(plain)`` returns an Argon2id hash with default params
    (memory_cost=64 MiB, time_cost=3, parallelism=4 — argon2-cffi's
    PasswordHasher defaults, which match OWASP "Argon2 Recommendation").
  * ``verify_user(email, plain)`` accepts EITHER an Argon2id hash (the
    new format, ``$argon2id$v=19$...``) OR a legacy SHA-256 hex hash
    (64 lowercase hex chars). On a successful SHA-256 match, the stored
    hash is upgraded in-place to Argon2id — "lazy rehash".
  * Newly created users via ``create_user`` always store Argon2id.

These tests pin both halves of the contract.
"""

from __future__ import annotations

import hashlib

import pytest

from hk_ipo_agent.api.auth import dependencies as dep
from hk_ipo_agent.common.enums import UserRole

# ---------------------------------------------------------------------- hashing


def test_hash_password_returns_argon2id_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """R6-2 — _hash_password returns an Argon2id encoded string."""
    h = dep._hash_password("hunter2")
    assert h.startswith("$argon2id$"), f"expected Argon2id prefix, got {h[:30]!r}"


def test_hash_password_distinct_salts_per_call() -> None:
    """R6-2 — Argon2 salts internally → two hashes of the same password differ."""
    a = dep._hash_password("hunter2")
    b = dep._hash_password("hunter2")
    assert a != b, "Argon2 must use a unique salt per hash"


def test_create_user_stores_argon2id_hash() -> None:
    """R6-2 — newly created users get an Argon2id hash, never SHA-256."""
    dep.reset_users_for_test()
    rec = dep.create_user(
        email="argon-test@hk.local",
        password="hunter2",  # pragma: allowlist secret
        roles=[UserRole.VIEWER],
    )
    assert rec.password_sha256.startswith("$argon2id$"), (
        # field is named password_sha256 for back-compat but now holds Argon2 hash
        f"expected Argon2id in storage, got {rec.password_sha256[:30]!r}"
    )


# ---------------------------------------------------------------------- verify


def test_verify_user_accepts_argon2_password() -> None:
    """R6-2 — verify works against the new Argon2id storage."""
    dep.reset_users_for_test()
    dep.create_user(
        email="argon-verify@hk.local",
        password="hunter2",  # pragma: allowlist secret
        roles=[UserRole.VIEWER],
    )
    assert dep.verify_user("argon-verify@hk.local", "hunter2") is not None
    assert dep.verify_user("argon-verify@hk.local", "wrong") is None


def test_verify_user_accepts_legacy_sha256_and_rehashes_to_argon2() -> None:
    """R6-2 — legacy SHA-256 hashes still log in, but their stored value is
    transparently upgraded to Argon2id on successful login.

    This is the "lazy rehash" pattern — pre-existing user rows from before
    the migration can authenticate, while every successful login moves
    them to the modern hash. After enough logins / a periodic sweep, no
    SHA-256 hashes remain.
    """
    dep.reset_users_for_test()
    rec = dep.create_user(
        email="legacy@hk.local",
        password="legacy",  # pragma: allowlist secret
        roles=[UserRole.VIEWER],
    )
    # Manually downgrade the storage to a legacy SHA-256 hash to simulate
    # a pre-R6-2 row.
    legacy_hash = hashlib.sha256(b"hkipo::legacy").hexdigest()
    rec.password_sha256 = legacy_hash
    assert len(rec.password_sha256) == 64
    assert not rec.password_sha256.startswith("$argon2")

    # Successful login with the plaintext used to make the legacy hash.
    out = dep.verify_user("legacy@hk.local", "legacy")
    assert out is not None, "legacy SHA-256 password must still authenticate"
    # The stored hash on the record is now Argon2id (lazy rehash happened).
    assert rec.password_sha256.startswith("$argon2id$"), (
        f"expected lazy rehash to Argon2id, still got {rec.password_sha256[:30]!r}"
    )


def test_verify_user_rejects_bad_password_under_both_formats() -> None:
    """R6-2 — wrong password rejected regardless of stored format."""
    dep.reset_users_for_test()
    rec = dep.create_user(
        email="reject@hk.local",
        password="right-password",  # pragma: allowlist secret
        roles=[UserRole.VIEWER],
    )

    # Argon2 storage.
    assert dep.verify_user("reject@hk.local", "wrong") is None

    # Legacy SHA-256 storage.
    rec.password_sha256 = hashlib.sha256(b"hkipo::right-password").hexdigest()
    assert dep.verify_user("reject@hk.local", "wrong") is None
