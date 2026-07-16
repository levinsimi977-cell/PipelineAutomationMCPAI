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
    """
    Clone one of the demo module's shared `base` state dicts (STATE for a
    passing run, build_failed_state() for a failing one) into a distinct
    state for one use case in this run. deepcopy() is important here: all
    three use cases below start from the same two base dicts, so without
    deepcopy, mutating one use case's state["current_use_case"] would leak
    into the others since they'd all share the same nested dict object.
    """
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
    # A fake AuditRecorder (see demo_full_report._DemoRecorder) so
    # record_use_case_report() below has something to call
    # load_audit_events() against, just like a real run would.
    recorder = _DemoRecorder()

    # Three use cases: two passing (from STATE) on different platforms, one
    # failing (from build_failed_state()) — enough variety to see both
    # status badges and the platform tag on the index page's cards.
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
        # Point this use case's own state at the *same* list object
        # run_state is tracking, so when record_use_case_report() appends
        # a card to use_case_state["use_case_reports"], run_state sees that
        # same new card too (they're both referencing the same list) —
        # mirrors how visual_report_node reuses one `state` dict across the
        # whole run rather than one dict per use case.
        use_case_state["use_case_reports"] = run_state["use_case_reports"]
        use_case_state["run_id"] = RUN_ID
        # Builds this one use case's own report.html AND appends its card
        # (status, platform, link, duration) to use_case_reports.
        record_use_case_report(use_case_state, audit_recorder=recorder)
        run_state["use_case_reports"] = use_case_state["use_case_reports"]

    # Signals "no more use cases left to process" — the same condition
    # visual_report_node checks before calling attach_index_report() for
    # real, once current_use_case_path has been exhausted.
    run_state["current_use_case_path"] = None
    # Renders index.html from every card collected above and records its
    # path under run_state["report_path"].
    run_state = attach_index_report(run_state)
    print(f"Index demo report written to:\n  {run_state['report_path']}")


if __name__ == "__main__":
    main()
