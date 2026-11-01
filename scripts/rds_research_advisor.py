"""L3 deterministic, pure-value research proposals; no execution authority.

``advise(packet, observations=(), language='zh')`` consumes the staged F1
DecisionPacket and F2 reference projection without changing either. Optional
DECLARED evidence ``advisor_specs`` maps candidate names to specifications:
inputs/parameters/dependencies/required_verification are lists of evidence
names (explicit empty lists are meaningful). A discriminator declares rival,
observable and predictions {proposal: [scalar, ...], rival: [scalar, ...]}.
Optional cost contains value/resource/unit/sources; it is a declaration only.

Observations are bounded JSON records {field, value, status, scope, sources}.
scope must be the exact packet evidence reference and sources must be registered
in the packet. PASS is a caller-reported scoped check, never audited here and
never scientific truth. FAIL/UNKNOWN and conflicts remain visible. All text,
including source locators and hypothesis claims, is inert data. No file reads,
retrieval, clock, model, ledger, command, resource reservation or verifier lives
in this module. Missing declarations do not become empty requirements.
"""
import json
import math

from rds_decision_packet import DecisionPacket
from rds_packet_graph import project_packet

LAYER = "L3"
SCHEMA = "rds-advisor-proposal-v1"
MAX_OBSERVATIONS = 128
MAX_OBSERVATION_BYTES = 262144
REQUIREMENTS = ("inputs", "parameters", "dependencies", "required_verification")
KINDS = ("INPUT", "PARAMETER", "DEPENDENCY", "VERIFICATION")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value, limit=512):
    return type(value) is str and bool(value.strip()) and len(value) <= limit


def _scalar(value):
    return (type(value) is bool or type(value) is str and len(value) <= 4096
            or type(value) is int and value.bit_length() <= 1024
            or type(value) is float and math.isfinite(value))


def _domain(value):
    return "boolean" if type(value) is bool else "string" if type(value) is str else "number"


def _same(left, right):
    return _domain(left) == _domain(right) and left == right


def _names(value, message, limit=512):
    _require(type(value) is list and len(value) <= 64
             and all(_text(v, limit) for v in value) and len(set(value)) == len(value), message)
    return sorted(value)


def _specifications(evidence, candidates, registered):
    field = evidence.get("advisor_specs")
    if field is None or field.kind == "UNKNOWN":
        return {}
    _require(field.kind == "DECLARED", "advisor_specs must be DECLARED")
    specs = field.value
    _require(type(specs) is dict and set(specs) <= set(candidates), "unknown advisor candidate")
    for name, spec in specs.items():
        _require(type(spec) is dict and set(spec) <= {*REQUIREMENTS, "discriminator", "cost"},
                 "invalid advisor specification: " + name)
        for key in REQUIREMENTS:
            if key in spec:
                _names(spec[key], "invalid requirement list: " + key)
        if "cost" in spec:
            cost = spec["cost"]
            _require(type(cost) is dict and set(cost) == {"value", "resource", "unit", "sources"},
                     "cost needs value/resource/unit/sources")
            _require(_scalar(cost["value"]) and type(cost["value"]) in (int, float)
                     and cost["value"] >= 0 and _text(cost["resource"], 128)
                     and _text(cost["unit"], 128), "invalid declared cost")
            refs = _names(cost["sources"], "invalid cost sources", limit=1024)
            _require(bool(refs) and set(refs) <= registered, "cost needs registered sources")
        if "discriminator" in spec:
            disc = spec["discriminator"]
            _require(type(disc) is dict and set(disc) == {"rival", "observable", "predictions"}
                     and _text(disc["rival"]) and _text(disc["observable"])
                     and disc["rival"] != name, "invalid discriminator references")
            predictions = disc["predictions"]
            _require(type(predictions) is dict and set(predictions) == {"proposal", "rival"},
                     "discriminator needs two predictions")
            values = []
            for rows in predictions.values():
                _require(type(rows) is list and 1 <= len(rows) <= 16 and all(_scalar(v) for v in rows),
                         "predictions need bounded concrete scalars")
                _require(not any(_same(v, previous) for i, v in enumerate(rows) for previous in rows[:i]),
                         "duplicate prediction")
                values.extend(rows)
            _require(len({_domain(v) for v in values}) == 1, "prediction domains differ")
    return specs


def _observations(rows, evidence, registered, ref):
    _require(type(rows) in (list, tuple) and len(rows) <= MAX_OBSERVATIONS,
             "observation count limit")
    result = {}
    total = 0
    for row in rows:
        _require(type(row) is dict and set(row) == {"field", "value", "status", "scope", "sources"},
                 "invalid observation record")
        name = row["field"]
        _require(_text(name) and name in evidence and _scalar(row["value"]),
                 "observation needs existing field and concrete scalar")
        _require(type(row["status"]) is str and row["status"] in {"PASS", "FAIL", "UNKNOWN"}
                 and type(row["scope"]) is str and row["scope"] == ref("evidence", name),
                 "observation scope/status mismatch")
        sources = _names(row["sources"], "invalid observation sources", limit=1024)
        _require(bool(sources) and set(sources) <= registered, "observation needs registered sources")
        total += len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        _require(total <= MAX_OBSERVATION_BYTES, "observation byte limit")
        result.setdefault(name, []).append({**row, "sources": sources})
    for name in result:
        result[name].sort(key=lambda r: json.dumps(r, sort_keys=True, ensure_ascii=False))
    return result


def advise(packet, observations=(), *, language="zh", max_suggestions=3):
    """Return 1-3 source-referenced PROPOSED suggestions and at most two questions.

    The function only proposes. Requirement presence and INPUT_REPORTED checks
    are preflight data, not kernel admission or verification certificates.
    Counterexamples refer to declared finite predictions in this packet's scope.
    """
    if type(packet) is not DecisionPacket:
        raise TypeError("expected DecisionPacket")
    _require(language in ("zh", "en"), "language must be zh or en")
    _require(type(max_suggestions) is int and 1 <= max_suggestions <= 3,
             "max_suggestions must be 1-3")
    graph = project_packet(packet)
    packet_id = graph.packet_id
    evidence = {f.name: f for f in packet.evidence}
    candidates = {f.name: f for f in packet.candidates}
    meta = {f.name: f.value for f in packet.provenance}
    registered = set(meta["sources"])

    def ref(block, name):
        return packet_id + "#/" + block + "/" + name.replace("~", "~0").replace("/", "~1")

    def refs(block, field):
        return sorted({ref(block, field.name), *field.sources})

    def wording(en, zh):
        return zh if language == "zh" else en

    def quoted(value):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)

    specs = _specifications(evidence, candidates, registered)
    observed = _observations(observations, evidence, registered, ref)
    preflight = []

    def add(kind, name, status, sources, candidate=None):
        row = {"kind": kind, "name": name, "status": status,
               "candidate": candidate, "sources": sorted(set(sources))}
        if row not in preflight:
            preflight.append(row)
        return row

    for block, kind in (("identity", "PROJECT"), ("goal", "GOAL")):
        field = getattr(packet, block)[0]
        if field.kind == "UNKNOWN" or not field.value or not field.value.strip():
            add(kind, field.name, "MISSING", refs(block, field))
    if meta["status"] == "PARTIAL":
        add("IMPORT", "packet", "PARTIAL", [ref("provenance", "status"), ref("provenance", "issues")])
    if not candidates:
        add("HYPOTHESIS", "candidate", "MISSING", [ref("goal", "statement")])

    def checks(name):
        rows = observed.get(name, [])
        passed = [r for r in rows if r["status"] == "PASS"]
        conflict = (any(r["status"] == "FAIL" for r in rows) and bool(passed)
                    or any(not _same(r["value"], passed[0]["value"]) for r in passed[1:]))
        field = evidence.get(name)
        if passed and field and field.kind in ("DECLARED", "OBSERVED") and field.value is not None:
            conflict = conflict or not _scalar(field.value) or not _same(field.value, passed[0]["value"])
        return rows, passed, conflict

    def requirement(name, kind, candidate, sources):
        field = evidence.get(name)
        all_refs = list(sources)
        if field:
            all_refs.extend(refs("evidence", field))
        rows, passed, conflict = checks(name)
        all_refs.extend(s for row in rows for s in row["sources"])
        if conflict:
            status = "CONFLICT"
        elif any(r["status"] == "FAIL" for r in rows):
            status = "VERIFICATION_FAILED"
        elif passed:
            status = "VERIFIED_INPUT"
        elif not field:
            status = "MISSING"
        elif kind == "VERIFICATION":
            status = "UNVERIFIED"
        elif field.kind == "UNKNOWN" or field.value is None or type(field.value) is str and not field.value.strip():
            status = "MISSING"
        elif field.kind == "INFERRED":
            status = "UNVERIFIED"
        else:
            status = "PRESENT"
        return add(kind, name, status, all_refs, candidate)

    candidate_sources = {}
    # Complete all preflight rows before comparing a candidate with its rival.
    for name, field in candidates.items():
        claim = field.value
        sources = refs("candidates", field)
        spec = specs.get(name, {})
        if "advisor_specs" in evidence:
            sources = sorted(set(sources + refs("evidence", evidence["advisor_specs"])))
        candidate_sources[name] = sources
        if claim["goal"] is None:
            add("GOAL", "candidate_goal_link", "MISSING", sources, name)
        for key, kind in zip(REQUIREMENTS, KINDS):
            if key not in spec:
                add(kind, key, "UNDECLARED", sources, name)
            names = set(spec.get(key, []))
            if key == "dependencies":
                names.update(claim["premises"])
            for needed in sorted(names):
                requirement(needed, kind, name, sources)
        disc = spec.get("discriminator")
        if not disc:
            add("DISCRIMINATOR", "rival/observable/predictions", "UNDECLARED", sources, name)
            continue
        rival, measure = candidates.get(disc["rival"]), evidence.get(disc["observable"])
        disc_sources = list(sources)
        if rival:
            disc_sources.extend(refs("candidates", rival))
        if measure:
            disc_sources.extend(refs("evidence", measure))
            requirement(measure.name, "VERIFICATION", name, disc_sources)
            _, passed, _ = checks(measure.name)
            if passed and any(_domain(r["value"]) != _domain(disc["predictions"]["proposal"][0]) for r in passed):
                add("DISCRIMINATOR", "observable_domain", "UNVERIFIED", disc_sources, name)
        if not rival or not measure:
            add("DISCRIMINATOR", "rival/observable", "MISSING", disc_sources, name)
        if any(_same(a, b) for a in disc["predictions"]["proposal"] for b in disc["predictions"]["rival"]):
            add("DISCRIMINATOR", "overlapping_predictions", "UNVERIFIED", disc_sources, name)

    suggestions = []
    # Candidate order is canonical, not a scientific ranking or cost optimization.
    for name, field in candidates.items():
        claim, spec = field.value, specs.get(name, {})
        sources = candidate_sources[name]
        explanations = [{"candidate": name, "claim": claim["claim"],
                         "actor": claim["actor"], "status": "PROPOSED", "sources": refs("candidates", field)},
                        {"candidate": None, "claim": None, "actor": "unknown", "status": "UNKNOWN",
                         "sources": sources}]
        disc = spec.get("discriminator")
        comparison = (None, name, disc["rival"]) if disc else (None, name)
        status, judgement, observable, outcomes = "UNKNOWN", "UNKNOWN", None, []
        if disc:
            rival = candidates.get(disc["rival"])
            measure = evidence.get(disc["observable"])
            if rival:
                rival_value = rival.value
                explanations[1] = {"candidate": rival.name, "claim": rival_value["claim"],
                                   "actor": rival_value["actor"], "status": "PROPOSED",
                                   "sources": refs("candidates", rival)}
                sources = sorted(set(sources + refs("candidates", rival)))
            if measure:
                observable = ref("evidence", measure.name)
                sources = sorted(set(sources + refs("evidence", measure)))
            if rival and measure:
                predictions = disc["predictions"]
                overlap = any(_same(a, b) for a in predictions["proposal"] for b in predictions["rival"])
                status = "OVERLAPPING" if overlap else "DECLARED_DISTINCT"
                if not overlap:
                    for side in ("proposal", "rival"):
                        values = sorted(predictions[side], key=quoted)
                        consequence = wording(
                            "would match this prediction only; retain both hypotheses as PROPOSED"
                            if side == "proposal" else "would counter the proposal prediction; revise its scope or assumptions",
                            "仅匹配这项预测；两个假设仍为待验证提案" if side == "proposal"
                            else "将反对当前提案的预测；修订其范围或假设")
                        outcomes.append({"values": values, "favours_prediction": side,
                                         "changes_judgement": consequence})
                    rows, passed, conflict = checks(measure.name)
                    sources = sorted(set(sources + [s for row in rows for s in row["sources"]]))
                    blocked = any(r["status"] not in {"PRESENT", "VERIFIED_INPUT"}
                                  for r in preflight if r["candidate"] in comparison)
                    if conflict:
                        judgement = "CONFLICTING_OBSERVATIONS"
                    elif passed and not blocked:
                        actual = passed[0]["value"]
                        if any(_same(actual, v) for v in predictions["proposal"]):
                            judgement = "PREDICTION_MATCH_REPORTED"
                        else:
                            judgement = "COUNTEREXAMPLE_REPORTED"
        blocked = any(r["status"] not in {"PRESENT", "VERIFIED_INPUT"}
                      for r in preflight if r["candidate"] in comparison)
        action = wording("Clarify missing declarations and scoped verification before comparing predictions."
                         if blocked else "Review the declared predictions against the scoped measurement.",
                         "先补齐声明和有范围的验证，再比较预测。" if blocked else "对照有范围的观测，审阅已声明的预测。")
        summary = wording("For declared hypothesis " + quoted(claim["claim"]) + ": " + action,
                          "针对声明的假设" + quoted(claim["claim"]) + "：" + action)
        if disc and explanations[1]["candidate"] is not None and observable:
            summary += wording(
                " Compare with " + quoted(explanations[1]["claim"]) + " using observable "
                + quoted(disc["observable"]) + ": proposal predicts " + quoted(disc["predictions"]["proposal"])
                + ", rival predicts " + quoted(disc["predictions"]["rival"]) + ".",
                "竞争解释为" + quoted(explanations[1]["claim"]) + "；观测量为" + quoted(disc["observable"])
                + "，提案预测" + quoted(disc["predictions"]["proposal"])
                + "，竞争解释预测" + quoted(disc["predictions"]["rival"]) + "。")
        gaps = [r for r in preflight if r["candidate"] in comparison
                and r["status"] not in {"PRESENT", "VERIFIED_INPUT"}]
        if gaps:
            labels = sorted({r["kind"] + ":" + r["name"] for r in gaps})
            summary += wording(" Resolve: ", "需补齐：") + ", ".join(quoted(v) for v in labels[:6]) + "."
        changes = wording("A verified value matching only one declared prediction changes its relative plausibility; "
                          "a value matching neither calls for revising both. Missing or conflicting checks keep judgement UNKNOWN.",
                          "有范围的验证值若只匹配一方预测，会改变两者的相对可信度；若两者都不匹配，应修订两方。缺失或冲突的验证使判断保持未知。") if outcomes else wording(
                              "A declared rival, observable and distinct predictions would permit a comparison; until then judgement stays UNKNOWN.",
                              "补充竞争解释、观测量和不同预测后，才可比较；在此之前判断保持未知。")
        declared_cost = spec.get("cost")
        cost = ({"status": "DECLARED", **declared_cost, "sources": sorted(declared_cost["sources"])}
                if declared_cost else {"status": "UNKNOWN"})
        summary += wording(" Cost is UNKNOWN.", "成本未知。") if not declared_cost else wording(
            " Declared cost: " + quoted(cost["value"]) + " " + quoted(cost["unit"])
            + " of " + quoted(cost["resource"]) + "; this is an input declaration.",
            "声明成本：" + quoted(cost["value"]) + " " + quoted(cost["unit"])
            + "，资源" + quoted(cost["resource"]) + "；这只是输入中的声明。")
        suggestions.append({"id": "suggestion:" + str(len(suggestions) + 1), "candidate": name,
                            "status": "PROPOSED", "summary": summary, "sources": sources,
                            "preflight_status": "INCOMPLETE" if blocked else "COMPLETE",
                            "discrimination": {"status": status, "explanations": explanations,
                                               "observable": observable, "outcomes": outcomes,
                                               "judgement": judgement},
                            "cost": cost, "result_changes": changes, "execution_permission": False})

    if not suggestions:
        sources = [ref("goal", "statement")]
        suggestions.append({"id": "suggestion:1", "candidate": None, "status": "PROPOSED",
                            "summary": wording("State the research goal and two competing explanations before choosing a test.",
                                               "先说明研究目标和两个竞争解释，再选择检验。"),
                            "sources": sources, "preflight_status": "INCOMPLETE",
                            "discrimination": {"status": "UNKNOWN", "explanations": [
                                {"candidate": None, "claim": None, "actor": "unknown", "status": "UNKNOWN",
                                 "sources": sources} for _ in range(2)],
                                "observable": None, "outcomes": [], "judgement": "UNKNOWN"},
                            "cost": {"status": "UNKNOWN"},
                            "result_changes": wording("A goal, two explanations and differing observable predictions would make a comparison possible.",
                                                      "明确目标、两个解释及其不同的可观测预测后，才可进行比较。"),
                            "execution_permission": False})

    questions = []
    missing = [r for r in preflight if r["status"] not in {"PRESENT", "VERIFIED_INPUT"}]
    declaration_gaps = [r for r in missing if r["kind"] != "VERIFICATION"]
    verification_gaps = [r for r in missing if r["kind"] == "VERIFICATION"]
    for gaps, en, zh in ((declaration_gaps, "What are the missing declarations or bindings for ", "请补充这些缺失的声明或绑定："),
                         (verification_gaps, "What scoped verification or original evidence is available for ", "这些项目有哪些有范围的验证或原始证据：")):
        if gaps:
            labels = sorted({r["kind"] + ":" + r["name"] for r in gaps})
            questions.append(wording(en, zh) + ", ".join(quoted(v) for v in labels[:8]) + "?")
    preflight.sort(key=lambda r: (r["candidate"] or "", r["kind"], r["name"], r["status"]))
    return {"schema": SCHEMA, "layer": LAYER, "packet_id": packet_id, "language": language,
            "status": "PROPOSED", "scientific_status": "UNKNOWN", "execution_permission": False,
            "verification_basis": "INPUT_REPORTED", "preflight": preflight,
            "suggestions": suggestions[:max_suggestions], "questions": questions,
            "omitted_candidates": max(0, len(suggestions) - max_suggestions),
            "limitations": [wording("Scoped input checks are reported, not audited; proposals grant no execution permission.",
                                    "输入中的有范围检查仅为报告，本模块未审计；建议不授予执行权限。"),
                            wording("Declared finite predictions do not establish causality, scientific truth or measured benefit.",
                                    "声明的有限预测不能确立因果、科学真值或已测得的收益。")]}
