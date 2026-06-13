"""
LaTeX-based resume tailoring.

The LLM edits the *text* inside the user's LaTeX source and leaves all layout
commands alone; we then compile with the user's own template, so the output
looks exactly like their resume. Far better than re-rendering extracted text.

Functions:
  tailor_latex(tex, jd)        -> edited .tex string
  compile_latex(name, workdir) -> (pdf_path | None, log)
  qa_compiled(pdf_path)        -> {pages, good, issues}
  make_redline_pdf(a, b, out)  -> colored source diff PDF
"""

import difflib
import html as _html
import os
import re
import shutil
import subprocess

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import HexColor
from reportlab.platypus import SimpleDocTemplate, Paragraph

import analyzer


TAILOR_SYSTEM = r"""You are editing a LaTeX resume to tailor it to a JOB DESCRIPTION.

Return the COMPLETE edited LaTeX document and NOTHING else (no commentary, no ``` fences).

What you MAY change:
- The human-readable text inside content commands: bullet/item text, the summary/profile,
  skills lists, and similar prose.
- You may rephrase to mirror the job description's language and surface relevant skills the
  candidate genuinely demonstrates.

What you MUST NOT change:
- The preamble, \documentclass, \usepackage lines, custom commands, environments, or ANY
  layout/structure/formatting commands. Keep all markup byte-for-byte except the prose you edit.
- Do not add or remove sections, entries, or packages.

Hard rules:
- NEVER invent employers, dates, titles, tools, degrees, or metrics. Only reword real content.
- Properly escape any LaTeX special characters you introduce: \% \& \$ \# \_ \{ \} and \textbackslash, \textasciitilde, \textasciicircum.
- The result MUST still compile. If unsure about a change, leave the original text.
Output: the full .tex source only."""


def _strip_fence(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    return raw.strip()


def tailor_latex(tex_source, jd_text):
    """LLM edits the LaTeX text content; returns the full edited .tex."""
    user = f"JOB DESCRIPTION:\n{jd_text}\n\n---\n\nLATEX RESUME SOURCE:\n{tex_source}"
    raw = analyzer.llm_complete(TAILOR_SYSTEM, user, max_tokens=8000, json_mode=False)
    edited = _strip_fence(raw)
    # Safety net: if the model returned something that clearly isn't a LaTeX doc, keep original.
    if "\\begin{document}" not in edited and "\\documentclass" not in edited:
        raise ValueError("Model did not return a LaTeX document.")
    return edited


def compile_latex(main_name, workdir, timeout=120):
    """Compile main_name inside workdir. Prefers tectonic, falls back to latexmk.
    Returns (pdf_path or None, combined log tail)."""
    src = ""
    try:
        with open(os.path.join(workdir, main_name), encoding="utf-8", errors="ignore") as fh:
            src = fh.read()
    except OSError:
        pass
    stem = os.path.splitext(main_name)[0]
    pdf_path = os.path.join(workdir, stem + ".pdf")
    log = ""

    def _run(cmd):
        nonlocal log
        try:
            p = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=timeout)
            log = (p.stdout or "") + "\n" + (p.stderr or "")
        except subprocess.TimeoutExpired:
            log = f"Compilation timed out after {timeout}s."
        return os.path.exists(pdf_path)

    # 1) tectonic — single binary, auto-fetches packages, handles fontspec
    if shutil.which("tectonic"):
        if _run(["tectonic", "--keep-logs", "--outdir", workdir, main_name]):
            return pdf_path, log

    # 2) latexmk with an engine guessed from the source
    if shutil.which("latexmk"):
        needs_unicode = ("fontspec" in src) or ("polyglossia" in src)
        engines = ["-pdfxe", "-pdflua", "-pdf"] if needs_unicode else ["-pdf", "-pdfxe", "-pdflua"]
        for i, eng in enumerate(engines):
            if i > 0:
                subprocess.run(["latexmk", "-C", main_name], cwd=workdir,
                               capture_output=True, text=True)
            if _run(["latexmk", eng, "-interaction=nonstopmode", "-halt-on-error", "-f", main_name]):
                return pdf_path, log

    return None, log


def error_tail(log, n=1200):
    """Pull the useful part of a LaTeX log: error lines, else the tail."""
    err_lines = [ln for ln in log.splitlines() if ln.startswith("!") or "Error" in ln]
    if err_lines:
        return "\n".join(err_lines[:12])
    return log[-n:]


def qa_compiled(pdf_path):
    """Page count + ATS parseability on the compiled PDF -> {pages, good, issues}."""
    import pdfplumber
    good, issues = [], []
    with pdfplumber.open(pdf_path) as pdf:
        pages = len(pdf.pages)
        text = "\n".join((pg.extract_text() or "") for pg in pdf.pages)

    good.append("Compiled successfully.")
    (good if pages <= 2 else issues).append(
        f"Fits on {pages} page(s)." if pages <= 2 else f"{pages} pages — likely too long for a resume."
    )
    ats = analyzer.ats_format_check(text)
    good.append(f"ATS-parse score on the PDF: {ats['score']}/100.")
    for i in ats["issues"]:
        issues.append(i)
    return {"pages": pages, "good": good, "issues": issues}


def make_redline_pdf(original_text, new_text, out_path):
    """Word-level diff of original vs edited source -> a colored redline PDF."""
    legend_s = ParagraphStyle("lg", fontName="Helvetica-Oblique", fontSize=9,
                              textColor=HexColor("#555555"), spaceAfter=10)
    body_s = ParagraphStyle("rl", fontName="Courier", fontSize=8.5, leading=12)

    def esc(t):
        return _html.escape(t or "")

    story = [Paragraph(
        "Source changes: <font color='#1a7f37'>green = added</font>, "
        "<font color='#b00020'>red strikethrough = removed</font>.",
        legend_s,
    )]
    o, n = original_text.split(), new_text.split()
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=o, b=n).get_opcodes():
        if tag == "equal":
            out.append(esc(" ".join(o[i1:i2])))
        elif tag == "delete":
            out.append(f"<font color='#b00020'><strike>{esc(' '.join(o[i1:i2]))}</strike></font>")
        elif tag == "insert":
            out.append(f"<font color='#1a7f37'>{esc(' '.join(n[j1:j2]))}</font>")
        elif tag == "replace":
            out.append(f"<font color='#b00020'><strike>{esc(' '.join(o[i1:i2]))}</strike></font>")
            out.append(f"<font color='#1a7f37'>{esc(' '.join(n[j1:j2]))}</font>")
    story.append(Paragraph(" ".join(p for p in out if p), body_s))
    SimpleDocTemplate(out_path, pagesize=LETTER, leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                      topMargin=0.6 * inch, bottomMargin=0.6 * inch).build(story)