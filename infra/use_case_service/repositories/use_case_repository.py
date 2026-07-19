from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from infra.storage.use_cases_table import (
    DEFAULT_USER_ID,
    UseCaseRecord,
    UseCasesTable,
    get_or_seed_use_cases_table,
    humanize_use_case_name,
    stable_use_case_id,
)
from infra.use_case_service.schemas import UseCaseContract

PROJECT_ROOT = Path(__file__).resolve().parents[3]
USE_CASES_DIR = PROJECT_ROOT / "data" / "useCases"
CATALOG_PATH = USE_CASES_DIR / "useCase.catalog.json"
APPLICATION_DIR = PROJECT_ROOT / "data" / "application"

# Keep in sync with resolve_and_replicate_app() in infra/application/app.py
PLATFORM_SAMPLE_APPS = {
    "android": "data/application/appsflyer-onelink-android-sample-apps-liaz-empty_app_new",
    "ios": "data/application/appsflyer-onelink-ios-sample-apps",
}

_table: Optional[UseCasesTable] = None


@dataclass
class CatalogEntry:
    id: str
    platform: str
    path: str
    type: str
    enabled: bool
    supports_rule_override: bool = True
    is_editable: bool = False


def _get_table() -> UseCasesTable:
    """Process-wide mock table; seeded once from the on-disk catalog."""
    global _table
    if _table is None:
        _table = get_or_seed_use_cases_table(catalog_path=CATALOG_PATH)
    return _table


def _public_id(record: UseCaseRecord) -> str:
    return record.catalog_id or record.use_case_id


def _is_custom_id(entry_id: str) -> bool:
    return entry_id.startswith("custom-")


def _record_to_entry(record: UseCaseRecord) -> CatalogEntry:
    entry_id = _public_id(record)
    custom = _is_custom_id(entry_id)
    return CatalogEntry(
        id=entry_id,
        platform=record.identifier,
        path=f"./custom/{entry_id}.json" if custom else f"./seed/{entry_id}.json",
        type="custom" if custom else "seed",
        enabled=True,
        supports_rule_override=True,
        is_editable=custom,
    )


def _find_record(entry_id: str, user_id: str = DEFAULT_USER_ID) -> Optional[UseCaseRecord]:
    for record in _get_table().list_use_cases_for_user(user_id):
        if _public_id(record) == entry_id:
            return record
    return None


def list_available_apps() -> list[str]:
    if not APPLICATION_DIR.exists():
        return []

    apps: list[str] = []
    for path in APPLICATION_DIR.iterdir():
        if path.is_file():
            apps.append(str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"))

    for rel in PLATFORM_SAMPLE_APPS.values():
        if (PROJECT_ROOT / rel).is_dir():
            apps.append(rel)

    return sorted(set(apps))


def list_use_cases(*, enabled_only: bool = False) -> list[CatalogEntry]:
    # Mock table only holds enabled (and newly created) records.
    _ = enabled_only
    return [
        _record_to_entry(record)
        for record in _get_table().list_use_cases_for_user(DEFAULT_USER_ID)
    ]


def load_use_case(entry: CatalogEntry) -> UseCaseContract:
    record = _find_record(entry.id)
    if record is None:
        raise ValueError(f"Unknown use case id: {entry.id}")
    return UseCaseContract.model_validate(record.json_file)


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

    existing_ids = {_public_id(r) for r in _get_table().list_use_cases_for_user(DEFAULT_USER_ID)}
    counter = 2
    while entry_id in existing_ids:
        entry_id = f"custom-{base}-{counter}"
        counter += 1

    record = UseCaseRecord(
        use_case_id=stable_use_case_id(entry_id),
        user_id=DEFAULT_USER_ID,
        use_case_name=humanize_use_case_name(entry_id),
        identifier=contract.platform,
        json_file=contract.model_dump(exclude_none=True),
        catalog_id=entry_id,
    )
    _get_table().put_use_case(record)
    return _record_to_entry(record)


def update_custom_use_case(entry_id: str, contract: UseCaseContract) -> CatalogEntry:
    record = _find_record(entry_id)
    if record is None:
        raise ValueError(f"Unknown use case id: {entry_id}")
    if not _is_custom_id(entry_id):
        raise ValueError(f"'{entry_id}' is not editable.")

    updated = UseCaseRecord(
        use_case_id=record.use_case_id,
        user_id=record.user_id,
        use_case_name=record.use_case_name,
        identifier=contract.platform,
        json_file=contract.model_dump(exclude_none=True),
        catalog_id=record.catalog_id,
    )
    _get_table().put_use_case(updated)
    return _record_to_entry(updated)


def delete_custom_use_case(entry_id: str) -> None:
    if not _is_custom_id(entry_id):
        raise ValueError(f"'{entry_id}' is not deletable (seed use case).")

    record = _find_record(entry_id)
    if record is None:
        raise ValueError(f"Unknown use case id: {entry_id}")

    _get_table().delete_use_case(record.use_case_id, record.user_id)
