"""
Single source of truth for persisting the use cases a user has selected for
"this run".

This mirrors the role use_case_repository.py plays for use case templates:
nothing outside this module should decide where a run selection file lives
or what shape it has. That keeps the on-disk format a single, stable
contract that other parts of the pipeline (built by other developers) can
rely on without reading any Streamlit/UI code.

IMPORTANT -- on-disk layout: every selected use case is written as its OWN
file, flat inside data/runs/ (no per-session subfolders). That is what lets
a teammate (or a downstream pipeline step) get "everything chosen for a run"
by simply listing/pulling every file in data/runs/, instead of having to
parse one aggregate manifest. Each filename is namespaced with the owning
session_id (`{session_id}__{use_case_id}.json`) purely to avoid collisions
when two sessions pick the same use case id -- it is still one file per use
case, not one file per session.

IMPORTANT -- lifecycle: saved run selection files are NOT a permanent
record, and saving is NOT "one file per Save click" either. It is scoped to
exactly one session: every save for a given session_id first clears out that
session's previously-written files and rewrites the current selection, so
repeatedly saving while still adjusting a selection never leaves stale
per-use-case files behind. Those files are only meant to exist from the
moment they are first saved until that session's run has had its result
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
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from ..schemas import UseCaseContract

# infra/user_interface_use_case/repositories/run_repository.py -> PiplineAutomatoinMCP
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = _PROJECT_ROOT / "data"
RUNS_DIR = DATA_DIR / "runs"

# Separates a session_id from a use_case_id inside a filename. Chosen because
# neither id ever contains a double underscore on its own (session ids are
# plain uuid4 hex, use case ids are slugified kebab-case).
_SESSION_SEPARATOR = "__"


class RunRepositoryError(Exception):
    """Raised when a run selection cannot be saved, found, or deleted."""


@dataclass(frozen=True)
class SavedRunSelection:
    """Result of persisting (or listing) a session's saved run selection."""

    session_id: str
    file_paths: List[Path]
    use_case_count: int


def _sanitize_id_for_filename(value: str) -> str:
    """Defensively strip anything that isn't filesystem-safe from an id."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-") or "use-case"


def _use_case_file_path(session_id: str, use_case_id: str) -> Path:
    safe_session = _sanitize_id_for_filename(session_id)
    safe_use_case = _sanitize_id_for_filename(use_case_id)
    return RUNS_DIR / f"{safe_session}{_SESSION_SEPARATOR}{safe_use_case}.json"


def _files_for_session(session_id: str) -> List[Path]:
    if not RUNS_DIR.exists():
        return []
    prefix = f"{_sanitize_id_for_filename(session_id)}{_SESSION_SEPARATOR}"
    return sorted(p for p in RUNS_DIR.iterdir() if p.is_file() and p.name.startswith(prefix))


def save_selected_use_cases(
    session_id: str,
    selected_use_cases: Dict[str, Dict[str, Any]],
) -> SavedRunSelection:
    """
    Persist the use cases currently selected for this session's run, one
    file per use case, flat inside data/runs/.

    Calling this again with the same session_id first removes every file
    this session previously wrote, then writes the current selection fresh.
    That keeps "save" a one-set-of-files-per-session operation -- a use case
    that was deselected since the last save will not leave a stale file
    behind, and re-saving never creates a second file to a use case another
    save already wrote.

    Raises RunRepositoryError if there is nothing to save -- an empty
    selection is never a valid "run" and should never produce files that a
    later consumer might mistake for a real, intentional selection.
    """
    if not selected_use_cases:
        raise RunRepositoryError("No use cases are selected for this run yet.")

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    for stale_path in _files_for_session(session_id):
        stale_path.unlink()

    saved_at = datetime.now(timezone.utc).isoformat()
    file_paths: List[Path] = []
    for use_case_id, info in selected_use_cases.items():
        contract: UseCaseContract = info["contract"]
        payload = {
            "session_id": session_id,
            "saved_at": saved_at,
            "use_case_id": use_case_id,
            "catalog_platform": info["catalog_platform"],
            "contract": json.loads(contract.to_pretty_json()),
        }
        file_path = _use_case_file_path(session_id, use_case_id)
        file_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        file_paths.append(file_path)

    return SavedRunSelection(
        session_id=session_id,
        file_paths=file_paths,
        use_case_count=len(file_paths),
    )


def delete_run_selection(session_id: str) -> None:
    """
    Permanently remove every use-case file this session has saved.

    This is the cleanup half of save_selected_use_cases(): it is expected to
    be called once -- and only once, right after -- the session identified
    by session_id has had its result reported back to the user. Until the
    code that reports results exists, nothing in this module calls this
    automatically; it is exposed so that code (or a manual UI action, in the
    meantime) can.
    """
    matching_files = _files_for_session(session_id)
    if not matching_files:
        raise RunRepositoryError(f"No saved run selection found for session '{session_id}'.")
    for file_path in matching_files:
        file_path.unlink()


def list_pending_run_selections() -> List[SavedRunSelection]:
    """
    List every session that still has saved use-case files sitting in
    data/runs/, i.e. not yet cleaned up, grouped back into one entry per
    session for the UI's benefit.

    In the finished pipeline this should typically be empty: each entry
    represents a session whose result has not been reported (and therefore
    deleted) yet. Exposed so the UI can show, and let a user manually clear,
    anything left over while that automatic step doesn't exist yet.
    """
    if not RUNS_DIR.exists():
        return []

    files_by_session: Dict[str, List[Path]] = {}
    for file_path in sorted(RUNS_DIR.iterdir()):
        if not file_path.is_file() or _SESSION_SEPARATOR not in file_path.stem:
            continue
        session_id = file_path.stem.split(_SESSION_SEPARATOR, 1)[0]
        files_by_session.setdefault(session_id, []).append(file_path)

    return [
        SavedRunSelection(session_id=session_id, file_paths=paths, use_case_count=len(paths))
        for session_id, paths in files_by_session.items()
    ]
