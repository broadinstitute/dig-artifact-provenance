

import json
import yaml
from jsonschema import Draft202012Validator

with open("schema/dapper.yaml") as f:
    schema = yaml.safe_load(f)

with open("provenance.json") as f:
    provenance = json.load(f)

validator = Draft202012Validator(schema)

errors = sorted(
    validator.iter_errors(provenance),
    key=lambda e: list(e.absolute_path)
)

if not errors:
    print("VALID")
else:
    print(f"INVALID: {len(errors)} validation error(s)")

    for error in errors:
        json_path = ".".join(str(x) for x in error.absolute_path)

        print()
        print(f"Path: {json_path or '<root>'}")
        print(f"Error: {error.message}")

        