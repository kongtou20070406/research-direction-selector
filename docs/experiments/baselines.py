"""Baselines for docs/experiments/dehaze-benchmark-design-v2.md.

Calibration, not competition: the point is to learn whether a 50k-parameter budget
leaves any headroom above the trivial and the classical methods.

    ~/miniconda3/envs/ai2025/python.exe docs/experiments/baselines.py --set test512
    ~/miniconda3/envs/ai2025/python.exe docs/experiments/baselines.py --set dev

PSNR protocol (pre-registered, do not change after seeing numbers):
  - sRGB, uint8, computed per image, border cropped by 4 px
  - reported both unweighted mean and pixel-weighted mean
"""
import argparse
import glob
import json
from pathlib import Path

import numpy as np
from PIL import Image


def load(path):
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float64) / 255.0


def psnr(a, b, crop=4):
    if crop:
        a = a[crop:-crop, crop:-crop]
        b = b[crop:-crop, crop:-crop]
    mse = float(np.mean((a - b) ** 2))
    if mse <= 1e-12:
        return 99.0
    return float(10.0 * np.log10(1.0 / mse))


def dark_channel(img, patch=15):
    from scipy.ndimage import minimum_filter
    return minimum_filter(img.min(axis=2), size=patch, mode="nearest")


def dcp(img, patch=15, omega=0.95, t0=0.1):
    """He et al. dark channel prior. Parameters frozen; do not tune per image."""
    dark = dark_channel(img, patch)
    flat = dark.reshape(-1)
    k = max(1, int(0.001 * flat.size))
    idx = np.argpartition(flat, -k)[-k:]
    ys, xs = np.unravel_index(idx, dark.shape)
    A = np.array([img[ys, xs, c].mean() for c in range(3)])
    norm = img / np.maximum(A, 1e-6)
    trans = 1.0 - omega * dark_channel(norm, patch)
    trans = np.clip(trans, t0, 1.0)[..., None]
    out = (img - A) / trans + A
    return np.clip(out, 0.0, 1.0)


def collect(root, which):
    root = Path(root)
    if which == "test512":
        pairs = []
        for g in sorted((root / "test/512").glob("*_GT.*")):
            h = g.with_name(g.name.replace("_GT", "_hazy"))
            if h.exists():
                pairs.append((str(g), str(h)))
        return pairs
    out = []
    for idx in range(33, 41):
        g = root / f"train/GT/{idx:02d}_outdoor_GT.jpg"
        h = root / f"train/hazy/{idx:02d}_outdoor_hazy.jpg"
        if g.exists() and h.exists():
            out.append((str(g), str(h)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"D:/Myfile/data/ohaze")
    ap.add_argument("--set", default="test512", choices=["test512", "dev"])
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    pairs = collect(args.root, args.set)
    if args.limit:
        pairs = pairs[:args.limit]
    rows = []
    for g, h in pairs:
        gt = load(g)
        hazy = load(h)
        if gt.shape != hazy.shape:
            rows.append({"name": Path(g).name, "skip": "shape mismatch",
                         "gt": list(gt.shape), "hazy": list(hazy.shape)})
            continue
        rows.append({
            "name": Path(g).name,
            "pixels": int(gt.shape[0] * gt.shape[1]),
            "identity": round(psnr(hazy, gt), 3),
            "dcp": round(psnr(dcp(hazy), gt), 3),
        })
    ok = [r for r in rows if "identity" in r]
    if not ok:
        print(json.dumps(rows, indent=1))
        return
    w = np.array([r["pixels"] for r in ok], dtype=np.float64)
    summary = {"set": args.set, "n": len(ok)}
    for key in ("identity", "dcp"):
        vals = np.array([r[key] for r in ok], dtype=np.float64)
        summary[key] = {
            "mean": round(float(vals.mean()), 3),
            "weighted": round(float((vals * w).sum() / w.sum()), 3),
            "min": round(float(vals.min()), 3),
            "max": round(float(vals.max()), 3),
        }
    print(json.dumps({"summary": summary, "per_image": ok}, indent=1))


if __name__ == "__main__":
    main()
