"""Transcribe video ads using OpenAI Whisper (runs locally, free).

Requires: pip install openai-whisper, and ffmpeg installed on the system.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import requests


def _download_video(url: str, dest: Path) -> bool:
    """Download a video URL to a local file. Returns True on success."""
    try:
        r = requests.get(url, stream=True, timeout=120)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
        return dest.stat().st_size > 0
    except Exception as e:
        print(f"      download error: {e}", flush=True)
        return False


def transcribe_videos(rows: list[dict]) -> list[dict]:
    """Add 'video_transcription' to each row that has a video creative.

    Modifies rows in place and returns them.
    Requires openai-whisper: pip install openai-whisper
    """
    try:
        import whisper
    except ImportError:
        print("  openai-whisper not installed. Run: pip install openai-whisper", flush=True)
        print("  skipping transcription.", flush=True)
        return rows

    print("  loading Whisper model (first run downloads ~140MB)...", flush=True)
    model = whisper.load_model("base")

    video_rows = [
        (i, r) for i, r in enumerate(rows)
        if any(c.get("type") == "video" for c in r.get("creative_urls", []))
    ]
    print(f"  transcribing {len(video_rows)} video ads (~10-15s each)...", flush=True)

    with tempfile.TemporaryDirectory() as tmp:
        for j, (i, row) in enumerate(video_rows, 1):
            video_url = next(
                c["url"] for c in row["creative_urls"] if c["type"] == "video"
            )
            ad_id = row.get("ad_library_id", f"ad_{i}")
            print(f"    [{j}/{len(video_rows)}] {row['facebook_page']} — {ad_id}", flush=True)

            video_path = Path(tmp) / f"{ad_id}.mp4"
            if not _download_video(video_url, video_path):
                continue

            try:
                result = model.transcribe(str(video_path), fp16=False)
                text = result.get("text", "").strip()
                rows[i]["video_transcription"] = text
                if text:
                    print(f"      \"{text[:80]}{'...' if len(text) > 80 else ''}\"", flush=True)
                else:
                    print(f"      (no speech detected)", flush=True)
            except Exception as e:
                print(f"      transcription error: {e}", flush=True)

    return rows
