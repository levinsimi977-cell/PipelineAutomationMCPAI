"""
Seed and inspect the mock UseCases DynamoDB table.

Usage:
    py -m infra.storage.demo_use_cases_table
"""

from __future__ import annotations

import json
from pathlib import Path

from infra.storage.use_cases_table import DEFAULT_USER_ID, seed_use_cases_from_catalog


def main() -> None:
    catalog_path = Path("data/useCases/useCase.catalog.json")
    table = seed_use_cases_from_catalog(catalog_path)

    print(f"Seeded {table.count()} use cases into mock table 'UseCases'\n")

    print(f"--- GSI query (Projection: ALL) for {DEFAULT_USER_ID} ---")
    for record in table.list_use_cases_for_user(DEFAULT_USER_ID):
        print(
            f"  {record.use_case_id} | {record.use_case_name} | "
            f"platform={record.json_file.get('platform')}"
        )

    first = table.list_use_cases_for_user(DEFAULT_USER_ID)[0]
    print("\n--- Main table get_item (first use case) ---")
    print(json.dumps(first.to_item(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
