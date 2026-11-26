"""Semantic layer for paper_gate.py (warnings only; embeddings are fuzzy, so nothing is auto-fixed).

Uses an OpenAI-compatible /v1/embeddings endpoint. Default: the local EmbeddingGemma bridge
(`embeddinggemma2`, 768-d) at http://127.0.0.1:18766 inside WSL Ubuntu. Vectors are cached in
PAPER_DIR/.gate_cache/ keyed by text hash, so reruns only embed edited sentences.

Checks
  restate  near-duplicate sentences across different paragraphs of the core body sections
           (e.g. a Related Work sentence that repeats the Experimental Settings)
  terms    unregistered phrases that sit close to a registered canonical term or variant;
           confirm real synonyms by adding them to paper_gate.json "terms", which the
           deterministic fixer then enforces
  support  abstract/conclusion claims without a close passage or matching experimental conditions
"""

import hashlib
import json
import re
import tempfile
import urllib.request
from pathlib import Path

import numpy as np

STOP = set("""a an the of for to in on at by with and or but from into over under via as is are was were be been
this that these those its it their our we they which while than then also only one two each all any both such
not no per more most less same other""".split())


def _pad(s: str, n: int) -> str:
    return (s + " " * n)[:n]


def _clean(text: str, masked=None) -> str:
    """Readable, length-preserving text: inline math keeps its numbers/letters, everything else LaTeX is blanked."""
    t = re.sub(r"(?<!\\)%[^\n]*", lambda m: " " * len(m.group(0)), text)
    t = re.sub(r"\\(?:section|subsection|subsubsection)\*?(?:\[[^\]]*\])?\{[^{}\n]*\}", lambda m: " " * len(m.group(0)), t)
    t = re.sub(r"\\begin\{(equation|align|gather|multline|eqnarray|algorithmic|tabular|verbatim)\*?\}.*?\\end\{\1\*?\}",
               lambda m: " " * len(m.group(0)), t, flags=re.S)
    t = re.sub(r"\\\[.*?\\\]|\$\$.*?\$\$", lambda m: " " * len(m.group(0)), t, flags=re.S)
    t = re.sub(r"(?<!\\)\$(.*?)(?<!\\)\$",
               lambda m: _pad(re.sub(r"\s+", " ", re.sub(r"\\[A-Za-z]+|[{}^_\\]", " ", m.group(1))).strip(), len(m.group(0))), t, flags=re.S)
    t = re.sub(r"\\(?:label|ref|eqref|cref|Cref|autoref|cite[pt]?|citeyear|citeauthor|includegraphics|url|href|input)\*?(?:\[[^\]]*\])?\{[^}]*\}",
               lambda m: " " * len(m.group(0)), t)
    t = re.sub(r"\\[A-Za-z]+\*?|[{}~\\]", lambda m: " " * len(m.group(0)), t)
    return t


def _sentences(src, masked):
    """Yield (sentence, offset, paragraph_id, in_caption) from one source file."""
    raw = src.text
    cap_spans = []
    for m in re.finditer(r"\\caption\{", raw):
        depth, i = 1, m.end()
        while i < len(raw) and depth:
            depth += {"{": 1, "}": -1}.get(raw[i], 0)
            i += 1
        cap_spans.append((m.start(), i))
    clean = _clean(raw, masked)
    para_breaks = [m.start() for m in re.finditer(r"\n\s*\n", raw)]
    protect = re.sub(r"\b(e\.g|i\.e|vs|Eq|Eqs|Fig|Sec|App|et al|cf|resp)\.", lambda m: m.group(0).replace(".", "\x00"), clean)
    protect = re.sub(r"(?<=\d)\.(?=\d)", "\x00", protect)
    start = 0
    for m in re.finditer(r"(?<=[.!?])\s+(?=[A-Z(])|\n\s*\n", protect):
        seg = clean[start:m.start()]
        text = re.sub(r"\s+", " ", seg).strip()
        if len(re.findall(r"[A-Za-z]{2,}", text)) >= 6:
            off = start + (len(seg) - len(seg.lstrip()))
            pid = sum(1 for b in para_breaks if b < off)
            yield text, off, pid, any(a <= off < b for a, b in cap_spans)
        start = m.end()
    tail = re.sub(r"\s+", " ", clean[start:]).strip()
    if len(re.findall(r"[A-Za-z]{2,}", tail)) >= 6:
        off = start + len(clean[start:]) - len(clean[start:].lstrip())
        yield tail, off, sum(1 for b in para_breaks if b < off), any(a <= off < b for a, b in cap_spans)


class Embedder:
    def __init__(self, paper: Path, scfg: dict):
        self.url = scfg.get("url", "http://127.0.0.1:18766/v1/embeddings")
        self.model = scfg.get("model", "embeddinggemma2")
        self.batch = int(scfg.get("batch", 32))
        self.dir = paper / ".gate_cache"
        self.path = self.dir / f"emb_{re.sub(r'[^A-Za-z0-9]+', '_', self.model)}.npz"
        self.cache = {}
        if self.path.exists():
            with np.load(self.path, allow_pickle=False) as z:
                self.cache = dict(zip(z["keys"].tolist(), z["vecs"].astype(np.float32)))
        self.dirty = False

    @staticmethod
    def key(t: str) -> str:
        return hashlib.sha1(t.encode("utf-8")).hexdigest()

    def __call__(self, texts):
        need = [t for t in dict.fromkeys(texts) if self.key(t) not in self.cache]
        for i in range(0, len(need), self.batch):
            chunk = need[i:i + self.batch]
            req = urllib.request.Request(self.url, data=json.dumps({"model": self.model, "input": chunk}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as response:
                data = json.load(response).get("data")
            if not isinstance(data, list) or len(data) != len(chunk):
                raise ValueError("embedding response has the wrong batch size")
            for index, (t, d) in enumerate(zip(chunk, sorted(data, key=lambda d: d.get("index", 0)))):
                v = np.asarray(d.get("embedding", []), dtype=np.float32)
                norm = np.linalg.norm(v)
                if d.get("index", index) != index or v.ndim != 1 or not np.isfinite(norm) or norm <= 0:
                    raise ValueError("invalid embedding vector or index")
                self.cache[self.key(t)] = v / norm
                self.dirty = True
        return np.stack([self.cache[self.key(t)] for t in texts]) if texts else np.zeros((0, 1), np.float32)

    def save(self):
        if self.dirty:
            self.dir.mkdir(exist_ok=True)
            keys = np.array(list(self.cache), dtype="U40")
            with tempfile.NamedTemporaryFile(dir=self.dir, suffix=".npz", delete=False) as temp:
                tmp_path = Path(temp.name)
            try:
                np.savez_compressed(tmp_path, keys=keys, vecs=np.stack(list(self.cache.values())).astype(np.float16))
                tmp_path.replace(self.path)
            finally:
                tmp_path.unlink(missing_ok=True)


WORD = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")


def _ngrams(text: str, nmin=2, nmax=4):
    for segment in re.split(r"[^A-Za-z0-9\s-]+", text):
        words = WORD.findall(segment)
        for n in range(nmin, nmax + 1):
            for i in range(len(words) - n + 1):
                gram = words[i:i+n]
                if gram[0].lower() in STOP or gram[-1].lower() in STOP or all(len(w) < 4 for w in gram):
                    continue
                if any(w.isdecimal() or w.lower() in {"and", "or", "but"} for w in gram):
                    continue
                yield " ".join(gram)


def _phrases(names):
    choices = [r"\s+".join(map(re.escape, n.split())) for n in sorted(set(names), key=len, reverse=True) if n.strip()]
    return re.compile(r"(?<![\w-])(?:" + "|".join(choices) + r")(?![\w-])", re.I) if choices else None


def _metric_groups(text, groups):
    return {name for name, pattern in groups.items() if pattern.search(text)}


def _restatements(pool, scfg, emb, rep, line_of):
    if not pool:
        return
    groups = {name: _phrases(terms) for name, terms in scfg.get("restate_metric_groups", {}).items() if terms}
    metrics = [_metric_groups(x[0], groups) for x in pool]
    vectors = emb([x[0] for x in pool])
    scores = vectors @ vectors.T
    threshold = float(scfg.get("restate_threshold", 0.86))
    hits = []
    for i, a in enumerate(pool):
        for j in range(i + 1, len(pool)):
            b = pool[j]
            if scores[i,j] < threshold or (a[1] is b[1] and a[3] == b[3]):
                continue
            different = bool(metrics[i] and metrics[j] and metrics[i].isdisjoint(metrics[j]))
            hits.append((different, -float(scores[i,j]), i, j))
    for different, neg_score, i, j in sorted(hits)[:int(scfg.get("max_reports", 8))]:
        a,b = pool[i],pool[j]
        suffix = " [different metrics]" if different else ""
        rep.warnings.append(f'restate  {-neg_score:.2f}  {a[1].path.name}:{line_of(a[1].text,a[2])} "{a[0][:90]}"  ~  '
                            f'{b[1].path.name}:{line_of(b[1].text,b[2])} "{b[0][:90]}"{suffix}')


CLAUSE_BREAK = re.compile(r";\s*|\s+\b(?:while|whereas|but|(?:that|which|and)\s+also)\b\s+", re.I)
PLANNED = re.compile(r"\b(?:future work|we (?:plan|intend) to|yet to be|not yet (?:tested|evaluated)|remains? (?:pending|the final experimental step))\b", re.I)
RESULT_VERB = re.compile(r"\b(?:(?:improv|reduc|achiev|evaluat|measur|compar)(?:e|es|ed|ing)|"
                         r"(?:reach|outperform|save|lead|cut|score|yield|beat|gain|report)(?:s|es|ed|ing)?|better|higher|lower)\b", re.I)
VALUE_UNIT = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:[,;]\s*)?(%|(?:dB|ms|GMACs?|GFLOPs?|fps)\b)", re.I)
DECIMAL = re.compile(r"(?<![\w.])\d+\.\d+(?![\w.])")


def _condition_evidence(heads, sents, paper, setup, emb, rep, line_of):
    """Locate measured passages for explicit empirical claims, within the paper's named conditions."""
    patterns = {(axis, label): _phrases([label, *aliases])
                for axis in ("tasks", "datasets", "metrics") for label, aliases in setup.get(axis, {}).items()}
    if not patterns:
        return
    implied = setup.get("dataset_tasks", {})  # e.g. GoPro -> deblurring: naming the benchmark names its task
    datasets = {label: pat for (axis, label), pat in patterns.items() if axis == "datasets"}

    def imply(labels):
        labels |= {("tasks", implied[lab]) for axis, lab in list(labels) if axis == "datasets" and lab in implied}
        return labels

    def conditions(text):
        return imply({key for key, pat in patterns.items() if pat.search(text)})

    def split_by_dataset(clause):
        """'31.48 dB on GoPro and 26.79 dB on O-HAZE' -> one claim per benchmark."""
        if len({lab for lab, pat in datasets.items() if pat.search(clause)}) < 2:
            return [clause]
        out, cur = [], ""
        for part in re.split(r"(?:,\s*|\s+)and\s+|;\s*", clause):
            has = any(pat.search(part) for pat in datasets.values())
            if cur and has and any(pat.search(cur) for pat in datasets.values()):
                out.append(cur)
                cur = part
            else:
                cur = f"{cur} and {part}" if cur else part
        return out + [cur]

    def quantities(text):
        return {(float(value), unit.casefold()) for value, unit in VALUE_UNIT.findall(text)}

    files = set(setup.get("files", []))
    passages = []
    paragraph_scope = {}
    for text, src, pos, paragraph, _ in sents:
        if src.path.relative_to(paper).as_posix() not in files:
            continue
        scope = paragraph_scope.setdefault((src.path, paragraph), {})
        if PLANNED.search(text):
            scope.clear()
            continue
        # Carry a named experimental setting down its own paragraph. Switching studies resets it.
        # Numeric measurements and metric names must come from the measured passage itself.
        para_start = src.text.rfind("\n\n", 0, pos)
        para_start = para_start + 2 if para_start >= 0 else 0
        title = re.match(r"\s*\\(?:textbf|paragraph)\{([^{}]{1,100})\}", src.text[para_start:pos+120])
        labels = conditions(text)
        if title and title.end()+para_start <= pos:
            labels.update(conditions(title[1]) if not scope else set())
        setting = {axis: {k for k in labels if k[0] == axis} for axis in ("datasets","tasks")}
        if any(values and axis in scope and values != scope[axis] for axis, values in setting.items()):
            scope.clear()
        for axis, values in setting.items():
            if len(values) == 1:
                scope[axis] = values
            elif values:
                scope.pop(axis, None)
            else:
                labels.update(scope.get(axis, set()))
        imply(labels)
        measured =VALUE_UNIT.search(text) or (any(k[0] == "metrics" for k in labels) and DECIMAL.search(text))
        if labels and measured:
            passages.append((text, src, pos, labels, quantities(text)))

    claims = []
    for text, src, pos, _, _ in heads:
        for clause in (c for part in CLAUSE_BREAK.split(text) for c in split_by_dataset(part)):
            labels = conditions(clause)
            if labels and not PLANNED.search(clause) and (RESULT_VERB.search(clause) or VALUE_UNIT.search(clause)):
                claims.append((clause.strip(), src, pos, labels))
    if not claims:
        return
    scores = emb([c[0] for c in claims]) @ emb([p[0] for p in passages]).T if passages else None
    for i, (clause, src, pos, needed) in enumerate(claims):
        names = ", ".join(label for _, label in sorted(needed))
        location = f"{src.path.name}:{line_of(src.text,pos)}"
        # Benchmark and task must match exactly; a claim that combines metrics (PSNR and MACs) may draw
        # each figure from a different sentence of the same study.
        core = {k for k in needed if k[0] != "metrics"}
        mets = {k for k in needed if k[0] == "metrics"}
        matches = [j for j,p in enumerate(passages) if core <= p[3] and (not mets or mets & p[3])]
        if matches:
            # Prefer passages mentioning the actual figures. A range may need two source sentences.
            remaining = quantities(clause)
            selected = []
            while remaining:
                j = max(matches, key=lambda k: (len(remaining & passages[k][4]), scores[i,k]))
                overlap = remaining & passages[j][4]
                if not overlap:
                    break
                selected.append(j)
                remaining -= overlap
            for j in selected or [max(matches, key=lambda k: scores[i,k])]:
                candidate, body, off, _, _ = passages[j]
                rep.infos.append(f'evidence candidate  {location} [{names}] -> {body.path.name}:{line_of(body.text,off)} "{candidate[:145]}"')
            if remaining:
                amounts = ", ".join(f"{v:g} {u}" for v,u in sorted(remaining))
                rep.warnings.append(f'support evidence  {location} [{names}]: locate [{amounts}] in the matching results for "{clause}"')
            continue
        detail = ""
        if passages:
            j = max(range(len(passages)), key=lambda k: (len(needed & passages[k][3]), scores[i,k]))
            _, body, off, available, _ = passages[j]
            missing = ", ".join(label for _, label in sorted(needed - available))
            detail = f"; closest measured passage {body.path.name}:{line_of(body.text,off)} lacks [{missing}]"
        rep.warnings.append(f'support evidence  {location} "{clause}" has no measured passage matching [{names}]{detail}')


def run(srcs, cfg, paper: Path, rep, masked, line_of):
    scfg = cfg.get("semantic", {})
    try:
        emb = Embedder(paper, scfg)
        emb(["probe"])
        _run(srcs, cfg, paper, rep, masked, line_of, emb)
    except Exception as exc:  # This optional analysis must leave the deterministic verdict intact.
        rep.warnings.append(f"semantic  optional checks incomplete ({type(exc).__name__}: {exc})")


def _run(srcs, cfg, paper, rep, masked, line_of, emb):
    scfg = cfg.get("semantic", {})

    sents = []  # (text, src, offset, pid, caption)
    for s in srcs:
        for text, off, pid, cap in _sentences(s, masked):
            sents.append((text, s, off, pid, cap))

    # --- restate: near-duplicate sentences across paragraphs of the core body sections
    core = set(scfg.get("restate_files", ["method.tex", "experiments.tex", "analysis.tex", "related_work.tex"]))
    pool = [x for x in sents if x[1].scope == "body" and x[1].path.name in core and not x[4]]
    _restatements(pool, scfg, emb, rep, line_of)

    # --- terms: unregistered near-synonyms of registered terms
    rules = cfg.get("terms", [])
    acr = cfg.get("acronyms", {})
    anchors = sorted({re.sub(r"\s*\(.*?\)", "", c).strip() for _, c in rules if len(c) > 4} | set(acr.values()))
    ignores = scfg.get("term_ignore", [])
    rejected_pairs = {(pair[0].lower(), pair[1].lower()) for pair in ignores if isinstance(pair, list) and len(pair) == 2}
    known = ({v.lower() for v, _ in rules} | {c.lower() for c in anchors}
             | {x.lower() for x in ignores if isinstance(x, str)} | {x.lower() for x in cfg.get("term_context", {})})
    # head nouns of registered phrases (+ simple plurals): candidates must end in one of them
    heads = {words[-1] for k in known if (words := WORD.findall(k))}
    heads |= {h + "s" for h in heads} | {h[:-1] for h in heads if h.endswith("s")}
    mask_rx = _phrases(known | {a.lower() for a in acr})
    where, counts = {}, {}
    for text, s, off, pid, cap in sents:
        if s.scope == "back":
            continue
        for seg in (mask_rx.split(text) if mask_rx else [text]):
            for g in _ngrams(seg):
                gl = g.lower()
                last = gl.split()[-1]
                if gl in known or last not in heads or any(f" {gl} " in f" {k} " for k in known):
                    continue
                counts[gl] = counts.get(gl, 0) + 1
                if gl not in where:
                    pos = text.lower().find(gl)
                    start = max(0, pos - 40)
                    where[gl] = (g, f'{s.path.name}:{line_of(s.text, off)} "{text[start:max(pos,0)+len(g)+48]}"')
    cands = sorted(counts)
    tthr = float(scfg.get("term_threshold", 0.84))
    if cands and anchors:
        A, C = emb(anchors), emb(cands)
        S = C @ A.T
        best = {}
        for ci, g in enumerate(cands):
            allowed = [i for i, anchor in enumerate(anchors) if (g, anchor.lower()) not in rejected_pairs]
            if not allowed:
                continue
            ai = max(allowed, key=lambda index: S[ci, index])
            if S[ci, ai] >= tthr:
                best.setdefault(anchors[ai], []).append((float(S[ci, ai]), g))
        for anchor, lst in sorted(best.items()):
            top = sorted(lst, reverse=True)[:5]
            examples = [f"'{where[g][0]}' {score:.2f} ({counts[g]}x) {where[g][1]}" for score,g in top]
            rep.warnings.append(f"terms?  near '{anchor}'\n    " + "\n    ".join(examples))

    # --- support: abstract / conclusion sentences without a close body sentence
    concl = cfg.get("conclusion_file", "conclusion.tex")
    heads = [x for x in sents if x[1].scope == "abstract" or x[1].path.name == concl]
    bodyp = [x for x in sents if x[1].scope in ("body", "back") and x[1].path.name not in (concl, "introduction.tex") and not x[4]]
    sthr = float(scfg.get("support_threshold", 0.70))
    if heads and bodyp:
        H, B = emb([x[0] for x in heads]), emb([x[0] for x in bodyp])
        S = H @ B.T
        for i, h in enumerate(heads):
            j = int(S[i].argmax())
            if S[i, j] < sthr:
                rep.warnings.append(f"support  {S[i, j]:.2f}  {h[1].path.name}:{line_of(h[1].text, h[2])} \"{h[0][:110]}\" has no close body sentence")
    if scfg.get("evidence"):
        _condition_evidence(heads, sents, paper, scfg["evidence"], emb, rep, line_of)
    emb.save()
    rep.infos.append(f"semantic  {len(sents)} sentences, {len(cands)} candidate phrases, cache {len(emb.cache)} vectors ({emb.model})")
