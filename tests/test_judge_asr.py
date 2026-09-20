"""Judge-side audio transcription (src/utils/judge_asr.py).

Two layers:
  * status() / degradation / truncation — pure unit tests, always run.
  * a REAL transcription of a spoken sample through sherpa-onnx — runs only on a
    host that has the optional deps + model (bash script/setup_judge_asr.sh) and
    otherwise skips with the reason status() gives, so "ASR silently absent" shows
    up in the test report instead of hiding behind mocked wiring.
"""
from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.utils import grading, judge_asr  # noqa: E402


def _write_tone(path: Path, seconds: float = 1.0, rate: int = 16000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(
            struct.pack("<h", int(8000 * math.sin(i / 10)))
            for i in range(int(seconds * rate))))
    return path


@pytest.fixture(autouse=True)
def _fresh_recognizer(monkeypatch):
    # The recognizer (and a failed load) is process-cached; isolate every test.
    monkeypatch.setattr(judge_asr, "_RECOGNIZER", None)
    monkeypatch.setattr(judge_asr, "_RECOGNIZER_FAILED", False)
    monkeypatch.delenv("WCB_JUDGE_AUDIO_TRANSCRIBE", raising=False)


# --------------------------------------------------------------------------- #
# status(): the preflight that makes a missing install visible
# --------------------------------------------------------------------------- #
def test_status_reports_the_kill_switch(monkeypatch):
    monkeypatch.setenv("WCB_JUDGE_AUDIO_TRANSCRIBE", "0")
    ok, detail = judge_asr.status()
    assert not ok and detail.startswith("disabled")


def test_status_names_the_missing_package(monkeypatch):
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    ok, detail = judge_asr.status()
    assert not ok and "sherpa-onnx not installed" in detail
    assert "requirements.txt" in detail and "setup_judge_asr.sh" in detail


def test_status_names_the_missing_model(monkeypatch, tmp_path):
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setenv("WCB_JUDGE_ASR_MODEL_DIR", str(tmp_path / "nope"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ok, detail = judge_asr.status()
    assert not ok and "setup_judge_asr.sh" in detail


def test_status_requires_tokens_next_to_the_model(monkeypatch, tmp_path):
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    (tmp_path / "encoder.int8.onnx").write_bytes(b"x")
    monkeypatch.setenv("WCB_JUDGE_ASR_MODEL_DIR", str(tmp_path))
    ok, detail = judge_asr.status()
    assert not ok and "tokens.txt" in detail
    (tmp_path / "tokens.txt").write_text("a 0\n", encoding="utf-8")
    assert judge_asr.status() == (True, str(tmp_path))


def test_status_does_not_load_the_model(monkeypatch, tmp_path):
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    (tmp_path / "encoder.int8.onnx").write_bytes(b"x")
    (tmp_path / "tokens.txt").write_text("a 0\n", encoding="utf-8")
    monkeypatch.setenv("WCB_JUDGE_ASR_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(judge_asr, "_load_recognizer",
                        lambda: pytest.fail("status() must stay cheap"))
    assert judge_asr.status()[0]


# --------------------------------------------------------------------------- #
# degradation + truncation
# --------------------------------------------------------------------------- #
def test_unavailable_asr_reaches_the_judge_as_an_explicit_marker(monkeypatch, tmp_path):
    monkeypatch.setattr(judge_asr, "_load_recognizer", lambda: None)
    wav = _write_tone(tmp_path / "memo.wav")
    assert judge_asr.transcribe(wav) is None
    block, _images = grading._deliverable_evidence_marker(wav, "memo.wav")
    assert "audio transcript unavailable" in block
    assert "audio 1.0s, 16000 Hz" in block
    assert "transcribed offline" not in block


class _FakeRecognizer:
    def __init__(self, text):
        self._text = text

    def create_stream(self):
        rec = self

        class _Stream:
            result = type("R", (), {"text": rec._text})()

            def accept_waveform(self, rate, samples):
                pass
        return _Stream()

    def decode_stream(self, stream):
        pass


def test_a_cut_transcript_says_it_was_cut(monkeypatch, tmp_path):
    monkeypatch.setattr(judge_asr, "_TRANSCRIPT_CHAR_CAP", 50)
    monkeypatch.setattr(judge_asr, "_load_recognizer", lambda: _FakeRecognizer("w" * 200))
    out = judge_asr.transcribe(_write_tone(tmp_path / "long.wav"))
    assert out.startswith("w" * 50)
    assert out.endswith("[transcript truncated at 50 chars]")


def test_a_short_transcript_is_untouched(monkeypatch, tmp_path):
    monkeypatch.setattr(judge_asr, "_load_recognizer", lambda: _FakeRecognizer("  hello there  "))
    assert judge_asr.transcribe(_write_tone(tmp_path / "s.wav")) == "hello there"


# --------------------------------------------------------------------------- #
# the real thing
# --------------------------------------------------------------------------- #
def _real_sample() -> Path | None:
    mdir = judge_asr._model_dir()
    if mdir is None:
        return None
    sample = mdir / "test_wavs" / "en.wav"
    return sample if sample.is_file() else None


def test_real_transcription_end_to_end():
    ok, detail = judge_asr.status()
    if not ok:
        pytest.skip(f"judge ASR not set up on this host: {detail}")
    sample = _real_sample()
    if sample is None:
        pytest.skip("model dir has no test_wavs/en.wav sample")
    text = judge_asr.transcribe(sample)
    assert text, "sherpa-onnx loaded but returned no text"
    lowered = text.lower()
    assert "country" in lowered and "ask" in lowered
    # ...and the same file, as a deliverable, reaches the judge as a transcript.
    block, _images = grading._deliverable_evidence_marker(sample, "speech.wav")
    assert "(audio, transcribed offline)" in block
    assert "country" in block.lower()
