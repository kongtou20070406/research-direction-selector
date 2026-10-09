"""Trusted deterministic synthetic evaluator; not a research accuracy benchmark."""
import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--factor", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    row = json.loads(Path(args.data).read_text(encoding="utf-8")).get(args.factor)
    row = row if isinstance(row, dict) else {"metric": row, "exit_code": 0}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"loss": row.get("metric")}, allow_nan=False) + "\n", encoding="utf-8")
    return row.get("exit_code", 0)


if __name__ == "__main__":
    sys.exit(main())
