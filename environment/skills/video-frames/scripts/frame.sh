#!/usr/bin/env bash
# Extract a single frame from a video into an image file.
#
# Usage:
#   frame.sh <video-file> [--time HH:MM:SS] [--index N] --out /path/to/frame.jpg
#
# Output (stdout): the path of the frame that was written.
# Output (stderr): step markers ("== frame: ... ==") and errors.
# Exit code: 0 on success; non-zero with a clear error on any failure.
#
# Design notes:
#   - Primary decoder is ffmpeg, which is baked into the agent image.
#   - Falls back to LOCAL OpenCV (opencv-python-headless, installed into the
#     agent container from the offline wheelhouse) when ffmpeg is absent --
#     e.g. an image built before ffmpeg was baked in, or a stripped
#     environment -- or when the ffmpeg call produces no frame. Only when
#     neither backend can decode do we hard-fail.
#   - Every rung verifies the output file is non-empty before declaring
#     success, and deletes it otherwise. ffmpeg exits 0 without writing
#     anything when a select= index falls past the end of the stream, so
#     without that check the caller would be handed a missing or truncated
#     image and no error. We never report success on absent output.
#   - WCB_VIDEO_FRAME_BACKEND pins a single rung (ffmpeg|opencv) for
#     debugging; WCB_FFMPEG_BIN points at an ffmpeg outside PATH. Both are
#     optional and default to walking the chain.
#
# Exit codes:
#   0  frame written
#   1  input file not found
#   2  usage error (includes a bad WCB_VIDEO_FRAME_BACKEND value)
#   3  no usable decoder backend at all (no ffmpeg, no OpenCV)
#   4  every available backend failed to produce a frame

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  frame.sh <video-file> [--time HH:MM:SS] [--index N] --out /path/to/frame.jpg

Examples:
  frame.sh video.mp4 --out /tmp/frame.jpg
  frame.sh video.mp4 --time 00:00:10 --out /tmp/frame-10s.jpg
  frame.sh video.mp4 --index 0 --out /tmp/frame0.png

Environment:
  WCB_VIDEO_FRAME_BACKEND  auto (default) | ffmpeg | opencv
  WCB_FFMPEG_BIN           ffmpeg binary to use (default: ffmpeg on PATH)
EOF
  exit 2
}

if [[ "${1:-}" == "" || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
fi

in="${1:-}"
shift || true

time=""
index=""
out=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --time)
      time="${2:-}"
      shift 2
      ;;
    --index)
      index="${2:-}"
      shift 2
      ;;
    --out)
      out="${2:-}"
      shift 2
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage
      ;;
  esac
done

if [[ ! -f "$in" ]]; then
  echo "File not found: $in" >&2
  exit 1
fi

if [[ "$out" == "" ]]; then
  echo "Missing --out" >&2
  usage
fi

mkdir -p "$(dirname "$out")"

backend_pin="${WCB_VIDEO_FRAME_BACKEND:-auto}"
ffmpeg_bin="${WCB_FFMPEG_BIN:-ffmpeg}"

case "$backend_pin" in
  auto | ffmpeg | opencv) ;;
  *)
    echo "frame.sh: WCB_VIDEO_FRAME_BACKEND must be auto, ffmpeg or opencv" >&2
    echo "  (got '$backend_pin')" >&2
    exit 2
    ;;
esac

# Probe both rungs up front so the "nothing available" error can name every
# backend we looked for rather than only the one we happened to try first.
have_ffmpeg="0"
tried=()
if [[ "$backend_pin" == "opencv" ]]; then
  tried+=("ffmpeg (skipped: WCB_VIDEO_FRAME_BACKEND=opencv)")
elif command -v "$ffmpeg_bin" >/dev/null 2>&1; then
  have_ffmpeg="1"
else
  tried+=("ffmpeg (no '$ffmpeg_bin' on PATH)")
fi

have_opencv="0"
if [[ "$backend_pin" == "ffmpeg" ]]; then
  tried+=("opencv (skipped: WCB_VIDEO_FRAME_BACKEND=ffmpeg)")
elif python3 -c 'import cv2' >/dev/null 2>&1; then
  have_opencv="1"
else
  tried+=("opencv (python3 -c 'import cv2' failed)")
fi

if [[ "$have_ffmpeg" == "0" && "$have_opencv" == "0" ]]; then
  echo "frame.sh: no usable video decoder backend." >&2
  for note in "${tried[@]}"; do
    echo "  tried $note" >&2
  done
  echo "  Install one of them inside the agent container:" >&2
  echo "    apt-get install -y ffmpeg           # preferred, also gives ffprobe" >&2
  echo "    pip install opencv-python-headless  # the cv2 fallback" >&2
  echo "  Or point WCB_FFMPEG_BIN at an ffmpeg binary outside PATH." >&2
  echo "  Both ship in the harness image; seeing this means the container was" >&2
  echo "  built from an older base or the skill-deps install failed." >&2
  exit 3
fi

frame_ffmpeg() {
  if [[ "$index" != "" ]]; then
    "$ffmpeg_bin" -hide_banner -loglevel error -y \
      -i "$in" \
      -vf "select=eq(n\\,${index})" \
      -vframes 1 \
      "$out"
  elif [[ "$time" != "" ]]; then
    "$ffmpeg_bin" -hide_banner -loglevel error -y \
      -ss "$time" \
      -i "$in" \
      -frames:v 1 \
      "$out"
  else
    "$ffmpeg_bin" -hide_banner -loglevel error -y \
      -i "$in" \
      -vf "select=eq(n\\,0)" \
      -vframes 1 \
      "$out"
  fi
}

frame_opencv() {
  python3 - "$in" "$out" "$index" "$time" <<'PY'
import sys

import cv2

src, dst, index, ts = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]


def seconds(spec):
    """Parse an ffmpeg-style position: 10, 1:30 and 00:00:10 all work."""
    total = 0.0
    for part in spec.split(":"):
        total = total * 60.0 + float(part)
    return total


cap = cv2.VideoCapture(src)
if not cap.isOpened():
    sys.stderr.write("frame.sh: OpenCV could not open %s\n" % src)
    sys.exit(1)
try:
    if index:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(index))
    elif ts:
        cap.set(cv2.CAP_PROP_POS_MSEC, seconds(ts) * 1000.0)
    ok, frame = cap.read()
finally:
    cap.release()

if not ok or frame is None:
    sys.stderr.write("frame.sh: OpenCV read no frame at the requested position\n")
    sys.exit(1)
if not cv2.imwrite(dst, frame):
    sys.stderr.write("frame.sh: OpenCV could not encode %s\n" % dst)
    sys.exit(1)
PY
}

wrote="0"

if [[ "$have_ffmpeg" == "1" ]]; then
  echo "== frame: ffmpeg ($ffmpeg_bin) -> $out ==" >&2
  rm -f "$out"
  if frame_ffmpeg && [[ -s "$out" ]]; then
    wrote="1"
  else
    rm -f "$out"
    echo "frame.sh: ffmpeg produced no frame for $in" >&2
    if [[ "$have_opencv" == "1" ]]; then
      echo "frame.sh: ffmpeg backend unusable; falling back to OpenCV." >&2
    fi
  fi
fi

if [[ "$wrote" == "0" && "$have_opencv" == "1" ]]; then
  echo "== frame: opencv (cv2) -> $out ==" >&2
  rm -f "$out"
  if frame_opencv && [[ -s "$out" ]]; then
    wrote="1"
  else
    rm -f "$out"
    echo "frame.sh: OpenCV produced no frame for $in" >&2
  fi
fi

if [[ "$wrote" == "0" ]]; then
  echo "frame.sh: every available backend failed to extract a frame from $in" >&2
  echo "  Check that the file is a decodable video and that any --index/--time" >&2
  echo "  falls inside its duration. No output was left at $out." >&2
  exit 4
fi

echo "$out"
