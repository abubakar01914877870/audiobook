#!/usr/bin/env python3
"""
upload_youtube.py — Upload a chapter video to YouTube (private) with thumbnail.

Usage:
    python upload_youtube.py ./clown_vol_1/output/ch_16

Reads from the folder:
    *_youtube.mp4       — video to upload
    *_thumbnail.png     — custom thumbnail (requires verified YouTube channel)
    *_meta.md           — title + description

Saves on success:
    *_upload.json       — video ID and URL (prevents re-upload on re-run)

First run: opens browser for Google OAuth login → saves token.json.
All subsequent runs are headless.
"""

import os
import sys
import json
import re

# Fix for importlib.metadata.packages_distributions missing in Python < 3.11
try:
    import importlib_metadata as importlib_metadata_pkg
    import importlib.metadata
    if not hasattr(importlib.metadata, 'packages_distributions'):
        importlib.metadata.packages_distributions = importlib_metadata_pkg.packages_distributions
except ImportError:
    pass
import argparse
from pathlib import Path
from typing import Optional

# ── YouTube API imports ───────────────────────────────────────────────────────
try:
    import httplib2
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
except ImportError:
    print("Missing dependencies. Run:")
    print("  pip install google-api-python-client google-auth-oauthlib google-auth-httplib2")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

SCRIPT_DIR       = Path(__file__).parent.parent   # project root
CLIENT_SECRETS   = SCRIPT_DIR / "client_secrets.json"
TOKEN_FILE       = SCRIPT_DIR / "token.json"

CATEGORY_ID      = "24"      # Entertainment
DEFAULT_PRIVACY  = "public"
DEFAULT_PLAYLIST = ""
YOUTUBE_TAGS     = [
    "BanglaStory", "BanglaAudiobook", "LordOfTheMysteries",
    "BengaliTranslated", "রহস্যের_প্রভু", "Bengali", "Audiobook",
]


# ── Auth ──────────────────────────────────────────────────────────────────────

def get_authenticated_service():
    if not CLIENT_SECRETS.exists():
        print(f"Error: client_secrets.json not found at {CLIENT_SECRETS}")
        print("Download it from Google Cloud Console → APIs & Services → Credentials")
        sys.exit(1)

    creds = None

    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception:
            print("  token.json is corrupt — re-authenticating...")
            TOKEN_FILE.unlink()
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing access token...")
            creds.refresh(Request())
        else:
            print("Opening browser for Google login (one-time)...")
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS), SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        print(f"Token saved: {TOKEN_FILE}")

    return build("youtube", "v3", credentials=creds)


# ── File detection ────────────────────────────────────────────────────────────

def find_file(folder: Path, suffix: str) -> Optional[Path]:
    for f in sorted(folder.iterdir()):
        if f.is_file() and f.name.endswith(suffix):
            return f
    return None


# ── Meta parsing ──────────────────────────────────────────────────────────────

def parse_meta(meta_path: Path) -> tuple:
    """Extract title and description from *_meta.md. Returns (title, description)."""
    content = meta_path.read_text(encoding="utf-8")

    # Title: line starting with **Title:**
    title_match = re.search(r'\*\*Title:\*\*\s*(.+)', content)
    title = title_match.group(1).strip() if title_match else meta_path.stem.replace("_meta", "")

    # Description: everything after **Description:**
    desc_match = re.search(r'\*\*Description:\*\*\s*\n(.*)', content, re.DOTALL)
    description = desc_match.group(1).strip() if desc_match else ""

    return title, description


def extract_tags_from_description(description: str) -> list:
    """Pull #hashtags out of the description and merge with default tags."""
    hashtags = re.findall(r'#(\w+)', description)
    combined = YOUTUBE_TAGS + [t for t in hashtags if t not in YOUTUBE_TAGS]
    return combined[:500]   # YouTube tag list has a 500-char total limit; trim safely


# ── Playlist helpers ──────────────────────────────────────────────────────────

def find_playlist_id(youtube, name: str) -> Optional[str]:
    """Look up a playlist by exact name. Returns playlist ID or None."""
    request = youtube.playlists().list(part="snippet", mine=True, maxResults=50)
    while request:
        response = request.execute()
        for item in response.get("items", []):
            if item["snippet"]["title"].strip() == name.strip():
                return item["id"]
        request = youtube.playlists().list_next(request, response)
    return None


def add_to_playlist(youtube, video_id: str, playlist_id: str):
    """Insert the video as the last item in the given playlist."""
    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {
                    "kind": "youtube#video",
                    "videoId": video_id,
                },
            }
        },
    ).execute()
    print(f"  Added to playlist.")


# ── Upload ────────────────────────────────────────────────────────────────────

def sanitize_title(title: str) -> str:
    """Enforce YouTube title rules: strip forbidden chars, truncate to 100 chars."""
    # YouTube forbids < and > in titles
    title = title.replace("<", "").replace(">", "")
    # Hard limit: 100 Unicode code points
    if len(title) > 100:
        title = title[:100].rstrip()
    return title


def upload_video(youtube, video_path: Path, title: str, description: str,
                 privacy: str) -> str:
    """Upload the video. Returns the YouTube video ID."""
    title = sanitize_title(title)
    print(f"\nUploading: {video_path.name}")
    print(f"  Title      : {title}")
    print(f"  Privacy    : {privacy}")
    print(f"  File size  : {video_path.stat().st_size / 1024 / 1024:.1f} MB")

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": extract_tags_from_description(description),
            "categoryId": CATEGORY_ID,
            "defaultLanguage": "bn",
            "defaultAudioLanguage": "bn",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=10 * 1024 * 1024,   # 10 MB chunks
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    last_pct = -1
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            if pct != last_pct:
                bar = "#" * (pct // 2) + "-" * (50 - pct // 2)
                print(f"\r  [{bar}] {pct}%", end="", flush=True)
                last_pct = pct

    print(f"\r  [{'#' * 50}] 100%")
    return response["id"]


def compress_thumbnail(src: Path, max_bytes: int = 2 * 1024 * 1024) -> Path:
    """Resize and compress thumbnail to stay under YouTube's 2 MB limit.
    Saves a temporary JPEG next to the original and returns its path."""
    from PIL import Image
    import tempfile

    img = Image.open(src).convert("RGB")

    # Scale down to fit within YouTube's max dimension (2MB limit, max 1280px on longest side).
    # Use the longest-side cap so portrait images stay portrait at full height.
    MAX_SIDE = 1280
    if max(img.width, img.height) > MAX_SIDE:
        if img.height >= img.width:  # portrait
            scale = MAX_SIDE / img.height
        else:                        # landscape
            scale = MAX_SIDE / img.width
        new_w = int(img.width * scale)
        new_h = int(img.height * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    tmp = Path(tempfile.mktemp(suffix="_thumb.jpg"))
    quality = 92
    while quality >= 50:
        img.save(tmp, format="JPEG", quality=quality, optimize=True)
        if tmp.stat().st_size <= max_bytes:
            break
        quality -= 8

    size_kb = tmp.stat().st_size / 1024
    print(f"  Thumbnail compressed to {size_kb:.0f} KB (quality={quality})")
    return tmp


def upload_thumbnail(youtube, video_id: str, thumbnail_path: Path):
    """Upload custom thumbnail. Silently skips if channel is not verified."""
    print(f"  Uploading thumbnail: {thumbnail_path.name}")

    tmp_path = None
    try:
        upload_path = thumbnail_path
        if thumbnail_path.stat().st_size > 2 * 1024 * 1024:
            upload_path = compress_thumbnail(thumbnail_path)
            tmp_path = upload_path

        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(str(upload_path), mimetype="image/jpeg"),
        ).execute()
        print("  Thumbnail set.")
    except HttpError as e:
        if "forbidden" in str(e).lower() or "403" in str(e):
            print("  Thumbnail skipped — channel needs to be verified on YouTube.")
        else:
            print(f"  Thumbnail error: {e}")
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Upload chapter video to YouTube.")
    parser.add_argument("folder", help="Chapter output folder (e.g. ./clown_vol_1/output/ch_16)")
    parser.add_argument("--privacy", default=DEFAULT_PRIVACY,
                        choices=["private", "unlisted", "public"],
                        help="Video privacy (default: public)")
    parser.add_argument("--playlist", default=DEFAULT_PLAYLIST,
                        help=f"Playlist name to add video to (default: \"{DEFAULT_PLAYLIST}\"). Pass empty string to skip.")
    args = parser.parse_args()

    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        print(f"Error: '{folder}' is not a directory.")
        sys.exit(1)

    # ── Locate files ──────────────────────────────────────────────────────────
    video     = find_file(folder, "_youtube.mp4")
    meta      = find_file(folder, "_meta.md")
    thumbnail = find_file(folder, "_thumbnail.png") or find_file(folder, "_thumb.png")
    upload_record = find_file(folder, "_upload.json")

    if not video:
        print(f"No *_youtube.mp4 found in {folder} — skipping.")
        sys.exit(0)

    if not meta:
        print(f"Warning: no *_meta.md found — using filename as title.")

    # ── Skip if already uploaded ───────────────────────────────────────────────
    if upload_record:
        data = json.loads(upload_record.read_text())
        print(f"Already uploaded: https://youtu.be/{data['video_id']}")
        print("Delete *_upload.json to re-upload.")
        sys.exit(0)

    # ── Parse metadata ────────────────────────────────────────────────────────
    if meta:
        title, description = parse_meta(meta)
    else:
        title = video.stem.replace("_youtube", "").replace("_", " ")
        description = ""

    # ── Auth + upload ─────────────────────────────────────────────────────────
    youtube  = get_authenticated_service()
    video_id = upload_video(youtube, video, title, description, args.privacy)

    if thumbnail:
        upload_thumbnail(youtube, video_id, thumbnail)
    else:
        print("  No thumbnail found — skipping.")

    # ── Add to playlist ───────────────────────────────────────────────────────
    playlist_id = None
    if args.playlist:
        print(f"\n  Looking up playlist: \"{args.playlist}\"...")
        playlist_id = find_playlist_id(youtube, args.playlist)
        if playlist_id:
            print(f"  Playlist found: {playlist_id}")
            add_to_playlist(youtube, video_id, playlist_id)
        else:
            print(f"  Warning: playlist \"{args.playlist}\" not found — skipping.")

    # ── Save upload record ────────────────────────────────────────────────────
    url = f"https://youtu.be/{video_id}"
    record = {"video_id": video_id, "url": url, "title": title, "privacy": args.privacy}
    if playlist_id:
        record["playlist_id"] = playlist_id
        record["playlist_name"] = args.playlist
    record_path = folder / f"{video.stem}_upload.json"
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2))

    print(f"\n  Done!")
    print(f"  YouTube URL : {url}")
    print(f"  Privacy     : {args.privacy}")
    print(f"  Record saved: {record_path.name}")


if __name__ == "__main__":
    try:
        main()
    except HttpError as e:
        print(f"\nYouTube API error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
