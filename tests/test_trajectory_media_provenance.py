"""Provenance coverage for src/utils/trajectory/local_media.py.

Defect C1: inline media extraction rewrote every media block's `source` to an
absolute HOST path (`file:///home/ec2-user/harness/.../artifacts/<task>/<uuid>.jpg`).
That string ships to the client inside output.json, where it is both an infra
leak (it names our harness box and directory layout) and a dangling reference
(the client has no such file).

These tests pin the fixed contract: the bytes still land in the artifacts
directory, but `source` names the CONTAINER path the agent actually read,
recovered from the originating tool call.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils.trajectory.local_media import (  # noqa: E402
    _UNKNOWN_ORIGIN,
    replace_inline_media_with_files,
)

TASK_ID = "koji_sloan"
Q12 = "/root/workspace/home/home/Pictures/q12.jpg"
Q13 = "/root/workspace/home/home/Pictures/q13.jpg"

# 1x1 PNG — real decodable bytes so the extraction path is exercised for real.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)
PNG_B64 = base64.b64encode(PNG_BYTES).decode()


# --- fixture builders ----------------------------------------------------
# Shapes mirror captured OpenClaw runs: an assistant message emitting a
# `toolCall`, then a `role:"toolResult"` message carrying `toolCallId` plus a
# text block and the inline image.


def _envelope(inner: dict, msg_id: str = "m1") -> dict:
    return {"type": "message", "id": msg_id, "timestamp": "", "message": inner}


def _tool_call_msg(call_id: str, path: str, *, key: str = "path",
                   name: str = "read") -> dict:
    return _envelope({
        "role": "assistant",
        "content": [{
            "type": "toolCall", "id": call_id, "name": name,
            "arguments": {key: path},
        }],
    })


def _image_block() -> dict:
    return {"type": "image", "data": PNG_B64, "mimeType": "image/jpeg"}


def _tool_result_msg(call_id, *, images: int = 1, name: str = "read") -> dict:
    inner: dict = {
        "role": "toolResult",
        "toolName": name,
        "isError": False,
        "content": (
            [{"type": "text", "text": "Read image file [image/jpeg]"}]
            + [_image_block() for _ in range(images)]
        ),
    }
    if call_id is not None:
        inner["toolCallId"] = call_id
    return _envelope(inner)


def _media_blocks(messages):
    out = []
    for msg in messages:
        inner = msg.get("message")
        if not isinstance(inner, dict):
            continue
        for block in inner.get("content") or []:
            if isinstance(block, dict) and block.get("type") in (
                "image", "video", "audio", "input_image"
            ):
                out.append(block)
    return out


def _artifact_files(artifacts_dir: Path):
    return sorted((artifacts_dir / TASK_ID).glob("*")) if artifacts_dir.exists() else []


# --- the core defect -----------------------------------------------------

def test_read_tool_image_source_is_container_path(tmp_path: Path) -> None:
    msgs = [_tool_call_msg("toolu_01Nnp", Q12), _tool_result_msg("toolu_01Nnp")]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    block, = _media_blocks(msgs)
    assert block["source"] == Q12


def test_source_never_carries_a_host_path(tmp_path: Path) -> None:
    msgs = [_tool_call_msg("toolu_01Nnp", Q12), _tool_result_msg("toolu_01Nnp")]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    blob = json.dumps(msgs)
    assert "file://" not in blob
    assert str(tmp_path) not in blob


def test_bytes_are_still_extracted_to_the_artifacts_dir(tmp_path: Path) -> None:
    msgs = [_tool_call_msg("toolu_01Nnp", Q12), _tool_result_msg("toolu_01Nnp")]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    written = _artifact_files(tmp_path)
    assert len(written) == 1
    assert written[0].read_bytes() == PNG_BYTES


def test_base64_payload_is_removed_from_the_block(tmp_path: Path) -> None:
    msgs = [_tool_call_msg("toolu_01Nnp", Q12), _tool_result_msg("toolu_01Nnp")]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    block, = _media_blocks(msgs)
    assert "data" not in block
    assert PNG_B64 not in json.dumps(msgs)


def test_rewrite_adds_no_new_block_fields(tmp_path: Path) -> None:
    msgs = [_tool_call_msg("toolu_01Nnp", Q12), _tool_result_msg("toolu_01Nnp")]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    block, = _media_blocks(msgs)
    assert set(block) == {"type", "source", "mimeType"}


def test_mime_type_is_preserved(tmp_path: Path) -> None:
    msgs = [_tool_call_msg("toolu_01Nnp", Q12), _tool_result_msg("toolu_01Nnp")]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    block, = _media_blocks(msgs)
    assert block["mimeType"] == "image/jpeg"


# --- binding rules -------------------------------------------------------

def test_multiple_images_from_one_call_share_that_calls_path(tmp_path: Path) -> None:
    msgs = [_tool_call_msg("toolu_A", Q12), _tool_result_msg("toolu_A", images=3)]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    blocks = _media_blocks(msgs)
    assert len(blocks) == 3
    assert [b["source"] for b in blocks] == [Q12, Q12, Q12]
    assert len(_artifact_files(tmp_path)) == 3


def test_two_sequential_reads_each_bind_their_own_path(tmp_path: Path) -> None:
    msgs = [
        _tool_call_msg("toolu_A", Q12),
        _tool_result_msg("toolu_A"),
        _tool_call_msg("toolu_B", Q13),
        _tool_result_msg("toolu_B"),
    ]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    assert [b["source"] for b in _media_blocks(msgs)] == [Q12, Q13]


def test_parallel_calls_bind_by_id_not_by_order(tmp_path: Path) -> None:
    """Two calls emitted before either result: ids must win over adjacency."""
    msgs = [
        _tool_call_msg("toolu_A", Q12),
        _tool_call_msg("toolu_B", Q13),
        _tool_result_msg("toolu_A"),
        _tool_result_msg("toolu_B"),
    ]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    assert [b["source"] for b in _media_blocks(msgs)] == [Q12, Q13]


def test_result_without_id_falls_back_to_preceding_call(tmp_path: Path) -> None:
    msgs = [_tool_call_msg("toolu_A", Q12), _tool_result_msg(None)]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    block, = _media_blocks(msgs)
    assert block["source"] == Q12


def test_tool_call_id_route_suffix_still_matches(tmp_path: Path) -> None:
    """sanitize_jsonl_message strips `|suffix` from block ids but not from the
    message-level toolCallId, so the two ends of a pair can disagree."""
    msgs = [
        _tool_call_msg("toolu_A", Q12),
        _tool_result_msg("toolu_A|route-suffix"),
    ]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    block, = _media_blocks(msgs)
    assert block["source"] == Q12


def test_file_path_argument_key_is_recognised(tmp_path: Path) -> None:
    msgs = [
        _tool_call_msg("toolu_A", Q12, key="file_path"),
        _tool_result_msg("toolu_A"),
    ]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    block, = _media_blocks(msgs)
    assert block["source"] == Q12


def test_origin_mechanism_is_tool_name_agnostic(tmp_path: Path) -> None:
    msgs = [
        _tool_call_msg("toolu_A", Q12, name="some_future_viewer_tool"),
        _tool_result_msg("toolu_A", name="some_future_viewer_tool"),
    ]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    block, = _media_blocks(msgs)
    assert block["source"] == Q12


# --- fallback ladder -----------------------------------------------------

def test_media_with_no_preceding_call_gets_neutral_marker(tmp_path: Path) -> None:
    msgs = [_tool_result_msg("toolu_orphan")]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    block, = _media_blocks(msgs)
    assert block["source"] == _UNKNOWN_ORIGIN
    assert "file://" not in json.dumps(msgs)
    assert str(tmp_path) not in json.dumps(msgs)


def test_neutral_marker_is_not_mistakable_for_a_path(tmp_path: Path) -> None:
    msgs = [_tool_result_msg(None)]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    block, = _media_blocks(msgs)
    assert not block["source"].startswith("/")
    assert "://" not in block["source"]


def test_user_attachment_does_not_inherit_the_agents_last_path(tmp_path: Path) -> None:
    """A user-supplied image is not the file the agent last wrote."""
    msgs = [
        _tool_call_msg("toolu_A", "/root/workspace/report.png", name="write"),
        _envelope({"role": "user", "content": [_image_block()]}),
    ]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    block, = _media_blocks(msgs)
    assert block["source"] == _UNKNOWN_ORIGIN


def test_a_consumed_call_is_not_reused_by_a_later_orphan(tmp_path: Path) -> None:
    msgs = [
        _tool_call_msg("toolu_A", Q12),
        _tool_result_msg("toolu_A"),
        _tool_result_msg(None),
    ]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    assert [b["source"] for b in _media_blocks(msgs)] == [Q12, _UNKNOWN_ORIGIN]


# --- the other two media encodings ---------------------------------------

def test_anthropic_source_dict_gets_container_path(tmp_path: Path) -> None:
    result = _envelope({
        "role": "toolResult", "toolCallId": "toolu_A", "toolName": "read",
        "content": [{
            "type": "image",
            "source": {
                "type": "base64", "media_type": "image/png", "data": PNG_B64,
            },
        }],
    })
    msgs = [_tool_call_msg("toolu_A", Q12), result]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    block, = _media_blocks(msgs)
    assert block["source"] == Q12
    assert block["mimeType"] == "image/png"
    assert _artifact_files(tmp_path)[0].read_bytes() == PNG_BYTES


def test_data_uri_string_source_gets_container_path(tmp_path: Path) -> None:
    result = _envelope({
        "role": "toolResult", "toolCallId": "toolu_A", "toolName": "read",
        "content": [{
            "type": "image", "source": "data:image/png;base64," + PNG_B64,
        }],
    })
    msgs = [_tool_call_msg("toolu_A", Q12), result]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    block, = _media_blocks(msgs)
    assert block["source"] == Q12
    assert _artifact_files(tmp_path)[0].read_bytes() == PNG_BYTES


def test_remote_url_source_is_left_alone(tmp_path: Path) -> None:
    result = _envelope({
        "role": "toolResult", "toolCallId": "toolu_A", "toolName": "read",
        "content": [{
            "type": "image",
            "source": {"type": "url", "url": "https://example.test/a.png"},
        }],
    })
    msgs = [_tool_call_msg("toolu_A", Q12), result]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    block, = _media_blocks(msgs)
    assert block["source"] == "https://example.test/a.png"


# --- envelopes, nesting, idempotency -------------------------------------

def test_hint_wrapped_envelopes_still_resolve_origin(tmp_path: Path) -> None:
    msgs = [
        {"is_accepted": 0, "hints": None, "message": _tool_call_msg("toolu_A", Q12)},
        {"is_accepted": 1, "hints": "keep going",
         "message": _tool_result_msg("toolu_A")},
    ]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    blocks = [
        b for m in msgs
        for b in m["message"]["message"]["content"]
        if b.get("type") == "image"
    ]
    assert [b["source"] for b in blocks] == [Q12]


def test_nested_content_blocks_inherit_the_same_origin(tmp_path: Path) -> None:
    result = _envelope({
        "role": "toolResult", "toolCallId": "toolu_A", "toolName": "read",
        "content": [{
            "type": "tool_result",
            "content": [_image_block(), _image_block()],
        }],
    })
    msgs = [_tool_call_msg("toolu_A", Q12), result]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    nested = result["message"]["content"][0]["content"]
    assert [b["source"] for b in nested] == [Q12, Q12]


def test_second_pass_does_not_mangle_an_already_rewritten_block(
    tmp_path: Path,
) -> None:
    msgs = [_tool_call_msg("toolu_A", Q12), _tool_result_msg("toolu_A")]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)
    first = json.dumps(msgs)
    files_after_first = len(_artifact_files(tmp_path))

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    assert json.dumps(msgs) == first
    assert len(_artifact_files(tmp_path)) == files_after_first


def test_openclaw_container_path_source_is_untouched(tmp_path: Path) -> None:
    src = "/home/node/.openclaw/workspace/shot.png"
    msgs = [_envelope({
        "role": "toolResult", "toolName": "read",
        "content": [{"type": "image", "source": src}],
    })]

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    block, = _media_blocks(msgs)
    assert block["source"] == src


# --- base_url (served-artifact) mode -------------------------------------

def test_base_url_mode_serves_a_client_resolvable_url(tmp_path: Path) -> None:
    msgs = [_tool_call_msg("toolu_A", Q12), _tool_result_msg("toolu_A")]

    replace_inline_media_with_files(
        msgs, TASK_ID, tmp_path, base_url="https://cdn.test/media"
    )

    block, = _media_blocks(msgs)
    assert block["source"].startswith("https://cdn.test/media/%s/" % TASK_ID)
    assert "file://" not in json.dumps(msgs)
    assert str(tmp_path) not in json.dumps(msgs)


# --- non-media messages are untouched ------------------------------------

@pytest.mark.parametrize("content", [None, "plain string", [], [{"type": "text"}]])
def test_messages_without_media_are_unchanged(tmp_path: Path, content) -> None:
    msgs = [_envelope({"role": "assistant", "content": content})]
    before = json.dumps(msgs)

    replace_inline_media_with_files(msgs, TASK_ID, tmp_path)

    assert json.dumps(msgs) == before
    assert not _artifact_files(tmp_path)
