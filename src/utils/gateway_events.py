"""Run-level friction events read off an openclaw ``gateway.log``.

Same on-disk-marker idea as injection_ok / task_gate: these events cost the
agent time or a tool call without failing the run, so they were visible only
in a log no score consumer opens (2026-09-19 ariadne_kostas_8c8579bb: a
"Shell heredoc execution" approval gate burned 120s and blocked the command
on a build the inline-eval guard reports as "not required"; one PNG failed
the image tool twice). Stdlib only; never raises.
"""
from __future__ import annotations

import re
from pathlib import Path

# Anchored on the gateway's own "<timestamp> [tag] ..." prefix: agent prose is
# echoed into gateway.log too and must not be able to forge an event.
_TS = r"^\d{4}-\d{2}-\d{2}T\S+ "
_PATTERNS = {
    "exec_obfuscation_flags": re.compile(_TS + r"\[exec\] obfuscation detected"),
    "exec_approval_waits": re.compile(_TS + r"\[ws\] .*exec\.approval\.waitDecision"),
    "approval_channel_errors": re.compile(_TS + r"\[ws\] .*Channel is required"),
    "image_tool_failures": re.compile(_TS + r"\[tools\] image failed"),
    "compaction_timeouts": re.compile(_TS + r"\[agent/embedded\] .*timed out during compaction"),
}
_WAIT_MS_RE = re.compile(r"exec\.approval\.waitDecision (\d+)ms")


def scan_gateway_log(path: Path) -> dict:
    """Non-zero event counts for *path*, or {} when clean/absent/unreadable."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    counts = dict.fromkeys(_PATTERNS, 0)
    wait_ms = 0
    for line in text.splitlines():
        for key, pat in _PATTERNS.items():
            if pat.search(line):
                counts[key] += 1
                if key == "exec_approval_waits":
                    m = _WAIT_MS_RE.search(line)
                    wait_ms += int(m.group(1)) if m else 0
    events = {k: v for k, v in counts.items() if v}
    if wait_ms:
        events["exec_approval_wait_ms"] = wait_ms
    return events
