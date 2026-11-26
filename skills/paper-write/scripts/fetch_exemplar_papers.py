"""Download exemplar papers from their official sources and extract page-marked text.

Reads ``references/exemplars/papers.json`` (id, card, title, url). For each entry it
downloads ``papers/<id>.pdf``, checks that the title appears on the first two pages,
writes ``papers/<id>.md`` with ``## Page N`` markers, and records sha256 and size in
``papers/manifest.lock.json``. Existing files whose hash matches the lock are kept.

The PDFs stay local: ``papers/`` is git-ignored because the upstream licences of the
papers do not cover redistribution. Requires PyMuPDF (``import fitz``).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True

SKILL_ROOT = Path(__file__).resolve().parents[1]
EXEMPLARS = SKILL_ROOT / "references" / "exemplars"
USER_AGENT = "paper-write-exemplar-fetch/1.0"
_WORD = re.compile(r"[a-z0-9]+")
_LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"}


def load_manifest(path: Path) -> list[dict]:
    entries = json.loads(path.read_text(encoding="utf-8"))
    ids = [e["id"] for e in entries]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate ids in {path.name}: {dupes}")
    for e in entries:
        missing = {"id", "card", "title", "url"} - e.keys()
        if missing:
            raise ValueError(f"{e.get('id', '?')}: missing fields {sorted(missing)}")
        if not e["url"].startswith("https://"):
            raise ValueError(f"{e['id']}: url must be https")
    return entries


def title_score(title: str, text: str) -> float:
    """Fraction of the title's content words (len >= 3) present in ``text``."""
    words = {w for w in _WORD.findall(title.lower()) if len(w) >= 3}
    if not words:
        return 1.0
    hay = set(_WORD.findall(text.lower().replace("-\n", "")))
    return len(words & hay) / len(words)


def extract_pages(pdf_path: Path) -> list[str]:
    import fitz  # PyMuPDF

    with fitz.open(pdf_path) as doc:
        return [page.get_text("text") for page in doc]


def pages_to_markdown(pages: list[str], entry: dict) -> str:
    head = [
        f"# {entry['title']}",
        "",
        f"Source: {entry['url']}",
        f"Card: `cards/{entry['card']}.md`. Text extracted with PyMuPDF; check the PDF for equations, figures and reading order.",
        "",
    ]
    body = []
    for i, text in enumerate(pages, 1):
        for bad, good in _LIGATURES.items():
            text = text.replace(bad, good)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        body.append(f"## Page {i}\n\n{text}\n")
    return "\n".join(head) + "\n" + "\n".join(body)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, timeout: int) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = dest.with_suffix(".part")
    with urllib.request.urlopen(req, timeout=timeout) as resp, tmp.open("wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    if not tmp.read_bytes()[:5].startswith(b"%PDF"):
        tmp.unlink()
        raise ValueError(f"not a PDF: {url}")
    tmp.replace(dest)


def fetch_all(entries: list[dict], papers_dir: Path, timeout: int = 180, min_score: float = 0.6,
              only: set[str] | None = None, downloader=download) -> tuple[dict, list[str]]:
    papers_dir.mkdir(parents=True, exist_ok=True)
    lock_path = papers_dir / "manifest.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.is_file() else {}
    errors: list[str] = []
    last_host = ""
    for e in entries:
        if only and e["id"] not in only:
            continue
        pdf, md = papers_dir / f"{e['id']}.pdf", papers_dir / f"{e['id']}.md"
        known = lock.get(e["id"], {})
        if pdf.is_file() and md.is_file() and known.get("sha256") == sha256(pdf) and known.get("url") == e["url"]:
            continue
        host = e["url"].split("/")[2]
        if host == last_host and "arxiv.org" in host:
            time.sleep(3)  # arXiv asks automated clients for one request every 3 s
        last_host = host
        try:
            downloader(e["url"], pdf, timeout)
            pages = extract_pages(pdf)
            score = title_score(e["title"], "\n".join(pages[:2]))
            if score < min_score:
                raise ValueError(f"title check failed ({score:.2f} < {min_score}); wrong paper at {e['url']}?")
            md.write_text(pages_to_markdown(pages, e), encoding="utf-8", newline="\n")
            lock[e["id"]] = {
                "url": e["url"], "sha256": sha256(pdf), "bytes": pdf.stat().st_size,
                "pages": len(pages), "title_score": round(score, 2),
                "fetched": _dt.date.today().isoformat(),
            }
            print(f"OK   {e['id']} ({len(pages)} pages, {pdf.stat().st_size / 1e6:.1f} MB, title {score:.2f})")
        except Exception as exc:  # report every failure, keep going
            errors.append(f"{e['id']}: {exc}")
            print(f"FAIL {e['id']}: {exc}")
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return lock, errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", type=Path, default=EXEMPLARS / "papers.json")
    ap.add_argument("--papers-dir", type=Path, default=EXEMPLARS / "papers")
    ap.add_argument("--only", nargs="*", help="ids to fetch (default: all)")
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args(argv)
    entries = load_manifest(args.manifest)
    _, errors = fetch_all(entries, args.papers_dir, timeout=args.timeout, only=set(args.only or []) or None)
    if errors:
        print(f"{len(errors)} of {len(entries)} papers failed")
        return 1
    print(f"all {len(entries)} papers present in {args.papers_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
