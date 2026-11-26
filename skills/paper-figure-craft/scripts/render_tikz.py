"""Compile standalone TikZ/PGFPlots .tex files into vector .svg and HiDPI .png via TeX Live 2025."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path


def compile_tikz(tex_path: str | Path, svg_out: str | Path, png_out: str | Path | None = None, dpi: int = 360) -> dict:
    tex_p = Path(tex_path).resolve()
    svg_p = Path(svg_out).resolve()
    svg_p.parent.mkdir(parents=True, exist_ok=True)

    pdflatex = shutil.which("pdflatex") or "pdflatex"
    pdftocairo = shutil.which("pdftocairo") or "pdftocairo"
    pdftoppm = shutil.which("pdftoppm") or "pdftoppm"
    dvisvgm = shutil.which("dvisvgm") or "dvisvgm"

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        staged_tex = tmp / "diagram.tex"
        shutil.copy2(tex_p, staged_tex)

        res = subprocess.run(
            [pdflatex, "-interaction=nonstopmode", "-halt-on-error", "diagram.tex"],
            cwd=str(tmp),
            capture_output=True,
            text=True,
            timeout=60,
        )
        pdf_built = tmp / "diagram.pdf"
        if res.returncode != 0 or not pdf_built.exists():
            raise RuntimeError(f"pdflatex failed:\n{res.stdout[-700:]}\n{res.stderr[-400:]}")

        # Convert PDF -> clean standalone vector SVG (paths-only, zero font-substitution risk)
        svg_tmp = tmp / "diagram.svg"
        if Path(pdftocairo).exists():
            res_svg = subprocess.run(
                [pdftocairo, "-svg", str(pdf_built), str(svg_tmp)],
                capture_output=True,
                text=True,
                timeout=45,
            )
            if res_svg.returncode != 0 or not svg_tmp.exists():
                raise RuntimeError(f"pdftocairo -svg failed: {res_svg.stderr}")
        else:
            res_svg = subprocess.run(
                [dvisvgm, "--pdf", "--no-fonts", "-o", str(svg_tmp), str(pdf_built)],
                capture_output=True,
                text=True,
                timeout=45,
            )
            if res_svg.returncode != 0 or not svg_tmp.exists():
                raise RuntimeError(f"dvisvgm failed: {res_svg.stderr}")

        shutil.copy2(svg_tmp, svg_p)
        out = {"svg": str(svg_p), "png": None}

        if png_out is not None:
            png_p = Path(png_out).resolve()
            png_p.parent.mkdir(parents=True, exist_ok=True)
            prefix = tmp / "page"
            res_png = subprocess.run(
                [pdftoppm, "-png", "-r", str(dpi), "-singlefile", str(pdf_built), str(prefix)],
                capture_output=True,
                text=True,
                timeout=45,
            )
            built_png = tmp / "page.png"
            if res_png.returncode != 0 or not built_png.exists():
                raise RuntimeError(f"pdftoppm failed: {res_png.stderr}")
            shutil.copy2(built_png, png_p)
            out["png"] = str(png_p)
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("tex")
    p.add_argument("--svg", required=True)
    p.add_argument("--png", default=None)
    p.add_argument("--dpi", type=int, default=360)
    args = p.parse_args()
    print("COMPILED_TIKZ:", compile_tikz(args.tex, args.svg, args.png, dpi=args.dpi))


if __name__ == "__main__":
    main()
