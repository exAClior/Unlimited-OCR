"""
Convert OCR-output Markdown (with LaTeX math) to compilable .tex.

Purpose-built for Unlimited-OCR output — handles:
  - \( ... \) inline math  →  passed through (already valid LaTeX)
  - \[ ... \] display math →  wrapped in equation* or passed through
  - ## Heading             →  \section{} / \chapter{}
  - ![Figure](path)       →  \includegraphics
  - *caption*              →  \textit{}
  - <!-- page N -->        →  page labels
  - Plain text             →  passed through

Usage:
    python md2tex.py outputs/wilde_qit_book/full.md -o outputs/wilde_qit_book/tex/full.tex
    python md2tex.py outputs/wilde_qit_book/chapters/*.md -o outputs/wilde_qit_book/tex/chapters/
"""

import argparse
import os
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Preamble
# ---------------------------------------------------------------------------

PREAMBLE = r"""\documentclass[11pt,a4paper]{report}

\usepackage[margin=1in]{geometry}
\usepackage{amsmath,amssymb,amsfonts}
\usepackage{graphicx}
\usepackage{hyperref}
\usepackage{fontspec}
\usepackage{unicode-math}
\usepackage{braket}
\usepackage{booktabs}

% Handle \tag inside \[ \] without equation environment
\newcommand{\tagaliasaliased}{}

\graphicspath{{../}}

\hypersetup{
    colorlinks=true,
    linkcolor=blue,
    urlcolor=blue,
}

\begin{document}
"""

POSTAMBLE = r"""
\end{document}
"""

# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

# Patterns
PAGE_COMMENT = re.compile(r"^<!--\s*page\s+(\d+)\s*-->$")
HEADING = re.compile(r"^(#{1,4})\s+(.+)$")
FIGURE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")
CAPTION = re.compile(r"^\*(.+)\*$", re.DOTALL)
DISPLAY_MATH_START = re.compile(r"^\\\[$")
DISPLAY_MATH_END = re.compile(r"^\\\]$")
HR = re.compile(r"^---+\s*$")


# Unicode chars that appear in text mode from OCR output → LaTeX replacements
UNICODE_TEXT_MAP = {
    '\u2212': '$-$',           # MINUS SIGN
    '\u2261': '$\\equiv$',     # IDENTICAL TO
    '\u2295': '$\\oplus$',     # CIRCLED PLUS
    '\u2227': '$\\wedge$',     # LOGICAL AND
    '\u27E9': '$\\rangle$',    # MATHEMATICAL RIGHT ANGLE BRACKET
    '\u27E8': '$\\langle$',    # MATHEMATICAL LEFT ANGLE BRACKET
    '\u03B1': '$\\alpha$',     # GREEK SMALL LETTER ALPHA
    '\u03B2': '$\\beta$',      # GREEK SMALL LETTER BETA
    '\u03C1': '$\\rho$',       # GREEK SMALL LETTER RHO
    '\u03C8': '$\\psi$',       # GREEK SMALL LETTER PSI
    '\u03C6': '$\\phi$',       # GREEK SMALL LETTER PHI
    '\u25A1': '$\\square$',    # WHITE SQUARE (QED)
    '\u2610': '$\\square$',    # BALLOT BOX (QED)
    '\u00B2': '$^2$',          # SUPERSCRIPT TWO
}


def sanitize_unicode(text: str) -> str:
    """Replace problematic Unicode chars with LaTeX equivalents.

    Only replaces characters in text-mode regions; math-mode regions
    are left untouched.
    """
    # Split on math delimiters to avoid touching math content
    parts = re.split(r'(\\\(.*?\\\)|\\\[.*?\\\])', text, flags=re.DOTALL)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            result.append(part)  # math — leave alone
        else:
            for uc, repl in UNICODE_TEXT_MAP.items():
                part = part.replace(uc, repl)
            result.append(part)
    return ''.join(result)


def escape_tex(text: str) -> str:
    r"""Escape characters that are special in LaTeX text mode.

    Preserves anything inside \( ... \) and \[ ... \] math delimiters.
    """
    # Sanitize unicode first
    text = sanitize_unicode(text)

    # Split on math delimiters, only escape non-math parts
    parts = re.split(r'(\\\(.*?\\\)|\\\[.*?\\\])', text, flags=re.DOTALL)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            # Math part — pass through unchanged
            result.append(part)
        else:
            # Text part — escape special chars
            part = part.replace('&', r'\&')
            part = part.replace('%', r'\%')
            part = part.replace('#', r'\#')
            # Don't escape _ and ^ — they might be in math-adjacent context
            # Don't escape { } — they might be LaTeX commands
            result.append(part)
    return ''.join(result)


def sanitize_display_math(block: str) -> str:
    """Fix common OCR artifacts in display math blocks.

    - Unmatched \left/\right → strip sizing (use bare delimiters)
    - Unmatched \begin{array}/\end{array} → close them
    - Unmatched \begin{aligned}/\end{aligned} → close them
    """
    # Count and balance \left / \right
    n_left = len(re.findall(r'\\left(?![a-zA-Z])', block))
    n_right = len(re.findall(r'\\right(?![a-zA-Z])', block))
    if n_left != n_right:
        # Strip all \left and \right, keep the delimiter character
        block = re.sub(r'\\left\s*([\[\]().|\\{\\}]|\\[a-z]+)', r'\1', block)
        block = re.sub(r'\\right\s*([\[\]().|\\{\\}]|\\[a-z]+)', r'\1', block)
        block = re.sub(r'\\left\b', '', block)
        block = re.sub(r'\\right\b', '', block)

    # Balance \begin{env} / \end{env} for common math environments
    for env in ('array', 'aligned', 'cases', 'matrix', 'pmatrix', 'bmatrix'):
        n_begin = len(re.findall(rf'\\begin\{{{env}\}}', block))
        n_end = len(re.findall(rf'\\end\{{{env}\}}', block))
        while n_end < n_begin:
            block = block.rstrip() + f' \\end{{{env}}}'
            n_end += 1
        while n_begin < n_end:
            # Orphan \end — remove it
            block = re.sub(rf'\\end\{{{env}\}}', '', block, count=1)
            n_end -= 1

    return block


def heading_level_cmd(hashes: str, title: str) -> str:
    """Map markdown heading level to LaTeX sectioning command."""
    level = len(hashes)
    # Clean trailing page numbers from TOC-style headings
    title_clean = re.sub(r"\s+\d+\s*$", "", title).strip()

    if re.match(r"^Part\s+[IVXLCDM]+", title_clean):
        return f"\\part{{{title_clean}}}"
    if re.match(r"^\d+\s+", title_clean):
        # Chapter heading: "3 The Noiseless Quantum Theory"
        return f"\\chapter*{{{title_clean}}}"

    mapping = {1: "chapter*", 2: "section*", 3: "subsection*", 4: "subsubsection*"}
    cmd = mapping.get(level, "subsubsection*")
    return f"\\{cmd}{{{title_clean}}}"


def convert_md_to_tex(md_text: str, standalone: bool = True, graphics_prefix: str = "") -> str:
    """Convert a markdown string to LaTeX."""
    lines = md_text.split("\n")
    output = []
    in_display_math = False
    math_block = []

    if standalone:
        output.append(PREAMBLE)

    i = 0
    while i < len(lines):
        line = lines[i]

        # Page comments → LaTeX label
        m = PAGE_COMMENT.match(line.strip())
        if m:
            output.append(f"% --- page {m.group(1)} ---")
            i += 1
            continue

        # Display math block: \[ ... \]
        if DISPLAY_MATH_START.match(line.strip()):
            in_display_math = True
            math_block = [line]
            i += 1
            continue

        if in_display_math:
            math_block.append(line)
            if DISPLAY_MATH_END.match(line.strip()):
                in_display_math = False
                block = "\n".join(math_block)
                block = sanitize_display_math(block)
                output.append(block)
            i += 1
            continue

        # Headings
        m = HEADING.match(line.strip())
        if m:
            cmd = heading_level_cmd(m.group(1), m.group(2))
            output.append(f"\n{cmd}\n")
            i += 1
            continue

        # Figures
        m = FIGURE.match(line.strip())
        if m:
            alt_text = m.group(1)
            img_path = m.group(2)
            if graphics_prefix:
                img_path = os.path.join(graphics_prefix, img_path)
            output.append(f"\\begin{{figure}}[htbp]")
            output.append(f"\\centering")
            output.append(f"\\includegraphics[width=0.8\\textwidth]{{{img_path}}}")
            # Look ahead for caption
            if i + 1 < len(lines):
                cap_m = CAPTION.match(lines[i + 1].strip())
                if cap_m:
                    cap_text = escape_tex(cap_m.group(1))
                    output.append(f"\\caption{{{cap_text}}}")
                    i += 1  # skip caption line
            output.append(f"\\end{{figure}}")
            i += 1
            continue

        # Standalone caption (not after figure)
        m = CAPTION.match(line.strip())
        if m:
            output.append(f"\\textit{{{escape_tex(m.group(1))}}}")
            i += 1
            continue

        # Horizontal rule (footnote separator in OCR output)
        if HR.match(line.strip()):
            output.append("\\bigskip\\noindent\\rule{\\textwidth}{0.4pt}")
            i += 1
            continue

        # Empty line → paragraph break
        if not line.strip():
            output.append("")
            i += 1
            continue

        # Regular text — escape and pass through
        output.append(escape_tex(line))
        i += 1

    if standalone:
        output.append(POSTAMBLE)

    return "\n".join(output)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Convert OCR Markdown to compilable LaTeX.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("inputs", nargs="+", help="Input .md file(s)")
    parser.add_argument("-o", "--output", required=True, help="Output .tex file or directory")
    parser.add_argument("--no-standalone", action="store_true", help="Omit preamble/postamble (fragment mode)")
    parser.add_argument("--graphics-prefix", default="", help="Prefix for graphics paths")
    args = parser.parse_args()

    output_is_dir = args.output.endswith("/") or (os.path.isdir(args.output) and len(args.inputs) > 1)

    for md_path in args.inputs:
        with open(md_path) as f:
            md_text = f.read()

        standalone = not args.no_standalone
        tex = convert_md_to_tex(md_text, standalone=standalone, graphics_prefix=args.graphics_prefix)

        if output_is_dir:
            os.makedirs(args.output, exist_ok=True)
            tex_path = os.path.join(args.output, Path(md_path).stem + ".tex")
        else:
            os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
            tex_path = args.output

        with open(tex_path, "w") as f:
            f.write(tex)
        print(f"  {md_path} → {tex_path}")


if __name__ == "__main__":
    main()
