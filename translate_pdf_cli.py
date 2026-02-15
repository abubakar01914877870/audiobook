import argparse
import subprocess
import os
import time
import requests
import fitz  # pymupdf
from fpdf import FPDF
from fpdf.enums import XPos, YPos

# Verified link for a static Bangla font (Kalpurush)
FONT_URL = "https://github.com/maateen/avidrogo/raw/master/fonts/Kalpurush.ttf"
# Check if user has it installed
USER_FONT_PATH = os.path.expanduser("~/Library/Fonts/kalpurush.ttf")
if os.path.exists(USER_FONT_PATH):
    FONT_PATH = USER_FONT_PATH
else:
    FONT_PATH = "Kalpurush.ttf"

def download_font():
    """Download the Bangla font if it doesn't exist."""
    if not os.path.exists(FONT_PATH) or os.path.getsize(FONT_PATH) < 1000:
        print(f"Downloading static Bangla font ({FONT_PATH}) for PDF support...")
        try:
            r = requests.get(FONT_URL)
            if r.status_code == 200:
                with open(FONT_PATH, 'wb') as f:
                    f.write(r.content)
                print("Font downloaded.")
            else:
                 print(f"Failed to download font. Status code: {r.status_code}")
        except Exception as e:
            print(f"Error downloading font: {e}")

class BanglaPDF(FPDF):
    def header(self):
        # Select Arial bold 15 (Standard 14 fonts are not Unicode, but header is English usually)
        self.set_font('Helvetica', 'B', 15)
        # Move to the right
        self.cell(80)
        # Title
        # self.cell(30, 10, text='Translation', align='C') 
        # Line break
        self.ln(20)

def extract_page_text(doc, page_num):
    """
    Extracts text from a single page (1-indexed).
    """
    try:
        page = doc.load_page(page_num - 1)
        return page.get_text()
    except Exception as e:
        print(f"Error extracting text from page {page_num}: {e}")
        return None

def translate_with_cli(text, model=None):
    """
    Calls the local 'gemini' CLI to translate the text.
    """
    if not text.strip():
        return ""

    prompt = """
    You are a professional translator and novelist. 
    Translate the following text from English to Bangla.
    
    Guidelines:
    1. Maintain the narrative flow, tone, and literary style of a novel.
    2. Do not translate proper nouns if they sound awkward, or provide a transliteration.
    3. Output text in standard Bangla script.
    4. Do not include any introductory or concluding remarks, only the translation.
    5. Preserve formatting like paragraphs and dialogue.

    Text to translate:
    """
    
    full_input = prompt + "\n\n" + text
    
    cmd = ["gemini"] 
    
    try:
        cmd.append(full_input)
        
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            encoding='utf-8'
        )
        
        if result.returncode != 0:
            print(f"CLI Error: {result.stderr}")
            return None
            
        return result.stdout.strip()

    except Exception as e:
        print(f"Subprocess error: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="Translate PDF to Bangla using local Gemini CLI.")
    parser.add_argument("pdf_path", help="Path to the PDF file")
    parser.add_argument("--start", type=int, default=1, help="Start page number (1-indexed)")
    parser.add_argument("--end", type=int, default=1, help="End page number (1-indexed)")
    parser.add_argument("--output", help="Output Markdown/PDF filename base (optional)")
    
    args = parser.parse_args()

    # Determine output filename base
    if not args.output:
        base_name = os.path.splitext(os.path.basename(args.pdf_path))[0]
        output_base = f"{base_name}_translated_{args.start}_{args.end}"
    else:
        output_base = os.path.splitext(args.output)[0]

    # Create output directory
    output_dir = "translated_files"
    os.makedirs(output_dir, exist_ok=True)

    md_output = os.path.join(output_dir, output_base + ".md")
    pdf_output = os.path.join(output_dir, output_base + ".pdf")

    # Ensure font exists
    download_font()

    # Initialize PDF
    pdf = BanglaPDF()
    # Add the font. Unicode=True is default in fpdf2 for TTF
    try:
        # Use a generic name 'Bangla' for the added font
        pdf.add_font('Bangla', '', FONT_PATH)
    except FileNotFoundError:
        print(f"Font file '{FONT_PATH}' not found. PDF generation will fail.")
        return
    except Exception as e:
        print(f"Error adding font: {e}")
        return

    pdf.set_auto_page_break(auto=True, margin=15)

    # Open Source PDF
    try:
        doc = fitz.open(args.pdf_path)
    except Exception as e:
        print(f"Could not open PDF: {e}")
        return

    total_pages = len(doc)
    if args.start < 1: args.start = 1
    if args.end > total_pages: args.end = total_pages

    print(f"Processing pages {args.start} to {args.end}...")
    print(f"Outputs: {md_output}, {pdf_output}")

    # Clear MD file
    with open(md_output, 'w', encoding='utf-8') as f:
        f.write(f"# Translation of {os.path.basename(args.pdf_path)}\n\n")

    # Add Title Page to PDF
    pdf.add_page()
    pdf.set_font("Helvetica", size=24)
    # updated cell call to avoid deprecation warnings
    pdf.cell(200, 10, text=f"Translation: {os.path.basename(args.pdf_path)}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
    pdf.ln(20)
    
    # Switch to Bangla font
    pdf.set_font("Bangla", size=12)

    for page_num in range(args.start, args.end + 1):
        print(f"Reading page {page_num}...")
        text = extract_page_text(doc, page_num)
        
        if not text.strip():
            print(f"Page {page_num} is empty.")
            continue
            
        print(f"Translating page {page_num}...")
        translated = translate_with_cli(text)
        
        if translated:
            # Write to MD
            with open(md_output, 'a', encoding='utf-8') as f:
                f.write(f"\n\n## Page {page_num}\n\n")
                f.write(translated)
            
            # Write to PDF
            pdf.add_page()
            # Original page header (English is fine)
            pdf.set_font("Helvetica", size=10)
            pdf.cell(200, 10, text=f"Original Page {page_num}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
            pdf.ln(5)
            
            # Bangla Content
            pdf.set_font("Bangla", size=14)
            pdf.multi_cell(0, 10, text=translated)
            
            print("Done.")
        else:
            print("Translation failed for this page.")
        
        # small delay
        time.sleep(1)

    # Save PDF
    try:
        pdf.output(pdf_output)
        print(f"\nPDF Translation complete! Saved to {pdf_output}")
    except Exception as e:
        print(f"\nError saving PDF: {e}")
    
    print(f"Markdown Translation complete! Saved to {md_output}")

if __name__ == "__main__":
    main()
