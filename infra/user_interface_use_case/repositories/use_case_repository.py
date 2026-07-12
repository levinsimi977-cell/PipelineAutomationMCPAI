"""
Single source of truth for reading and writing use cases.

Nothing outside this module should touch the catalog file or the use case
JSON files directly. The UI (or any future caller) always goes through
these functions, so there is exactly one place that:
  - knows the on-disk layout (catalog + common/ios/android/custom folders),
  - guarantees every use case handed back has already passed UseCaseContract
    validation,
  - enforces that seed use cases are read-only and only custom ones can be
    edited or deleted.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..schemas import UseCaseContract
from ..utils import encode_dev_key

# infra/user_interface_use_case/repositories/use_case_repository.py -> PiplineAutomatoinMCP
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = _PROJECT_ROOT / "data"
USE_CASES_DIR = DATA_DIR / "useCases"
CATALOG_PATH = USE_CASES_DIR / "useCase.catalog.json"
CUSTOM_DIR = USE_CASES_DIR / "custom"
APPLICATIONS_DIR = DATA_DIR / "application"


class UseCaseRepositoryError(Exception):
    """Raised for invalid operations, e.g. editing/deleting a seed use case."""


@dataclass(frozen=True)
class CatalogEntry:
    """One row of the catalog — metadata about a use case, not its content."""

    id: str
    platform: str
    path: str
    type: str
    enabled: bool
    source: str = "seed"

    @property
    def is_editable(self) -> bool:
        """Only user-created (custom) use cases may be edited or deleted."""
        return self.source == "custom"

    def resolve_path(self) -> Path:
        """Absolute filesystem path of this entry's use case JSON file."""
        return (USE_CASES_DIR / self.path).resolve()


def _load_catalog_raw() -> Dict[str, Any]:
    """Read the catalog file as a plain dict; start empty if it doesn't exist yet."""
    if not CATALOG_PATH.exists():
        return {"version": "1.0.0", "selectionStrategy": {}, "useCases": []}
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _save_catalog_raw(catalog: Dict[str, Any]) -> None:
    """Persist the catalog dict, preserving version/selectionStrategy untouched."""
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")


def _to_entry(raw: Dict[str, Any]) -> CatalogEntry:
    """Convert a raw catalog dict into a CatalogEntry, defaulting old rows to 'seed'."""
    return CatalogEntry(
        id=raw["id"],
        platform=raw["platform"],
        path=raw["path"],
        type=raw.get("type", "smoke"),
        enabled=raw.get("enabled", True),
        source=raw.get("source", "seed"),
    )


def list_use_cases(
    *,
    platform: Optional[str] = None,
    source: Optional[str] = None,
    enabled_only: bool = True,
) -> List[CatalogEntry]:
    """List catalog entries, optionally filtered by platform/source/enabled state."""
    catalog = _load_catalog_raw()
    entries = [_to_entry(raw) for raw in catalog.get("useCases", [])]

    if enabled_only:
        entries = [entry for entry in entries if entry.enabled]
    if platform is not None:
        entries = [entry for entry in entries if entry.platform == platform]
    if source is not None:
        entries = [entry for entry in entries if entry.source == source]
    return entries


def load_use_case(entry: CatalogEntry) -> UseCaseContract:
    """Load and validate the JSON file a catalog entry points to."""
    return UseCaseContract.from_file(entry.resolve_path())


def list_available_apps() -> List[str]:
    """
    List app binaries/bundles under data/application for the app_path picker.

    Returned as paths relative to the project root (matching the format
    already used inside the seed use case files, e.g. "data/application/banana.app"),
    so a selection can be dropped straight into app_path with no reformatting.
    """
    if not APPLICATIONS_DIR.exists():
        return []
    apps = sorted(child.name for child in APPLICATIONS_DIR.iterdir())
    return [f"data/application/{name}" for name in apps]


def _slugify(text: str) -> str:
    """Turn free-text into a filesystem/id-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "use-case"


def _unique_custom_id(base_id: str) -> str:
    """Avoid catalog id collisions by appending -2, -3, ... if needed."""
    existing_ids = {entry.id for entry in list_use_cases(enabled_only=False)}
    candidate = base_id
    suffix = 2
    while candidate in existing_ids:
        candidate = f"{base_id}-{suffix}"
        suffix += 1
    return candidate


def save_custom_use_case(
    contract: UseCaseContract,
    name: str,
    use_case_type: str = "custom",
) -> CatalogEntry:
    """
    Persist a brand-new custom use case: write its JSON file under custom/
    and register it in the catalog with source="custom" so it is editable
    and deletable later.
    """
    use_case_id = _unique_custom_id(f"custom-{_slugify(name)}")
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    file_path = CUSTOM_DIR / f"{use_case_id}.json"
    file_path.write_text(contract.to_pretty_json() + "\n", encoding="utf-8")

    catalog = _load_catalog_raw()
    catalog.setdefault("useCases", []).append(
        {
            "id": use_case_id,
            "platform": contract.platform,
            "path": f"./custom/{use_case_id}.json",
            "type": use_case_type,
            "enabled": True,
            "source": "custom",
        }
    )
    _save_catalog_raw(catalog)
    return _to_entry(catalog["useCases"][-1])


def _require_custom_entry(use_case_id: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Find a catalog entry by id and ensure it is a custom (editable) one."""
    catalog = _load_catalog_raw()
    for raw in catalog.get("useCases", []):
        if raw["id"] == use_case_id:
            if raw.get("source", "seed") != "custom":
                raise UseCaseRepositoryError(
                    f"'{use_case_id}' is a seed use case and cannot be modified or deleted."
                )
            return catalog, raw
    raise UseCaseRepositoryError(f"No use case found with id '{use_case_id}'.")


def update_custom_use_case(use_case_id: str, contract: UseCaseContract) -> CatalogEntry:
    """Overwrite an existing custom use case's file in place after re-validation."""
    catalog, raw = _require_custom_entry(use_case_id)
    file_path = (USE_CASES_DIR / raw["path"]).resolve()
    file_path.write_text(contract.to_pretty_json() + "\n", encoding="utf-8")

    raw["platform"] = contract.platform
    _save_catalog_raw(catalog)
    return _to_entry(raw)


def delete_custom_use_case(use_case_id: str) -> None:
    """Remove a custom use case's file and its catalog entry. Seed entries are rejected."""
    catalog, raw = _require_custom_entry(use_case_id)
    file_path = (USE_CASES_DIR / raw["path"]).resolve()
    if file_path.exists():
        file_path.unlink()

    catalog["useCases"] = [
        entry for entry in catalog.get("useCases", []) if entry["id"] != use_case_id
    ]
    _save_catalog_raw(catalog)


def resolve_for_run(
    contract: UseCaseContract,
    app_id: str,
    dev_key: str,
) -> UseCaseContract:
    """
    Overlay this run's personal credentials onto a use case template.

    Returns a new, fully re-validated UseCaseContract — the template's own
    file on disk (seed or custom) is never modified by this call. This is
    what lets the same shared seed/custom template be safely reused across
    different users/runs without one person's credentials leaking into it.
    """
    data = json.loads(contract.to_pretty_json())
    data["app_id"] = app_id
    data["dev_key"] = encode_dev_key(dev_key)
    return UseCaseContract.model_validate(data)
