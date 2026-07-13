from pathlib import Path

import pytest

from infra.storage.use_cases_table import (
    DEFAULT_USER_ID,
    UseCaseRecord,
    UseCasesTable,
    seed_use_cases_from_catalog,
    stable_use_case_id,
)


@pytest.fixture
def catalog_path() -> Path:
    return Path("data/useCases/useCase.catalog.json")


@pytest.fixture
def table() -> UseCasesTable:
    return UseCasesTable()


def test_put_and_get_use_case(table: UseCasesTable) -> None:
    record = UseCaseRecord(
        use_case_id="3fa85f64-5717-4562-b3fc-2c963f66afa6",
        user_id="user-001",
        use_case_name="Login flow validation",
        identifier="auth-module",
        json_file={"steps": [{"action": "open_app"}]},
    )

    table.put_use_case(record)
    loaded = table.get_use_case(record.use_case_id, record.user_id)

    assert loaded == record


def test_query_use_case_by_id(table: UseCasesTable) -> None:
    record = UseCaseRecord(
        use_case_id="11111111-1111-1111-1111-111111111111",
        user_id="user-001",
        use_case_name="Deep link smoke",
        identifier="common",
        json_file={"platform": "ios"},
    )
    table.put_use_case(record)

    results = table.list_use_cases_by_id(record.use_case_id)

    assert len(results) == 1
    assert results[0].use_case_name == "Deep link smoke"


def test_list_use_cases_for_user_returns_full_records_via_gsi_all(table: UseCasesTable) -> None:
    table.put_use_case(
        UseCaseRecord(
            use_case_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            user_id="user-001",
            use_case_name="First case",
            identifier="ios",
            json_file={"platform": "ios", "prompt_goal": "Validate iOS flow"},
        )
    )
    table.put_use_case(
        UseCaseRecord(
            use_case_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            user_id="user-001",
            use_case_name="Second case",
            identifier="android",
            json_file={"platform": "android", "prompt_goal": "Validate Android flow"},
        )
    )
    table.put_use_case(
        UseCaseRecord(
            use_case_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
            user_id="user-002",
            use_case_name="Other user case",
            identifier="common",
            json_file={"platform": "common"},
        )
    )

    user_cases = table.list_use_cases_for_user("user-001")

    assert len(user_cases) == 2
    assert {case.use_case_name for case in user_cases} == {"First case", "Second case"}
    assert user_cases[0].json_file["prompt_goal"].startswith("Validate")


def test_update_use_case_field(table: UseCasesTable) -> None:
    record = UseCaseRecord(
        use_case_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
        user_id="user-001",
        use_case_name="Original name",
        identifier="ios",
        json_file={"platform": "ios", "test_status": "READY"},
    )
    table.put_use_case(record)

    updated = table.update_use_case(
        record.use_case_id,
        record.user_id,
        {"useCaseName": "Updated name"},
    )

    assert updated is not None
    assert updated.use_case_name == "Updated name"
    assert updated.json_file["test_status"] == "READY"


def test_update_json_file_merges_nested_fields(table: UseCasesTable) -> None:
    record = UseCaseRecord(
        use_case_id="eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
        user_id="user-001",
        use_case_name="Json patch case",
        identifier="ios",
        json_file={"platform": "ios", "test_status": "READY", "nested": {"a": 1}},
    )
    table.put_use_case(record)

    updated = table.update_json_file(
        record.use_case_id,
        record.user_id,
        {"test_status": "RUNNING", "agent_messages": ["started"]},
    )

    assert updated is not None
    assert updated.json_file["test_status"] == "RUNNING"
    assert updated.json_file["platform"] == "ios"
    assert updated.json_file["nested"] == {"a": 1}
    assert updated.json_file["agent_messages"] == ["started"]


def test_put_item_deep_copy_prevents_external_mutation(table: UseCasesTable) -> None:
    json_file = {"platform": "ios"}
    record = UseCaseRecord(
        use_case_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        user_id="user-001",
        use_case_name="Isolation case",
        identifier="ios",
        json_file=json_file,
    )
    table.put_use_case(record)

    json_file["platform"] = "android"
    loaded = table.get_use_case(record.use_case_id, record.user_id)

    assert loaded is not None
    assert loaded.json_file["platform"] == "ios"


def test_seed_use_cases_from_catalog(catalog_path: Path) -> None:
    table = seed_use_cases_from_catalog(catalog_path, user_id=DEFAULT_USER_ID)

    assert table.count() == 5

    ios_case_id = stable_use_case_id("ios-universal-link-validation")
    ios_case = table.get_use_case(ios_case_id, DEFAULT_USER_ID)

    assert ios_case is not None
    assert ios_case.identifier == "ios"
    assert ios_case.catalog_id == "ios-universal-link-validation"
    assert ios_case.json_file["platform"] == "ios"
    assert "universal link" in ios_case.json_file["prompt_goal"].lower()

    user_cases = table.list_use_cases_for_user(DEFAULT_USER_ID)
    assert len(user_cases) == 5
