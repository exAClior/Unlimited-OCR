"""
Post-process Unlimited-OCR page outputs into clean Markdown and LaTeX,
with figures extracted from the source PDF and chapter-level splitting.

Usage:
    # Single merged file (default)
    python postprocess.py \\
        --page_dir outputs/wilde_qit \\
        --pdf wilde_qit.pdf \\
        --output_dir outputs/wilde_qit_book

    # Also convert to LaTeX
    python postprocess.py \\
        --page_dir outputs/wilde_qit \\
        --pdf wilde_qit.pdf \\
        --output_dir outputs/wilde_qit_book \\
        --tex

Produces:
    outputs/wilde_qit_book/
        full.md                  # Merged clean Markdown
        chapters/
            00_frontmatter.md
            01_concepts_in_quantum_shannon_theory.md
            02_classical_shannon_theory.md
            ...
        figures/
            page_0050_fig_1.png
            ...
        tex/                     # (with --tex)
            full.tex
            chapters/
                00_frontmatter.tex
                ...
"""

import argparse
import os
import re
import shutil
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# Tag parsing
# ---------------------------------------------------------------------------

TAG_RE = re.compile(
    r"<\|det\|>(\w+) \[(\d+), (\d+), (\d+), (\d+)\]<\|/det\|>(.*?)(?=<\|det\|>|\Z)",
    re.DOTALL,
)

# The model emits coordinates in a 0-999 normalized grid.
MODEL_COORD_MAX = 999


def parse_page(text: str) -> list[dict]:
    """Parse a raw page into a list of detected elements."""
    elements = []
    for m in TAG_RE.finditer(text):
        elements.append(
            {
                "type": m.group(1),
                "bbox": (int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))),
                "content": m.group(6).strip(),
            }
        )
    return elements


# ---------------------------------------------------------------------------
# Figure extraction
# ---------------------------------------------------------------------------


def crop_figure(pdf_path: str, page_idx: int, bbox: tuple, out_path: str, dpi: int = 300):
    """Render a PDF page and crop the figure region defined by normalized bbox."""
    import fitz

    doc = fitz.open(pdf_path)
    page = doc[page_idx]
    page_rect = page.rect
    mat = fitz.Matrix(dpi / 72, dpi / 72)

    # Map normalized coords (0-999) -> PDF points
    px1 = bbox[0] / MODEL_COORD_MAX * page_rect.width
    py1 = bbox[1] / MODEL_COORD_MAX * page_rect.height
    px2 = bbox[2] / MODEL_COORD_MAX * page_rect.width
    py2 = bbox[3] / MODEL_COORD_MAX * page_rect.height

    # Pad 2% to avoid clipping edges
    pad_x = 0.02 * page_rect.width
    pad_y = 0.02 * page_rect.height
    clip = fitz.Rect(
        max(0, px1 - pad_x),
        max(0, py1 - pad_y),
        min(page_rect.width, px2 + pad_x),
        min(page_rect.height, py2 + pad_y),
    )

    cropped = page.get_pixmap(matrix=mat, clip=clip)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cropped.save(out_path)
    doc.close()


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------

HEADING_MAP = {
    "title": "##",
    "header": "##",
}


def elements_to_markdown(
    elements: list[dict],
    page_num: int,
    pdf_path: str | None,
    figure_dir: str | None,
    page_idx: int,
    figure_rel_prefix: str = "figures",
) -> str:
    """Convert parsed elements into clean Markdown."""
    parts = []
    fig_counter = 0

    for el in elements:
        typ = el["type"]
        content = el["content"]

        if typ == "page_number":
            continue

        if typ == "image":
            fig_counter += 1
            if pdf_path and figure_dir:
                fig_name = f"page_{page_num:04d}_fig_{fig_counter}.png"
                fig_path = os.path.join(figure_dir, fig_name)
                if not os.path.exists(fig_path):
                    crop_figure(pdf_path, page_idx, el["bbox"], fig_path)
                rel_path = f"{figure_rel_prefix}/{fig_name}"
                parts.append(f"\n![Figure]({rel_path})\n")
            else:
                parts.append("\n[Figure: image not extracted]\n")
            continue

        if typ == "image_caption":
            parts.append(f"\n*{content}*\n")
            continue

        if typ == "page_footnote":
            parts.append(f"\n---\n{content}\n")
            continue

        if typ in HEADING_MAP:
            prefix = HEADING_MAP[typ]
            parts.append(f"\n{prefix} {content}\n")
            continue

        if typ == "equation":
            parts.append(f"\n{content}\n")
            continue

        # text, table, reference, abstract, etc.
        parts.append(f"\n{content}\n")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Chapter detection
# ---------------------------------------------------------------------------

# Matches: "## 1 Title Here" or "## 26 Title Here"
CHAPTER_RE = re.compile(r"^## (\d+)\s+(.+)$", re.MULTILINE)
# Matches: "## Part I Title" or "## Part VII Title"
PART_RE = re.compile(r"^## (Part\s+[IVXLCDM]+)\s*(.*)$", re.MULTILINE)


def slugify(text: str) -> str:
    """Convert a title to a filename-safe slug."""
    text = text.lower().strip()
    # Remove trailing page numbers from TOC-style headings (e.g., "Title 123")
    text = re.sub(r"\s+\d+\s*$", "", text)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = text.strip("_")
    return text[:60]


def detect_toc_end(full_md: str) -> int:
    """Find where the table of contents ends and real content begins.

    Heuristic: the TOC region has many chapter/section headings packed
    densely with no substantial text between them. Real content has
    paragraphs of prose. We look for the first chapter heading that is
    followed by a real paragraph (>200 chars before the next heading).
    """
    # Find all chapter headings
    chapter_matches = list(CHAPTER_RE.finditer(full_md))
    for m in chapter_matches:
        # Check if this heading is followed by substantial text
        start = m.end()
        # Find next heading
        next_heading = re.search(r"^## ", full_md[start:], re.MULTILINE)
        if next_heading:
            between = full_md[start : start + next_heading.start()]
        else:
            between = full_md[start : start + 2000]

        # If there's substantial prose (not just other headings), this is real content
        prose = re.sub(r"^##.*$", "", between, flags=re.MULTILINE).strip()
        if len(prose) > 200:
            return m.start()

    return 0


def split_chapters(full_md: str) -> list[dict]:
    """Split the full markdown into chapters.

    Returns a list of dicts: {"number": "01", "title": "...", "slug": "...", "content": "..."}
    """
    # Find where real content begins (skip TOC)
    content_start = detect_toc_end(full_md)
    frontmatter = full_md[:content_start].strip()
    body = full_md[content_start:]

    chapters = []

    # Frontmatter (title pages, TOC, preface, etc.)
    if frontmatter:
        chapters.append({
            "number": "00",
            "title": "Front Matter",
            "slug": "00_frontmatter",
            "content": frontmatter,
        })

    # Find all chapter and part boundaries in body
    boundaries = []
    for m in CHAPTER_RE.finditer(body):
        ch_num = int(m.group(1))
        title = m.group(2).strip()
        # Skip TOC-like entries (short title + trailing number)
        boundaries.append({
            "pos": m.start(),
            "type": "chapter",
            "number": f"{ch_num:02d}",
            "title": title,
        })

    for m in PART_RE.finditer(body):
        boundaries.append({
            "pos": m.start(),
            "type": "part",
            "number": m.group(1),
            "title": m.group(2).strip() if m.group(2) else "",
        })

    # Sort by position
    boundaries.sort(key=lambda x: x["pos"])

    # Deduplicate: if a chapter heading appears twice within 5 lines, keep the later one
    # (the first is often a running header)
    deduped = []
    for b in boundaries:
        if deduped and b["type"] == "chapter" and deduped[-1]["type"] == "chapter":
            if b["number"] == deduped[-1]["number"]:
                # Same chapter number — keep the later one (likely the real heading)
                deduped[-1] = b
                continue
        deduped.append(b)
    boundaries = deduped

    # Extract content between boundaries
    for i, b in enumerate(boundaries):
        start = b["pos"]
        end = boundaries[i + 1]["pos"] if i + 1 < len(boundaries) else len(body)
        content = body[start:end].strip()

        if b["type"] == "part":
            # Parts are just dividers — prepend to next chapter or emit standalone
            slug = slugify(f"part_{b['number']}_{b['title']}")
            chapters.append({
                "number": b["number"].replace(" ", "_"),
                "title": f"{b['number']}: {b['title']}" if b['title'] else b['number'],
                "slug": slug,
                "content": content,
            })
        else:
            title_clean = re.sub(r"\s+\d+\s*$", "", b["title"])
            slug = f"{b['number']}_{slugify(title_clean)}"
            chapters.append({
                "number": b["number"],
                "title": f"Chapter {int(b['number'])}: {title_clean}",
                "slug": slug,
                "content": content,
            })

    return chapters


# ---------------------------------------------------------------------------
# LaTeX conversion
# ---------------------------------------------------------------------------


def md_to_tex(md_path: str, tex_path: str):
    """Convert a Markdown file to LaTeX using pandoc."""
    os.makedirs(os.path.dirname(tex_path) or ".", exist_ok=True)
    # Sanitize stray --- lines
    with open(md_path) as f:
        content = f.read()
    content = re.sub(r"^---\s*$", r"\\bigskip\\noindent\\rule{\\textwidth}{0.4pt}", content, flags=re.MULTILINE)
    cmd = [
        "pandoc", "-f", "markdown",
        "-o", tex_path,
        "--standalone",
        "-V", "documentclass=report",
        "-V", "geometry:margin=1in",
    ]
    subprocess.run(cmd, input=content, check=True, capture_output=True, text=True)


def md_to_tex_fragment(md_path: str, tex_path: str):
    """Convert a Markdown file to a LaTeX fragment (no preamble) using pandoc."""
    os.makedirs(os.path.dirname(tex_path) or ".", exist_ok=True)
    # Read, sanitize stray YAML-looking --- lines, pipe via stdin
    with open(md_path) as f:
        content = f.read()
    # Replace bare "---" lines (footnote separators) that pandoc misreads as YAML
    content = re.sub(r"^---\s*$", r"\\bigskip\\noindent\\rule{\\textwidth}{0.4pt}", content, flags=re.MULTILINE)
    cmd = ["pandoc", "-f", "markdown", "-o", tex_path]
    subprocess.run(cmd, input=content, check=True, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Post-process OCR outputs: merge, extract figures, split chapters, convert to LaTeX.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--page_dir", required=True, help="Directory of per-page .md files from infer.py")
    parser.add_argument("--pdf", default="", help="Source PDF for figure extraction")
    parser.add_argument("--output_dir", required=True, help="Output directory for all artifacts")
    parser.add_argument("--dpi", type=int, default=300, help="DPI for figure rendering")
    parser.add_argument("--tex", action="store_true", help="Also convert to LaTeX via pandoc")
    args = parser.parse_args()

    page_dir = Path(args.page_dir)
    page_files = sorted(page_dir.glob("*.md"))
    if not page_files:
        print(f"No .md files found in {page_dir}")
        return

    output_dir = Path(args.output_dir)
    figure_dir = output_dir / "figures"
    chapter_dir = output_dir / "chapters"
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(chapter_dir, exist_ok=True)

    pdf_path = args.pdf if args.pdf else None
    if pdf_path:
        os.makedirs(figure_dir, exist_ok=True)

    print(f"Pages: {len(page_files)}")
    print(f"PDF: {pdf_path or 'none (skipping figures)'}")
    print(f"Output: {output_dir}")

    # ------------------------------------------------------------------
    # Step 1: Parse all pages and build merged Markdown
    # ------------------------------------------------------------------
    print("\n[1/4] Parsing pages and extracting figures...")
    all_parts = []
    fig_total = 0

    for page_file in page_files:
        m = re.search(r"page_(\d+)", page_file.name)
        if not m:
            continue
        page_num = int(m.group(1))
        page_idx = page_num - 1

        with open(page_file) as f:
            raw = f.read()

        elements = parse_page(raw)
        if not elements:
            continue

        fig_total += sum(1 for el in elements if el["type"] == "image")

        md = elements_to_markdown(
            elements, page_num, pdf_path, str(figure_dir), page_idx,
            figure_rel_prefix="figures",
        )
        md = re.sub(r"\n{3,}", "\n\n", md).strip()

        if md:
            all_parts.append(f"\n\n<!-- page {page_num} -->\n\n{md}")

    full_md = "\n".join(all_parts).strip()
    full_md_path = output_dir / "full.md"
    with open(full_md_path, "w") as f:
        f.write(full_md)

    fig_count = len(list(figure_dir.glob("*.png"))) if pdf_path else 0
    print(f"  -> {full_md_path} ({len(full_md):,} chars, {full_md.count(chr(10))+1:,} lines)")
    if pdf_path:
        print(f"  -> {fig_count} figures extracted to {figure_dir}/")

    # ------------------------------------------------------------------
    # Step 2: Split into chapters
    # ------------------------------------------------------------------
    print("\n[2/4] Splitting into chapters...")
    chapters = split_chapters(full_md)

    for ch in chapters:
        ch_path = chapter_dir / f"{ch['slug']}.md"
        # Fix figure paths: chapters/ is one level deeper than output_dir
        content = ch["content"].replace("![Figure](figures/", "![Figure](../figures/")
        with open(ch_path, "w") as f:
            f.write(content)
        line_count = content.count("\n") + 1
        print(f"  {ch['slug']}.md  ({line_count:,} lines) — {ch['title']}")

    # ------------------------------------------------------------------
    # Step 3: Convert to LaTeX (optional)
    # ------------------------------------------------------------------
    if args.tex:
        pandoc_path = shutil.which("pandoc")
        if not pandoc_path:
            print("\n[3/4] Skipping LaTeX — pandoc not found. Install: apt install pandoc")
        else:
            print(f"\n[3/4] Converting to LaTeX (pandoc: {pandoc_path})...")
            tex_dir = output_dir / "tex"
            tex_chapter_dir = tex_dir / "chapters"
            os.makedirs(tex_chapter_dir, exist_ok=True)

            # Full book
            full_tex = tex_dir / "full.tex"
            try:
                md_to_tex(str(full_md_path), str(full_tex))
                print(f"  -> {full_tex}")
            except subprocess.CalledProcessError as e:
                print(f"  FAILED full.tex: {e.stderr[:200]}")

            # Per-chapter
            for ch in chapters:
                ch_md = chapter_dir / f"{ch['slug']}.md"
                ch_tex = tex_chapter_dir / f"{ch['slug']}.tex"
                try:
                    md_to_tex_fragment(str(ch_md), str(ch_tex))
                    print(f"  -> {ch_tex.name}")
                except subprocess.CalledProcessError as e:
                    print(f"  FAILED {ch_tex.name}: {e.stderr[:200]}")
    else:
        print("\n[3/4] Skipping LaTeX (use --tex to enable)")

    # ------------------------------------------------------------------
    # Step 4: Summary
    # ------------------------------------------------------------------
    print(f"\n[4/4] Done!")
    print(f"  Output directory: {output_dir}/")
    print(f"  ├── full.md              (merged Markdown)")
    print(f"  ├── chapters/            ({len(chapters)} chapter files)")
    print(f"  ├── figures/             ({fig_count} PNGs)")
    if args.tex:
        print(f"  └── tex/                 (LaTeX output)")
        print(f"       ├── full.tex")
        print(f"       └── chapters/       ({len(chapters)} chapter .tex files)")


if __name__ == "__main__":
    main()
