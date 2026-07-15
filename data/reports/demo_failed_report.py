"""
Generate a failed-run demo report for previewing failure states in the HTML report.

Starts from demo_full_report.STATE (the passing-run fixture) and mutates a
copy of it to look like a run that failed at compilation_check — deep_link,
test_runner, and visual_report never ran, so those nodes show as "Skipped"
("not_run") in the report rather than "Failed", matching how a real
mid-pipeline failure would leave later nodes untouched.

Usage:
    python -m data.reports.demo_failed_report
"""

from __future__ import annotations

from copy import deepcopy

from data.reports.build_report import generate_run_report
from data.reports.demo_full_report import AUDIT_EVENTS, PIPELINE_NODES, STATE, _DemoRecorder

RUN_ID = "20260714_failed_demo_ios_deeplink"
REPORT_PATH = f"data/reports/2026-07-14/{RUN_ID}/report.html"

# Only 6 of the 11 pipeline nodes are listed here — deep_link, test_runner,
# and visual_report are deliberately absent (the run never reached them),
# and sdk_agent only has 2 of its normal 3 entries (verify_prompt never ran
# either, since compilation_check failed right after event_prompt).
FAILED_NODE_LOGS = {
    "json_use_case_input": {"status": "Success", "message": "Materialized 3 use case JSON files."},
    "artifact_generator": {"status": "Success", "message": "Loaded use case and registered answer_policy."},
    "environment_setup": {"status": "Success", "message": "Sandbox cloned; MCP alive; app validated."},
    "prompt_agent": {"status": "Success", "message": "Generated integrate/event/verify prompts."},
    "sdk_agent": [
        {"status": "Success", "prompt_type": "integrate_prompt", "message": "SDK integration completed."},
        {"status": "Success", "prompt_type": "event_prompt", "message": "In-app event af_purchase wired."},
    ],
    "compilation_check": {
        "status": "Fail",
        "message": "xcodebuild failed — undefined symbol _AppsFlyerLib in BananasViewController.o",
    },
    "user_actions": {"status": "Success", "message": "User action simulation passed."},
    "emulator": {"status": "Success", "message": "Appium started; app launched."},
}


def build_failed_state() -> dict:
    """Deep-copies the passing-run fixture, then overrides just the fields that a failure at compilation_check would actually change."""
    state = deepcopy(STATE)
    state.update({
        "run_id": RUN_ID,
        "report_path": REPORT_PATH,
        "test_status": "FAIL",
        "sdk_verified": False,
        "compilation_passed": False,
        "is_tool_order_valid": False,
        "is_tool_order_valid_message": (
            "Sequence invalid.\n"
            "verifyIosDeepLink was invoked before createIosDeepLink for ios."
        ),
        "fail_reason": "Compilation check failed after event wiring pass.",
        "ended_at": "2026-07-14T09:18:03",
    })

    # Mirrors the generic nodes_log the real workflow would have written up
    # to the point of failure — only the 9 entries that actually happened
    # (nothing for deep_link/test_runner/visual_report, since the pipeline
    # never got there).
    state["nodes_log"] = [
        {"node": "json_use_case_input", "status": "SUCCESS", "message": FAILED_NODE_LOGS["json_use_case_input"]["message"]},
        {"node": "artifact_generator", "status": "SUCCESS", "message": FAILED_NODE_LOGS["artifact_generator"]["message"]},
        {"node": "environment_setup", "status": "SUCCESS", "message": FAILED_NODE_LOGS["environment_setup"]["message"]},
        {"node": "prompt_agent", "status": "SUCCESS", "message": FAILED_NODE_LOGS["prompt_agent"]["message"]},
        {"node": "sdk_agent", "status": "Success", "prompt_type": "integrate_prompt", "message": FAILED_NODE_LOGS["sdk_agent"][0]["message"]},
        {"node": "sdk_agent", "status": "Success", "prompt_type": "event_prompt", "message": FAILED_NODE_LOGS["sdk_agent"][1]["message"]},
        {"node": "compilation_check", "status": "FAIL", "message": FAILED_NODE_LOGS["compilation_check"]["message"]},
        {"node": "user_actions", "status": "SUCCESS", "message": FAILED_NODE_LOGS["user_actions"]["message"]},
        {"node": "emulator", "status": "SUCCESS", "message": FAILED_NODE_LOGS["emulator"]["message"]},
    ]

    # STATE (the passing-run fixture) had all 11 nodes marked visited=True
    # with a log entry each — strip that out first so this failed run
    # starts from a clean slate instead of inheriting the passing run's
    # per-node keys.
    for node in PIPELINE_NODES:
        state.pop(f"{node}_is_visited", None)
        state.pop(f"{node}_log", None)

    # Then re-mark only the nodes that are keys in FAILED_NODE_LOGS as
    # visited — deep_link/test_runner/visual_report stay unmarked, so
    # RunReportBuilder._resolve_node_is_visited() reports them as "not_run"
    # ("Skipped") in the report, exactly like a real run that stopped early.
    visited_nodes = set(FAILED_NODE_LOGS.keys())
    for node in PIPELINE_NODES:
        state[f"{node}_is_visited"] = node in visited_nodes
        if node in FAILED_NODE_LOGS:
            state[f"{node}_log"] = FAILED_NODE_LOGS[node]

    return state


def main() -> None:
    path = generate_run_report(build_failed_state(), audit_recorder=_DemoRecorder())
    print(f"Failed demo report written to:\n  {path.resolve()}")


if __name__ == "__main__":
    main()
