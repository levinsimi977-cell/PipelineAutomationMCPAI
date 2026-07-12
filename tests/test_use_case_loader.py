import json
from pathlib import Path

import pytest

from infra.storage.use_cases_table import UseCasesTable, seed_use_cases_from_catalog
from infra.workflow.use_case_loader import (
    load_and_select_use_cases,
    select_use_cases_for_platform,
)


@pytest.fixture
def catalog_path() -> Path:
    return Path("data/useCases/useCase.catalog.json")


@pytest.fixture
def seeded_table(catalog_path: Path) -> UseCasesTable:
    return seed_use_cases_from_catalog(catalog_path)


def test_select_use_cases_for_ios_platform(seeded_table: UseCasesTable, catalog_path: Path) -> None:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    records = seeded_table.list_use_cases_for_user("user-001")

    selected = select_use_cases_for_platform(records, "ios", catalog)

    assert len(selected) == 4
    catalog_platforms = {record.identifier for record in selected}
    assert catalog_platforms == {"common", "ios"}


def test_load_and_select_use_cases_writes_pipeline_file(
    seeded_table: UseCasesTable,
    tmp_path: Path,
) -> None:
    result = load_and_select_use_cases(
        platform="android",
        user_id="user-001",
        run_id="test-run-001",
        table=seeded_table,
        runs_dir=tmp_path,
    )

    assert result["test_status"] == "READY"
    assert result["use_case_count"] == 4
    assert len(result["use_case_ids"]) == 4

    output_path = Path(result["selected_use_cases_path"])
    assert output_path.exists()

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(payload["useCases"]) == 4
    assert payload["useCases"][0]["useCaseId"]
    assert payload["useCases"][0]["prompt_goal"]


def test_load_and_select_use_cases_fails_when_no_matches(
    tmp_path: Path,
) -> None:
    empty_table = UseCasesTable()

    with pytest.raises(ValueError, match="No use cases found"):
        load_and_select_use_cases(
            platform="ios",
            user_id="user-001",
            run_id="empty-run",
            table=empty_table,
            runs_dir=tmp_path,
        )
