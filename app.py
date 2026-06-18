"""
app.py — Flask backend for VEmbed Video Combiner
Run via:   ./start.sh   (or start.bat on Windows)
Direct:    source venv/bin/activate && python app.py
Browser:   http://localhost:5000
"""

import os
import re
import sys
import uuid
import shutil
import threading
import traceback

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from flask import Flask, render_template, request, jsonify, send_file
from moviepy import VideoFileClip, concatenate_videoclips, ImageClip, CompositeVideoClip
from moviepy.video.fx import FadeIn, FadeOut

# ---------------------------------------------------------------------------
# App config
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024  # 4 GB

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR  = os.path.join(BASE_DIR, "input")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
FRAMES_DIR = os.path.join(BASE_DIR, "frames")

_jobs: dict      = {}
_jobs_lock       = threading.Lock()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_time(ts: str | None) -> float | None:
    ts = (ts or "").strip()
    if not ts:
        return None
    parts = [int(p) for p in ts.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None


def _hex_to_rgba(hex_color: str, opacity_pct: int) -> tuple:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (r, g, b, int(255 * max(0, min(100, opacity_pct)) / 100))


def _sanitize_log(msg: str) -> str:
    """Strip server filesystem paths from log text shown in the browser."""
    msg = msg.replace(BASE_DIR + os.sep, "")
    msg = msg.replace(BASE_DIR, "")
    # Remove any remaining absolute unix paths
    msg = re.sub(r"(?<!\w)/(?:home|root|usr|var|tmp|mnt|srv|opt)(?:/\S*)?", "", msg)
    return msg.strip()


_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]

# PIL rotation angles per orientation key (counterclockwise)
_ORIENTATION_ANGLE = {
    "horizontal": 0,
    "vertical":   90,   # reads bottom → top
    "deg90":     -90,   # reads top → bottom (90° clockwise)
    "deg270":    180,   # upside-down horizontal (270° clockwise)
}


def _make_watermark(width: int, height: int, duration: float,
                    text: str, size: int, color_hex: str,
                    opacity_pct: int, orientation: str = "horizontal") -> ImageClip:
    rgba = _hex_to_rgba(color_hex, opacity_pct)

    font = None
    for fp in _FONT_CANDIDATES:
        if os.path.isfile(fp):
            font = ImageFont.truetype(fp, size)
            break
    if font is None:
        font = ImageFont.load_default()

    # Measure text
    tmp  = Image.new("RGBA", (1, 1))
    bbox = ImageDraw.Draw(tmp).textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    # Draw text on a transparent stamp
    pad   = 4
    stamp = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(stamp).text((pad, pad), text, font=font, fill=rgba)

    angle = _ORIENTATION_ANGLE.get(orientation, 0)
    if angle != 0:
        stamp = stamp.rotate(angle, expand=True)

    sw, sh = stamp.size
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    cols = max(1, width  // (sw + 50))
    rows = max(1, height // (sh + 45))
    cs   = width  // cols
    rs   = height // rows

    for r in range(rows):
        for c in range(cols):
            x = c * cs + (cs - sw) // 2
            y = r * rs + (rs - sh) // 2
            canvas.paste(stamp, (x, y), stamp)

    return ImageClip(np.array(canvas), duration=duration)

# ---------------------------------------------------------------------------
# Background job processor
# ---------------------------------------------------------------------------

def _process_job(job_id: str, config: dict) -> None:
    job = _jobs[job_id]
    log = job["log"]

    def info(msg: str) -> None:
        log.append(_sanitize_log(str(msg)))

    try:
        # Flatten video sections
        flat: list[tuple] = []
        for v in config["videos"]:
            label, path, sections = v["label"], v["path"], v["sections"]
            multi = len(sections) > 1
            for si, (start, end) in enumerate(sections):
                fl = f"{label}_s{si + 1}" if multi else label
                flat.append((fl, path, start, end))

        if not flat:
            job["status"] = "error"
            info("ERROR: No video clips to process.")
            return

        clips = []
        fade  = config.get("fade_duration", 0.5)

        for i, (fl, path, start, end) in enumerate(flat):
            fname = os.path.basename(path)
            info(f"[{fl}] Loading {fname} …")
            clip    = VideoFileClip(path)
            t_start = _parse_time(start)
            t_end   = _parse_time(end)

            if t_start is not None or t_end is not None:
                clip = clip.subclipped(t_start or 0, t_end)
                info(f"  Trimmed: {start or '00:00'} → {end or 'end'}  ({clip.duration:.2f}s)")
            else:
                info(f"  Full clip ({clip.duration:.2f}s)")

            # Extract frames
            fdir = os.path.join(FRAMES_DIR, fl)
            if os.path.isdir(fdir):
                shutil.rmtree(fdir)
            os.makedirs(fdir)
            ex_fps = clip.fps or 25
            count  = 0
            for fi, frame in enumerate(clip.iter_frames(fps=ex_fps, dtype="uint8")):
                cv2.imwrite(
                    os.path.join(fdir, f"frame_{fi:06d}.jpg"),
                    cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
                )
                count = fi + 1
            info(f"  ✓ {count} frames extracted → frames/{fl}/")

            if fade > 0:
                effects = []
                if i > 0:               effects.append(FadeIn(fade))
                if i < len(flat) - 1:  effects.append(FadeOut(fade))
                if effects:
                    clip = clip.with_effects(effects)

            clips.append(clip)

        fps = config.get("fps") or clips[0].fps
        info(f"\nConcatenating {len(clips)} clip(s) at {fps} FPS …")
        final = concatenate_videoclips(clips, method="compose")

        # Watermark
        wm_text = config.get("watermark_text", "")
        if wm_text:
            info(f"Rendering watermark ({config.get('watermark_orientation','horizontal')}) …")
            wm = _make_watermark(
                final.w, final.h, final.duration,
                wm_text,
                config.get("watermark_size", 36),
                config.get("watermark_color", "#b4b4b4"),
                config.get("watermark_opacity", 33),
                config.get("watermark_orientation", "horizontal"),
            )
            render = CompositeVideoClip([final, wm])
        else:
            render = final

        # Output
        out_name = (config.get("output_filename") or "output").strip()
        if not out_name.lower().endswith(".mp4"):
            out_name += ".mp4"

        out_dir = os.path.expanduser(config.get("output_dir") or OUTPUT_DIR)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, out_name)

        v_codec  = config.get("video_codec", "libx264")
        a_codec  = config.get("audio_codec", "aac")
        crf      = config.get("crf", 23)
        ffmpeg_p = ["-crf", str(crf)] if v_codec in ("libx264", "libx265") else []

        info(f"Encoding  [{v_codec} / {a_codec} / CRF {crf}] …")
        render.write_videofile(
            out_path, fps=fps,
            codec=v_codec, audio_codec=a_codec,
            ffmpeg_params=ffmpeg_p,
            logger=None,
        )

        for c in clips:
            c.close()
        final.close()
        if wm_text:
            render.close()

        job["status"] = "done"
        job["output"] = out_name
        info(f"✓ Done!  Output: {out_name}")

    except Exception as exc:
        job["status"] = "error"
        info(f"ERROR: {exc}")
        info(traceback.format_exc())

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("video_combine.html")


@app.route("/generate", methods=["POST"])
def generate():
    try:
        wm_enabled = request.form.get("wm_enabled") == "on"

        config = {
            "fade_duration":        float(request.form.get("fade_duration") or 0),
            "watermark_text":       (request.form.get("watermark_text") or "").strip() if wm_enabled else "",
            "watermark_size":       max(10, min(200, int(request.form.get("watermark_size") or 36))),
            "watermark_color":      request.form.get("watermark_color") or "#b4b4b4",
            "watermark_opacity":    max(0, min(100, int(request.form.get("watermark_opacity") or 33))),
            "watermark_orientation": request.form.get("watermark_orientation") or "horizontal",
            "output_filename":      (request.form.get("output_filename") or "output").strip(),
            "output_dir":           (request.form.get("output_dir") or "").strip() or None,
            "fps":                  int(request.form["fps"]) if request.form.get("fps") else None,
            "video_codec":          request.form.get("video_codec") or "libx264",
            "audio_codec":          request.form.get("audio_codec") or "aac",
            "crf":                  int(request.form.get("crf") or 23),
            "videos":               [],
        }

        os.makedirs(INPUT_DIR, exist_ok=True)

        for i in range(1, 5):
            label = f"v{i}"
            file  = request.files.get(f"video_{label}")
            if not file or not file.filename:
                continue

            sections = []
            for j in range(1, 4):
                start = (request.form.get(f"video_{label}_s{j}_start") or "").strip()
                end   = (request.form.get(f"video_{label}_s{j}_end")   or "").strip()
                if j == 1:
                    sections.append((start or None, end or None))
                elif start or end:
                    sections.append((start or None, end or None))

            ext       = os.path.splitext(file.filename)[1] or ".mp4"
            save_path = os.path.join(INPUT_DIR, f"{label}{ext}")
            file.save(save_path)

            config["videos"].append({"label": label, "path": save_path, "sections": sections})

        if not config["videos"]:
            return jsonify({"error": "Please upload at least one video file."}), 400

        job_id = str(uuid.uuid4())
        with _jobs_lock:
            _jobs[job_id] = {"status": "processing", "log": [], "output": None}

        threading.Thread(target=_process_job, args=(job_id, config), daemon=True).start()
        return jsonify({"job_id": job_id})

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/status/<job_id>")
def job_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({"status": job["status"], "log": list(job["log"]), "output": job["output"]})


@app.route("/download/<path:filename>")
def download(filename: str):
    safe = os.path.basename(filename)
    path = os.path.join(OUTPUT_DIR, safe)
    if not os.path.isfile(path):
        return "File not found", 404
    return send_file(path, as_attachment=True, download_name=safe)


@app.route("/browse")
def browse_dir():
    """Server-side directory browser for output folder selection."""
    requested = request.args.get("path", "")
    home      = os.path.expanduser("~")
    start     = os.path.realpath(requested) if requested else OUTPUT_DIR

    # Keep navigation within the user's home directory (safety guard)
    if not start.startswith(home):
        start = home

    try:
        entries = sorted(
            n for n in os.listdir(start)
            if os.path.isdir(os.path.join(start, n)) and not n.startswith(".")
        )
        parent = os.path.dirname(start)
        # Don't let navigation go above home
        safe_parent = parent if parent.startswith(home) and parent != start else None

        return jsonify({
            "current":  start,
            "label":    os.path.basename(start) or start,
            "parent":   safe_parent,
            "entries":  entries,
        })
    except PermissionError:
        return jsonify({"error": "Permission denied"}), 403
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

# ---------------------------------------------------------------------------

if __name__ == "__main__":
    for d in (INPUT_DIR, OUTPUT_DIR, FRAMES_DIR):
        os.makedirs(d, exist_ok=True)
    print("─" * 50)
    print("  VEmbed Video Combiner")
    print("  Open: http://localhost:5000")
    print("─" * 50)
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
