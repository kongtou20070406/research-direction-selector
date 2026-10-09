"""Read-only, bounded artifact import. Observation never certifies a mechanism."""
from copy import deepcopy
from pathlib import Path

from rds_costs import COST_BINDING_FIELDS, finite, receipt_identity, receipt_issues, sha256, summarize_costs
from rds_verify_types import digest, require
from rds_source_documents import (SourceDocument, MAX_ROWS, BINDING_FIELDS, strict_json,
                                  _finite_tree, _pointer, _parse, _metadata, _extract)

SCHEMA = "rds-artifact-manifest-v1"
REPORT_SCHEMA = "rds-artifact-report-v1"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SOURCES = 64
MAX_FACTS = 256
SELF_SIGNED = {"verified", "pass", "manipulation_verified", "falsifier_triggered",
               "primary_metric_gain", "final_run_authorized", "matched_recipe", "matched_compute"}


def _identity_string(value):
    return isinstance(value, str) and bool(value.strip()) and value.strip().upper() != "UNKNOWN"


class ArtifactFact(dict):
    """In-memory importer origin; serialized dictionaries cannot self-sign it."""
    def __init__(self, record, *, reading_identity=None):
        super().__init__(record)
        self.provenance_status = {"OBSERVED": "ARTIFACT_OBSERVED", "DECLARED": "ARTIFACT_DECLARED",
                                  "DERIVED": "PROGRAM_DERIVED"}.get(record.get("kind"), "UNKNOWN")
        # Importer-owned in-memory identity, like provenance_status. Public
        # physical locators stay unchanged; serialized flags cannot mint a read.
        self.reading_identity = reading_identity


def _read(path, base):
    path = (base / path).resolve()
    path.relative_to(base)
    with path.open("rb") as handle:
        raw = handle.read(MAX_FILE_BYTES + 1)
    require(len(raw) <= MAX_FILE_BYTES, "file exceeds 2 MiB limit")
    return raw


def _fact(fid, value, kind, source, binding, *, reading_identity=None, **extra):
    record = {"id": fid, "value": value, "kind": kind, "source": deepcopy(source),
              "binding": deepcopy(binding), "reliable": kind != "UNKNOWN", **extra}
    return ArtifactFact(record, reading_identity=reading_identity)


def _unknown(record, reason):
    if record["kind"] != "UNKNOWN":
        record["declared_value"] = record["value"]
    record.update(value=None, kind="UNKNOWN", reliable=False, reason=reason)
    record.provenance_status = "UNKNOWN"


def ingest_manifest(path, root=None, receipts=None):
    """Import only named values, keeping missing/conflicting evidence explicit.

    Hashes bind exact bytes. Manifest binding metadata remains a declaration;
    an OBSERVED fact means that value was read, never executor/mechanism proof.
    `receipts` accepts optional records from an append-only project store.
    """
    path = Path(path).resolve()
    base = Path(root).resolve() if root is not None else path.parent
    manifest = strict_json(_read(str(path), base).decode("utf-8-sig"))
    require(isinstance(manifest, dict) and manifest.get("schema") == SCHEMA, "unsupported manifest schema")
    sources, derived = manifest.get("sources", []), manifest.get("derived", [])
    require(isinstance(sources, list) and len(sources) <= MAX_SOURCES, "source limit exceeded")
    require(isinstance(derived, list) and len(derived) <= MAX_FACTS, "derived fact limit exceeded")
    report = {"schema": REPORT_SCHEMA, "status": "IMPORTED", "manifest_sha256": digest(manifest),
              "facts": {}, "source_inventory": [], "conflicts": [], "missing": [],
              "context": {"decision": deepcopy(manifest.get("decision", "choose_next")), "facts": {},
                          "costs": {}, "budget": deepcopy(manifest.get("budget", {}))}}
    require(receipts is None or isinstance(receipts, (list, tuple)), "receipts must be a list")
    receipt_records, by_run, owners, source_ids = list(receipts or []), {}, {}, set()
    file_cache, source_runs, whole_json = {}, {}, {}
    # Lifetime hints only: a failed resolution is still handled in its original
    # source position below. This never admits a source or bypasses its checks.
    last_uses = {}
    for position, spec in enumerate(sources):
        if isinstance(spec, dict) and isinstance(spec.get("path"), str):
            try:
                last_uses[str((base / spec["path"]).resolve())] = position
            except (OSError, ValueError, RuntimeError):
                pass
    for position, spec in enumerate(sources):
        require(isinstance(spec, dict) and isinstance(spec.get("id"), str) and spec["id"], "source needs an id")
        require(spec["id"] not in source_ids, "duplicate source id")
        source_ids.add(spec["id"])
        selectors = spec.get("facts", [])
        require(isinstance(selectors, list) and len(selectors) + len(report["facts"]) <= MAX_FACTS, "fact limit exceeded")
        kind, name = spec.get("kind"), spec.get("path")
        require(kind in {"config", "metric", "log", "receipt"} and isinstance(name, str), "source needs kind and path")
        fmt = spec.get("format", Path(name).suffix.lstrip(".").lower())
        if fmt in ("txt", "log"):
            fmt = "kv"
        require(kind not in {"config", "receipt"} or fmt == "json", "config/receipt must be JSON")
        inventory = {"id": spec["id"], "kind": kind, "path": name, "status": "UNKNOWN"}
        report["source_inventory"].append(inventory)
        binding = deepcopy(spec.get("binding", {}))
        require(isinstance(binding, dict), "source binding must be an object")
        source_runs[spec["id"]] = {binding["run_id"]} if _identity_string(binding.get("run_id")) else set()
        problems, document, actual_sha, single_json_line = [], None, None, False
        cache_key, source_document = None, None
        try:
            cache_key = str((base / name).resolve())
            if cache_key not in file_cache:
                raw = _read(name, base)
                file_cache[cache_key] = (raw, digest(raw), SourceDocument(raw))
            raw, actual_sha, source_document = file_cache[cache_key]
            inventory["sha256"] = actual_sha
            require(sha256(spec.get("expected_sha256")), "expected_sha256 is missing or invalid")
            require(actual_sha == spec["expected_sha256"], "source hash mismatch")
            document = source_document.document(kind, fmt)
            if fmt == "json":
                whole_json[actual_sha] = True
            elif fmt == "jsonl" and len(document) == 1:
                # Only a whole-file JSON value can alias a JSON pointer. Keep
                # genuine multi-record JSONL line identities and public locators.
                if actual_sha not in whole_json:
                    try:
                        strict_json(raw.decode("utf-8-sig"))
                        whole_json[actual_sha] = True
                    except (ValueError, UnicodeError, RecursionError):
                        whole_json[actual_sha] = False
                single_json_line = whole_json[actual_sha]
            observed, collisions = source_document.metadata(kind, fmt)
            if _identity_string(observed.get("run_id")):
                source_runs[spec["id"]].add(observed["run_id"])
            for key in BINDING_FIELDS:
                if key in binding and key in observed and binding[key] != observed[key]:
                    collisions.append(key)
                elif key not in binding and key in observed:
                    binding[key] = deepcopy(observed[key])
            if kind == "config" and binding.get("config_sha256") not in (None, actual_sha):
                collisions.append("config_sha256")
            if collisions:
                report["conflicts"].append({"source_id": spec["id"], "fields": sorted(set(collisions))})
                problems.append("conflicting source binding")
            absent = [k for k in ("run_id",) + COST_BINDING_FIELDS
                      if not (sha256(binding.get(k)) if k.endswith("sha256") else _identity_string(binding.get(k)))]
            if kind == "metric" and (not isinstance(binding.get("metric"), dict) or
                                      not all(_identity_string(binding["metric"].get(k)) for k in ("definition", "reduction"))):
                absent.append("metric.definition/reduction")
            if absent:
                problems.append("missing or invalid binding: " + ", ".join(absent))
            if kind == "receipt":
                problems.extend(receipt_issues(document, {k: binding[k] for k in ("run_id",) + COST_BINDING_FIELDS if k in binding}))
                receipt_records.append(document)
            inventory.update(status="CONFLICT" if collisions else "MISSING" if problems else "READ", binding=deepcopy(binding),
                             binding_status="DECLARED_AND_CHECKED_WHERE_PRESENT")
        except (OSError, ValueError, KeyError, TypeError, UnicodeError, RecursionError) as exc:
            problems.append(str(exc))
            inventory["status"] = "MISSING"
        if problems:
            report["missing"].append({"source_id": spec["id"], "reasons": problems})
        run_id = binding.get("run_id")
        if _identity_string(run_id):
            previous = by_run.setdefault(run_id, {})
            for key in BINDING_FIELDS[1:]:
                if key not in binding:
                    continue
                if key in previous and previous[key][0] != binding[key]:
                    report["conflicts"].append({"run_id": run_id, "fields": [key],
                                                "source_ids": [previous[key][1], spec["id"]]})
                else:
                    previous[key] = (deepcopy(binding[key]), spec["id"])
        for selector in selectors:
            require(isinstance(selector, dict) and isinstance(selector.get("id"), str) and selector["id"], "fact needs id")
            fid = selector["id"]
            value, locator, selected, reason = None, "unresolved", "", "; ".join(problems)
            if document is not None and not problems:
                try:
                    value, locator, selected = source_document.extract(selector, kind, fmt)
                    _finite_tree(value)
                    if selected in SELF_SIGNED or fid in SELF_SIGNED:
                        reason = "self-signed validation cannot be imported as evidence"
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    reason = str(exc)
                    report["missing"].append({"fact_id": fid, "source_id": spec["id"], "reason": reason})
            fact_kind = "UNKNOWN" if reason else "DECLARED" if kind == "config" else "OBSERVED"
            reading_locator = "pointer:" + selector["pointer"] if single_json_line and not reason else locator
            fact = _fact(fid, None if reason else value, fact_kind,
                         {"path": name, "sha256": actual_sha, "locator": locator}, binding,
                         reading_identity=(actual_sha, reading_locator) if fact_kind == "OBSERVED" else None,
                         **({"reason": reason, "declared_value": value} if reason else {}))
            if fid in report["facts"]:
                report["conflicts"].append({"fact_id": fid, "reason": "duplicate fact identity",
                                            "source_ids": [owners[fid], spec["id"]]})
                _unknown(report["facts"][fid], "duplicate fact identity")
            else:
                report["facts"][fid], owners[fid] = fact, spec["id"]
        if source_document is not None and position >= last_uses.get(cache_key, position):
            source_document.clear()
    # Store receipts are also identity evidence; do not ignore a contradictory one.
    for receipt in list(receipts or []):
        if not isinstance(receipt, dict):
            report["missing"].append({"reason": "provided receipt is not an object"})
            continue
        identity, collisions = receipt_identity(receipt)
        run_id = identity.get("run_id")
        for key, (value, source_id) in by_run.get(run_id, {}).items():
            if key in identity and value != identity[key]:
                collisions.append(key)
        if collisions:
            report["conflicts"].append({"run_id": run_id, "fields": sorted(set(collisions)),
                                        "source_ids": [item[1] for item in by_run.get(run_id, {}).values()]})
    # Poison every involved source, never let import order choose the winner.
    bad_sources = set()
    for conflict in report["conflicts"]:
        bad_sources.update(conflict.get("source_ids", []))
        if conflict.get("source_id"):
            bad_sources.add(conflict["source_id"])
    # Costs belong to source/run identities even when no fact is selected.
    bad_runs = {c["run_id"] for c in report["conflicts"] if isinstance(c.get("run_id"), str)}
    bad_runs.update(run_id for source_id in bad_sources for run_id in source_runs.get(source_id, ()))
    for fid, record in report["facts"].items():
        if owners[fid] in bad_sources:
            _unknown(record, "source identity conflict")
    for spec in derived:
        require(isinstance(spec, dict) and isinstance(spec.get("id"), str), "derived fact needs id")
        fid, method, ids = spec["id"], spec.get("method"), spec.get("input_fact_ids")
        require(fid not in report["facts"], "duplicate derived fact id")
        require(method in {"difference", "mean"} and isinstance(ids, list) and 0 < len(ids) <= MAX_FACTS and
                all(isinstance(i, str) and i for i in ids) and len(set(ids)) == len(ids),
                "only bounded difference/mean derivations are supported")
        require(method != "difference" or len(ids) == 2, "difference needs two inputs")
        inputs = [report["facts"].get(i) for i in ids]
        reason = ""
        if any(not r or r["kind"] == "UNKNOWN" or not finite(r["value"]) for r in inputs):
            reason = "derived input missing, conflicted or non-numeric"
        else:
            for key in ("data_sha256", "data_split", "metric"):
                if any(key not in r["binding"] for r in inputs) or any(inputs[0]["binding"][key] != r["binding"][key] for r in inputs):
                    reason = "derived inputs have unknown or incompatible " + key
        value = None if reason else inputs[0]["value"] - inputs[1]["value"] if method == "difference" else sum(r["value"] / len(inputs) for r in inputs)
        if value is not None and not finite(value):
            reason, value = "non-finite derived result", None
        record = _fact(fid, value, "UNKNOWN" if reason else "DERIVED",
                       {"path": path.name, "sha256": report["manifest_sha256"], "locator": "derived:" + fid},
                       {"inputs": [r["binding"] if r else None for r in inputs]},
                       input_fact_ids=ids, method=method, conditions={"same_data_split_metric": not bool(reason),
                       "declared": deepcopy(spec.get("conditions", []))}, **({"reason": reason} if reason else {}))
        report["facts"][fid] = record
        if reason:
            report["missing"].append({"fact_id": fid, "reason": reason})
    report["cost_report"] = summarize_costs(receipt_records)
    report["context"]["facts"] = report["facts"]
    # Explicitly choose one historical resource; never collapse a resource vector.
    cost_bindings = manifest.get("cost_bindings", [])
    require(isinstance(cost_bindings, list) and len(cost_bindings) <= MAX_FACTS, "cost binding limit exceeded")
    for cost in cost_bindings:
        require(isinstance(cost, dict) and all(isinstance(cost.get(k), str) and cost[k] for k in
                ("action_id", "run_id", "resource", "comparison_group")), "cost binding needs action/run/resource/comparison group")
        action_id = cost["action_id"]
        require(action_id not in report["context"]["costs"], "duplicate action cost binding")
        rows = [m for m in report["cost_report"]["measurements"] if m["run_id"] == cost["run_id"] and
                m["resource"] == cost["resource"] and ("attempt_id" not in cost or m["attempt_id"] == cost["attempt_id"])]
        reasons = []
        if len(rows) != 1:
            reasons.append("historical resource missing or ambiguous; select a completed attempt")
        if cost["run_id"] in bad_runs:
            reasons.append("historical run has an import identity conflict")
        if rows and "unit" in cost and rows[0]["unit"] != cost["unit"]:
            reasons.append("historical cost unit mismatch")
        records = [r for r in receipt_records if isinstance(r, dict) and r.get("run_id") == cost["run_id"] and
                   ("attempt_id" not in cost or r.get("attempt_id", r.get("run_id")) == cost["attempt_id"])]
        binding = receipt_identity(records[0])[0] if records else {}
        source = rows[0]["source"] if rows else {"receipt_id": cost["run_id"], "locator": "/resources/" + cost["resource"]}
        value = rows[0]["value"] if rows and not reasons else None
        record = _fact(action_id, value, "UNKNOWN" if reasons else "OBSERVED", source, binding,
                       resource=cost["resource"], unit=rows[0]["unit"] if rows else cost.get("unit"),
                       comparison_group=cost["comparison_group"], historical=True,
                       prediction_status="UNKNOWN", **({"reason": "; ".join(reasons),
                       "declared_value": rows[0]["value"] if len(rows) == 1 else None} if reasons else {}))
        report["context"]["costs"][action_id] = record
        if reasons:
            report["missing"].append({"action_id": action_id, "reasons": reasons})
    report["status"] = "CONFLICT" if report["conflicts"] else "INCOMPLETE" if report["missing"] or any(
        f["kind"] == "UNKNOWN" for f in report["facts"].values()) else "IMPORTED"
    return report
