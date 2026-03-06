---
description: how to split a PDF into chapters or a page range
---

## Split a PDF

### Auto-split by chapters (reads TOC/bookmarks)

```bash
python split_pdf.py "<path_to_pdf>" --output "<output_folder>"
```

Example:

```bash
python split_pdf.py "faceless vol 2/Faceless - LotM Vol. 2.pdf" --output "faceless vol 2/chapter_split"
```

Output: `Chapter_01_Chapter Title.pdf`

---

### Extract a specific page range

```bash
python split_pdf.py "<path_to_pdf>" --start <N> --end <M> --output "<output_folder>"
```

Example — extract pages 10 to 20:

```bash
python split_pdf.py "book.pdf" --start 10 --end 20 --output "./output"
```

### Extract a range with a custom filename

```bash
python split_pdf.py "book.pdf" --start 10 --end 20 --output "./output" --name chapter_intro
```
