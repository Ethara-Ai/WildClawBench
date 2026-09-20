"""Offline audio transcription for judge evidence.

The judges accept no audio modality, so audio deliverables reach the
judge as TEXT: this module transcribes them host-side with a local
sherpa-onnx model (default: NVIDIA Parakeet TDT 0.6B v3 int8 — 6.34% WER,
beats hosted whisper-large-v3, CPU-only, no network at grade time).

Every import is guarded and every public function degrades to None instead
of raising: a host without sherpa-onnx / model files / PyAV grades exactly
as before (presence/duration markers, truncation-abstain downstream).
"""
from __future__ import annotations

import logging
import os
import struct
import wave
from pathlib import Path

logger = logging.getLogger(__name__)

_TRANSCRIPT_CHAR_CAP = 100_000
_RECOGNIZER = None
_RECOGNIZER_FAILED = False


def transcription_enabled() -> bool:
    return os.environ.get("WCB_JUDGE_AUDIO_TRANSCRIBE", "1").strip() != "0"


def _model_dir() -> Path | None:
    raw = os.environ.get("WCB_JUDGE_ASR_MODEL_DIR", "").strip()
    candidates = [Path(raw)] if raw else []
    candidates.append(Path.home() / ".wcb" / "asr")
    for c in candidates:
        if c.is_dir() and list(c.glob("*.onnx")):
            return c
    return None


def status() -> tuple[bool, str]:
    """(ready, detail) WITHOUT loading the model: a cheap preflight so a host that
    cannot transcribe says so up front instead of silently grading audio
    deliverables on a duration marker alone."""
    if not transcription_enabled():
        return False, "disabled (WCB_JUDGE_AUDIO_TRANSCRIBE=0)"
    import importlib.util
    if importlib.util.find_spec("sherpa_onnx") is None:
        return False, ("sherpa-onnx not installed "
                       "(pip install -r requirements.txt, or bash script/setup_judge_asr.sh)")
    mdir = _model_dir()
    if mdir is None:
        return False, ("no model under WCB_JUDGE_ASR_MODEL_DIR or ~/.wcb/asr "
                       "(bash script/setup_judge_asr.sh)")
    if not (mdir / "tokens.txt").is_file():
        return False, f"{mdir} has no tokens.txt"
    return True, str(mdir)


def _load_recognizer():
    # Process-cached: model load is seconds, transcription is milliseconds.
    # _RECOGNIZER_FAILED pins a failed load so a broken install logs once
    # instead of re-attempting per audio file.
    global _RECOGNIZER, _RECOGNIZER_FAILED
    if _RECOGNIZER is not None or _RECOGNIZER_FAILED:
        return _RECOGNIZER
    try:
        import sherpa_onnx
    except ImportError:
        _RECOGNIZER_FAILED = True
        logger.info("judge ASR unavailable: sherpa-onnx not installed")
        return None
    mdir = _model_dir()
    if mdir is None:
        _RECOGNIZER_FAILED = True
        logger.info(
            "judge ASR unavailable: no model dir (set WCB_JUDGE_ASR_MODEL_DIR "
            "or place a sherpa-onnx model under ~/.wcb/asr)")
        return None
    try:
        def _one(pattern: str) -> str:
            hits = sorted(mdir.glob(pattern))
            return str(hits[0]) if hits else ""

        encoder = _one("encoder*.onnx")
        tokens = str(mdir / "tokens.txt")
        if encoder:
            _RECOGNIZER = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=encoder,
                decoder=_one("decoder*.onnx"),
                joiner=_one("joiner*.onnx"),
                tokens=tokens,
                num_threads=int(os.environ.get("WCB_JUDGE_ASR_THREADS", "4")),
                model_type="nemo_transducer",
            )
        else:
            model = _one("model*.onnx")
            _RECOGNIZER = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=model, tokens=tokens,
                num_threads=int(os.environ.get("WCB_JUDGE_ASR_THREADS", "4")),
            )
    except Exception as exc:  # noqa: BLE001 - grading must never fail on ASR
        _RECOGNIZER_FAILED = True
        logger.warning("judge ASR model load failed (%s): %s", mdir, str(exc)[:200])
        return None
    logger.info("judge ASR ready: %s", mdir)
    return _RECOGNIZER


def _decode_wav(path: Path) -> tuple[list[float], int] | None:
    # Stdlib decode for PCM wav: no PyAV needed for the dominant format.
    try:
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            n_ch = w.getnchannels()
            width = w.getsampwidth()
            raw = w.readframes(w.getnframes())
    except Exception:  # noqa: BLE001
        return None
    if width != 2:
        return None
    count = len(raw) // 2
    ints = struct.unpack(f"<{count}h", raw[: count * 2])
    if n_ch > 1:
        ints = ints[::n_ch]
    return [s / 32768.0 for s in ints], rate


def _decode_with_av(path: Path) -> tuple[list[float], int] | None:
    try:
        import av
    except ImportError:
        return None
    try:
        samples: list[float] = []
        with av.open(str(path)) as container:
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            for frame in container.decode(audio=0):
                for rf in resampler.resample(frame):
                    arr = rf.to_ndarray().flatten()
                    samples.extend(float(x) / 32768.0 for x in arr)
        return (samples, 16000) if samples else None
    except Exception:  # noqa: BLE001
        return None


def wav_duration_marker(path: Path) -> str | None:
    """Tier-0 stdlib fallback: duration/rate for 'delivers a 30s wav' criteria."""
    try:
        with wave.open(str(path), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            if rate <= 0:
                return None
            return (f"audio {frames / rate:.1f}s, {rate} Hz, "
                    f"{w.getnchannels()} channel(s)")
    except Exception:  # noqa: BLE001
        return None


def transcribe(path: Path) -> str | None:
    """Offline transcript of an audio deliverable, or None (degrade to marker)."""
    if not transcription_enabled():
        return None
    rec = _load_recognizer()
    if rec is None:
        return None
    decoded = None
    if path.suffix.lower() == ".wav":
        decoded = _decode_wav(path)
    if decoded is None:
        decoded = _decode_with_av(path)
    if decoded is None:
        return None
    samples, rate = decoded
    if not samples:
        return None
    try:
        stream = rec.create_stream()
        stream.accept_waveform(rate, samples)
        rec.decode_stream(stream)
        text = (stream.result.text or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("judge ASR transcription failed for %s: %s",
                       path.name, str(exc)[:200])
        return None
    if len(text) > _TRANSCRIPT_CHAR_CAP:
        # Same rule as document evidence: text beyond the cap is not sent, and
        # the judge is told so rather than reading a cut transcript as complete.
        text = (text[:_TRANSCRIPT_CHAR_CAP]
                + f"\n... [transcript truncated at {_TRANSCRIPT_CHAR_CAP} chars]")
    return text or None
