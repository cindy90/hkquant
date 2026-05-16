"""Tests for `hk_ipo_agent.common.enums`."""

from __future__ import annotations

from hk_ipo_agent.common.enums import (
    CHECKPOINT_DAY_TERMINAL,
    CHECKPOINT_DAYS,
    ROLE_PERMISSIONS,
    VALID_TRANSITIONS,
    AgentRole,
    DecisionType,
    IPOLifecycleStateType,
    ListingType,
    Permission,
    UserRole,
)


def test_listing_type_values() -> None:
    assert ListingType.CH18C_COMMERCIALIZED == "18C-COMM"
    assert ListingType.AH_DUAL == "AH"


def test_agent_role_count() -> None:
    assert len(list(AgentRole)) == 7


def test_decision_type_values() -> None:
    assert {d.value for d in DecisionType} == {"participate", "partial", "skip", "wait"}


def test_valid_transitions_complete() -> None:
    """Every IPOLifecycleStateType MUST appear as a key in VALID_TRANSITIONS."""
    assert set(VALID_TRANSITIONS) == set(IPOLifecycleStateType)


def test_terminal_states_have_no_outgoing() -> None:
    """WITHDRAWN / HEARING_FAILED / PRICING_PULLED / TERMINATED are terminal (no transitions out)."""
    for s in (
        IPOLifecycleStateType.WITHDRAWN,
        IPOLifecycleStateType.HEARING_FAILED,
        IPOLifecycleStateType.PRICING_PULLED,
        IPOLifecycleStateType.TERMINATED,
    ):
        assert VALID_TRANSITIONS[s] == []


def test_no_backwards_transitions() -> None:
    """State machine must never allow LISTED -> PRE_LISTING etc."""
    assert IPOLifecycleStateType.PRE_LISTING not in VALID_TRANSITIONS[IPOLifecycleStateType.LISTED]
    assert (
        IPOLifecycleStateType.PRE_LISTING not in VALID_TRANSITIONS[IPOLifecycleStateType.PRICING]
    )
    assert IPOLifecycleStateType.PRICING not in VALID_TRANSITIONS[IPOLifecycleStateType.LISTED]


def test_role_permissions_admin_superset() -> None:
    """ADMIN must hold every permission held by REVIEWER / SENIOR_REVIEWER / OPERATOR."""
    union = (
        ROLE_PERMISSIONS[UserRole.REVIEWER]
        | ROLE_PERMISSIONS[UserRole.SENIOR_REVIEWER]
        | ROLE_PERMISSIONS[UserRole.OPERATOR]
    )
    assert union.issubset(ROLE_PERMISSIONS[UserRole.ADMIN])


def test_viewer_has_no_write_permissions() -> None:
    """VIEWER is read-only."""
    viewer_perms = ROLE_PERMISSIONS[UserRole.VIEWER]
    write_perms = {
        Permission.SUBMIT_REVIEW,
        Permission.PROPOSE_ADJUSTMENT,
        Permission.ACCEPT_PROPOSAL,
        Permission.REJECT_PROPOSAL,
        Permission.MANAGE_CONFIG,
        Permission.MANAGE_USERS,
        Permission.MANAGE_SCHEDULER,
    }
    assert viewer_perms.isdisjoint(write_perms)


def test_auditor_read_only_plus_audit() -> None:
    """AUDITOR is read-only but can read audit logs."""
    perms = ROLE_PERMISSIONS[UserRole.AUDITOR]
    assert Permission.READ_AUDIT in perms
    assert Permission.SUBMIT_REVIEW not in perms
    assert Permission.MANAGE_USERS not in perms


def test_senior_reviewer_can_decide_proposals() -> None:
    perms = ROLE_PERMISSIONS[UserRole.SENIOR_REVIEWER]
    assert Permission.ACCEPT_PROPOSAL in perms
    assert Permission.REJECT_PROPOSAL in perms


def test_only_admin_manages_users() -> None:
    for role, perms in ROLE_PERMISSIONS.items():
        if role == UserRole.ADMIN:
            assert Permission.MANAGE_USERS in perms
        else:
            assert Permission.MANAGE_USERS not in perms


def test_checkpoint_days_are_fixed() -> None:
    """Per PROJECT_SPEC.md §11, these must never be modified."""
    assert CHECKPOINT_DAYS == (1, 5, 10, 22, 30, 60, 90, 126, 180, 252, 360)
    assert CHECKPOINT_DAY_TERMINAL == -1


def test_checkpoint_days_monotone() -> None:
    assert list(CHECKPOINT_DAYS) == sorted(CHECKPOINT_DAYS)
