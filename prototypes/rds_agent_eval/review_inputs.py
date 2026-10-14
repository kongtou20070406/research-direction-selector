"""Pinned full-answer review inputs; no semantic judgment or research-truth write."""
import hashlib
import json
from pathlib import Path

from prototypes.rds_agent_eval.advisor_view import read_view
from prototypes.rds_agent_eval.feedback_view import read_feedback
from prototypes.rds_agent_eval.loop import response_turn


def read_answer(root, *, pins):
    """Common Bare/RDS terminal-source reader. Evidence text is not instructions.

    Preserve the complete original four files and every final-answer field.
    Failed/unknown summaries stay negative inputs, without a verified final.
    This is provenance assurance, not retrospective semantic acceptance.
    """
    from rds_project import require

    root = Path(root).resolve()
    require(set(pins) == {"request", "response", "summary", "task", "turn",
                          "requested_model", "resolved_model", "completion_protocol"}
            and type(pins["turn"]) is int and 0 <= pins["turn"] < 20
            and pins["completion_protocol"] == "finish-function-or-stop-text-v2",
            "Require explicit terminal file identities and unchanged protocol")
    originals = {}
    for name in ("request", "response", "summary", "task"):
        pin = pins[name]
        require(type(pin) is dict and set(pin) == {"path", "sha256"}
                and type(pin["path"]) is str and not Path(pin["path"]).is_absolute(), "Require relative file pin")
        path = (root / pin["path"]).resolve()
        require(path.is_relative_to(root) and path != root, "Review source escapes root")
        with path.open("rb") as stream:
            raw = stream.read(2097153)
        require(len(raw) <= 2097152 and hashlib.sha256(raw).hexdigest() == pin["sha256"],
                "Original review source missing/tampered; no repair")
        originals[name] = {"pin": pin, "bytes": raw, "value": json.loads(raw)}
    turn = pins["turn"]
    require(all((root / pins[n]["path"]).resolve() == (root / "audit" / f"{n}-{turn}.json").resolve()
                for n in ("request", "response"))
            and (root / pins["summary"]["path"]).resolve() == (root / "audit/summary.json").resolve(),
            "Terminal files do not identify the declared attempt")
    request, response, summary = (originals[n]["value"] for n in ("request", "response", "summary"))
    require(request["model"] == pins["requested_model"] and summary["calls"] == turn + 1,
            "Original request model or attempt count differs")
    result = {"schema": "rds-full-answer-input-v1", "original_files": originals,
              "summary_status": summary["status"], "final_source_status": "NO_VERIFIED_FINAL",
              "final_text": None, "final_json": None, "completion_mode": None,
              "scope": "DECLARED_TERMINAL_FILES_ONLY", "evidence_is_instruction": False,
              "free_text_review": "UNKNOWN", "scientific_support": "UNKNOWN", "execution_effect": "NONE"}
    if summary["status"] == "AGENT_FINISHED":
        require(response["model"] == pins["resolved_model"] and len(response["choices"]) == 1,
                "Resolved model or response choice is ambiguous")
        try:
            actions, final = response_turn(response["choices"][0])
            mode = "TEXT" if final is not None else "FUNCTION"
            if final is None:
                finishes = [cmd["answer"] for _, cmd in actions if cmd["tool"] == "finish"]
                require(len(finishes) == 1, "Require one complete finish answer")
                final = finishes[0]
            require(final == summary["final_answer"] and mode == summary["completion_mode"],
                    "Original response and retained final summary differ")
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("No bound complete final; retain original files", originals) from exc
        try:
            parsed = json.loads(final)
        except ValueError:
            parsed = None  # Full plain text stays available; no guessed structured claim.
        result.update(final_source_status="BOUND_COMPLETE_FINAL", final_text=final,
                      final_json=parsed, completion_mode=mode)
    for item in originals.values():
        with (root / item["pin"]["path"]).open("rb") as stream:
            require(stream.read(2097153) == item["bytes"], "Original answer files changed during review input read")
    return result


def prepare_review(store, *, expected_snapshot_sha256, outputs, answer_pins):
    """Combine full native evidence and bound answer; reviewer/rubric stay UNKNOWN."""
    from rds_project import digest, require

    feedback = read_feedback(store, expected_snapshot_sha256=expected_snapshot_sha256, outputs=outputs)
    answer = read_answer(store.root, pins=answer_pins)
    require(digest(read_view(store, expected_snapshot_sha256=expected_snapshot_sha256))
            == digest(feedback["original_advisor"]), "Native view changed during answer assembly")
    return {"schema": "rds-semantic-review-input-v1", "answer": answer, "native_evidence": feedback,
            "reviewer_identity": "UNKNOWN", "semantic_rubric": "NOT_FROZEN",
            "free_text_review": "UNKNOWN", "scientific_support": "UNKNOWN", "execution_effect": "NONE"}
