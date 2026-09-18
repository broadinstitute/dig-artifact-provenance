#!/usr/bin/env python3
"""Validate a provenance JSON file against a YAML JSON Schema.

Run from the repository root:

    python3 src/python/validation/provenance_validator.py \
      --in-schema notes/schema/provenance/dapper.yaml \
      --in-file-to-validate data/bottom-line-provenance/example.json

Required arguments:

- ``--in-schema``: YAML schema file to validate against
- ``--in-file-to-validate``: JSON provenance file to validate
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a provenance JSON file against a YAML schema.")
    parser.add_argument(
        "--in-schema",
        required=True,
        help="YAML schema file to validate against.",
    )
    parser.add_argument(
        "--in-file-to-validate",
        required=True,
        help="JSON provenance file to validate.",
    )
    return parser.parse_args()


def load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise RuntimeError("Missing required Python package 'PyYAML'.") from exc

    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def validate(schema_path: Path, file_to_validate: Path) -> int:
    try:
        from jsonschema import Draft202012Validator
    except ModuleNotFoundError:
        print("ERROR: Missing required Python package 'jsonschema'.")
        return 2

    try:
        schema = load_yaml(schema_path)
        provenance = load_json(file_to_validate)
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 2

    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(provenance),
        key=lambda error: list(error.absolute_path),
    )

    if not errors:
        print("VALID")
        return 0

    print(f"INVALID: {len(errors)} validation error(s)")

    for error in errors:
        json_path = ".".join(str(part) for part in error.absolute_path)

        print()
        print(f"Path: {json_path or '<root>'}")
        print(f"Error: {error.message}")

    return 1


def main() -> int:
    args = parse_args()
    schema_path = Path(args.in_schema).expanduser().resolve()
    file_to_validate = Path(args.in_file_to_validate).expanduser().resolve()
    return validate(schema_path, file_to_validate)


if __name__ == "__main__":
    raise SystemExit(main())
