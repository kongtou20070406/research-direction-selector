"""Score a submitted dehaze directory. Trusts images, not self-reported numbers.

Protocol, frozen in prompt-shared.md:
  sRGB, clip to [0,1], round to uint8, crop 4 px, per-image then mean.
  SSIM: skimage default, data_range=255, channel_axis=-1.

    python score.py --pred <dir with 41.jpg..45.jpg> --gt <dir with 41_outdoor_GT.jpg..>
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity


def load(path):
    return np.asarray(Image.open(path).convert("RGB"))


def score_pair(pred, gt, crop=4):
    if pred.shape != gt.shape:
        return {"error": "shape mismatch", "pred": list(pred.shape), "gt": list(gt.shape)}
    p = pred[crop:-crop, crop:-crop].astype(np.float64)
    g = gt[crop:-crop, crop:-crop].astype(np.float64)
    mse = float(np.mean((p - g) ** 2))
    psnr = 99.0 if mse <= 1e-12 else float(10.0 * np.log10(255.0 ** 2 / mse))
    ssim = float(structural_similarity(pred[crop:-crop, crop:-crop],
                                       gt[crop:-crop, crop:-crop],
                                       data_range=255, channel_axis=-1))
    return {"psnr": round(psnr, 4), "ssim": round(ssim, 4)}


def find(directory, stem):
    hits = [p for p in directory.glob(f"{stem}*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"}]
    return hits[0] if len(hits) == 1 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", required=True)
    ap.add_argument("--gt", required=True)
    ap.add_argument("--ids", default="41,42,43,44,45")
    args = ap.parse_args()
    pred_dir, gt_dir = Path(args.pred), Path(args.gt)
    rows = []
    for stem in args.ids.split(","):
        pred = find(pred_dir, stem)
        gt = find(gt_dir, f"{stem}_outdoor_GT")
        if pred is None or gt is None:
            rows.append({"id": stem, "error": "missing", "pred": str(pred), "gt": str(gt)})
            continue
        rows.append({"id": stem, **score_pair(load(pred), load(gt))})
    ok = [r for r in rows if "psnr" in r]
    summary = {"n": len(ok)}
    if ok:
        summary["psnr_mean"] = round(sum(r["psnr"] for r in ok) / len(ok), 4)
        summary["ssim_mean"] = round(sum(r["ssim"] for r in ok) / len(ok), 4)
    print(json.dumps({"summary": summary, "per_image": rows}, indent=1))


if __name__ == "__main__":
    main()
