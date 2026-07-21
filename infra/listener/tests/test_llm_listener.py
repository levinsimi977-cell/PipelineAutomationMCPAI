"""
Regression test for infra/listener/llm_listener.py's nodes_log text_preview
size.

nodes_log["text_preview"] is the only place the SDK agent's final message
is surfaced back to the user/UI when the Classifier reports FAILURE (see
ui/app.py's `st.json(last_result.get("nodes_log", []))`). It used to be
capped at 200 chars -- often just the opening sentence of a multi-section
agent report, with the actual failure reasoning near the end -- making it
impossible to diagnose a failure from the UI alone.
"""

from __future__ import annotations

from types import SimpleNamespace

from infra.listener.llm_listener import (
    TEXT_PREVIEW_LIMIT,
    _fallback_status_from_json,
    listener_on_agent_turn,
    listener_on_text,
)


def test_text_preview_limit_is_generous():
    """Guards against silently shrinking the preview back down."""
    assert TEXT_PREVIEW_LIMIT >= 2000


def test_failure_text_preview_keeps_far_more_than_200_chars():
    long_report = (
        "1. SDK Integration Verification\nStatus: PASS\n\n" + ("Evidence line.\n" * 50) + "STATUS: FAILURE"
    )
    assert len(long_report) > 200

    _, updates = listener_on_text({}, "sdk_agent", long_report)

    log_entry = updates["nodes_log"][-1]
    assert log_entry["listener"] == "FAIL"
    assert log_entry["text_preview"] == long_report[:TEXT_PREVIEW_LIMIT]
    assert "STATUS: FAILURE" in log_entry["text_preview"]


def test_success_text_preview_keeps_far_more_than_200_chars():
    long_report = ("Evidence line.\n" * 50) + "STATUS: SUCCESS"
    assert len(long_report) > 200

    _, updates = listener_on_text({}, "sdk_agent", long_report)

    log_entry = updates["nodes_log"][-1]
    assert log_entry["listener"] == "SUCCESS"
    assert log_entry["text_preview"] == long_report[:TEXT_PREVIEW_LIMIT]


# ---------------------------------------------------------------------------
# _fallback_status_from_json / listener_on_agent_turn
#
# Regression coverage for the bug where a stage's generated prompt required
# an exact JSON schema for the agent's final answer ("Return ONLY this JSON
# object ... no additional commentary") and the agent's response therefore
# never contained the literal `STATUS: SUCCESS`/`STATUS: FAILURE` line rule
# 13 requires. Without a fallback, that JSON verdict is invisible to the
# orchestrator, which just keeps re-prompting "continue" -- re-eliciting the
# same JSON report turn after turn -- until MAX_TURNS is exhausted with
# nothing to show for it (observed with common-basic-launch-navigation's
# verify_prompt).
# ---------------------------------------------------------------------------


def test_fallback_status_from_json_returns_success_from_last_match():
    text = (
        '{"verifications": {"sdk_integration_present": {"status": "SUCCESS"}}, '
        '"overall_result": {"status": "SUCCESS"}}'
    )
    assert _fallback_status_from_json(text) == "SUCCESS"


def test_fallback_status_from_json_returns_failure_from_last_match():
    text = (
        '{"verifications": {"sdk_integration_present": {"status": "SUCCESS"}}, '
        '"overall_result": {"status": "FAILURE"}}'
    )
    assert _fallback_status_from_json(text) == "FAILURE"


def test_fallback_status_from_json_ignores_partial_success_and_inconclusive():
    assert _fallback_status_from_json('{"overall_result": {"status": "PARTIAL_SUCCESS"}}') is None
    assert _fallback_status_from_json('{"overall_result": {"status": "INCONCLUSIVE"}}') is None


def test_fallback_status_from_json_none_when_no_status_field():
    assert _fallback_status_from_json("Here is what I did: ...no JSON status here.") is None


def _human(content):
    return SimpleNamespace(type="human", content=content, tool_calls=None)


def _ai(content):
    return SimpleNamespace(type="ai", content=content, tool_calls=[])


def test_listener_on_agent_turn_done_when_json_status_success_lacks_status_line():
    json_report = (
        '{\n  "verifications": {"sdk_integration_present": {"status": "SUCCESS"}},\n'
        '  "overall_result": {"status": "SUCCESS", "summary": "All good."}\n}'
    )
    messages = [_human("verify_prompt"), _ai(json_report)]

    action, next_prompt, updates = listener_on_agent_turn({}, "sdk_agent", "verify_prompt", messages)

    assert action == "done"
    assert next_prompt is None
    assert updates.get("test_status") != "FAIL"


def test_listener_on_agent_turn_fails_when_json_status_failure_lacks_status_line():
    json_report = (
        '{\n  "verifications": {"sdk_integration_present": {"status": "FAILURE"}},\n'
        '  "overall_result": {"status": "FAILURE", "summary": "Missing SDK init."}\n}'
    )
    messages = [_human("verify_prompt"), _ai(json_report)]

    action, next_prompt, updates = listener_on_agent_turn({}, "sdk_agent", "verify_prompt", messages)

    assert action == "fail"
    assert next_prompt is None
    assert updates.get("test_status") == "FAIL"


def test_listener_on_agent_turn_still_continues_when_json_status_is_ambiguous():
    json_report = '{"overall_result": {"status": "PARTIAL_SUCCESS", "summary": "Some checks inconclusive."}}'
    messages = [_human("verify_prompt"), _ai(json_report)]

    action, next_prompt, _updates = listener_on_agent_turn({}, "sdk_agent", "verify_prompt", messages)

    assert action == "continue"
    assert next_prompt is not None


def test_listener_on_agent_turn_prefers_explicit_status_line_over_json_fallback():
    """A literal STATUS: line always wins, even if a JSON status field
    earlier in the same message would otherwise disagree."""
    text = '{"overall_result": {"status": "FAILURE"}}\n\nSTATUS: SUCCESS'
    messages = [_human("verify_prompt"), _ai(text)]

    action, _next_prompt, updates = listener_on_agent_turn({}, "sdk_agent", "verify_prompt", messages)

    assert action == "done"
    assert updates.get("test_status") != "FAIL"
