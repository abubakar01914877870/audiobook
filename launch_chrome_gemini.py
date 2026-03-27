#!/usr/bin/env python3
"""Launch Chrome with the 'gemini' profile (Profile 9) including all extensions."""

import subprocess
import sys
import os

CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE_DIR = "/Users/abubakarsiddique/Library/Application Support/Google/Chrome"
PROFILE_NAME = "Profile 9"  # 'gemini' profile

def launch_chrome(url: str = None):
    args = [
        CHROME_PATH,
        f"--profile-directory={PROFILE_NAME}",
        f"--user-data-dir={PROFILE_DIR}",
    ]

    if url:
        args.append(url)

    print(f"Launching Chrome with profile: gemini ({PROFILE_NAME})")
    subprocess.Popen(args)

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else None
    launch_chrome(url)
