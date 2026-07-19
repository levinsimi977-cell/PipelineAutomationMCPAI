"""
Tests for infra/agents/userActions/node.py.

Focus: the gate that decides whether `events.wired.json` is required before
running the Appium tap/discovery pipeline. `run_user_actions_pipeline` itself
is monkeypatched out — these tests only exercise `user_actions_node`'s own
branching logic.
"""

from __future__ import annotations

from pathlib import Path

import infra.agents.userActions.node as node_module
from infra.agents.userActions.node import user_actions_node


def _base_state(tmp_path: Path, **overrides) -> dict:
    state = {
        "sandbox_path": str(tmp_path),
        "platform": "android",
        "answer_policy": {"in_app_event": {"inapp_event_method": "none"}},
    }
    state.update(overrides)
    return state


def test_skips_when_no_events_configured_and_manifest_missing(tmp_path):
    """
    Regression test: `inapp_event_method` defaults to "none" in the use-case
    schema, and use cases that never wire an in-app event (e.g. pure
    deep-link/app-link validation) legitimately never produce
    events.wired.json. This must be a skip, not a hard failure.
    """
    state = _base_state(tmp_path)

    result = user_actions_node(state)

    assert result["visited_user_actions"] is True
    assert result["next_node"] == "deep_link"
    assert result.get("test_status") != "FAIL"
    log_entry = result["nodes_log"][-1]
    assert log_entry["status"] == "Skipped"


def test_skips_when_event_method_is_validate_payload(tmp_path):
    """
    Regression test: `inapp_event_method: "validate_payload"` (e.g.
    af_app_opened, verified automatically on first launch) has no UI control
    to create or tap -- unlike "log_event"/"button_tap", it never produces
    events.wired.json either, and must also be a skip, not a hard failure.
    """
    state = _base_state(
        tmp_path,
        answer_policy={
            "in_app_event": {"inapp_event_method": "validate_payload", "event_name": "af_app_opened"}
        },
    )

    result = user_actions_node(state)

    assert result["visited_user_actions"] is True
    assert result["next_node"] == "deep_link"
    assert result.get("test_status") != "FAIL"
    assert result["nodes_log"][-1]["status"] == "Skipped"


def test_fails_when_events_configured_but_manifest_missing(tmp_path):
    """If the policy DOES expect an in-app event, a missing manifest is a
    real failure (event_prompt should have called write_events_manifest)."""
    state = _base_state(
        tmp_path,
        answer_policy={"in_app_event": {"inapp_event_method": "log_event", "event_name": "af_purchase"}},
    )

    result = user_actions_node(state)

    assert result["test_status"] == "FAIL"
    log_entry = result["nodes_log"][-1]
    assert log_entry["status"] == "Fail"
    assert "events.wired.json" in log_entry["details"]["error"]


def test_fails_when_sandbox_path_missing_regardless_of_event_policy(tmp_path):
    state = _base_state(tmp_path, sandbox_path=None)

    result = user_actions_node(state)

    assert result["test_status"] == "FAIL"
    assert result["nodes_log"][-1]["status"] == "Fail"


def test_fails_when_platform_missing_regardless_of_event_policy(tmp_path):
    state = _base_state(tmp_path, platform=None)

    result = user_actions_node(state)

    assert result["test_status"] == "FAIL"
    assert result["nodes_log"][-1]["status"] == "Fail"


def test_incoming_question_short_circuits(tmp_path):
    state = _base_state(tmp_path, incoming_question="Which event should I trigger?")

    result = user_actions_node(state)

    assert result["next_node"] == "user_actions"
    assert "nodes_log" not in result or result["nodes_log"] == []


def test_runs_pipeline_and_propagates_success(tmp_path, monkeypatch):
    manifest_path = tmp_path / "events.wired.json"
    manifest_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        node_module,
        "run_user_actions_pipeline",
        lambda **kwargs: {
            "status": "Success",
            "phase": "taps",
            "discovery_validation": {"passed": True},
            "tap_validation": {"passed": True},
            "event_count": 1,
            "tap_count": 1,
        },
    )

    state = _base_state(tmp_path)

    result = user_actions_node(state)

    assert result["visited_user_actions"] is True
    assert result["next_node"] == "deep_link"
    assert result.get("test_status") != "FAIL"
    assert result["nodes_log"][-1]["status"] == "Success"


def test_runs_pipeline_and_propagates_failure(tmp_path, monkeypatch):
    """
    Regression test: a genuine tap/discovery failure must set test_status
    so route_after_user_actions (which gates on test_status, not next_node)
    actually routes to test_runner instead of silently continuing on to
    deep_link.
    """
    manifest_path = tmp_path / "events.wired.json"
    manifest_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        node_module,
        "run_user_actions_pipeline",
        lambda **kwargs: {
            "status": "Fail",
            "phase": "taps",
            "discovery_validation": {"passed": True},
            "tap_validation": {"passed": False},
            "event_count": 1,
            "tap_count": 1,
        },
    )

    state = _base_state(tmp_path)

    result = user_actions_node(state)

    assert result["test_status"] == "FAIL"
    assert result["next_node"] == "user_actions"
    assert result["nodes_log"][-1]["status"] == "Fail"
