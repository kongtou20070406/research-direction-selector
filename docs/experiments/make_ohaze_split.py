"""Build the OHaze split manifest for docs/experiments/dehaze-benchmark-design.md.

Does not copy pixels. Writes a JSON manifest with path, size and SHA-256 per file,
plus a public variant that hides the held-out paths.

    ~/miniconda3/envs/ai2025/python.exe docs/experiments/make_ohaze_split.py
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

OUTDOOR = re.compile(r"^(\d+)_outdoor_(GT|hazy)\.jpe?g$", re.IGNORECASE)
INDOOR = re.compile(r"^(\d+)_indoor_(GT|hazy)\.jpe?g$", re.IGNORECASE)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def size_of(path):
    try:
        from PIL import Image
        with Image.open(path) as im:
            return list(im.size)
    except Exception:
        return None


def record(root, path):
    return {"path": str(Path(path).relative_to(root)).replace("\\", "/"),
            "bytes": path.stat().st_size,
            "size": size_of(path),
            "sha256": sha256(path)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"D:/Myfile/data/ohaze")
    ap.add_argument("--outdir", default="docs/experiments")
    args = ap.parse_args()
    root = Path(args.root)
    if not root.is_dir():
        raise SystemExit(f"no such root: {root}")

    pairs = {"train": [], "dev": [], "test-A": [], "test-B": []}
    index = {}

    for sub in ("train/GT", "train/hazy", "test/GT", "test/hazy"):
        for f in sorted((root / sub).glob("*")):
            m = OUTDOOR.match(f.name)
            if not m:
                continue
            idx = int(m.group(1))
            kind = "GT" if m.group(2).upper() == "GT" else "hazy"
            index.setdefault(("outdoor", idx), {})[kind] = f

    for (scene, idx), pair in sorted(index.items()):
        if "GT" not in pair or "hazy" not in pair:
            continue
        if 1 <= idx <= 32:
            bucket = "train"
        elif 33 <= idx <= 40:
            bucket = "dev"
        elif 41 <= idx <= 45:
            bucket = "test-A"
        else:
            continue
        pairs[bucket].append({
            "id": f"{scene}-{idx:02d}",
            "gt": record(root, pair["GT"]),
            "hazy": record(root, pair["hazy"]),
        })

    indoor = {}
    for f in sorted((root / "test/512").glob("*")):
        m = INDOOR.match(f.name)
        if not m:
            continue
        idx = int(m.group(1))
        kind = "GT" if m.group(2).upper() == "GT" else "hazy"
        indoor.setdefault(idx, {})[kind] = f
    for idx, pair in sorted(indoor.items()):
        if "GT" in pair and "hazy" in pair:
            pairs["test-B"].append({
                "id": f"indoor-{idx:02d}",
                "gt": record(root, pair["GT"]),
                "hazy": record(root, pair["hazy"]),
            })

    full = {
        "schema": 1,
        "source": str(root),
        "note": "Held-out blocks (test-A, test-B) are listed here for the grader only.",
        "counts": {k: len(v) for k, v in pairs.items()},
        "pairs": pairs,
    }
    public = {
        "schema": 1,
        "note": "Given to entrants. Held-out blocks appear as counts and hashes only.",
        "counts": {k: len(v) for k, v in pairs.items()},
        "pairs": {"train": pairs["train"], "dev": pairs["dev"]},
        "sealed": {
            k: [{"id": p["id"], "gt_sha256": p["gt"]["sha256"], "hazy_sha256": p["hazy"]["sha256"],
                 "size": p["gt"]["size"]} for p in pairs[k]]
            for k in ("test-A", "test-B")
        },
    }
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "ohaze-split.json").write_text(json.dumps(full, indent=1), encoding="utf-8")
    (outdir / "ohaze-split-public.json").write_text(json.dumps(public, indent=1), encoding="utf-8")
    print(json.dumps(full["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
