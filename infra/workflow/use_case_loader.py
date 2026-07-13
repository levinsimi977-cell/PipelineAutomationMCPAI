from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from infra.storage.use_cases_table import (
    DEFAULT_CATALOG_PATH,
    DEFAULT_USER_ID,
    UseCaseRecord,
    UseCasesTable,
    get_or_seed_use_cases_table,
)

DEFAULT_RUNS_DIR = Path("data/runs")


def load_catalog(catalog_path: Path = DEFAULT_CATALOG_PATH) -> Dict[str, Any]:
    return json.loads(catalog_path.read_text(encoding="utf-8"))


def resolve_record_platform(record: UseCaseRecord) -> str:
    """Catalog platform used for selection (common / ios / android)."""
    return record.identifier or record.json_file.get("platform", "")


def select_use_cases_for_platform(
    records: List[UseCaseRecord],
    platform: str,
    catalog: Dict[str, Any],
) -> List[UseCaseRecord]:
    strategy = catalog.get("selectionStrategy", {})
    always_include = strategy.get("alwaysIncludePlatform", "common")
    also_include_selected = strategy.get("alsoIncludeSelectedPlatform", True)

    selected: List[UseCaseRecord] = []
    for record in records:
        record_platform = resolve_record_platform(record)
        if record_platform == always_include:
            selected.append(record)
        elif also_include_selected and record_platform == platform:
            selected.append(record)

    return sorted(selected, key=lambda record: record.use_case_name)


def record_to_pipeline_payload(record: UseCaseRecord) -> Dict[str, Any]:
    payload = dict(record.json_file)
    payload.update(
        {
            "id": record.catalog_id or record.use_case_id,
            "useCaseId": record.use_case_id,
            "useCaseName": record.use_case_name,
            "identifier": record.identifier,
            "catalogPlatform": record.identifier,
            "platform": record.json_file.get("platform") or record.identifier,
        }
    )
    if record.json_file.get("prompt_goal") and "prompt" not in payload:
        payload["prompt"] = record.json_file["prompt_goal"]
    return payload


def build_selected_use_cases_payload(records: List[UseCaseRecord]) -> Dict[str, Any]:
    return {
        "useCases": [record_to_pipeline_payload(record) for record in records],
    }


def write_selected_use_cases_file(
    payload: Dict[str, Any],
    run_id: str,
    runs_dir: Path = DEFAULT_RUNS_DIR,
) -> Path:
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / "selected_use_cases.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def generate_run_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{timestamp}_{uuid.uuid4().hex[:8]}"


def load_and_select_use_cases(
    *,
    platform: str,
    user_id: str = DEFAULT_USER_ID,
    run_id: Optional[str] = None,
    catalog_path: Path = DEFAULT_CATALOG_PATH,
    table: Optional[UseCasesTable] = None,
    runs_dir: Path = DEFAULT_RUNS_DIR,
) -> Dict[str, Any]:
    """
    Load use cases from the mock DynamoDB table, apply catalog selection rules,
    and materialize the selected payload for downstream pipeline nodes.
    """
    repository = get_or_seed_use_cases_table(
        catalog_path=catalog_path,
        user_id=user_id,
        table=table,
    )
    catalog = load_catalog(catalog_path)
    all_records = repository.list_use_cases_for_user(user_id)
    selected_records = select_use_cases_for_platform(all_records, platform, catalog)

    if not selected_records:
        raise ValueError(
            f"No use cases found for user '{user_id}' and platform '{platform}'."
        )

    resolved_run_id = run_id or generate_run_id()
    payload = build_selected_use_cases_payload(selected_records)
    selected_path = write_selected_use_cases_file(payload, resolved_run_id, runs_dir=runs_dir)

    primary = selected_records[0]
    return {
        "run_id": resolved_run_id,
        "user_id": user_id,
        "platform": platform,
        "use_case_ids": [record.use_case_id for record in selected_records],
        "selected_use_cases_path": str(selected_path.resolve()),
        "selected_use_cases": payload["useCases"],
        "use_case_count": len(selected_records),
        "test_status": "READY",
        "primary_use_case_id": primary.use_case_id,
        "primary_use_case_name": primary.use_case_name,
    }
