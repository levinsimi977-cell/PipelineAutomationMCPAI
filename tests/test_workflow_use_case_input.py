import json
from pathlib import Path

import pytest

from infra.storage.use_cases_table import seed_use_cases_from_catalog
from infra.workflow.workflow_nodes import json_use_case_input_node


@pytest.fixture
def seeded_table(tmp_path: Path):
    catalog_path = Path("data/useCases/useCase.catalog.json")
    return seed_use_cases_from_catalog(catalog_path)


def test_json_use_case_input_node_loads_from_mock_dynamodb(
    seeded_table,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "infra.workflow.use_case_loader.get_or_seed_use_cases_table",
        lambda **kwargs: seeded_table,
    )
    monkeypatch.setattr("infra.workflow.use_case_loader.DEFAULT_RUNS_DIR", tmp_path)

    state = {
        "visited_user_actions": False,
        "last_prompt_type": "integrate_prompt",
        "platform": "ios",
        "run_id": "pipeline-run-001",
    }

    result = json_use_case_input_node(state)

    assert result["test_status"] == "READY"
    assert result["use_case_count"] == 4
    assert result["platform"] == "ios"
    assert "selected_use_cases_path" in result

    output_path = Path(result["selected_use_cases_path"])
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(payload["useCases"]) == 4


def test_json_use_case_input_node_handles_missing_platform() -> None:
    state = {
        "visited_user_actions": False,
        "last_prompt_type": "integrate_prompt",
    }

    result = json_use_case_input_node(state)

    assert result["test_status"] == "FAIL"
    assert "Missing 'platform'" in result["error_reason"]
