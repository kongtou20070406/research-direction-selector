"""Probe arm for docs/experiments/architecture-protocol.md.

Bare-attention task: give the model nothing but a single attention layer and a
linear head, then ask which structural addition helps - measured at MATCHED
train loss, not at a fixed budget endpoint.

CPU only. Run with the ai2025 interpreter:

    ~/miniconda3/envs/ai2025/python.exe docs/experiments/run_architecture_cross.py --probe

Outputs one JSON record per run to stdout (or --out). No files are written into
the repo by default.
"""
import argparse
import json
import math
import time

import numpy as np
import torch
import torch.nn as nn
from sklearn.datasets import load_digits
from sklearn.model_selection import train_test_split

TOKENS, DIM = 8, 8          # 64 pixels -> 8 tokens x 8 dims


def data(seed=0, token_budget=800):
    digits = load_digits()
    x = digits.data.astype(np.float32) / 16.0
    x = x.reshape(-1, TOKENS, DIM)
    y = digits.target.astype(np.int64)
    xtr, xte, ytr, yte = train_test_split(x, y, test_size=0.3, random_state=seed, stratify=y)
    return (torch.from_numpy(xtr), torch.from_numpy(ytr),
            torch.from_numpy(xte), torch.from_numpy(yte))


class Block(nn.Module):
    """One attention layer. `kind` adds exactly one mechanism."""

    def __init__(self, kind, dim=DIM, classes=10):
        super().__init__()
        self.kind = kind
        self.qkv = nn.Linear(dim, 3 * dim)
        self.has_ln = kind == "pre-ln"
        self.ln = nn.LayerNorm(dim) if self.has_ln else nn.Identity()
        self.has_res = kind in ("residual", "pre-ln", "mlp")
        self.has_mlp = kind == "mlp"
        if self.has_mlp:
            self.mlp = nn.Sequential(nn.Linear(dim, 2 * dim), nn.GELU(), nn.Linear(2 * dim, dim))
        self.head = nn.Linear(dim, classes)

    def forward(self, x):
        h = self.ln(x) if self.has_ln else x
        q, k, v = self.qkv(h).chunk(3, dim=-1)
        att = torch.softmax(q @ k.transpose(-1, -2) / math.sqrt(DIM), dim=-1)
        out = att @ v
        if self.has_mlp:
            out = out + self.mlp(out)
        if self.has_res:
            out = out + x
        return self.head(out.mean(dim=1))


def run(kind, seed, order, wd, steps=220, batch=64, lr=3e-2, target=None):
    torch.manual_seed(seed)
    np.random.seed(seed)
    xtr, ytr, xte, yte = data(seed=0)
    model = Block(kind)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=wd)
    lossf = nn.CrossEntropyLoss()
    n = xtr.shape[0]
    rng = np.random.default_rng(seed)
    traj, hit = [], None
    t0 = time.time()
    cur_idx = None
    for step in range(steps):
        if order == "shuffle":
            idx = rng.permutation(n)
        else:  # curriculum: easy (low current loss) first, re-estimated periodically
            if cur_idx is None or step % 80 == 0:
                with torch.no_grad():
                    per = torch.nn.functional.cross_entropy(
                        model(xtr), ytr, reduction="none").numpy()
                cur_idx = np.argsort(per)
            idx = cur_idx
        for b in range(0, n, batch):
            sel = torch.as_tensor(idx[b:b + batch])
            opt.zero_grad()
            loss = lossf(model(xtr[sel]), ytr[sel])
            loss.backward()
            opt.step()
            if hit is None and target is not None and loss.item() <= target:
                with torch.no_grad():
                    dev = float(lossf(model(xte).reshape(xte.shape[0], -1), yte))
                    acc = float((model(xte).argmax(1) == yte).float().mean())
                hit = {"step": step, "train_loss": loss.item(), "dev_loss": dev, "dev_acc": acc}
                break
        with torch.no_grad():
            tr = float(lossf(model(xtr), ytr))
            te = float(lossf(model(xte), yte))
            acc = float((model(xte).argmax(1) == yte).float().mean())
        traj.append({"step": step, "train_loss": tr, "dev_loss": te, "dev_acc": acc})
    return {"arch": kind, "seed": seed, "order": order, "wd": wd,
            "final": traj[-1], "matched": hit, "seconds": round(time.time() - t0, 1),
            "traj": traj[::10], "n_traj": len(traj)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--target", type=float, default=0.02)
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--steps", type=int, default=220)
    ap.add_argument("--wd", type=float, default=0.0)
    ap.add_argument("--order", default="shuffle", choices=["shuffle", "curriculum"])
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    kinds = ["bare", "residual", "pre-ln", "mlp"] if args.probe else ["bare"]
    rows = []
    for kind in kinds:
        for seed in range(args.seeds):
            r = run(kind, seed=seed, order=args.order, wd=args.wd,
                    target=args.target, steps=args.steps)
            rows.append(r)
            print(json.dumps({k: r[k] for k in ("arch", "seed", "order", "wd", "seconds")} |
                             {"final_train": round(r["final"]["train_loss"], 3),
                              "final_acc": round(r["final"]["dev_acc"], 3)}, ensure_ascii=False))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1)


if __name__ == "__main__":
    main()
