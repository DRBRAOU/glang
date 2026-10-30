#!/usr/bin/env python3
"""Create a ZIP of short TEST TONES matching a CSV's audio_filename column.

For trying the bulk importer only. These are beeps, not Gondi speech; never
publish them. Real recordings must come from consenting expert speakers.

    python3 scripts/make-test-audio-zip.py samples/gondi-vaani-100-prompts.csv test-tones.zip
"""
import csv
import io
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
import zipfile
from pathlib import Path


def tone_wav(freq: float, seconds: float = 0.8, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * freq * i / rate)))
                               for i in range(int(rate * seconds))))
    return buf.getvalue()


def main(csv_path: str, zip_path: str) -> None:
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    has_ffmpeg = shutil.which("ffmpeg") is not None
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z, tempfile.TemporaryDirectory() as tmp:
        for i, row in enumerate(rows):
            name = row["audio_filename"].strip()
            if not name:
                continue
            wav = tone_wav(330 + 30 * i)
            if name.lower().endswith(".mp3"):
                if not has_ffmpeg:
                    sys.exit("ffmpeg is needed to make .mp3 test files (or change the CSV to .wav).")
                src, dst = Path(tmp, "t.wav"), Path(tmp, "t.mp3")
                src.write_bytes(wav)
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), str(dst)], check=True)
                z.writestr(name, dst.read_bytes())
            else:
                z.writestr(name, wav)
    print(f"Wrote {zip_path} with {len(rows)} test tones (NOT for publication).")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
