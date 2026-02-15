# Audiobook & Novel Translator (Bangla)

This project uses Google's Gemini AI to translate PDFs and Web Novels into Bangla.

## Setup

1.  **Clone the repository** (if applicable) or navigate to the project directory.

2.  **Create a virtual environment**:
    ```bash
    python3 -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

3.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

4.  **Configure API Key**:
    - Copy `.env.example` to `.env`:
      ```bash
      cp .env.example .env
      ```
    - Open `.env` and add your Google Gemini API key.

## Usage

To translate a specific range of pages from a PDF, use the `translate_pdf_playwright.py` script.

### Command Syntax

```bash
python translate_pdf_playwright.py "path/to/input.pdf" --start [START_PAGE] --end [END_PAGE] --output_name "output_filename"
```

### Arguments

- `input_file`: Path to the PDF file you want to translate.
- `--start`: The starting page number (1-indexed). Default is 1.
- `--end`: The ending page number (1-indexed). This argument is **required**.
- `--output_name`: The base name for the output Markdown and PDF files (without extension). Default is "output".

### Example

Here is an example command to translate pages 102 to 112 of "Clown - LotM Vol. 1.pdf":

```bash
python translate_pdf_playwright.py "input_file/Clown - LotM Vol. 1.pdf" --start 102 --end 112 --output_name "Chapter_9_The_Notebookpage_102_to_112"
```

The translated files will be saved in the `translated_folder/` directory.
