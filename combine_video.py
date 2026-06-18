"""
combine_video.py
Combines two or more video clips (with configurable start/end times) into one output video.

QUICK CONFIG — edit the section below, then run the script.
"""

import os
import sys
import shutil
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy import VideoFileClip, concatenate_videoclips, ImageClip, CompositeVideoClip
from moviepy.video.fx import FadeIn, FadeOut

# ---------------------------------------------------------------------------
# USER CONFIG
# ---------------------------------------------------------------------------

# Each entry: (label, filename, [(start, end), ...])
#
# - label    : used for the frames subfolder name (v1, v2, …)
# - filename : file inside video_gen/input/
# - sections : list of (start, end) pairs in "MM:SS" / "H:MM:SS" format.
#              Use None for start/end to mean the natural beginning or end.
#              Add multiple pairs to pull several sections from the same file.
#
# Examples:
#   single section  → [("00:00", "00:17")]
#   two sections    → [("00:30", "00:45"), ("02:30", "02:45")]
#   full clip       → [(None, None)]

VIDEOS = [
    ("v1", "v1.mp4", [("00:07", "00:15")]),
    ("v2", "v2.mp4", [("00:00", "00:05")]),
    ("v3", "v3.mp4", [("00:00", "00:05")]),
    ("v4", "v4.mp4", [("00:30", "00:35"), ("01:55", "02:05")]),
]

# Output filename (goes into video_gen/output/)
OUTPUT_FILE = "video_output_commercial.mp4"

# Target FPS. Set to None to keep each clip's native FPS (the first clip's
# FPS is then used for the whole output).
TARGET_FPS = 25          # or None

# Fade-to-black duration in seconds between clips. Set to 0 to disable.
FADE_DURATION = 0.3

# Watermark settings. Set WATERMARK_TEXT to "" to disable.
# Color is RGBA — alpha 0=transparent, 255=opaque. 80-100 is a subtle grey.
WATERMARK_TEXT  = "VEmbed"
WATERMARK_SIZE  = 36
WATERMARK_COLOR = (180, 180, 180, 85)

# ---------------------------------------------------------------------------
# INTERNAL HELPERS — no need to edit below
# ---------------------------------------------------------------------------

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR  = os.path.join(BASE_DIR, "input")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
FRAMES_DIR = os.path.join(BASE_DIR, "frames")


def _parse_time(ts: str | None) -> float | None:
    """Convert 'MM:SS' / 'H:MM:SS' string to seconds, or return None."""
    if ts is None:
        return None
    parts = [int(p) for p in ts.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"Cannot parse time string: {ts!r}")


def _detect_codec(path: str) -> str:
    """Return the codec name of the first video stream via OpenCV."""
    cap = cv2.VideoCapture(path)
    fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
    cap.release()
    codec = "".join(chr((fourcc_int >> (8 * i)) & 0xFF) for i in range(4)).strip()
    return codec if codec else "unknown"


def _extract_frames(clip: VideoFileClip, frames_subdir: str) -> None:
    """Save every frame of *clip* as a JPEG into *frames_subdir*."""
    os.makedirs(frames_subdir, exist_ok=True)
    fps = clip.fps or 25
    for i, frame in enumerate(clip.iter_frames(fps=fps, dtype="uint8")):
        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        out_path = os.path.join(frames_subdir, f"frame_{i:06d}.jpg")
        cv2.imwrite(out_path, frame_bgr)
    print(f"  Saved {i + 1} frames → {frames_subdir}")


def load_clip(label: str, filename: str, start: str | None, end: str | None) -> VideoFileClip:
    path = os.path.join(INPUT_DIR, filename)
    if not os.path.isfile(path):
        sys.exit(f"[ERROR] Input file not found: {path}")

    codec = _detect_codec(path)
    print(f"[{label}] {filename}  |  detected codec: {codec}")

    clip = VideoFileClip(path)
    t_start = _parse_time(start)
    t_end   = _parse_time(end)

    if t_start is not None or t_end is not None:
        clip = clip.subclipped(t_start or 0, t_end)
        trim_info = f"{start or '0:00'} → {end or 'end'}"
        print(f"  Trimmed to {trim_info}  ({clip.duration:.2f}s)")
    else:
        print(f"  Using full clip ({clip.duration:.2f}s)")

    return clip


def _make_watermark_overlay(width: int, height: int, duration: float) -> ImageClip:
    """Build a tiled semi-transparent text overlay the same size as the video."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Try common system font paths; fall back to PIL default.
    _font_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]
    font = None
    for fp in _font_candidates:
        if os.path.isfile(fp):
            font = ImageFont.truetype(fp, WATERMARK_SIZE)
            break
    if font is None:
        font = ImageFont.load_default()

    bbox   = draw.textbbox((0, 0), WATERMARK_TEXT, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    # Fit as many columns/rows as possible and space them evenly.
    h_gap, v_gap = 60, 55
    cols = max(1, width  // (text_w + h_gap))
    rows = max(1, height // (text_h + v_gap))
    col_step = width  // cols
    row_step = height // rows

    for r in range(rows):
        for c in range(cols):
            x = c * col_step + (col_step - text_w) // 2
            y = r * row_step + (row_step - text_h) // 2
            draw.text((x, y), WATERMARK_TEXT, font=font, fill=WATERMARK_COLOR)

    arr = np.array(img)          # shape (H, W, 4) RGBA
    return ImageClip(arr, duration=duration)


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Flatten VIDEOS into individual (frames_label, filename, start, end) clips.
    # A video with N sections produces N consecutive clips.
    flat_clips: list[tuple[str, str, str | None, str | None]] = []
    for label, filename, sections in VIDEOS:
        multi = len(sections) > 1
        for s_idx, (start, end) in enumerate(sections):
            frames_label = f"{label}_s{s_idx + 1}" if multi else label
            flat_clips.append((frames_label, filename, start, end))

    clips = []
    for i, (frames_label, filename, start, end) in enumerate(flat_clips):
        clip = load_clip(frames_label, filename, start, end)

        frames_subdir = os.path.join(FRAMES_DIR, frames_label)
        # Clear any previous frames for this slot
        if os.path.isdir(frames_subdir):
            shutil.rmtree(frames_subdir)
        print(f"  Extracting frames …")
        _extract_frames(clip, frames_subdir)

        if FADE_DURATION > 0:
            is_first = (i == 0)
            is_last  = (i == len(flat_clips) - 1)
            effects = []
            if not is_first:
                effects.append(FadeIn(FADE_DURATION))
            if not is_last:
                effects.append(FadeOut(FADE_DURATION))
            if effects:
                clip = clip.with_effects(effects)

        clips.append(clip)

    # Determine FPS
    fps = TARGET_FPS if TARGET_FPS else clips[0].fps
    print(f"\nConcatenating {len(clips)} clip(s) at {fps} fps …")

    final = concatenate_videoclips(clips, method="compose")

    # Apply watermark if enabled.
    if WATERMARK_TEXT:
        print("\nRendering watermark overlay …")
        wm_overlay = _make_watermark_overlay(final.w, final.h, final.duration)
        render_clip = CompositeVideoClip([final, wm_overlay])
    else:
        render_clip = final

    output_path = os.path.join(OUTPUT_DIR, OUTPUT_FILE)
    render_clip.write_videofile(
        output_path,
        fps=fps,
        codec="libx264",
        audio_codec="aac",
        logger="bar",
    )
    print(f"\nDone! Output saved to: {output_path}")

    for clip in clips:
        clip.close()
    final.close()
    if WATERMARK_TEXT:
        render_clip.close()


if __name__ == "__main__":
    main()
