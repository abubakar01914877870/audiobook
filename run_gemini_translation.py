import fitz
import subprocess
import os
import sys
import argparse

def extract_text(pdf_path):
    try:
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        return text.strip()
    except Exception as e:
        print(f"Error reading PDF: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="Translate a PDF chapter to Bengali using Gemini CLI.")
    parser.add_argument("pdf_input", help="Path to the source PDF file or filename")
    parser.add_argument("output_folder", help="Directory to save the translated markdown file")
    
    args = parser.parse_args()
    
    pdf_path = args.pdf_input
    output_dir = args.output_folder

    if not os.path.exists(pdf_path):
        print(f"Error: PDF file '{pdf_path}' not found.")
        sys.exit(1)

    if not os.path.exists(output_dir):
        print(f"Creating output folder: {output_dir}")
        os.makedirs(output_dir, exist_ok=True)

    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    output_md = os.path.join(output_dir, f"{base_name}.md")

    print(f"Extracting text from {pdf_path}...")
    text = extract_text(pdf_path)
    if not text:
        print("No text extracted. Aborting.")
        sys.exit(1)

    system_prompt = """Role: Expert Literary Translator (English to Bengali)
Style Reference: Mimic the writing style of Muhammed Zafar Iqbal's "Jolmanob" (simple, fluid, engaging, and teen-friendly).

Core Guidelines:
Target Audience: Teenagers. Use very simple, modern, and colloquial (Cholitobhasha) Bengali. Avoid archaic or heavy Sanskrit-based words.
Word Pairing Format: Avoid unnecessary English. Use the format translated_word (English_Word) only when the Bengali term is technical, rare, or potentially difficult for a teenager to grasp.
Naming & Addressing: * Keep character names consistent (e.g., ক্লেইন (Klein)).
Keep Novel and Chapter titles in the original format.
Strict Address Rule: Use informal/friendly pronouns like "সে" (Shey) or "তুমি" (Tumi). Never use formal "Apni" or "Tini".
Dialogue: Conversations must be direct, natural, and friendly—sounding like how people actually speak.
Atmosphere: Maintain a sense of mystery and thrill (Sci-Fi/Fantasy vibe) while ensuring the flow is seamless and easy to read.
Task: Replace the existing text entirely with the new translation. Do not provide summaries; provide a full, immersive narrative."""

    full_prompt = system_prompt + "\n\n=== Original English Text ===\n\n" + text

    print("Running gemini CLI (headless mode with -p flag)...")
    
    try:
        with open(output_md, "w", encoding="utf-8") as f:
            result = subprocess.run(
                ["gemini", "-p", full_prompt, "-y"],
                stdout=f,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                stdin=subprocess.DEVNULL
            )
        
        if result.returncode == 0:
            print(f"\nSuccess! Translation saved to: {output_md}")
            if os.path.exists(output_md):
                print(f"File size: {os.path.getsize(output_md)} bytes")
        else:
            print(f"Error (exit code {result.returncode}):")
            print("STDERR:", result.stderr)
    except Exception as e:
        print(f"Script error: {e}")

if __name__ == "__main__":
    main()
