# Remote PC Setup Prompt

Copy and paste the entire text below into Claude Code on the remote PC.

---

I need you to create a local HTTP API server on this machine. This server will receive translation requests from another computer, pass the text to your local Claude CLI, and return the translation as a JSON response.

## What to build

Create a file called `translation_server.py` in the current directory. It must run a simple HTTP server (use only Python standard library — no Flask, no FastAPI). The server listens on port `5050` by default, with an optional `--port` argument.

## API contract

**Endpoint:** `POST /translate`

**Request body (JSON):**
```json
{
    "text": "<English novel chapter text>",
    "system_prompt": "<translation instructions>"
}
```

**Success response (JSON, HTTP 200):**
```json
{
    "success": true,
    "translation": "<full Bengali translated text>"
}
```

**Error response (JSON, HTTP 200):**
```json
{
    "success": false,
    "error": "<error message>"
}
```

## How to process each request

1. Parse the incoming JSON body and extract `text` and `system_prompt`.
2. Build a full prompt by combining them exactly like this:
   ```
   {system_prompt}

   === Original English Text ===

   {text}
   ```
3. Run the local Claude CLI with that prompt using subprocess:
   ```
   claude -p "<full_prompt>" --output-format text
   ```
   - Set a timeout of **1800 seconds** (30 minutes) on the subprocess call.
   - Capture stdout and stderr.
4. If Claude CLI exits with code 0, return `{"success": true, "translation": "<stdout>"}`.
5. If it fails or times out, return `{"success": false, "error": "<stderr or timeout message>"}`.
6. Clean the Claude output before returning:
   - Strip ANSI escape sequences.
   - If the output is wrapped in triple backticks (` ``` `), extract only the content inside.
   - Strip leading/trailing whitespace.

## Additional requirements

- Handle `POST /translate` only. For any other path or method return HTTP 404.
- Log each request to console: timestamp, method, path, response status.
- If the request body is not valid JSON or is missing `text`/`system_prompt`, return `{"success": false, "error": "invalid request"}` with HTTP 400.
- The server should handle one request at a time (no threading needed).
- Add a `--port` CLI argument (default: `5050`).

## How to start the server

After creating the file, start it with:
```bash
python translation_server.py
# or on a custom port:
python translation_server.py --port 8080
```

Print the local IP and port when the server starts so I know where to send requests.

Create the file now and then start the server.
