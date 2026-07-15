"""
Generate a demo of the run-level index page — the overview that lists every
use case in a run as a card (with its own pass/fail status), where clicking
a card opens that use case's own detail report.

This is what a real multi-use-case run produces via visual_report_node
(record_use_case_report() per use case, then attach_index_report() once the
loop is done) — reproduced here with demo data since the demo scripts only
ever simulated a single use case before.

Usage:
    python -m data.reports.demo_index_report
"""

from __future__ import annotations

from copy import deepcopy

from data.reports.build_report import attach_index_report, record_use_case_report
from data.reports.demo_failed_report import build_failed_state
from data.reports.demo_full_report import STATE, _DemoRecorder

RUN_ID = "20260715_multi_use_case_demo"


def _use_case_state(base: dict, use_case_id: str, prompt_goal: str, platform: str) -> dict:
    state = deepcopy(base)
    state["run_id"] = RUN_ID
    state["platform"] = platform
    state["current_use_case"] = {
        "id": use_case_id,
        "platform": platform,
        "prompt_goal": prompt_goal,
    }
    return state


def main() -> None:
    recorder = _DemoRecorder()

    use_case_states = [
        _use_case_state(
            STATE,
            "common-first-open-sdk-presence",
            "Validate SDK presence and first-open event on launch.",
            "android",
        ),
        _use_case_state(
            STATE,
            "common-deeplink-smoke",
            "Smoke-test OneLink deep link handling end to end.",
            "ios",
        ),
        _use_case_state(
            build_failed_state(),
            "ios-deeplink-validation",
            "Validate deep link opens the expected destination screen.",
            "ios",
        ),
    ]

    # One state dict threaded through every use case in the run, exactly like
    # visual_report_node does across loop iterations — use_case_reports
    # accumulates a card per use case as record_use_case_report() is called.
    run_state: dict = {"run_id": RUN_ID, "use_case_reports": []}

    for use_case_state in use_case_states:
        use_case_state["use_case_reports"] = run_state["use_case_reports"]
        use_case_state["run_id"] = RUN_ID
        record_use_case_report(use_case_state, audit_recorder=recorder)
        run_state["use_case_reports"] = use_case_state["use_case_reports"]

    run_state["current_use_case_path"] = None
    run_state = attach_index_report(run_state)
    print(f"Index demo report written to:\n  {run_state['report_path']}")


if __name__ == "__main__":
    main()
