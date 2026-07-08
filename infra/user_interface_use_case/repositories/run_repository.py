"""
Single source of truth for persisting the use cases a user has selected for
"this run".

This mirrors the role use_case_repository.py plays for use case templates:
nothing outside this module should decide where a run selection file lives
or what shape it has. That keeps the on-disk format a single, stable
contract that other parts of the pipeline (built by other developers) can
rely on without reading any Streamlit/UI code.

IMPORTANT -- lifecycle: a saved run selection is NOT a permanent record, and
it is NOT "one file per Save click" either. It is scoped to exactly one
session: every save for a given session_id overwrites that same session's
file, so repeatedly saving while still adjusting a selection never produces
more than one file per session. That file is only meant to exist from the
moment it is first saved until that session's run has had its result
reported back -- whatever code implements "report the result back" (outside
this module's scope) is expected to call delete_run_selection(session_id)
immediately after reporting, so data/runs/ never accumulates history. This
module only provides that deletion capability -- it cannot call it itself,
since it has no way of knowing when a run's result has actually been
reported.

The session_id itself is deliberately NOT generated in this module: "what
counts as one session" is a UI-layer concept (e.g. a Streamlit session), so
the caller is responsible for creating one stable id and passing it in every
time it saves or deletes.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from ..schemas import UseCaseContract

# infra/user_interface_use_case/repositories/run_repository.py -> PiplineAutomatoinMCP
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = _PROJECT_ROOT / "data"
RUNS_DIR = DATA_DIR / "runs"
SELECTION_FILENAME = "selected_use_cases.json"


class RunRepositoryError(Exception):
    """Raised when a run selection cannot be saved, found, or deleted."""


@dataclass(frozen=True)
class SavedRunSelection:
    """Result of persisting a run selection: what was written and where."""

    session_id: str
    file_path: Path
    use_case_count: int


def _serialize_selection(
    selected_use_cases: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Turn the UI's {id: {"contract":..., "catalog_platform":...}} map into plain JSON-able dicts."""
    entries: List[Dict[str, Any]] = []
    for use_case_id, info in selected_use_cases.items():
        contract: UseCaseContract = info["contract"]
        entries.append(
            {
                "id": use_case_id,
                "catalog_platform": info["catalog_platform"],
                "contract": json.loads(contract.to_pretty_json()),
            }
        )
    return entries


def save_selected_use_cases(
    session_id: str,
    selected_use_cases: Dict[str, Dict[str, Any]],
) -> SavedRunSelection:
    """
    Persist the use cases currently selected for this session's run.

    Calling this again with the same session_id overwrites the previous save
    for that session in place -- it never creates a second file. That is
    what keeps "save" a one-file-per-session operation instead of a growing
    history of every click.

    Raises RunRepositoryError if there is nothing to save -- an empty
    selection is never a valid "run" and should never produce a file that a
    later consumer might mistake for a real, intentional selection.
    """
    if not selected_use_cases:
        raise RunRepositoryError("No use cases are selected for this run yet.")

    session_dir = RUNS_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "session_id": session_id,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "use_case_count": len(selected_use_cases),
        "selected_use_cases": _serialize_selection(selected_use_cases),
    }

    file_path = session_dir / SELECTION_FILENAME
    file_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    return SavedRunSelection(
        session_id=session_id,
        file_path=file_path,
        use_case_count=len(selected_use_cases),
    )


def delete_run_selection(session_id: str) -> None:
    """
    Permanently remove a session's saved run selection and its folder.

    This is the cleanup half of save_selected_use_cases(): it is expected to
    be called once -- and only once, right after -- the session identified
    by session_id has had its result reported back to the user. Until the
    code that reports results exists, nothing in this module calls this
    automatically; it is exposed so that code (or a manual UI action, in the
    meantime) can.
    """
    session_dir = RUNS_DIR / session_id
    if not session_dir.exists():
        raise RunRepositoryError(f"No saved run selection found for session '{session_id}'.")
    shutil.rmtree(session_dir)


def list_pending_run_selections() -> List[SavedRunSelection]:
    """
    List every session's run selection currently saved on disk, i.e. not yet
    cleaned up.

    Because saving is one-file-per-session, this should normally contain at
    most one entry per session that has ever saved a selection -- never a
    growing pile from repeated clicks within the same session. In the
    finished pipeline it should typically be empty: each entry represents a
    session whose result has not been reported (and therefore deleted) yet.
    Exposed so the UI can show, and let a user manually clear, anything left
    over while that automatic step doesn't exist yet.
    """
    if not RUNS_DIR.exists():
        return []

    pending: List[SavedRunSelection] = []
    for session_dir in sorted(RUNS_DIR.iterdir()):
        file_path = session_dir / SELECTION_FILENAME
        if not file_path.exists():
            continue
        try:
            manifest = json.loads(file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        pending.append(
            SavedRunSelection(
                session_id=manifest.get("session_id", session_dir.name),
                file_path=file_path,
                use_case_count=manifest.get("use_case_count", 0),
            )
        )
    return pending
