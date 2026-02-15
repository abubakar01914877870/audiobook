import os
import argparse
import fitz  # pymupdf
import warnings
import google.generativeai as genai
from dotenv import load_dotenv

# Suppress deprecation warnings from google libraries
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

# Load environment variables
load_dotenv()

# Configure Gemini
API_KEY = os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    print("Error: GOOGLE_API_KEY not found in .env file.")
    print("Please create a .env file with your API key.")
    exit(1)

genai.configure(api_key=API_KEY)

def get_model(model_name='gemini-2.0-flash'):
    try:
        return genai.GenerativeModel(model_name)
    except Exception as e:
        print(f"Error loading model {model_name}: {e}")
        # Fallback
        return genai.GenerativeModel('gemini-2.0-flash')

# Global model variable will be set in main
model = None

def extract_text(pdf_path, start_page, end_page):
    """
    Extracts text from a PDF within the given page range.
    Pages are 1-indexed.
    """
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"Error opening PDF: {e}")
        return None

    full_text = ""
    total_pages = len(doc)
    
    # Validate page numbers
    if start_page < 1: start_page = 1
    if end_page > total_pages: end_page = total_pages
    
    print(f"Extracting text from pages {start_page} to {end_page}...")

    for page_num in range(start_page - 1, end_page):
        page = doc.load_page(page_num)
        text = page.get_text()
        full_text += f"\n\n--- Page {page_num + 1} ---\n\n"
        full_text += text

    return full_text

import time

def chunk_text(text, chunk_size=3000):
    """
    Splits text into chunks of approximately chunk_size characters, 
    trying to break at newlines to preserve sentence structure.
    """
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break
        
        # Try to find the last newline within the chunk to avoid breaking sentences
        last_newline = text.rfind('\n', start, end)
        if last_newline != -1 and last_newline > start + chunk_size // 2:
            end = last_newline + 1
        
        chunks.append(text[start:end])
        start = end
    return chunks

def translate_chunk_with_retry(chunk, retries=5, delay=10):
    """
    Translates a single chunk with retry logic for 429 errors.
    """
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

    for attempt in range(retries):
        try:
            response = model.generate_content(prompt + chunk)
            return response.text
        except Exception as e:
            if "429" in str(e):
                wait_time = delay * (2 ** attempt) # Exponential backoff
                print(f"Quota exceeded. Retrying in {wait_time} seconds... (Attempt {attempt + 1}/{retries})")
                time.sleep(wait_time)
            else:
                print(f"Translation error: {e}")
                return None
    return None

def translate_text(text):
    """
    Translates the given text to Bangla using Gemini, handling chunks.
    """
    if not text.strip():
        return ""

    print("Sending text to Gemini for translation...")
    
    chunks = chunk_text(text)
    translated_full = ""
    
    total_chunks = len(chunks)
    for i, chunk in enumerate(chunks):
        print(f"Translating chunk {i+1}/{total_chunks}...")
        translated_chunk = translate_chunk_with_retry(chunk)
        if translated_chunk:
            translated_full += translated_chunk + "\n"
        else:
            print(f"Failed to translate chunk {i+1}. Stopping.")
            break
            
        # Optional: small sleep between successful chunks to be nice to the API
        time.sleep(2)

    return translated_full

def save_to_markdown(text, output_path):
    """
    Saves the translated text to a markdown file.
    """
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f"Successfully saved translation to: {output_path}")
    except Exception as e:
        print(f"Error saving file: {e}")

def main():
    parser = argparse.ArgumentParser(description="Translate PDF pages to Bangla.")
    parser.add_argument("pdf_path", help="Path to the PDF file")
    parser.add_argument("--start", type=int, default=1, help="Start page number (1-indexed)")
    parser.add_argument("--end", type=int, default=1, help="End page number (1-indexed)")
    parser.add_argument("--output", help="Output Markdown file path (optional)")
    parser.add_argument("--model", default="gemini-2.0-flash", help="Gemini model to use (e.g., gemini-2.5-pro)")

    args = parser.parse_args()
    
    global model
    model = get_model(args.model)

    # Determine output filename if not provided
    if not args.output:
        base_name = os.path.splitext(os.path.basename(args.pdf_path))[0]
        args.output = f"{base_name}_translated_{args.start}_{args.end}.md"

    # 1. Extract
    source_text = extract_text(args.pdf_path, args.start, args.end)
    if not source_text:
        return

    # 2. Translate
    # Note: For very large ranges, we might want to split this. 
    # But for a "range" designated by user, we process as one block for now.
    translated_text = translate_text(source_text)
    
    if translated_text:
        # 3. Save
        save_to_markdown(translated_text, args.output)

if __name__ == "__main__":
    main()
