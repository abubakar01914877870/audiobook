"""
split_pdf.py — PDF Splitter Utility
====================================
Two modes:

  1. AUTO CHAPTER SPLIT (no --start/--end)
     Reads the PDF's built-in table of contents (bookmarks).
     If none found, scans page text for chapter headings.
     Saves one PDF file per chapter.

  2. MANUAL PAGE RANGE (--start N --end M)
     Extracts exactly pages N through M into a single new PDF.

Usage:
  python split_pdf.py <pdf> [--output FOLDER] [--start N] [--end M] [--name FILENAME]

Examples:
  python split_pdf.py book.pdf --output ./chapters
  python split_pdf.py book.pdf --start 10 --end 20 --output ./output
  python split_pdf.py book.pdf --start 10 --end 20 --output ./output --name my_section
"""

import fitz  # pymupdf
import argparse
import os
import re
import sys
from typing import Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sanitize_filename(name: str) -> str:
    """Remove characters unsafe for filenames, but preserve spaces."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f_]', '', name)
    name = re.sub(r'\s+', ' ', name.strip())
    return name[:80]


def get_toc_chapters(doc: fitz.Document):
    """
    Return [(title, start_page_1idx), ...] from the PDF's built-in TOC.
    Only top-level (level-1) entries are used as chapter boundaries.
    Returns None if the TOC is empty or has no level-1 entries.
    """
    toc = doc.get_toc()  # [[level, title, page_1idx], ...]
    chapters = [(title, page) for level, title, page in toc if level == 1 and page > 0]
    return chapters if len(chapters) > 1 else None


def detect_chapters_from_text(doc: fitz.Document):
    """
    Scan each page's text for a line matching a chapter heading pattern
    (e.g. "Chapter 1", "Chapter 214", "CHAPTER ONE").
    Returns [(heading_text, start_page_1idx), ...] or None if fewer than 2 found.
    """
    chapters = []
    pattern = re.compile(
        r'^(chapter[\s\-_]+[\divxlcXIVLC]+[\s\S]{0,80})',
        re.IGNORECASE
    )
    for page_num in range(len(doc)):
        text = doc[page_num].get_text().strip()
        # Only look at the first few lines of the page
        for line in text.splitlines()[:10]:
            line = line.strip()
            if pattern.match(line):
                chapters.append((line, page_num + 1))
                break  # one chapter per page

    return chapters if len(chapters) > 1 else None


# ---------------------------------------------------------------------------
# Mode 1 — auto chapter split
# ---------------------------------------------------------------------------

def split_by_chapters(pdf_path: str, output_dir: str):
    """Auto-split a PDF into individual chapter PDFs."""
    doc = fitz.open(pdf_path)
    total_pages = len(doc)

    # Try TOC first, fall back to text scan
    all_boundaries = get_toc_chapters(doc)
    if all_boundaries:
        print(f"Found {len(all_boundaries)} total bookmarks in TOC.")
    else:
        print("No usable TOC found. Scanning page text for chapter headings...")
        all_boundaries = detect_chapters_from_text(doc)

    if not all_boundaries:
        print("\n⚠ Could not detect chapters automatically.")
        print("  Options:")
        print("    • Use --start and --end to manually extract a page range.")
        print(f"    • This PDF has {total_pages} pages.")
        doc.close()
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)
    
    # Filter for chapter-like entries but keep all boundaries for end_page logic
    chapters_to_process = []
    for i, (title, start_page) in enumerate(all_boundaries):
        if re.match(r'^Chapter\s+\d+', title, re.IGNORECASE):
            end_page = all_boundaries[i + 1][1] - 1 if i + 1 < len(all_boundaries) else total_pages
            chapters_to_process.append((title, start_page, end_page))

    print(f"Splitting into {len(chapters_to_process)} chapters → {output_dir}\n")

    for i, (title, start_page, end_page) in enumerate(chapters_to_process):
        # Build output filename: chapter_483_Title.pdf
        # Try to parse "Chapter 483: Title" or "Chapter 483 Title"
        match = re.search(r'Chapter\s+(\d+)[:\s]*(.*)', title, re.IGNORECASE)
        if match:
            ch_num = match.group(1)
            ch_name = match.group(2).strip()
            safe_name = sanitize_filename(ch_name)
            if safe_name:
                out_filename = f"chapter_{ch_num}_{safe_name}.pdf"
            else:
                out_filename = f"chapter_{ch_num}.pdf"
        else:
            safe_title = sanitize_filename(title)
            out_filename = f"Chapter_{i + 1:03d}_{safe_title}.pdf"

        out_path = os.path.join(output_dir, out_filename)

        writer = fitz.open()
        writer.insert_pdf(doc, from_page=start_page - 1, to_page=end_page - 1)
        writer.save(out_path)
        writer.close()

        page_count = end_page - start_page + 1
        print(f"  ✓ [{i + 1:03d}] {title}  (pages {start_page}–{end_page}, {page_count} pg)  →  {out_filename}")

    doc.close()
    print(f"\nDone! {len(chapters_to_process)} chapter PDFs saved to: {output_dir}")


# ---------------------------------------------------------------------------
# Mode 2 — manual page range extraction
# ---------------------------------------------------------------------------

def split_page_range(pdf_path: str, start_page: int, end_page: int,
                     output_dir: str, output_name: Optional[str] = None):
    """Extract a specific page range from a PDF into a new PDF file."""
    doc = fitz.open(pdf_path)
    total_pages = len(doc)

    # Clamp
    if start_page < 1:
        print(f"Warning: start page {start_page} < 1, clamping to 1.")
        start_page = 1
    if end_page > total_pages:
        print(f"Warning: end page {end_page} > total pages ({total_pages}), clamping.")
        end_page = total_pages
    if start_page > end_page:
        print(f"Error: start page ({start_page}) must be ≤ end page ({end_page}).")
        doc.close()
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    if output_name:
        fname = output_name if output_name.lower().endswith('.pdf') else output_name + '.pdf'
    else:
        pdf_stem = os.path.splitext(os.path.basename(pdf_path))[0]
        fname = f"{pdf_stem}_pages_{start_page}_to_{end_page}.pdf"

    out_path = os.path.join(output_dir, fname)

    writer = fitz.open()
    writer.insert_pdf(doc, from_page=start_page - 1, to_page=end_page - 1)
    writer.save(out_path)
    writer.close()
    doc.close()

    page_count = end_page - start_page + 1
    print(f"✓ Extracted {page_count} page(s) ({start_page}–{end_page}) → {out_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Split a PDF by chapters (auto) or extract a page range (manual).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  # Auto-split by chapters (uses TOC bookmarks, falls back to text scan):
  python split_pdf.py "faceless vol 2/Faceless.pdf" --output ./chapters

  # Extract pages 10 to 20 into a new PDF:
  python split_pdf.py book.pdf --start 10 --end 20 --output ./output

  # Extract pages 10-20 with a custom output filename:
  python split_pdf.py book.pdf --start 10 --end 20 --output ./output --name chapter_intro
"""
    )

    parser.add_argument(
        "pdf",
        help="Path to the source PDF file"
    )
    parser.add_argument(
        "--output", "-o",
        default="./split_output",
        help="Output folder path (default: ./split_output)"
    )
    parser.add_argument(
        "--start", "-s",
        type=int,
        metavar="PAGE",
        help="Start page number (1-indexed) — enables manual range mode"
    )
    parser.add_argument(
        "--end", "-e",
        type=int,
        metavar="PAGE",
        help="End page number (1-indexed) — enables manual range mode"
    )
    parser.add_argument(
        "--name", "-n",
        metavar="FILENAME",
        help="Output filename (without .pdf) for range mode (optional)"
    )

    args = parser.parse_args()

    # Validate PDF exists
    if not os.path.isfile(args.pdf):
        print(f"Error: PDF not found: {args.pdf}")
        sys.exit(1)

    # Decide mode
    has_start = args.start is not None
    has_end = args.end is not None

    if has_start and has_end:
        # Manual range mode
        split_page_range(args.pdf, args.start, args.end, args.output, args.name)
    elif has_start or has_end:
        # Partial — error
        print("Error: Provide both --start and --end for range extraction.")
        print("  For a single page use: --start N --end N")
        sys.exit(1)
    else:
        # Auto chapter split mode
        split_by_chapters(args.pdf, args.output)


if __name__ == "__main__":
    main()
