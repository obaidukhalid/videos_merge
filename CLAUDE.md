# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

VEmbed Video Combiner — a Flask web app that uploads, trims, concatenates, and watermarks video clips using MoviePy + OpenCV. It also extracts every frame as JPEG for inspection.

## Commands

```bash
# Activate venv and run the web server (http://localhost:5000)
source venv/bin/activate && python app.py

# Run the standalone CLI script (edit USER CONFIG block at top first)
source venv/bin/activate && python combine_video.py

# Install dependencies
pip install -r requirements.txt
```

There are no tests or linters configured.

## Architecture

Two entry points share the same video processing pipeline:

- **`app.py`** — Flask web server with a single-page UI. Accepts up to 4 video uploads via multipart form, processes them in a background thread, and exposes job status via polling (`/status/<job_id>`). Jobs are stored in an in-memory dict (`_jobs`) protected by a threading lock.
- **`combine_video.py`** — Standalone CLI script. Configuration is hardcoded in the `VIDEOS` / `OUTPUT_FILE` / etc. constants at the top of the file. Runs synchronously.

### Processing pipeline (same in both entry points)

1. Each video's sections are flattened into individual `(label, path, start, end)` tuples
2. Clips are loaded with MoviePy's `VideoFileClip`, trimmed via `.subclipped()`
3. Every frame is extracted to `frames/<label>/` as numbered JPEGs using OpenCV
4. FadeIn/FadeOut effects are applied at clip boundaries
5. Clips are concatenated with `concatenate_videoclips(method="compose")`
6. Optional tiled watermark overlay is composited on top (PIL renders text to RGBA canvas → MoviePy `ImageClip`)
7. Final video is written with `write_videofile()` (default: H.264 + AAC)

### Key directories

- `input/` — uploaded source videos (server-side temp storage)
- `output/` — rendered output videos (gitignored, including all *.mp4)
- `frames/` — extracted frame JPEGs organized by clip label
- `templates/video_combine.html` — single-file SPA (all HTML/CSS/JS inline)

### Web UI details

`video_combine.html` is a self-contained single file with no build step. All CSS and JS are inline. The frontend uses vanilla JS — no frameworks. Key UI flows:
- Drag-and-drop video upload with up to 3 sections per video
- Live watermark preview (text, color, opacity, orientation)
- Server-side folder browser modal (`/browse` endpoint)
- Job progress polling with a styled console log

### Flask routes

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Serves the SPA |
| `/generate` | POST | Accepts form + files, starts background job |
| `/status/<job_id>` | GET | Returns job status + log lines (polled by frontend) |
| `/download/<filename>` | GET | Serves output file from `output/` |
| `/browse` | GET | Server-side directory listing for folder picker |

## Key Dependencies

- **moviepy 2.x** — video editing (note: uses `moviepy` import, not `moviepy.editor`)
- **opencv-python-headless** — frame extraction and codec detection
- **Pillow** — watermark text rendering to RGBA images
- **Flask** — web server (4GB max upload configured)
