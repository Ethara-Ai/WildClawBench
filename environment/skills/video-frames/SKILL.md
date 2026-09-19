---
name: video-frames
description: Extract frames or short clips from videos using ffmpeg, with an automatic OpenCV fallback when ffmpeg is missing.
homepage: https://ffmpeg.org
metadata: {"clawdbot":{"emoji":"🎞️","requires":{"bins":["python3"]},"pip":["opencv-python-headless"],"install":[{"id":"brew","kind":"brew","formula":"ffmpeg","bins":["ffmpeg"],"label":"Install ffmpeg (brew)"}]}}
---

# Video Frames (ffmpeg, OpenCV fallback)

Extract a single frame from a video, or create quick thumbnails for inspection.

## Quick start

First frame:

```bash
{baseDir}/scripts/frame.sh /path/to/video.mp4 --out /tmp/frame.jpg
```

At a timestamp:

```bash
{baseDir}/scripts/frame.sh /path/to/video.mp4 --time 00:00:10 --out /tmp/frame-10s.jpg
```

## Notes

- Prefer `--time` for “what is happening around here?”.
- Use a `.jpg` for quick share; use `.png` for crisp UI frames.

## How decoding works (so you can debug if it fails)

`frame.sh` decodes with `ffmpeg` when it is on `PATH`. If ffmpeg is missing
— an image built before it was baked in, or a stripped environment — the
script falls back on its own to OpenCV (`cv2`, from
`opencv-python-headless`), seeking by `--index` (`CAP_PROP_POS_FRAMES`) or
`--time` (`CAP_PROP_POS_MSEC`). You do not need to invoke the fallback
manually. ffmpeg stays preferred: it handles more containers and codecs.

Each rung checks that a non-empty file actually landed at `--out` before
reporting success, and removes the file otherwise. ffmpeg exits 0 without
writing anything when a `--index` falls past the end of the stream, so
without that check you would get a success exit and no image.

Exit codes:

- `0` — frame written; the path is echoed on stdout.
- `1` — input file not found.
- `2` — usage error (bad flag, missing `--out`, bad `WCB_VIDEO_FRAME_BACKEND`).
- `3` — no decoder at all: neither ffmpeg nor `cv2`. Treat as an image/
  skill-deps regression; both ship in the harness image.
- `4` — a decoder ran but no backend produced a frame. Usually a corrupt
  file, or an `--index`/`--time` past the end of the video.

## Requires

- `python3` — installed in the image. Runs the OpenCV fallback.
- `ffmpeg` — preferred decoder; installed in the image. Not hard-gated, so
  the skill still loads (and still works via OpenCV) without it.
- `opencv-python-headless` — the `cv2` fallback, installed from the offline
  wheelhouse.
- `WCB_VIDEO_FRAME_BACKEND` — optional; `auto` (default), `ffmpeg` or
  `opencv` to pin one rung when debugging.
- `WCB_FFMPEG_BIN` — optional; path to an ffmpeg binary outside `PATH`.
