from __future__ import annotations

import json
from pathlib import Path

import jsonschema


def validate_json_file(payload_path: str, schema_path: str) -> None:
    payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    jsonschema.validate(instance=payload, schema=schema)


if __name__ == "__main__":
    validate_json_file(
        payload_path="data/useCases/runtime-config.example.json",
        schema_path="data/useCases/runtime-config.schema.json",
    )
    print("Runtime config is valid.")