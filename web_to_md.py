import argparse
import subprocess
import os
import sys
import re
from playwright.sync_api import sync_playwright

def get_page_content(url):
    """
    Fetches the raw text content of a webpage using Playwright.
    Returns a dictionary with 'title' and 'text'.
    """
    print(f"Fetching content from: {url}")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(url, wait_until='networkidle')
            
            # extract title
            title = page.title()
            
            # extract body text - we'll let Gemini filter the noise
            # inner_text preserves some structure compared to text_content
            body_text = page.inner_text("body") 
            
            browser.close()
            
            return {
                "title": title,
                "text": body_text
            }
    except Exception as e:
        print(f"Error fetching page: {e}")
        return None

def filter_with_gemini(raw_text):
    """
    Uses Gemini CLI to extract the story and format it as Markdown.
    """
    print("Filtering and formatting content with Gemini CLI...")
    
    prompt = """
You are an expert editor and formatter. I will provide you with raw text scraped from a webpage containing a Bangla story. 
Your task is to:
1. Identify the Main Title of the story.
2. Extract the full Story content.
3. Remove all "noise" such as navigation menus, advertisements, sidebar links, social media buttons, footer text, and copyright notices.
4. Format the output as clean Markdown.
   - Use # for the Main Title.
   - Use paragraph breaks appropriately.
   - Maintain the original Bangla text and any necessary formatting (bold/italics) if present in the narrative.
   
RETURN ONLY THE MARKDOWN CONTENT. Do not include any introductory or concluding remarks.

Raw Text:
"""
    
    full_prompt = f"{prompt}\n{raw_text}"
    
    # Check for argument length limits roughly (macOS arg limit is high, but good to be aware)
    if len(full_prompt) > 100000:
        print("Warning: Input text is very long. Truncating to safe limit for CLI to avoid errors (this might cut the story).")
        full_prompt = full_prompt[:100000]

    try:
        result = subprocess.run(
            ['gemini', 'ask', full_prompt],
            capture_output=True,
            text=True,
            encoding='utf-8'
        )

        if result.returncode != 0:
            print(f"Gemini CLI Error: {result.stderr}")
            return None
            
        return result.stdout.strip()
        
    except Exception as e:
        print(f"Error calling Gemini CLI: {e}")
        return None

def sanitize_filename(name):
    """
    Sanitizes a string to be safe for filenames.
    """
    # Remove invalid chars
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    # Replace spaces with underscores or hyphens
    name = name.replace(" ", "_").strip()
    return name

def main():
    parser = argparse.ArgumentParser(description="Scrape a Bangla story from a webpage and save as Markdown.")
    parser.add_argument("url", help="The URL of the webpage to scrape")
    parser.add_argument("--output_dir", default="scraped_stories", help="Directory to save the output file")
    
    args = parser.parse_args()
    
    # 1. Get Content
    content_data = get_page_content(args.url)
    if not content_data:
        print("Failed to retrieve content.")
        sys.exit(1)
        
    print(f"Page Title Found: {content_data['title']}")
    
    # 2. Filter with Gemini
    # We strip huge whitespace to save some tokens/arg space
    clean_raw_text = re.sub(r'\n\s*\n', '\n', content_data['text'])
    
    filtered_md = filter_with_gemini(clean_raw_text)
    
    if not filtered_md:
        print("Failed to filter content with Gemini.")
        sys.exit(1)
    
    # 3. Save File
    # Extract title from Markdown if possible (first line # Title)
    # otherwise use page title
    lines = filtered_md.split('\n')
    file_title = content_data['title']
    
    if lines and lines[0].startswith('# '):
        # Use the title from the markdown content ensuring it's not too long
        extracted_title = lines[0].replace('# ', '').strip()
        if len(extracted_title) < 100: # sanity check
            file_title = extracted_title

    safe_filename = sanitize_filename(file_title) + ".md"
    
    if not os.path.exists(args.output_dir):
        os.makedirs(args.output_dir)
        
    output_path = os.path.join(args.output_dir, safe_filename)
    
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(filtered_md)
        print(f"Success! Story saved to: {output_path}")
    except Exception as e:
        print(f"Error saving file: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
