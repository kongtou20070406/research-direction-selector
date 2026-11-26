import importlib.util
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("layout_audit", ROOT / "skills" / "latex-typeset" / "scripts" / "layout_audit.py")
la = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(la)


def test_parse_tex_log_tracks_nested_files_and_issues():
    log = "\n".join([
        "(./main.tex (./sections_en/03_method.tex",
        "Overfull \\hbox (14.2pt too wide) in paragraph at lines 42--45",
        "Underfull \\vbox (badness 10000) has occurred while \\output is active",
        "Missing character: There is no ⚠ in font SimSun!",
        ") (./sections_en/05_exp.tex",
        "LaTeX Warning: Float too large for page by 18.5pt on input line 91.",
        "LaTeX Warning: Reference `tab:missing' on page 6 undefined on input line 103.",
        "))",
    ])
    findings = la.parse_tex_log(log)
    by_kind = {f["kind"]: f for f in findings}
    assert by_kind["OVERFULL_HBOX"]["severity"] == "error"
    assert by_kind["OVERFULL_HBOX"]["file"] == "./sections_en/03_method.tex"
    assert by_kind["OVERFULL_HBOX"]["line"] == "L42-45"
    assert by_kind["UNDERFULL_VBOX"]["severity"] == "warning"
    assert by_kind["MISSING_CHAR"]["severity"] == "error"
    assert by_kind["FLOAT_TOO_LARGE"]["file"] == "./sections_en/05_exp.tex"
    assert by_kind["FLOAT_TOO_LARGE"]["line"] == "L91"
    assert by_kind["UNDEFINED_REF"]["line"] == "L103"


def test_scan_tex_sources_catches_hacks_and_ignores_comments(tmp_path: Path):
    bad = tmp_path / "bad.tex"
    bad.write_text(
        "\n".join([
            "% \\vspace{-12pt} commented out should be ignored",
            "\\vspace{-8pt}",
            "\\renewcommand{\\baselinestretch}{0.92}",
            "\\begin{figure}[H]",
            "\\begin{table}[t]\\begin{tabular}{|l|c|}\\hline a & b \\\\\\end{tabular}\\caption{Cap}\\end{table}",
            "\\scalebox{0.70}{\\tiny foo}",
        ]),
        encoding="utf-8",
    )
    findings = la.scan_tex_sources([bad], venue="icml")
    kinds = {f["kind"]: f["severity"] for f in findings}
    assert kinds["NEGATIVE_VSPACE_HACK"] == "error"
    assert kinds["SPACING_HACK"] == "error"
    assert kinds["FLOAT_H_SPAM"] == "warning"
    assert kinds["TABLE_VERTICAL_RULES"] == "warning"
    assert kinds["TABLE_HLINE"] == "warning"
    assert kinds["TABLE_CAPTION_BELOW"] == "warning"
    assert kinds["SHRINK_SCALEBOX"] == "warning"
    assert kinds["TINY_FONT"] == "warning"

    clean = tmp_path / "clean.tex"
    clean.write_text(
        "\\begin{table}[t]\\caption{Clean}\\small\\begin{tabular}{@{}lc@{}}\\toprule a & b \\\\\\bottomrule\\end{tabular}\\end{table}\n",
        encoding="utf-8",
    )
    assert la.scan_tex_sources([clean], venue="icml") == []


def test_prose_preservation_guard_allows_layout_changes_blocks_word_or_number_drift(tmp_path: Path):
    tex = tmp_path / "03_method.tex"
    tex.write_text("We reduce rollout MSE from 0.364 to 0.255 across 3 seeds.\n", encoding="utf-8")
    snap = tmp_path / "snap.json"
    la.snapshot_prose([tex], snap, root=tmp_path)

    # Pure layout edits pass
    tex.write_text(
        "\\looseness=-1\nWe reduce rollout MSE from\n0.364 to 0.255 across 3 seeds. % layout tweak\n",
        encoding="utf-8",
    )
    assert la.verify_prose([tex], snap, root=tmp_path) == []

    # Numeric or wording edit fails
    tex.write_text("We reduce rollout MSE from 0.364 to 0.250 across 3 seeds.\n", encoding="utf-8")
    diffs = la.verify_prose([tex], snap, root=tmp_path)
    assert len(diffs) == 1 and "0.255" in diffs[0]["reason"]


def test_audit_pdf_detects_overlap_margin_orphan_void_page_limit_and_renders_png(tmp_path: Path):
    # 1) Defective PDF: page 1 has overlap, margin breach, and bottom orphan heading; page 2 is spare; page 3+ triggers page limit.
    bad_pdf = tmp_path / "bad.pdf"
    doc = fitz.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((72, 100), "Body paragraph line one.", fontsize=10)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 30), False)
    pix.clear_with(200)
    p1.insert_image(fitz.Rect(72, 88, 172, 118), pixmap=pix)
    p1.insert_text((5, 200), "Margin breach on left", fontsize=10)
    p1.insert_text((72, 760), "3.1 Orphan Heading Near Bottom", fontsize=12)

    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((72, 80), "Top block", fontsize=10)
    p2.insert_text((72, 500), "Bottom block after huge void", fontsize=10)

    for i in range(7):
        pg = doc.new_page(width=595, height=842)
        pg.insert_text((72, 80), f"Page {i + 3} body", fontsize=10)
    doc.save(bad_pdf)
    doc.close()

    out_dir = tmp_path / "preview"
    findings = la.audit_pdf(bad_pdf, venue="icml", render_dir=out_dir)
    kinds = {f["kind"] for f in findings}
    assert {"PAGE_LIMIT", "MARGIN", "OVERLAP", "ORPHAN", "VOID"} <= kinds
    assert (out_dir / "page_01.png").is_file() and (out_dir / "page_01.png").stat().st_size > 0

    # 2) Clean PDF passes with 0 errors
    good_pdf = tmp_path / "good.pdf"
    gdoc = fitz.open()
    gp = gdoc.new_page(width=595, height=842)
    gp.insert_text((72, 100), "1. Introduction", fontsize=12)
    gp.insert_text((72, 140), "Clean well-spaced paragraph inside safe margins.", fontsize=10)
    gdoc.save(good_pdf)
    gdoc.close()
    good_findings = la.audit_pdf(good_pdf, venue="icml")
    assert [f for f in good_findings if f["severity"] == "error"] == []
