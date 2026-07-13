from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class DynamoTableClient(Protocol):
    """
    Storage contract shared by the in-memory mock and a future Boto3 adapter.

    Swap implementations via `create_table_client()` without changing UseCasesTable
    or the pipeline nodes that depend on it.
    """

    table_name: str
    partition_key: str
    sort_key: str

    def put_item(self, item: Dict[str, Any]) -> Dict[str, Any]: ...

    def get_item(self, partition_value: str, sort_value: str) -> Optional[Dict[str, Any]]: ...

    def update_item(
        self,
        partition_value: str,
        sort_value: str,
        updates: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]: ...

    def query(
        self,
        partition_value: str,
        sort_value: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...

    def query_gsi(
        self,
        index_name: str,
        partition_value: str,
        sort_value: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...

    def delete_item(self, partition_value: str, sort_value: str) -> bool: ...

    def scan(self) -> List[Dict[str, Any]]: ...

    def count(self) -> int: ...

    def clear(self) -> None: ...
