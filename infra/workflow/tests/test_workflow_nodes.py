"""
Tests for infra/workflow/workflow_nodes.py::artifact_generator_node's
device_id wiring.

Regression coverage: answer_policy.android.device_id (configured via
ui/app.py's use-case builder) used to never make it into
state["device_id"], so emulator_node always saw it as missing and silently
skipped booting any device at all -- see nodeEmulator.py's own fallback
fix and its tests for the other half of this bug.
"""

from __future__ import annotations

import json

from infra.workflow.workflow_nodes import artifact_generator_node


def _write_use_case(tmp_path, *, android_policy):
    use_case = {
        "id": "case-1",
        "platform": "android",
        "app_path": "data/application/banana.app",
        "prompt_goal": "goal",
        "answer_policy": {
            "android": android_policy,
            "in_app_event": {"inapp_event_method": "none"},
        },
        "installation_agent_summary": "",
    }
    path = tmp_path / "case-1.json"
    path.write_text(json.dumps(use_case), encoding="utf-8")
    return str(path)


def test_wires_configured_android_device_id_into_state(tmp_path):
    case_path = _write_use_case(tmp_path, android_policy={"device_id": "emulator-5554"})
    state = {"current_use_case_path": case_path, "run_id": "run-1"}

    result = artifact_generator_node(state)

    assert result["device_id"] == "emulator-5554"


def test_leaves_device_id_unset_when_android_policy_has_none(tmp_path):
    """answer_policy.android == null (e.g. 'common' use cases) must not
    crash, and must leave device_id unset so emulator_node's own
    auto-detection fallback kicks in."""
    case_path = _write_use_case(tmp_path, android_policy=None)
    state = {"current_use_case_path": case_path, "run_id": "run-1"}

    result = artifact_generator_node(state)

    assert result.get("device_id") is None


def test_does_not_overwrite_existing_device_id_when_policy_has_none(tmp_path):
    """If a device_id was already set on state (e.g. carried over from a
    prior node), an unconfigured policy must not clobber it."""
    case_path = _write_use_case(tmp_path, android_policy=None)
    state = {"current_use_case_path": case_path, "run_id": "run-1", "device_id": "already-set"}

    result = artifact_generator_node(state)

    assert result["device_id"] == "already-set"
