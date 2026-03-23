# Audiobook Translation Project Context

This file contains important context, rules, and architecture notes for the project. By reading this file, AI assistants can understand the user's preferences, the project's logic, and the tools used.

## 1. Project Overview

This project contains Python scripts designed to extract English text from PDF chapters and translate them into literary Bengali. The output is saved in both raw Markdown (`.md`) and Bengali-font embedded PDF (`.pdf`) formats.

## 2. Core Translation Scripts

- **`run_gemini_translation.py` (Main)**: Uses the local `gemini` CLI for translation. It dynamically fetches available Gemini AI models using the `google.genai` Python SDK and prioritizes them based on capability and speed (e.g., `gemini-3.1-pro`, `gemini-3-pro`, `gemini-2.5-pro` etc).
- **Fallback Logic**: If a model fails to translate, the script automatically attempts translation using the next prioritized model down the list.
- **`md_to_pdf.py` & PDF Scripts**: Used to generate High-Quality Bengali PDFs from the translated Markdown using a specified font (`Kalpurush.ttf` or system fonts).

## 3. Critical Rules for Code Modificaton

- **DO NOT USE LOCAL PYTHON CLEANING LOGIC**: The user STRICTLY prefers the clean formatted output to come directly from the `gemini` CLI natively. DO NOT attempt to write or use local Python stripping functions (e.g., `strip_ansi` or complex python regex extraction) to clean the CLI output. Let the CLI output its raw text directly to the file.
- **Python Compatibility**: The system runs Python 3.9+. When using `importlib.metadata`, account for `sys.version_info < (3, 10)` compatibility blocks (e.g., using `importlib_metadata` package).
- **Packages**: Prefers `google-genai` over the older `google-generativeai` for Python logic involving AI operations (like listing available models).

## 4. Translation Guidelines (`system_prompt` Configuration)

- **Role**: Expert Literary Translator (English to Bengali).
- **Style Reference**: Mimic the writing style of Muhammed Zafar Iqbal's "Jolmanob" (simple, fluid, engaging, and teen-friendly).
- **Target Audience**: Teenagers. Use very simple, modern, and colloquial (Cholitobhasha) Bengali. Avoid archaic or heavy Sanskrit-based words.
- **Pronouns & Strict Address Rule**: Use informal/friendly pronouns like "সে" (Shey) or "তুমি" (Tumi). NEVER use formal "Apni" or "Tini" when characters converse, unless context absolutely demands it.
- **Atmosphere**: Maintain a sense of mystery and thrill (Sci-Fi/Fantasy vibe) while ensuring the flow is seamless and easy to read.
- **Formatting Constraints**: No summaries allowed. Output must replace the existing text entirely with a full, immersive narrative translation.

## 6. Permissions & Autonomy

- **Full File Access**: Gemini has PERMANENT and EXPLICIT permission to create, read, update, and delete any files within this project folder (`/home/abubakar/Documents/audiobook/`). 
- **Autonomous Execution**: When a directive is issued to modify files, Gemini should proceed autonomously without seeking further confirmation for individual file operations, provided they align with the project's goals and existing rules.
- **Auto-Approval**: The user trusts Gemini's operations within this workspace. Gemini should act as a senior engineer with full write access to the codebase.
