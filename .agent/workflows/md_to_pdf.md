---
description: how to convert translated markdown files to Bengali PDF
---

## Convert .md → Bengali PDF

// turbo-all

1. Convert a single markdown file to PDF:

   ```bash
   python md_to_pdf.py "<path_to_md_file>"
   ```

   Example:

   ```bash
   python md_to_pdf.py "faceless vol 2/translated/Chapter_001_The Faceless.md"
   ```

2. Specify a custom output path:

   ```bash
   python md_to_pdf.py "input.md" --output "output_folder/Chapter_001.pdf"
   ```

3. If Playwright is not set up yet, install browsers first:
   ```bash
   playwright install
   ```

Output PDF will use the Bengali font configured in `md_to_pdf.py`.
