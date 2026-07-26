"""
One-off debugging helper: run the pipeline for a single catalog use case
without going through the Streamlit UI, so failures can be investigated
directly from this script's own stdout (no racing against sandbox/report
cleanup, no needing a browser).

Usage:
    python3 scripts/headless_run.py [use_case_id] [run_platform]

Not part of the app itself -- ad-hoc investigation tool only.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from infra.load_env import load_project_env
from infra.use_case_service.repositories import run_repository as run_repo
from infra.use_case_service.repositories import use_case_repository as uc_repo
from infra.workflow import run_launcher


def main() -> None:
    use_case_id = sys.argv[1] if len(sys.argv) > 1 else "common-deeplink-smoke"
    run_platform = sys.argv[2] if len(sys.argv) > 2 else "ios"

    load_project_env()

    entries = {e.id: e for e in uc_repo.list_use_cases()}
    if use_case_id not in entries:
        print(f"Unknown use case id: {use_case_id}. Available: {sorted(entries)}")
        sys.exit(1)

    contract = uc_repo.load_use_case(entries[use_case_id])
    contract = contract.model_copy(update={"run_platform": run_platform})

    selected_map = {
        use_case_id: {
            "contract": contract,
            "selected_rule_profiles": contract.rules_policy.default_profiles,
        }
    }

    run_id = uuid.uuid4().hex
    run_repo.save_selected_use_cases(run_id, selected_map)
    print(f"=== Starting headless run {run_id} for {use_case_id} ({run_platform}) ===")

    final_state = run_launcher.start_workflow(run_id)

    print("\n=== FINAL STATE SUMMARY ===")
    print("test_status:", final_state.get("test_status"))
    print("last_prompt_type:", final_state.get("last_prompt_type"))
    print("prompt_just_run:", final_state.get("prompt_just_run"))
    print("report_path:", final_state.get("report_path"))
    print("fail_reason:", str(final_state.get("fail_reason"))[:2000])
    print("\nnodes_log:")
    for entry in final_state.get("nodes_log") or []:
        print(" -", entry)


if __name__ == "__main__":
    main()
