from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from infra.use_case_service.schemas import UseCaseContract

PROJECT_ROOT = Path(__file__).resolve().parents[3]
USE_CASES_DIR = PROJECT_ROOT / "data" / "useCases"
CATALOG_PATH = USE_CASES_DIR / "useCase.catalog.json"
APPLICATION_DIR = PROJECT_ROOT / "data" / "application"


@dataclass
class CatalogEntry:
    id: str
    platform: str
    path: str
    type: str
    enabled: bool
    supports_rule_override: bool = True
    is_editable: bool = False


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_catalog() -> dict:
    return _read_json(CATALOG_PATH)


def _save_catalog(catalog: dict) -> None:
    _write_json(CATALOG_PATH, catalog)


def list_available_apps() -> list[str]:
    if not APPLICATION_DIR.exists():
        return []
    return sorted(
        str(p.relative_to(PROJECT_ROOT)).replace("\\", "/")
        for p in APPLICATION_DIR.iterdir()
        if p.is_file()
    )


def list_use_cases(*, enabled_only: bool = False) -> list[CatalogEntry]:
    catalog = _load_catalog()
    out: list[CatalogEntry] = []
    for item in catalog.get("useCases", []):
        if enabled_only and not item.get("enabled", True):
            continue
        rel = item["path"]
        out.append(
            CatalogEntry(
                id=item["id"],
                platform=item["platform"],
                path=rel,
                type=item.get("type", "custom"),
                enabled=item.get("enabled", True),
                supports_rule_override=item.get("supports_rule_override", True),
                is_editable=rel.startswith("./custom/"),
            )
        )
    return out


def _entry_file(entry: CatalogEntry) -> Path:
    return USE_CASES_DIR / entry.path.replace("./", "")


def load_use_case(entry: CatalogEntry) -> UseCaseContract:
    return UseCaseContract.model_validate(_read_json(_entry_file(entry)))


def resolve_for_run(contract: UseCaseContract, *, app_id: str, dev_key: str) -> UseCaseContract:
    payload = contract.model_dump()
    payload["app_id"] = app_id
    payload["dev_key"] = dev_key
    return UseCaseContract.model_validate(payload)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "custom"


def save_custom_use_case(contract: UseCaseContract, *, name: str) -> CatalogEntry:
    base = _slug(name)
    entry_id = f"custom-{base}"
    rel_path = f"./custom/{entry_id}.json"
    file_path = USE_CASES_DIR / rel_path.replace("./", "")

    counter = 2
    while file_path.exists():
        entry_id = f"custom-{base}-{counter}"
        rel_path = f"./custom/{entry_id}.json"
        file_path = USE_CASES_DIR / rel_path.replace("./", "")
        counter += 1

    _write_json(file_path, contract.model_dump(exclude_none=True))

    catalog = _load_catalog()
    catalog["useCases"] = [u for u in catalog.get("useCases", []) if u.get("id") != entry_id] + [
        {
            "id": entry_id,
            "platform": contract.platform,
            "path": rel_path,
            "type": "custom",
            "enabled": True,
            "supports_rule_override": True,
        }
    ]
    _save_catalog(catalog)

    return CatalogEntry(
        id=entry_id,
        platform=contract.platform,
        path=rel_path,
        type="custom",
        enabled=True,
        supports_rule_override=True,
        is_editable=True,
    )


def update_custom_use_case(entry_id: str, contract: UseCaseContract) -> CatalogEntry:
    entries = {e.id: e for e in list_use_cases(enabled_only=False)}
    if entry_id not in entries:
        raise ValueError(f"Unknown use case id: {entry_id}")
    entry = entries[entry_id]
    if not entry.is_editable:
        raise ValueError(f"'{entry_id}' is not editable.")
    _write_json(_entry_file(entry), contract.model_dump(exclude_none=True))
    return entry


def delete_custom_use_case(entry_id: str) -> None:
    catalog = _load_catalog()
    keep: list[dict] = []
    delete_path: Path | None = None

    for item in catalog.get("useCases", []):
        if item.get("id") == entry_id:
            rel = item.get("path", "")
            if not rel.startswith("./custom/"):
                raise ValueError(f"'{entry_id}' is not deletable (seed use case).")
            delete_path = USE_CASES_DIR / rel.replace("./", "")
            continue
        keep.append(item)

    catalog["useCases"] = keep
    _save_catalog(catalog)

    if delete_path and delete_path.exists():
        delete_path.unlink()
