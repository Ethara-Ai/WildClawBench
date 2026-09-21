"""Extract inline media from trajectory messages onto disk.

Local-only replacement for `_replace_inline_media_with_s3` from
kensei2_sandbox.py. Handles the three image storage formats produced by
OpenClaw clients:
1. OpenClaw direct: `{type:image, data:<b64>, mimeType:...}`
2. Anthropic dict source: `{type:image, source:{type:base64, media_type, data}}`
3. String source: `data:image/...;base64,...` URI or
   `/home/node/.openclaw/...` container path

The bytes are always written to `artifacts_dir/<task_id>/<uuid>.<ext>` so the
shipped transcript stays small and operators can still eyeball what the agent
saw. What the rewritten block's `source` *says*, however, is the
**container/workspace path the agent actually read** — recovered from the
tool call that produced the media — never the host path of the extracted
artifact. A `file:///home/ec2-user/harness/.../artifacts/...` string in a
client-shipped `output.json` is both an infra leak and a dangling reference:
the client has no such file and no such machine.

Provenance is recovered in two passes:

* Pass 1 (`_collect_origin_paths`) walks every message and records each
  `toolCall` block that carries a path-ish string argument, keyed by the tool
  call id. The mechanism is tool-name agnostic — `read`, `write`, `edit`,
  `image` and anything else that names a file all register the same way.
* Pass 2 (the rewrite walk) resolves each media-bearing message back to its
  originating path, preferring the explicit `toolCallId` linkage that OpenClaw
  emits on `role:"toolResult"` messages and falling back to emission-order
  adjacency (the most recent unconsumed path-bearing call) when no id is
  present. Every media block in one result inherits that one path.

When nothing resolves, `source` becomes the neutral marker
`inline-media (extracted)`. No branch ever writes a host path into `source`.
"""

from __future__ import annotations

import base64
import logging
import re
import uuid
from pathlib import Path
from typing import Dict, Iterator, List, Mapping, Optional

_logger = logging.getLogger(__name__)

_MEDIA_BLOCK_TYPES = {"image", "video", "audio", "input_image"}

_DATA_URI_RE = re.compile(r"^data:([^;]+);base64,(.+)$", re.DOTALL)
_CONTAINER_PATH_RE = re.compile(
    r"^/home/node/\.openclaw/(?:workspace|uploads|media)/.+"
)

# Argument keys that name a file on a tool call. Ordered by how literal the
# key is about being a path, so `{"path": ..., "filename": ...}` prefers
# `path`. Mirrors `_WRITE_PATH_KEYS` in multimodal_meta.py plus the keys the
# read/image tools actually use in captured runs.
_PATH_ARG_KEYS = ("path", "file_path", "filePath", "filename", "file", "image")

# What `source` says when provenance cannot be recovered. Deliberately not a
# path of any kind: a client reading this must not think they can open it.
_UNKNOWN_ORIGIN = "inline-media (extracted)"

_MIME_EXT_MAP = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
    "image/svg+xml": "svg",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "video/quicktime": "mov",
    "audio/mp3": "mp3",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/ogg": "ogg",
    # .m4a files report several different MIME strings depending on the
    # producer: stdlib mimetypes returns "audio/mp4a-latm", browsers emit
    # "audio/mp4" or "audio/x-m4a", and some tools use "audio/aac". Without
    # these entries _ext_for() falls through to mime.split("/")[-1] and
    # produces ugly extensions like "mp4a-latm" that judges and downstream
    # readers don't recognize.
    "audio/mp4": "m4a",
    "audio/mp4a-latm": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "aac",
    "audio/x-aac": "aac",
    "application/pdf": "pdf",
}


def _ext_for(mime: str) -> str:
    if not mime:
        return "bin"
    return _MIME_EXT_MAP.get(mime.lower(), mime.split("/")[-1] or "bin")


def _write_bytes(artifacts_root: Path, task_id: str, data: bytes, mime: str) -> Path:
    out_dir = artifacts_root / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = "%s.%s" % (uuid.uuid4().hex[:12], _ext_for(mime))
    path = out_dir / fname
    path.write_bytes(data)
    return path


def _to_url(path: Path, base_url: Optional[str], task_id: str) -> str:
    if base_url:
        return "%s/%s/%s" % (base_url.rstrip("/"), task_id, path.name)
    return "file://%s" % path.resolve()


def _source_for(
    path: Path, base_url: Optional[str], task_id: str, origin: Optional[str]
) -> str:
    """Decide what the rewritten block's `source` should say.

    With `base_url` set the artifact is served over HTTP and the URL is a real,
    client-resolvable reference, so it stays. Without one we are in local mode:
    the only honest thing to publish is where the agent read the file inside
    its container, or the neutral marker when that is unknowable. `_to_url`'s
    `file://` host path is never a valid answer here.
    """
    if base_url:
        return _to_url(path, base_url, task_id)
    return origin or _UNKNOWN_ORIGIN


def _normalize_call_id(value) -> str:
    """Strip OpenClaw's `|route-suffix` so both sides of a pair compare equal.

    `sanitize_jsonl_message` splits the suffix off block-level `toolCallId`s
    but not the message-level one, so the two ends of the same pair can
    disagree by a suffix by the time they reach us.
    """
    if not isinstance(value, str):
        return ""
    return value.strip().split("|", 1)[0]


def _path_argument(block: Mapping) -> Optional[str]:
    """Return the file path a `toolCall` block names, if it names one."""
    if not isinstance(block, Mapping) or block.get("type") != "toolCall":
        return None
    args = block.get("arguments")
    if not isinstance(args, Mapping):
        return None
    for key in _PATH_ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _iter_blocks(content) -> Iterator[dict]:
    """Yield every dict block in a content list, recursing into nested content."""
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        yield block
        inner = block.get("content")
        if isinstance(inner, list):
            yield from _iter_blocks(inner)


def _inner_message(msg) -> Optional[dict]:
    """Unwrap `{is_accepted, hints, message:{...}}` envelopes to the real message."""
    if not isinstance(msg, dict):
        return None
    envelope = msg
    while (
        isinstance(envelope, dict)
        and "message" in envelope
        and isinstance(envelope["message"], dict)
        and "role" not in envelope["message"]
        and "content" not in envelope["message"]
    ):
        envelope = envelope["message"]
    inner = envelope.get("message") if isinstance(envelope, dict) else None
    return inner if isinstance(inner, dict) else None


def _collect_origin_paths(messages: List[dict]) -> Dict[str, str]:
    """Pass 1: map tool call id -> the container path that call named."""
    origins: Dict[str, str] = {}
    for msg in messages:
        inner = _inner_message(msg)
        if inner is None:
            continue
        for block in _iter_blocks(inner.get("content")):
            path = _path_argument(block)
            if path is None:
                continue
            call_id = _normalize_call_id(block.get("id"))
            # First writer wins: a repeated id is a retry of the same call,
            # and the original argument is the one the transcript showed.
            if call_id and call_id not in origins:
                origins[call_id] = path
    return origins


def _result_call_id(inner: Mapping) -> str:
    """Tool call id a result message points back at, message- or block-level."""
    call_id = _normalize_call_id(inner.get("toolCallId"))
    if call_id:
        return call_id
    for block in _iter_blocks(inner.get("content")):
        for key in ("toolCallId", "tool_use_id"):
            call_id = _normalize_call_id(block.get(key))
            if call_id:
                return call_id
    return ""


def _has_media(content) -> bool:
    return any(b.get("type") in _MEDIA_BLOCK_TYPES for b in _iter_blocks(content))


def _rewrite_block(
    block: dict,
    task_id: str,
    artifacts_root: Path,
    base_url: Optional[str],
    origin: Optional[str],
) -> None:
    if not isinstance(block, dict):
        return
    btype = block.get("type")
    if btype not in _MEDIA_BLOCK_TYPES:
        return

    mime = block.get("mimeType") or block.get("mediaType") or ""

    # Format 1: OpenClaw direct {type, data, mimeType}
    if isinstance(block.get("data"), str) and block.get("data"):
        try:
            raw = base64.b64decode(block["data"])
        except (ValueError, TypeError):
            return
        path = _write_bytes(artifacts_root, task_id, raw, mime)
        block.pop("data", None)
        block.pop("mimeType", None)
        block["source"] = _source_for(path, base_url, task_id, origin)
        if mime:
            block["mimeType"] = mime
        return

    src = block.get("source")
    # Format 2: Anthropic dict source {type:base64, media_type, data}
    if isinstance(src, dict):
        if src.get("type") == "base64" and isinstance(src.get("data"), str):
            mime = src.get("media_type") or mime
            try:
                raw = base64.b64decode(src["data"])
            except (ValueError, TypeError):
                return
            path = _write_bytes(artifacts_root, task_id, raw, mime)
            block["source"] = _source_for(path, base_url, task_id, origin)
            if mime and not block.get("mimeType"):
                block["mimeType"] = mime
        elif src.get("type") == "url" and isinstance(src.get("url"), str):
            block["source"] = src["url"]
        return

    # Format 3: String source — data: URI or container path
    if isinstance(src, str):
        m = _DATA_URI_RE.match(src)
        if m:
            mime = m.group(1) or mime
            try:
                raw = base64.b64decode(m.group(2))
            except (ValueError, TypeError):
                return
            path = _write_bytes(artifacts_root, task_id, raw, mime)
            block["source"] = _source_for(path, base_url, task_id, origin)
            if mime and not block.get("mimeType"):
                block["mimeType"] = mime
            return
        if _CONTAINER_PATH_RE.match(src):
            # Container-only path — keep as a relative reference; we cannot
            # read it from outside the sandbox at this point.  The caller
            # is expected to have copied it out before invoking this pass.
            return


def _walk_content(
    content,
    task_id: str,
    artifacts_root: Path,
    base_url: Optional[str],
    origin: Optional[str],
) -> None:
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                _rewrite_block(block, task_id, artifacts_root, base_url, origin)
                inner = block.get("content")
                if isinstance(inner, list):
                    _walk_content(inner, task_id, artifacts_root, base_url, origin)


def replace_inline_media_with_files(
    messages: List[dict],
    task_id: str,
    artifacts_dir: Path,
    base_url: Optional[str] = None,
) -> List[dict]:
    """Walk messages and extract any inline media to local files.

    `artifacts_dir` is a base directory; files are written under
    `artifacts_dir/<task_id>/`. The rewritten block's `source` names the
    container path the agent read the media from, recovered from the
    originating tool call, or `inline-media (extracted)` when that cannot be
    determined — never the host path of the extracted artifact. `base_url`
    overrides this with a servable URL prefix (e.g. for a local HTTP server),
    which is a real client-resolvable reference rather than an infra leak.
    """
    artifacts_root = Path(artifacts_dir)
    origins = _collect_origin_paths(messages)

    # Emission-order fallback state: path-bearing calls seen so far that no
    # media result has claimed yet, oldest first.
    pending: List[tuple] = []

    for msg in messages:
        inner = _inner_message(msg)
        if inner is None:
            continue

        content = inner.get("content")
        for block in _iter_blocks(content):
            path_arg = _path_argument(block)
            if path_arg is not None:
                pending.append((_normalize_call_id(block.get("id")), path_arg))

        if not _has_media(content):
            continue

        origin: Optional[str] = None
        call_id = _result_call_id(inner)
        if call_id and call_id in origins:
            # Explicit linkage: OpenClaw stamps `toolCallId` on every
            # `role:"toolResult"` message, so this is the path in practice.
            origin = origins[call_id]
            for idx, (pid, _path) in enumerate(pending):
                if pid == call_id:
                    pending.pop(idx)
                    break
        elif pending and inner.get("role") not in ("user", "system"):
            # Tool output only: user-message media is a task attachment, and
            # binding it to the agent's last touched file would invent a
            # provenance that never existed.
            _pid, origin = pending.pop()

        _walk_content(content, task_id, artifacts_root, base_url, origin)
    return messages
