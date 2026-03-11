import fitz
import os
import sys
import argparse
import time
import re
import json
import urllib.request
import urllib.error


def resolve_path(path):
    """If path doesn't exist, scan parent dir for a file whose name matches
    after normalizing Unicode quote variants (e.g. curly apostrophe U+2019 vs ASCII apostrophe)."""
    if os.path.exists(path):
        return path

    def normalize_quotes(s):
        return re.sub(r"[\u2018\u2019\u201a\u201b\u2032\u2035\u0060\u00b4']", "'", s)

    parent = os.path.dirname(path) or '.'
    target = normalize_quotes(os.path.basename(path)).lower()
    try:
        for entry in os.listdir(parent):
            if normalize_quotes(entry).lower() == target:
                return os.path.join(parent, entry)
    except OSError:
        pass
    return path


def extract_text(pdf_path):
    """Extract and clean text from a PDF file."""
    try:
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        return clean_pdf_text(text)
    except Exception as e:
        print(f"Error reading PDF {pdf_path}: {e}")
        return None


def clean_pdf_text(text):
    """Remove PDF noise (page numbers, repeated blanks) to reduce input tokens."""
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if re.fullmatch(r'[-–—]?\s*\d+\s*[-–—]?', stripped):
            continue
        cleaned.append(stripped)
    result = re.sub(r'\n{3,}', '\n\n', '\n'.join(cleaned))
    return result.strip()


def clean_translation(text):
    """Clean the API response to contain only the translated text."""
    # Remove ANSI escape sequences
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    text = ansi_escape.sub('', text)

    # Extract from triple backtick code blocks if present
    code_blocks = re.findall(r'```(?:markdown)?\n?(.*?)\n?```', text, re.DOTALL)
    if code_blocks:
        code_blocks.sort(key=len, reverse=True)
        text = code_blocks[0]

    return text.strip()


def call_translation_api(api_url, english_text, system_prompt, retries=3):
    """Send a POST request to the remote translation API.

    Request body (JSON):
        {
            "text": "<extracted English chapter text>",
            "system_prompt": "<translation instructions>"
        }

    Expected response body (JSON):
        {
            "success": true,
            "translation": "<Bengali translated text>"
        }

    Returns the cleaned translation string, or None if all attempts fail.
    """
    payload = json.dumps({
        "text": english_text,
        "system_prompt": system_prompt
    }).encode("utf-8")

    for attempt in range(1, retries + 1):
        try:
            print(f"  API call attempt {attempt}/{retries} → {api_url}")
            req = urllib.request.Request(
                api_url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                },
                method="POST"
            )

            with urllib.request.urlopen(req, timeout=1800) as resp:
                raw = resp.read().decode("utf-8")

            data = json.loads(raw)

            if not isinstance(data, dict):
                print(f"  Unexpected response format (not a JSON object).")
                raise ValueError("Response is not a JSON object")

            if not data.get("success", False):
                error_msg = data.get("error", "unknown error")
                print(f"  API returned success=false: {error_msg}")
                raise ValueError(f"API error: {error_msg}")

            translation = data.get("translation", "").strip()
            if not translation:
                print(f"  API returned empty translation.")
                raise ValueError("Empty translation in response")

            return clean_translation(translation)

        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code} error: {e.reason}")
            try:
                body = e.read().decode("utf-8")
                print(f"  Response body: {body[:300]}")
            except Exception:
                pass
        except urllib.error.URLError as e:
            print(f"  URL error (connection failed): {e.reason}")
        except TimeoutError:
            print(f"  Request timed out (attempt {attempt}/{retries}).")
        except json.JSONDecodeError as e:
            print(f"  Failed to parse JSON response: {e}")
        except KeyboardInterrupt:
            print("\nInterrupted by user. Exiting...")
            sys.exit(1)
        except Exception as e:
            print(f"  Exception: {e}")

        if attempt < retries:
            wait = 10 * attempt
            print(f"  Retrying in {wait}s...")
            time.sleep(wait)

    print("  All retry attempts exhausted.")
    return None


def build_output_filename(pdf_path):
    """Build output .md filename from the PDF stem."""
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    return f"{stem}.md"


def translate_file(pdf_path, output_dir, api_url, system_prompt):
    """Translate a single PDF chapter via remote API and save as .md."""
    output_filename = build_output_filename(pdf_path)
    output_md = os.path.join(output_dir, output_filename)

    if os.path.exists(output_md):
        print(f"Skipping {pdf_path} — output already exists: {output_md}")
        return True

    print(f"\n--- Processing: {pdf_path} ---")
    print(f"Output file: {output_filename}")

    text = extract_text(pdf_path)
    if not text:
        print(f"Skipping {pdf_path} — no text extracted.")
        return False

    print(f"Sending to API ({len(text)} chars)...")
    translated_text = call_translation_api(api_url, text, system_prompt)

    if translated_text:
        with open(output_md, "w", encoding="utf-8") as f:
            f.write(translated_text)
        print(f"Success! Saved: {output_md} ({os.path.getsize(output_md)} bytes)")
        return True
    else:
        print(f"Translation failed for {pdf_path}.")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Translate PDF chapter(s) to Bengali via a remote translation API."
    )
    parser.add_argument("pdf_input", help="Path to a PDF file or a directory of PDFs")
    parser.add_argument("output_folder", help="Directory to save translated .md files")
    parser.add_argument("--api-url", required=True, help="Remote translation API endpoint URL (e.g. http://192.168.1.10:5050/translate)")

    args = parser.parse_args()

    pdf_input = resolve_path(args.pdf_input)
    output_dir = args.output_folder
    api_url = args.api_url.rstrip("/")

    if not os.path.exists(pdf_input):
        print(f"Error: Input '{pdf_input}' not found.")
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    system_prompt = """Translate the English novel chapter below into Bengali (Cholitobhasha).
Style: Muhammed Zafar Iqbal — simple, fluid, teen-friendly. No archaic or Sanskrit-heavy words.
Pronouns: সে/তুমি only. Never আপনি/তিনি.
Names: keep consistent. Novel/chapter titles: keep original format.
English terms: use বাংলা (English) format only for technical or rare words.
Dialogue: natural, direct, colloquial.
Output: full translation only — no summary, no commentary, no preamble."""

    # Collect PDFs
    pdf_files = []
    if os.path.isdir(pdf_input):
        pdf_files = sorted([
            os.path.join(pdf_input, f)
            for f in os.listdir(pdf_input)
            if f.lower().endswith(".pdf")
        ])
        print(f"Found {len(pdf_files)} PDF(s) in: {pdf_input}")
    else:
        pdf_files = [pdf_input]

    if not pdf_files:
        print("No PDF files found to process.")
        return

    failed = []

    try:
        for i, pdf_path in enumerate(pdf_files):
            print(f"\nProgress: {i+1}/{len(pdf_files)}")
            success = translate_file(pdf_path, output_dir, api_url, system_prompt)
            if not success:
                failed.append(pdf_path)
            if i < len(pdf_files) - 1:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\nInterrupted by user. Exiting...")
        sys.exit(1)

    print("\n--- All tasks completed ---")
    if failed:
        print(f"Failed ({len(failed)}):")
        for f in failed:
            print(f"  - {f}")
    else:
        print("All files translated successfully.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user. Exiting...")
        sys.exit(1)
