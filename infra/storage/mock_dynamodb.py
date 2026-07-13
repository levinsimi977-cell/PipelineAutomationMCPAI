from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

ProjectionType = Literal["ALL", "INCLUDE", "KEYS_ONLY"]


@dataclass(frozen=True)
class GlobalSecondaryIndex:
    """Describes how a GSI maps source attributes onto index keys."""

    name: str
    partition_key: str
    sort_key: str
    projection_type: ProjectionType = "ALL"
    projected_attributes: tuple[str, ...] = ()


class MockDynamoTable:
    """
    In-memory DynamoDB table mock with optional Global Secondary Indexes.

    Items are stored as plain dictionaries. The table enforces unique (PK, SK)
    pairs on the main table and applies GSI projection rules on index queries.
    """

    def __init__(
        self,
        table_name: str,
        partition_key: str,
        sort_key: str,
        global_secondary_indexes: Optional[List[GlobalSecondaryIndex]] = None,
    ) -> None:
        self.table_name = table_name
        self.partition_key = partition_key
        self.sort_key = sort_key
        self.global_secondary_indexes = global_secondary_indexes or []
        self._items: Dict[tuple[str, str], Dict[str, Any]] = {}

    def put_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        pk = item[self.partition_key]
        sk = item[self.sort_key]
        stored_item = copy.deepcopy(item)
        self._items[(pk, sk)] = stored_item
        return copy.deepcopy(stored_item)

    def get_item(self, partition_value: str, sort_value: str) -> Optional[Dict[str, Any]]:
        item = self._items.get((partition_value, sort_value))
        if item is None:
            return None
        return copy.deepcopy(item)

    def update_item(
        self,
        partition_value: str,
        sort_value: str,
        updates: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        key = (partition_value, sort_value)
        if key not in self._items:
            return None

        item = copy.deepcopy(self._items[key])
        for attribute, value in updates.items():
            if attribute in {self.partition_key, self.sort_key}:
                raise ValueError(f"Cannot update key attribute '{attribute}'")
            item[attribute] = copy.deepcopy(value)

        self._items[key] = item
        return copy.deepcopy(item)

    def query(
        self,
        partition_value: str,
        sort_value: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        matches = [
            copy.deepcopy(item)
            for (pk, sk), item in self._items.items()
            if pk == partition_value and (sort_value is None or sk == sort_value)
        ]
        return sorted(matches, key=lambda item: item[self.sort_key])

    def query_gsi(
        self,
        index_name: str,
        partition_value: str,
        sort_value: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        index = self._get_index(index_name)
        matches: List[Dict[str, Any]] = []

        for item in self._items.values():
            if item.get(index.partition_key) != partition_value:
                continue
            if sort_value is not None and item.get(index.sort_key) != sort_value:
                continue
            matches.append(self._project_item_for_index(item, index))

        return sorted(matches, key=lambda item: item[index.sort_key])

    def delete_item(self, partition_value: str, sort_value: str) -> bool:
        return self._items.pop((partition_value, sort_value), None) is not None

    def scan(self) -> List[Dict[str, Any]]:
        return sorted(
            [copy.deepcopy(item) for item in self._items.values()],
            key=lambda item: (item[self.partition_key], item[self.sort_key]),
        )

    def count(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items.clear()

    def _project_item_for_index(
        self,
        item: Dict[str, Any],
        index: GlobalSecondaryIndex,
    ) -> Dict[str, Any]:
        source = copy.deepcopy(item)

        if index.projection_type == "ALL":
            return source

        projected = {
            index.partition_key: source[index.partition_key],
            index.sort_key: source[index.sort_key],
        }

        if index.projection_type == "INCLUDE":
            for attribute in index.projected_attributes:
                if attribute in source:
                    projected[attribute] = source[attribute]

        return projected

    def _get_index(self, index_name: str) -> GlobalSecondaryIndex:
        for index in self.global_secondary_indexes:
            if index.name == index_name:
                return index
        raise KeyError(f"GSI '{index_name}' is not defined on table '{self.table_name}'")
