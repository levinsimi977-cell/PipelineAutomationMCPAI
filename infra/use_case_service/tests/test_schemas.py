"""
Tests for infra/use_case_service/schemas.py::VerifySdkPolicy.validate_basic_navigation.

Opt-in field consumed by emulator_node's navigation smoke test (see
infra/agents/sdkAgent/tools/emulator.py::run_basic_navigation_smoke): must
default to off so existing use cases that don't mention navigation are
unaffected.
"""

from __future__ import annotations

from infra.use_case_service.schemas import VerifySdkPolicy


def test_validate_basic_navigation_defaults_to_false():
    policy = VerifySdkPolicy()

    assert policy.validate_basic_navigation is False


def test_validate_basic_navigation_can_be_enabled():
    policy = VerifySdkPolicy(validate_basic_navigation=True)

    assert policy.validate_basic_navigation is True
