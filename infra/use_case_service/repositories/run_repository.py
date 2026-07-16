from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = PROJECT_ROOT / "data" / "runs"


class RunRepositoryError(Exception):
    pass


@dataclass
class SavedRunSelection:
    session_id: str
    use_case_count: int


@dataclass
class PendingRunSelection:
    session_id: str
    use_case_count: int


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def save_selected_use_cases(session_id: str, selected_map: dict[str, dict]) -> SavedRunSelection:
    run_dir = RUNS_DIR / session_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    selected_use_case_ids: list[str] = []
    selected_rule_profiles_by_use_case: dict[str, list[str]] = {}

    for use_case_id, info in selected_map.items():
        contract = info["contract"]
        selected_use_case_ids.append(use_case_id)

        _write_json(run_dir / f"{use_case_id}.json", contract.model_dump(exclude_none=True))

        selected_profiles = info.get("selected_rule_profiles")
        if not selected_profiles:
            selected_profiles = contract.rules_policy.default_profiles
        selected_rule_profiles_by_use_case[use_case_id] = selected_profiles

    runtime_config = {
        "selected_use_case_ids": selected_use_case_ids,
        "selected_rule_profiles_by_use_case": selected_rule_profiles_by_use_case,
        "disabled_rule_ids": [],
        "runtime_overrides": {},
    }
    _write_json(run_dir / "runtime-config.json", runtime_config)

    return SavedRunSelection(session_id=session_id, use_case_count=len(selected_use_case_ids))


def load_runtime_config(session_id: str) -> dict[str, Any]:
    """Read back the runtime-config.json written by save_selected_use_cases()."""
    config_path = RUNS_DIR / session_id / "runtime-config.json"
    if not config_path.exists():
        raise RunRepositoryError(
            f"No saved run selection found for session '{session_id}'. "
            "Save the selection before running the workflow."
        )
    return json.loads(config_path.read_text(encoding="utf-8"))


def load_selected_use_cases(session_id: str) -> list[dict[str, Any]]:
    """
    Rebuild the list of use-case payloads for a saved run, in the same
    order as runtime-config.json's selected_use_case_ids. Each dict is the
    use case's full contract (as written by save_selected_use_cases) plus
    its catalog id under "id" — the shape json_use_case_input_node expects
    in PipelineState["selected_use_cases"].
    """
    runtime_config = load_runtime_config(session_id)
    run_dir = RUNS_DIR / session_id

    use_cases: list[dict[str, Any]] = []
    for use_case_id in runtime_config["selected_use_case_ids"]:
        use_case_path = run_dir / f"{use_case_id}.json"
        if not use_case_path.exists():
            raise RunRepositoryError(
                f"runtime-config.json references '{use_case_id}' but "
                f"{use_case_path} is missing."
            )
        payload = json.loads(use_case_path.read_text(encoding="utf-8"))
        payload["id"] = use_case_id
        use_cases.append(payload)
    return use_cases


def list_pending_run_selections() -> list[PendingRunSelection]:
    if not RUNS_DIR.exists():
        return []

    pending: list[PendingRunSelection] = []
    for item in RUNS_DIR.iterdir():
        if not item.is_dir():
            continue
        count = len([p for p in item.glob("*.json") if p.name != "runtime-config.json"])
        pending.append(PendingRunSelection(session_id=item.name, use_case_count=count))

    return sorted(pending, key=lambda x: x.session_id)


def delete_run_selection(session_id: str) -> None:
    run_dir = RUNS_DIR / session_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
