"""Render standalone self-contained HTML+SVG figures offline via Playwright Chromium or headless Chrome."""

from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys
from pathlib import Path


def find_chromium_exe() -> str | None:
    local_app = os.environ.get("LOCALAPPDATA", "")
    prog_files = os.environ.get("PROGRAMFILES", "")
    prog_x86 = os.environ.get("PROGRAMFILES(X86)", "")
    patterns = [
        os.path.join(local_app, "ms-playwright", "chromium-*", "chrome-win*", "chrome.exe"),
        os.path.join(local_app, "ms-playwright", "chromium_headless_shell-*", "chrome-win*", "chrome-headless-shell.exe"),
    ]
    if prog_files:
        patterns.append(os.path.join(prog_files, "Google", "Chrome", "Application", "chrome.exe"))
    if prog_x86:
        patterns.append(os.path.join(prog_x86, "Microsoft", "Edge", "Application", "msedge.exe"))
    for pat in patterns:
        matches = sorted(glob.glob(pat), reverse=True)
        for m in matches:
            if os.path.isfile(m):
                return m
    return None


def extract_standalone_svg(html_path: Path, svg_out: Path) -> bool:
    html = html_path.read_text(encoding="utf-8")
    m = re.search(r"<svg[\s\S]*?</svg>", html, flags=re.IGNORECASE)
    if not m:
        return False
    svg = m.group(0)
    if 'xmlns="http://www.w3.org/2000/svg"' not in svg:
        svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
    svg_out.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + svg + "\n", encoding="utf-8")
    return True


def render_html(
    html_path: str | Path,
    png_out: str | Path,
    svg_out: str | Path | None = None,
    width: int = 1400,
    height: int = 500,
    scale: int = 3,
    trim_whitespace: bool = True,
) -> dict:
    from PIL import Image, ImageChops

    hp = Path(html_path).resolve()
    pp = Path(png_out).resolve()
    pp.parent.mkdir(parents=True, exist_ok=True)
    exe = find_chromium_exe()
    if exe is None:
        raise RuntimeError("No local Chromium/Edge binary found for offline HTML/SVG rendering.")

    if svg_out is not None:
        extract_standalone_svg(hp, Path(svg_out).resolve())

    win_w = int(width)
    win_h = int(height)
    url = hp.as_uri()
    cmd = [
        exe,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--hide-scrollbars",
        f"--force-device-scale-factor={scale}",
        f"--window-size={win_w},{win_h}",
        f"--screenshot={pp}",
        url,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    if res.returncode != 0 or not pp.exists():
        # Retry with old headless flag if headless=new is unsupported
        cmd[1] = "--headless"
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        if res.returncode != 0 or not pp.exists():
            raise RuntimeError(f"Chromium screenshot failed: {res.stderr[:300]}")

    if trim_whitespace:
        im = Image.open(pp).convert("RGB")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        diff = ImageChops.difference(im, bg)
        bbox = diff.getbbox()
        if bbox:
            pad = max(12, scale * 6)
            x0 = max(0, bbox[0] - pad)
            y0 = max(0, bbox[1] - pad)
            x1 = min(im.width, bbox[2] + pad)
            y1 = min(im.height, bbox[3] + pad)
            im = im.crop((x0, y0, x1, y1))
            im.save(pp, dpi=(300, 300))

    final_im = Image.open(pp)
    return {"png": str(pp), "svg": str(svg_out) if svg_out else None, "size": final_im.size, "exe": exe}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("html")
    p.add_argument("--png", required=True)
    p.add_argument("--svg", default=None)
    p.add_argument("--width", type=int, default=1440)
    p.add_argument("--height", type=int, default=560)
    p.add_argument("--scale", type=int, default=3)
    args = p.parse_args()
    info = render_html(args.html, args.png, svg_out=args.svg, width=args.width, height=args.height, scale=args.scale)
    print("RENDERED:", info)


if __name__ == "__main__":
    main()
