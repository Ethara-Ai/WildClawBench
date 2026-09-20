"""Engine ladder of environment/skills/audio-extract/scripts/transcribe.sh.

    whisper.cpp -> sidecar -> openai-whisper

Every rung is a stub (shell scripts on PATH, a `whisper` module on PYTHONPATH),
so this runs offline and pins the ORDER, the fall-through on failure, and the
exit code when the last rung dies. Each stub appends its name to a shared log.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "environment" / "skills" / "audio-extract" / "scripts" / "transcribe.sh"

_FFMPEG = """#!/usr/bin/env bash
echo ffmpeg >> "$LADDER_LOG"
out="${@: -1}"
printf 'RIFFfake' > "$out"
"""

_WHISPER_CLI = """#!/usr/bin/env bash
echo cpp >> "$LADDER_LOG"
[[ "${CPP_FAIL:-0}" == "1" ]] && exit 1
base=""
while [[ $# -gt 0 ]]; do [[ "$1" == "-of" ]] && base="$2"; shift; done
printf 'from whisper.cpp\\n' > "${base}.txt"
"""

_CURL = """#!/usr/bin/env bash
echo sidecar >> "$LADDER_LOG"
out=""
while [[ $# -gt 0 ]]; do [[ "$1" == "-o" ]] && out="$2"; shift; done
case "${SIDECAR_MODE:-ok}" in
  ok)        printf '{"text": "from sidecar"}' > "$out"; printf '200' ;;
  http500)   printf '{"error": "boom"}' > "$out"; printf '500' ;;
  transport) exit 7 ;;
esac
"""

_WHISPER_PY = """
import os
# Importable without side effects: transcribe.sh probes `import whisper` to decide
# whether the rung exists at all, and that probe must not count as walking it.
class _M:
    def transcribe(self, wav):
        return {"text": " from openai-whisper "}
def load_model(name, download_root=None):
    open(os.environ["LADDER_LOG"], "a").write("py\\n")
    assert name == "small"
    if os.environ.get("PY_FAIL") == "1":
        raise RuntimeError("weights corrupt")
    return _M()
"""

def _run(tmp_path, *, cpp=False, sidecar=False, py=False, env=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stubs = {"ffmpeg": _FFMPEG, "curl": _CURL}
    if cpp:
        stubs["whisper-cli"] = _WHISPER_CLI
    for name, body in stubs.items():
        f = bin_dir / name
        f.write_text(body, encoding="utf-8")
        f.chmod(0o755)

    model = tmp_path / "ggml.bin"
    if cpp:
        model.write_bytes(b"x")
    py_dir = tmp_path / "py"
    py_dir.mkdir()
    weights = tmp_path / "weights"
    if py:
        (py_dir / "whisper.py").write_text(_WHISPER_PY, encoding="utf-8")
        weights.mkdir()
    else:
        # Shadow any real openai-whisper on the test host.
        (py_dir / "whisper.py").write_text("raise ImportError('absent')\n", encoding="utf-8")

    media = tmp_path / "memo.m4a"
    media.write_bytes(b"audio")
    log = tmp_path / "ladder.log"
    full_env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "PYTHONPATH": str(py_dir),
        "LADDER_LOG": str(log),
        "WCB_WHISPER_MODEL": str(model),
        "WCB_WHISPER_MODEL_DIR": str(weights),
        "WCB_TRANSCRIBE_SCRATCH_DIR": str(tmp_path / "scratch"),
        "HOME": str(tmp_path),
    }
    if sidecar:
        full_env["WCB_AUDIO_TRANSCRIBE_URL"] = "http://sidecar:4000/v1/audio/transcriptions"
    full_env.update(env or {})
    proc = subprocess.run(["bash", str(_SCRIPT), str(media)], env=full_env,
                          capture_output=True, text=True, timeout=60)
    walked = [l for l in log.read_text().split() if l != "ffmpeg"] if log.exists() else []
    return proc, walked


def test_whisper_cpp_wins_when_present(tmp_path):
    proc, walked = _run(tmp_path, cpp=True, sidecar=True, py=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "from whisper.cpp\n"
    assert walked == ["cpp"]


def test_sidecar_beats_openai_whisper(tmp_path):
    proc, walked = _run(tmp_path, sidecar=True, py=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "from sidecar\n"
    assert walked == ["sidecar"]


def test_openai_whisper_alone_is_enough(tmp_path):
    # The OAuth / Bedrock-only batch: no whisper route, image bakes openai-whisper.
    proc, walked = _run(tmp_path, py=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "from openai-whisper\n"
    assert walked == ["py"]


@pytest.mark.parametrize("mode", ["http500", "transport"])
def test_failed_sidecar_falls_to_openai_whisper(tmp_path, mode):
    proc, walked = _run(tmp_path, sidecar=True, py=True, env={"SIDECAR_MODE": mode})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "from openai-whisper\n"
    assert walked == ["sidecar", "py"]
    assert "trying the next available engine" in proc.stderr


def test_failed_whisper_cpp_falls_through_without_leaking_output(tmp_path):
    proc, walked = _run(tmp_path, cpp=True, sidecar=True, env={"CPP_FAIL": "1"})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "from sidecar\n"
    assert walked == ["cpp", "sidecar"]


@pytest.mark.parametrize("mode,rc", [("http500", 6), ("transport", 5)])
def test_last_rung_failure_keeps_its_exit_code(tmp_path, mode, rc):
    proc, _ = _run(tmp_path, sidecar=True, env={"SIDECAR_MODE": mode})
    assert proc.returncode == rc
    assert proc.stdout == ""


def test_openai_whisper_failure_as_last_rung_exits_8(tmp_path):
    proc, walked = _run(tmp_path, py=True, env={"PY_FAIL": "1"})
    assert proc.returncode == 8
    assert walked == ["py"]


def test_no_engine_at_all_exits_3_and_names_every_rung(tmp_path):
    proc, walked = _run(tmp_path)
    assert proc.returncode == 3
    assert walked == []
    for needle in ("whisper.cpp", "WCB_AUDIO_TRANSCRIBE_URL", "openai-whisper"):
        assert needle in proc.stderr


def test_weights_dir_without_the_package_does_not_claim_the_rung(tmp_path):
    (tmp_path / "weights").mkdir()
    proc, walked = _run(tmp_path)  # py=False -> `import whisper` raises
    assert proc.returncode == 3
    assert walked == []


def test_forced_local_skips_the_sidecar(tmp_path):
    proc, walked = _run(tmp_path, sidecar=True, py=True,
                        env={"WCB_TRANSCRIBE_ENGINE": "local"})
    assert proc.returncode == 0, proc.stderr
    assert walked == ["py"]


def test_forced_sidecar_skips_local_engines(tmp_path):
    proc, walked = _run(tmp_path, cpp=True, sidecar=True, py=True,
                        env={"WCB_TRANSCRIBE_ENGINE": "sidecar"})
    assert proc.returncode == 0, proc.stderr
    assert walked == ["sidecar"]


def test_forced_local_with_nothing_local_exits_3(tmp_path):
    proc, _ = _run(tmp_path, sidecar=True, env={"WCB_TRANSCRIBE_ENGINE": "local"})
    assert proc.returncode == 3


def test_invalid_forced_engine_exits_2(tmp_path):
    proc, _ = _run(tmp_path, sidecar=True, env={"WCB_TRANSCRIBE_ENGINE": "cloud"})
    assert proc.returncode == 2
