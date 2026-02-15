import argparse
import os
import re
import sys
from playwright.sync_api import sync_playwright

def md_to_html(md_text):
    """
    Converts basic Markdown to HTML using line-by-line processing.
    Supports: Headers, Bold, Italics, Paragraphs, Lists.
    """
    html_parts = []
    lines = md_text.split('\n')
    
    current_paragraph = []
    current_list = []
    
    def flush():
        nonlocal current_paragraph, current_list
        if current_list:
            html_parts.append(f"<ul>{''.join(current_list)}</ul>")
            current_list = []
        if current_paragraph:
            # Join lines with space for standard markdown paragraph wrapping
            p_content = " ".join(current_paragraph).strip()
            if p_content:
                html_parts.append(f"<p>{process_inline(p_content)}</p>")
            current_paragraph = []

    for line in lines:
        stripped_line = line.strip()
        
        # Empty line -> Flush current block
        if not stripped_line:
            flush()
            continue
            
        # Headers
        header_match = re.match(r'^(#{1,6})\s+(.*)', stripped_line)
        if header_match:
            flush()
            level = len(header_match.group(1))
            content = header_match.group(2).strip()
            html_parts.append(f"<h{level}>{process_inline(content)}</h{level}>")
            continue
            
        # List items
        if stripped_line.startswith('- ') or stripped_line.startswith('* '):
            if current_paragraph:
                flush()
            current_list.append(f"<li>{process_inline(stripped_line[2:].strip())}</li>")
            continue
        
        # If we are in a list but this line is not a list item, flush the list
        if current_list:
            flush()

        # Text line (accumulate into paragraph)
        current_paragraph.append(stripped_line)
        
    # Final flush
    flush()

    return "\n".join(html_parts)

def process_inline(text):
    """
    Processes inline Markdown elements: Bold, Italics.
    """
    # Bold **text** or __text__
    text = re.sub(r'(\*\*|__)(.*?)\1', r'<strong>\2</strong>', text)
    
    # Italics *text* or _text_
    text = re.sub(r'(\*|_)(.*?)\1', r'<em>\2</em>', text)
    
    # Code `text`
    text = re.sub(r'`(.*?)`', r'<code>\1</code>', text)
    
    return text

def create_html_document(body_content, title="Document"):
    """
    Wraps the HTML body with specific styling for Bangla.
    """
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
            padding: 20px;
            text-align: justify;
            text-justify: inter-word;
        }}
        
        h1, h2, h3, h4, h5, h6 {{
            color: #000;
            margin-top: 1.5em;
            margin-bottom: 0.5em;
            text-align: center; /* Generally centers headers in this style */
        }}

        h1 {{ font-size: 28px; font-weight: 700; }}
        h2 {{ font-size: 24px; font-weight: 600; }}
        h3 {{ font-size: 20px; font-weight: 600; }}
        
        p {{
            margin-bottom: 1.5em;
            text-align: justify;
        }}
        
        strong {{
            font-weight: 700;
        }}
        
        em {{
            font-style: italic;
        }}

        ul {{
            margin-bottom: 1.5em;
            padding-left: 20px;
        }}
        
        li {{
            margin-bottom: 0.5em;
        }}

        /* Print styles to ensure good PDF output */
        @media print {{
            body {{
                font-size: 12pt;
                max_width: 100%;
                padding: 0;
            }}
            
            p {{
                orphans: 3;
                widows: 3;
            }}
            
            h1, h2, h3 {{
                page-break-after: avoid;
            }}
        }}
    </style>
</head>
<body>
    {body_content}
</body>
</html>
"""
    return html

def generate_pdf(html_content, output_path):
    """
    Generates a PDF from HTML strings using Playwright.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            
            # Set content and wait for network idle (fonts)
            page.set_content(html_content, wait_until='networkidle')
            
            # PDF options
            page.pdf(
                path=output_path,
                format="A4",
                margin={"top": "2cm", "bottom": "2cm", "left": "2cm", "right": "2cm"},
                print_background=True
            )
            browser.close()
        print(f"Successfully created PDF: {output_path}")
        return True
    except Exception as e:
        print(f"Error generating PDF: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Convert Markdown file to PDF using Playwright (with Bangla font support).")
    parser.add_argument("input_file", help="Path to input Markdown file (.md)")
    parser.add_argument("--output", "-o", help="Path/Filename for output PDF (optional). Defaults to input filename with .pdf extension.")

    args = parser.parse_args()
    
    input_path = os.path.abspath(args.input_file)
    
    if not os.path.exists(input_path):
        print(f"Error: File not found: {input_path}")
        sys.exit(1)
        
    # Determine output path
    if args.output:
        output_path = args.output
        if not output_path.lower().endswith('.pdf'):
            output_path += ".pdf"
    else:
        # Default: same directory, same basename, .pdf extension
        folder = os.path.dirname(input_path)
        basename = os.path.splitext(os.path.basename(input_path))[0]
        output_path = os.path.join(folder, f"{basename}.pdf")

    print(f"Converting '{input_path}' to '{output_path}'...")
    
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            md_content = f.read()
            
        # Convert to HTML
        html_body = md_to_html(md_content)
        
        # Wrap in Full HTML Document with CSS
        title = os.path.splitext(os.path.basename(input_path))[0].replace('_', ' ').title()
        full_html = create_html_document(html_body, title=title)
        
        # Generate PDF
        success = generate_pdf(full_html, output_path)
        
        if success:
            sys.exit(0)
        else:
            sys.exit(1)
            
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
