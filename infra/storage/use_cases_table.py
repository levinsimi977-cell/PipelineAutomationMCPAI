from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from infra.storage.mock_dynamodb import GlobalSecondaryIndex, MockDynamoTable
from infra.storage.protocol import DynamoTableClient

USE_CASES_TABLE_NAME = "UseCases"
USER_ID_INDEX_NAME = "userId-index"
DEFAULT_USER_ID = "user-001"
USE_CASE_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
DEFAULT_CATALOG_PATH = Path("data/useCases/useCase.catalog.json")


@dataclass(frozen=True)
class UseCaseRecord:
    use_case_id: str
    user_id: str
    use_case_name: str
    identifier: str
    json_file: Dict[str, Any]
    catalog_id: Optional[str] = None

    def to_item(self) -> Dict[str, Any]:
        item = {
            "useCaseId": self.use_case_id,
            "userId": self.user_id,
            "useCaseName": self.use_case_name,
            "identifier": self.identifier,
            "jsonFile": self.json_file,
        }
        if self.catalog_id is not None:
            item["catalogId"] = self.catalog_id
        return item

    @classmethod
    def from_item(cls, item: Dict[str, Any]) -> "UseCaseRecord":
        return cls(
            use_case_id=item["useCaseId"],
            user_id=item["userId"],
            use_case_name=item["useCaseName"],
            identifier=item["identifier"],
            json_file=item["jsonFile"],
            catalog_id=item.get("catalogId"),
        )


def create_use_cases_table(table: Optional[DynamoTableClient] = None) -> MockDynamoTable:
    if table is not None:
        return table  # type: ignore[return-value]

    return MockDynamoTable(
        table_name=USE_CASES_TABLE_NAME,
        partition_key="useCaseId",
        sort_key="userId",
        global_secondary_indexes=[
            GlobalSecondaryIndex(
                name=USER_ID_INDEX_NAME,
                partition_key="userId",
                sort_key="useCaseId",
                projection_type="ALL",
            )
        ],
    )


def create_table_client() -> DynamoTableClient:
    """Factory for the active DynamoDB client implementation."""
    if os.getenv("USE_BOTO3_DYNAMODB", "").lower() in {"1", "true", "yes"}:
        raise NotImplementedError(
            "Boto3 DynamoDB adapter is not implemented yet. "
            "Unset USE_BOTO3_DYNAMODB to use the in-memory mock."
        )
    return create_use_cases_table()


class UseCasesTable:
    """
    Repository for the UseCases DynamoDB table shown in the architecture diagram.

    Main table keys:
      - PK: useCaseId
      - SK: userId

    GSI (userId-index):
      - PK: userId
      - SK: useCaseId
      - Projection: ALL
    """

    def __init__(self, table: Optional[DynamoTableClient] = None) -> None:
        self.table = table or create_table_client()

    def put_use_case(self, record: UseCaseRecord) -> UseCaseRecord:
        self.table.put_item(record.to_item())
        return record

    def get_use_case(self, use_case_id: str, user_id: str) -> Optional[UseCaseRecord]:
        item = self.table.get_item(use_case_id, user_id)
        if item is None:
            return None
        return UseCaseRecord.from_item(item)

    def update_use_case(
        self,
        use_case_id: str,
        user_id: str,
        updates: Dict[str, Any],
    ) -> Optional[UseCaseRecord]:
        item = self.table.update_item(use_case_id, user_id, updates)
        if item is None:
            return None
        return UseCaseRecord.from_item(item)

    def update_json_file(
        self,
        use_case_id: str,
        user_id: str,
        patch: Dict[str, Any],
    ) -> Optional[UseCaseRecord]:
        current = self.table.get_item(use_case_id, user_id)
        if current is None:
            return None

        merged_json = {**current.get("jsonFile", {}), **patch}
        updated = self.table.update_item(use_case_id, user_id, {"jsonFile": merged_json})
        if updated is None:
            return None
        return UseCaseRecord.from_item(updated)

    def list_use_cases_for_user(self, user_id: str) -> List[UseCaseRecord]:
        items = self.table.query_gsi(USER_ID_INDEX_NAME, user_id)
        return [UseCaseRecord.from_item(item) for item in items]

    def list_full_use_cases_for_user(self, user_id: str) -> List[UseCaseRecord]:
        """Backward-compatible alias; GSI projection ALL already returns full items."""
        return self.list_use_cases_for_user(user_id)

    def list_use_cases_by_id(self, use_case_id: str) -> List[UseCaseRecord]:
        return [UseCaseRecord.from_item(item) for item in self.table.query(use_case_id)]

    def delete_use_case(self, use_case_id: str, user_id: str) -> bool:
        return self.table.delete_item(use_case_id, user_id)

    def count(self) -> int:
        return self.table.count()


def stable_use_case_id(catalog_id: str) -> str:
    """Derive a deterministic UUID from a catalog entry id."""
    return str(uuid.uuid5(USE_CASE_NAMESPACE, catalog_id))


def humanize_use_case_name(catalog_id: str) -> str:
    return catalog_id.replace("-", " ").strip()


def seed_use_cases_from_catalog(
    catalog_path: Path,
    user_id: str = DEFAULT_USER_ID,
    table: Optional[UseCasesTable] = None,
) -> UseCasesTable:
    """Load enabled use cases from the local catalog JSON into the mock DynamoDB table."""
    repository = table or UseCasesTable()
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog_dir = catalog_path.parent

    for entry in catalog.get("useCases", []):
        if not entry.get("enabled", True):
            continue

        relative_path = entry["path"]
        json_path = (catalog_dir / relative_path).resolve()
        # A catalog entry may reference a use-case file that is not present on
        # this machine (e.g. gitignored `custom/` files that were never shared).
        # Skip such entries instead of crashing the whole seeding process.
        try:
            json_file = json.loads(json_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            print(
                f"[use_cases_table] Skipping catalog entry '{entry.get('id')}': "
                f"could not load '{json_path}' ({exc})."
            )
            continue

        record = UseCaseRecord(
            use_case_id=stable_use_case_id(entry["id"]),
            user_id=user_id,
            use_case_name=humanize_use_case_name(entry["id"]),
            identifier=entry.get("platform") or entry.get("type") or "general",
            json_file=json_file,
            catalog_id=entry["id"],
        )
        repository.put_use_case(record)

    return repository


def get_or_seed_use_cases_table(
    catalog_path: Path = DEFAULT_CATALOG_PATH,
    user_id: str = DEFAULT_USER_ID,
    table: Optional[UseCasesTable] = None,
) -> UseCasesTable:
    """Return a populated table, seeding from the catalog only when none is provided."""
    if table is not None:
        return table

    repository = UseCasesTable()
    if repository.count() == 0:
        seed_use_cases_from_catalog(catalog_path, user_id=user_id, table=repository)
    return repository
