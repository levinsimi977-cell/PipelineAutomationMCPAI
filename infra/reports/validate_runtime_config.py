from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema
from jsonschema import ValidationError


def _read_json_file(file_path: Path) -> dict:
    """
    Read and parse a JSON file into a Python dictionary.

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If file content is not valid JSON.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    return json.loads(file_path.read_text(encoding="utf-8"))


def validate_json_file(payload_path: str, schema_path: str) -> tuple[bool, str]:
    """
    Validate a payload JSON file against a schema JSON file.

    Returns:
        (is_valid, message):
            is_valid = True if valid, False otherwise.
            message = human-readable explanation.
    """
    payload_file = Path(payload_path)
    schema_file = Path(schema_path)

    try:
        payload = _read_json_file(payload_file)
        schema = _read_json_file(schema_file)

        jsonschema.validate(instance=payload, schema=schema)
        return True, "Runtime config is valid."

    except FileNotFoundError as exc:
        return False, f"[FILE ERROR] {exc}"

    except json.JSONDecodeError as exc:
        return (
            False,
            f"[JSON PARSE ERROR] Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
        )

    except ValidationError as exc:
        # Show the failing path in payload for fast debugging
        path = ".".join(str(part) for part in exc.path) or "<root>"
        return (
            False,
            f"[SCHEMA VALIDATION ERROR] Path: {path} | Message: {exc.message}",
        )

    except Exception as exc:  # Fallback to avoid crashing with raw traceback
        return False, f"[UNEXPECTED ERROR] {exc}"


if __name__ == "__main__":
    ok, message = validate_json_file(
        payload_path="data/useCases/runtime-config.example.json",
        schema_path="data/useCases/runtime-config.schema.json",
    )

    print(message)
    sys.exit(0 if ok else 1)