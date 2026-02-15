import argparse
import subprocess
import os
import sys
import fitz  # pymupdf
from playwright.sync_api import sync_playwright

def extract_text_from_page(pdf_path, page_num):
    """
    Extracts text from a specific page of the PDF (1-indexed).
    """
    try:
        doc = fitz.open(pdf_path)
        # fitz uses 0-indexed pages
        page = doc.load_page(page_num - 1)
        text = page.get_text()
        doc.close()
        return text
    except Exception as e:
        print(f"Error extracting text from page {page_num}: {e}")
        return None

def translate_text_with_cli(text):
    """
    Translates text using the local 'gemini' CLI.
    """
    if not text or not text.strip():
        return ""

    # Constructed prompt based on user requirements
    prompt_instruction = """
You are a professional translator and creative novel writer. Translate the following text from English to Bangla.
Context: This is an English novel being translated into a Bangla novel for a Bangladeshi audience.

Instructions:
1. **STRICTLY NO HINDI WORDS.** Use only standard Bangla words.
2. Maintain the narrative flow and literary style of a novel.
3. For specific terms or context where the Bangla meaning might be ambiguous, you MUST include the original English word in parentheses immediately after the Bangla translation. Format: "বাংলা শব্দ (English Word)".
4. Do NOT translate word-for-word. Focus on capturing the emotion and theme.
5. Output ONLY the translated text. No explanations.

Text to translate:
"""
    # Combine instruction and text. 
    # Note: If the text is huge, we might hit CLI arg limits. 
    # But for a single page of a novel, it should be fine.
    
    # We will pass the instruction + text as the argument to "ask"
    full_prompt = f"{prompt_instruction}\n{text}"
    
    try:
        # Running the gemini cli command: gemini ask "..."
        # We pass the full prompt as a single argument.
        result = subprocess.run(
            ['gemini', 'ask', full_prompt],
            capture_output=True,
            text=True,
            encoding='utf-8'
        )

        if result.returncode != 0:
            print(f"Gemini CLI Error: {result.stderr}")
            if "429" in result.stderr:
                 print("Rate limit hit? Waiting a bit might help, but CLI typically handles its own logic or fails.")
            return None

        # valid output
        return result.stdout.strip()

    except Exception as e:
        print(f"Subprocess Execution Error: {e}")
        return None

def save_to_markdown(content, output_path):
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Markdown saved to: {output_path}")
    except Exception as e:
        print(f"Error saving Markdown: {e}")

def save_to_pdf_playwright(html_content, output_path):
    """
    Generates a PDF from HTML content using Playwright.
    """
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            
            # We set the content. 
            # We wait for network idle to ensure fonts (if remote) are loaded, 
            # though here we might be using system fonts or base64.
            page.set_content(html_content, wait_until='networkidle')
            
            # PDF options
            page.pdf(
                path=output_path,
                format="A4",
                margin={"top": "2cm", "bottom": "2cm", "left": "2cm", "right": "2cm"},
                print_background=True
            )
            browser.close()
        print(f"PDF saved to: {output_path}")

    except Exception as e:
        print(f"Error generating PDF with Playwright: {e}")

def create_html_content(translated_text, title):
    """
    Wraps the translated text in HTML with specific styling.
    """
    # Convert newlines to <p> or <br> for HTML rendering
    # Simple strategy: Double newline = paragraph. Single newline = <br> or ignore.
    # For a novel, usually double newline is a paragraph break.
    
    paragraphs = translated_text.split('\n\n')
    html_body = ""
    for para in paragraphs:
        if para.strip():
            html_body += f"<p>{para.strip()}</p>\n"

    html = f"""
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Hind+Siliguri:wght@300;400;500;600;700&display=swap');
        
        body {{
            font-family: 'Hind Siliguri', 'SolaimanLipi', sans-serif;
            font-size: 16px;
            line-height: 1.8;
            color: #333;
            max_width: 800px;
            margin: 0 auto;
            text-align: justify;
            text-justify: inter-word;
        }}
        
        h1 {{
            text-align: center;
            font-size: 24px;
            margin-bottom: 40px;
            color: #000;
        }}
        
        p {{
            margin-bottom: 20px;
            text-align: justify;
        }}
        
        /* Optional: Add page breaks for printing if needed, 
           but usually Playwright handles flow text well. */
        @media print {{
            body {{
                font-size: 12pt;
            }}
        }}
    </style>
</head>
<body>
    <h1>{title}</h1>
    {html_body}
</body>
</html>
"""
    return html

def main():
    parser = argparse.ArgumentParser(description="Extract, Translate (Gemini CLI), and PDF (Playwright).")
    parser.add_argument("input_file", help="Path to input PDF file")
    parser.add_argument("--start", type=int, default=1, help="Start page (1-indexed)")
    parser.add_argument("--end", type=int, required=True, help="End page (1-indexed)")
    parser.add_argument("--output_name", help="Base name for output files (without extension)", default="output")

    args = parser.parse_args()

    input_path = args.input_file
    if not os.path.exists(input_path):
        print(f"Error: Input file '{input_path}' not found.")
        return

    # Check for output directory
    output_dir = "translated_folder"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    full_translated_text = ""
    
    print(f"Processing '{input_path}' from page {args.start} to {args.end}...")

    for page_num in range(args.start, args.end + 1):
        print(f"--- Processing Page {page_num} ---")
        
        # 1. Extract
        source_text = extract_text_from_page(input_path, page_num)
        if not source_text:
            print(f"  [Warn] No text found on page {page_num}. Skipping.")
            continue

        print(f"  Text extracted ({len(source_text)} chars). Translating...")

        # 2. Translate
        translated_chunk = translate_text_with_cli(source_text)
        
        if translated_chunk:
            print("  Translation successful.")
            full_translated_text += f"\n\n<!-- Page {page_num} -->\n\n"
            full_translated_text += translated_chunk
        else:
            print(f"  [Error] Failed to translate page {page_num}.")
            full_translated_text += f"\n\n<!-- Page {page_num} (Translation Failed) -->\n\n"

    # 3. Save Markdown
    out_base = args.output_name
    md_filename = os.path.join(output_dir, f"{out_base}.md")
    save_to_markdown(full_translated_text, md_filename)

    # 4. Save PDF
    pdf_filename = os.path.join(output_dir, f"{out_base}.pdf")
    print("Generating PDF...")
    
    # We give the PDF a title based on the filename or user input
    doc_title = f"Translation: {os.path.basename(input_path)}"
    html_content = create_html_content(full_translated_text, doc_title)
    
    save_to_pdf_playwright(html_content, pdf_filename)
    
    print("Done.")

if __name__ == "__main__":
    main()
