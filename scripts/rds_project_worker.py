"""One scheduler-owned attempt; never dispatches a new run or a retry."""
import argparse
import traceback

from rds_app import open_store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args()
    store = open_store(args.root)
    try:
        store._execute_claim(args.run_id, args.attempt_id)
    except Exception:
        # Only a validated, known run may choose the log directory.
        run = store.run(args.run_id)
        log = store.artifact_dir / run["id"]
        log.mkdir(parents=True, exist_ok=True)
        with (log / "worker.log").open("a", encoding="utf-8") as out:
            traceback.print_exc(file=out)
        raise


if __name__ == "__main__":
    main()
