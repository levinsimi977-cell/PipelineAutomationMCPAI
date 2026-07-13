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
