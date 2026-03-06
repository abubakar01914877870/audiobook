---
description: how to translate PDF chapters to Bengali markdown
---

## Translate PDF chapters → Bengali .md files

// turbo-all

1. Check model quota before starting:

   ```bash
   gemini /stats
   ```

2. Run translation on a folder of chapter PDFs:

   ```bash
   python run_gemini_translation.py "<path_to_chapter_pdfs_folder>" "<output_folder>"
   ```

   Example:

   ```bash
   python run_gemini_translation.py "faceless vol 2/chapter_split" "faceless vol 2/translated"
   ```

3. The script will:
   - Check all model quotas in parallel
   - Skip already-translated chapters
   - Auto-fallback to the next model if one fails
   - Save output as `Chapter_001_Chapter Name.md`
