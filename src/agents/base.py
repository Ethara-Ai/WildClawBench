from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
import subprocess
from typing import Any, Callable


@dataclass(frozen=True)
class AgentTaskSpec:
    task_id: str
    task: dict[str, Any]
    workspace_path: str
    prompt: str
    timeout_seconds: int
    output_dir: Path
    model: str
    thinking: str | None = None
    models_config: dict[str, Any] | None = None
    lobster: dict[str, Any] | None = None
    # Multi-turn / staged-injection support (ClawMark-style). When ``turns`` is
    # set, the agent is invoked once per turn on the SAME session (context is
    # retained). ``turns[0]`` is the initial task prompt; each later entry is a
    # follow-up message (a neutral "re-verify" nudge for silent injection).
    # Before running turn ``i`` (for i >= 1), the runner calls ``before_turn(i)``
    # while the agent is idle, which applies that stage's silent mock-data
    # injection. Only the openclaw backend honours these fields; other backends
    # run a single turn from ``prompt`` and ignore them.
    turns: tuple[str, ...] | None = None
    before_turn: Callable[[int], None] | None = None
    # Multi-agent (sub-agent spawning) support. When ``multi_agent_enabled`` is
    # set, the openclaw runner injects the spawn-subagent skill into the agent
    # container before the turn loop, so the agent can spawn bounded sub-agent
    # sessions. ``multi_agent_config`` carries the per-turn expectations and
    # allowed-tools config (see ``src/utils/spawn_tree_checks.py``). Only the
    # openclaw backend honours these fields; other backends ignore them.
    multi_agent_enabled: bool = False
    multi_agent_config: dict[str, Any] | None = None
    # Pull-based turn feed (see ``src/utils/turn_source.py``). When set, the
    # openclaw runner pulls each turn's message from it instead of iterating the
    # static ``turns`` tuple — used by interactive Mode 2 (HumanTurnSource) to
    # let a human pace the turns. ``before_turn`` still fires per boundary.
    # None = static path. Openclaw-only, like ``turns``.
    turn_source: Any | None = None


@dataclass
class AgentExecution:
    elapsed_time: float
    error: str | None = None
    gateway_proc: subprocess.Popen[str] | None = None
    agent_proc: subprocess.Popen[str] | None = None
    # Turn accounting (additive). ``turns_completed`` = number of turns whose
    # agent invocation completed without timing out; a turn-0 timeout yields 0.
    # ``timed_out_turn`` = index of the turn that timed out (remaining turns
    # were dropped), None otherwise. The openclaw runner sets these
    # accurately; single-shot backends leave the defaults (1 = the one
    # successful turn) and set turns_completed=0 only via their error paths
    # if they ever adopt the fields.
    turns_completed: int = 1
    timed_out_turn: int | None = None
    # ``turns_planned`` = the schedule's turn count when the turn source knows
    # it (None for open-ended sources and single-shot backends, which cannot
    # under-deliver). Gate incompleteness on turns_completed < turns_planned,
    # NOT on timed_out_turn: a timeout is only one of several ways a run ends
    # short (turn-source errors and unsupported multi-turn backends are others).
    turns_planned: int | None = None
    # True when the multi-agent stall-recovery hook injected an extra real
    # turn (_recover_synthesis_if_stalled) — a legitimate repair, recorded so
    # a reviewer counting messages sees why the transcript has one more turn.
    recovery_turn_fired: bool = False
    # Turn indices whose stall-guarded retry re-sent the scripted message on
    # the same session (BUGREPORT_turn_completion.md §2.3). The retry may leave
    # the turn duplicated in-session; only the runner can distinguish that
    # from a turn-source scripting bug, so it is recorded at the moment the
    # retry fires rather than re-derived from the transcript.
    turns_duplicated: list[int] = field(default_factory=list)
    # Turn indices whose invocation finished but produced zero LLM traffic
    # (dead-route fast-fail; see LLM_TIMEOUT_EVIDENCE). Not counted in
    # turns_completed, so these runs flag run_incomplete.
    turns_empty: list[int] = field(default_factory=list)
    # Turn indices that produced real LLM traffic but ended in timeout/stall
    # before completing (the invocation did genuine work, then the gateway
    # wedged or the budget ran out). NOT counted in turns_completed — the run
    # still flags run_incomplete — but recorded so a reviewer can distinguish
    # "did 85% then stalled" from "produced nothing", which turns_completed
    # alone collapses to the same number.
    turns_partial: list[int] = field(default_factory=list)


class BaseAgent(ABC):
    @property
    @abstractmethod
    def expects_gateway(self) -> bool:
        """Whether this backend starts a long-running gateway process."""

    @property
    @abstractmethod
    def transcript_container_path(self) -> str:
        """Path to chat transcript inside the runtime container."""

    def prepare_grading_transcript(self, task_id: str) -> str:
        """Prepare and return the transcript path used for grading."""
        _ = task_id
        return self.transcript_container_path

    @abstractmethod
    def run_task(self, spec: AgentTaskSpec) -> AgentExecution:
        """Execute a task and return process handles, timing and error state."""

    @abstractmethod
    def collect_usage(self, task_id: str, output_dir: Path, elapsed_time: float) -> dict[str, Any]:
        """Collect token usage and cost for one task."""
