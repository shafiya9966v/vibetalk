"""
Utility script to convert MP4 and other audio/video formats to WAV (16kHz, 16-bit, mono).
This format is universally supported and optimal for Gnani Prisma STT and other speech models.

Usage:
    # Convert a single file:
    python convert_audio.py english.mp4

    # Convert all .mp4 files in the current folder:
    python convert_audio.py --all
"""

import os
import sys
import subprocess
from pathlib import Path


def convert_to_wav(input_path: str, output_path: str | None = None) -> str:
    """Converts an audio/video file to 16kHz mono WAV using ffmpeg."""
    in_file = Path(input_path)
    if not in_file.exists():
        raise FileNotFoundError(f"File not found: {input_path}")

    if output_path is None:
        out_file = in_file.with_suffix(".wav")
    else:
        out_file = Path(output_path)

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(in_file),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(out_file),
    ]

    print(f"Converting '{in_file.name}' -> '{out_file.name}' (16kHz, mono WAV)...")
    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        print(f"  [OK] Saved: {out_file.name}")
        return str(out_file)
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode("utf-8", errors="ignore")
        print(f"  [ERROR] Conversion failed for '{in_file.name}':\n{err_msg}")
        raise
    except FileNotFoundError:
        print("  [ERROR] 'ffmpeg' was not found in your PATH.")
        print("    Please install FFmpeg to convert media files.")
        raise


def convert_all_mp4_in_dir(directory: str = ".") -> None:
    """Converts all .mp4 files in the given directory to .wav."""
    dir_path = Path(directory)
    mp4_files = sorted(dir_path.glob("*.mp4"))

    if not mp4_files:
        print(f"No .mp4 files found in {dir_path.resolve()}")
        return

    print(f"Found {len(mp4_files)} .mp4 file(s) to convert in {dir_path.resolve()}:")
    converted = 0
    for file in mp4_files:
        try:
            convert_to_wav(str(file))
            converted += 1
        except Exception as e:
            print(f"Skipping {file.name} due to error: {e}")

    print(f"\nDone! Successfully converted {converted}/{len(mp4_files)} files.")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage:")
        print("    python convert_audio.py <file.mp4> [output.wav]")
        print("    python convert_audio.py --all")
        sys.exit(0)

    if sys.argv[1] == "--all":
        convert_all_mp4_in_dir(".")
    else:
        input_file = sys.argv[1]
        out_file = sys.argv[2] if len(sys.argv) > 2 else None
        convert_to_wav(input_file, out_file)
