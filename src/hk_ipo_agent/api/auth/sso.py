"""SSO providers (OKTA / AzureAD / Google) — Phase 9 deferred per ADR 0011.

Phase 7 MVP keeps only local JWT (see ``dependencies.py``). When SSO is
wired in Phase 9, each provider gets a class here that exchanges an
external token for a local JWT after mapping the external user identity
to a local ``UserAccount``.
"""

from __future__ import annotations


def is_sso_configured() -> bool:
    """Phase 7 MVP: always False. Phase 9 implements per-provider config check."""
    return False


__all__ = ("is_sso_configured",)
