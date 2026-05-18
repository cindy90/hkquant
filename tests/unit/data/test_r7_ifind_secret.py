"""R7-5 — IFindClient holds password as SecretStr internally; never as plain str.

Pre-R7-5 ``IFindClient.__init__`` did:

    self.password = (
        password if password is not None else settings.ifind.password.get_secret_value()
    )

Storing the cleartext on ``self.password`` exposed it via:
  * ``client.__dict__`` — any pickle / debug dump leaked the password
  * ``repr(client)`` — default __repr__ printed the dict
  * Stack traces — locals dump captures it
  * Future audit-log serialisers might catch it

Post-R7-5:
  * Internal attribute renamed to ``_password: SecretStr`` (private,
    type-marked).
  * The SDK call site extracts via ``.get_secret_value()`` at the moment
    of use (THS_iFinDLogin). The plaintext lives in a local for the
    duration of one stack frame, not on a long-lived instance attribute.
  * No public ``password`` attribute.
"""

from __future__ import annotations

from pydantic import SecretStr

from hk_ipo_agent.data.sources.ifind_client import IFindClient


def test_password_is_not_a_public_attribute() -> None:
    """R7-5 — ``IFindClient.password`` (public) MUST NOT exist after init.

    Before R7-5 the constructor wrote ``self.password = <cleartext>``;
    this test pins that nobody re-adds it.
    """
    client = IFindClient(username="u", password="pw")
    assert not hasattr(client, "password") or not isinstance(
        getattr(client, "password", None), str
    ), (
        "R7-5: IFindClient.password must not be a plain string attribute — "
        "move it to ``_password: SecretStr`` per PLAN R7-5."
    )


def test_internal_password_is_secret_str() -> None:
    """R7-5 — the internal ``_password`` attribute is a ``SecretStr``."""
    client = IFindClient(username="u", password="pw")
    assert hasattr(client, "_password"), "R7-5: expected internal _password attribute"
    stored = client._password
    assert isinstance(stored, SecretStr), (
        f"R7-5: _password must be a SecretStr, got {type(stored).__name__}"
    )
    # Round-trip: get_secret_value returns the cleartext we provided.
    assert stored.get_secret_value() == "pw"


def test_repr_does_not_leak_password() -> None:
    """R7-5 — ``repr(client)`` doesn't include the cleartext password.

    SecretStr's __repr__ returns ``SecretStr('**********')`` — so just
    holding the password as a SecretStr is enough to satisfy this. We
    explicitly test repr to guard against future code that might
    explicitly include the password in __repr__.
    """
    client = IFindClient(username="u", password="super-secret-123")
    repr_str = repr(client)
    assert "super-secret-123" not in repr_str, (
        "R7-5: cleartext password leaked through repr(client)"
    )


def test_init_accepts_secretstr_password() -> None:
    """R7-5 — caller can pass a SecretStr directly (avoid mid-stack cleartext)."""
    client = IFindClient(username="u", password=SecretStr("from-secretstr"))
    assert client._password.get_secret_value() == "from-secretstr"
