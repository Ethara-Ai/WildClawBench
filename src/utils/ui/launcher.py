"""Full-screen Textual launcher form for the harness (`wcb run [task]`).

Replaces the long ``eval/run_batch.py`` flag soup with a GUI-like config
screen: the user picks Task / Agent Backend / Model / OAuth Account /
Parallel Workers from dropdowns, optionally opens the "Advanced" section,
and presses **Start**. The selections come back as a plain dict which
``eval/wcb.py`` maps onto the existing run_batch argv (so all validation
and defaults still live in ``src/utils/cli_args.py``).

Design notes:
  * All Textual imports are guarded (same convention as ``tui.py``) so this
    module imports cleanly when ``textual`` is absent; ``launcher_available``
    reports that and the caller can fall back to plain CLI flags.
  * Last-used selections persist to ``~/.wcb/launcher.json`` so repeat runs
    are a two-keystroke affair (Enter on Start).
  * ``--stream`` is not a choice here: streaming is always on for launcher
    runs. ``--interactive`` is resolved automatically from the task layout
    (multi-turn ``prompts.txt`` => interactive) unless overridden under
    Advanced.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.widgets import (
        Button, Collapsible, Footer, Header, Input, Label, Select, Static, Switch,
    )
    _TEXTUAL_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when textual is missing
    _TEXTUAL_AVAILABLE = False


AUTO = "auto"
ON = "on"
OFF = "off"

BACKENDS = ["openclaw", "claudecode", "codex", "hermesagent"]

# agents.defaults.thinkingDefault values openclaw recognizes ("default" = omit
# the flag and let the backend decide).
THINKING_LEVELS = ["default", "off", "minimal", "low", "medium", "high", "xhigh"]

# Sidecar-served model ids (see src/utils/litellm_sidecar.py model_list).
MODEL_CHOICES = [
    "claude-opus-4.7",
    "claude-opus-4.8",
    "claude-opus-4-6",
    "claude-fable-5",
    "claude-sonnet-4-6",
    "gpt-5.5",
    "gpt-5.6",
]

PREFS_PATH = Path.home() / ".wcb" / "launcher.json"
OAUTH_POOL_DIR = Path.home() / ".wcb" / "oauth_pool"

# Sentinel Select values (Select options must be hashable, non-None).
_OAUTH_NONE = "__none__"
_OAUTH_DEFAULT = "default"


def launcher_available() -> bool:
    return _TEXTUAL_AVAILABLE


def discover_tasks(root: Path) -> List[str]:
    """Task dirs under input/ that look runnable (prompt.txt or prompts.txt)."""
    tasks: List[str] = []
    input_dir = root / "input"
    if input_dir.is_dir():
        for entry in sorted(input_dir.iterdir()):
            if entry.is_dir() and (
                (entry / "prompt.txt").is_file() or (entry / "prompts.txt").is_file()
            ):
                tasks.append(str(entry.relative_to(root)))
    return tasks


def discover_oauth_accounts() -> List[Tuple[str, str]]:
    """(label, value) pairs for the OAuth Account dropdown.

    Values are the strings WCB_CC_ACCOUNT_POOL accepts: a JSON file path, or
    the special ``default`` keychain spec resolved by src/utils/claude_oauth.
    """
    accounts: List[Tuple[str, str]] = []
    if OAUTH_POOL_DIR.is_dir():
        for f in sorted(OAUTH_POOL_DIR.glob("*.json")):
            accounts.append((f.stem, str(f)))
    accounts.append(("keychain (default)", _OAUTH_DEFAULT))
    accounts.append(("none — API key / Bedrock", _OAUTH_NONE))
    return accounts


def is_multiturn_task(root: Path, task: str) -> bool:
    return (root / task / "prompts.txt").is_file()


def load_prefs() -> Dict[str, Any]:
    try:
        with open(PREFS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_prefs(prefs: Dict[str, Any]) -> None:
    try:
        PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(PREFS_PATH, "w", encoding="utf-8") as fh:
            json.dump(prefs, fh, indent=2)
    except Exception:
        pass  # prefs are a convenience, never fatal


def resolve_interactive(config: Dict[str, Any], root: Path, isatty: bool) -> bool:
    """Auto mode: interactive iff multi-turn task + openclaw + parallel 1 + tty."""
    mode = config.get("interactive", AUTO)
    if mode == ON:
        return True
    if mode == OFF:
        return False
    return (
        isatty
        and config.get("backend") == "openclaw"
        and int(config.get("parallel", 1)) == 1
        and bool(config.get("task"))
        and is_multiturn_task(root, config["task"])
    )


def config_to_argv(config: Dict[str, Any], root: Path, isatty: bool = True) -> List[str]:
    """Map launcher selections onto the canonical run_batch argv."""
    argv = ["--task", config["task"]]
    argv += ["--agent-backend", config["backend"]]
    model = (config.get("custom_model") or "").strip() or config["model"]
    argv += ["--model", model]
    argv += ["--parallel", str(int(config.get("parallel", 1)))]
    if config.get("litellm", True):
        argv.append("--litellm")
    else:
        argv.append("--no-litellm")
    if config.get("mock_stack", True):
        argv.append("--mock-stack")
    if config.get("oauth_account") not in (None, "", _OAUTH_NONE):
        argv.append("--use-claude-oauth")
    if config.get("rebuild_mocks"):
        argv.append("--rebuild-mocks")
    if config.get("judge_council", ON) == OFF:
        argv.append("--no-judge-council")
    else:
        argv.append("--judge-council")
    if config.get("generate_tests", AUTO) == ON:
        argv.append("--generate-tests")
    elif config.get("generate_tests", AUTO) == OFF:
        argv.append("--no-generate-tests")
    thinking = (config.get("thinking") or "").strip()
    if thinking and thinking != "default":
        argv += ["--thinking", thinking]
    # Streaming is the launcher default; interactive is resolved, not asked.
    argv.append("--stream")
    if resolve_interactive(config, root, isatty):
        argv.append("--interactive")
    return argv


def oauth_env_overrides(config: Dict[str, Any]) -> Dict[str, str]:
    """Env vars the selected OAuth account implies (set before run_batch runs)."""
    account = config.get("oauth_account")
    if account in (None, "", _OAUTH_NONE):
        return {}
    return {
        "WCB_USE_CLAUDE_OAUTH": "1",
        "WCB_CC_ACCOUNT_POOL": account,
    }


if _TEXTUAL_AVAILABLE:

    class LauncherApp(App):
        """Config form. ``run()`` returns the selection dict, or None on quit."""

        TITLE = "WildClawBench — Run Configuration"
        BINDINGS = [
            Binding("ctrl+s", "start", "Start"),
            Binding("escape", "quit", "Quit"),
        ]
        CSS = """
        #form {
            padding: 1 2;
        }
        .field-label {
            margin-top: 1;
            color: $text-muted;
        }
        .switch-row {
            height: auto;
            margin-top: 1;
        }
        .switch-row Label {
            padding: 1 1 0 1;
        }
        #hint {
            color: $text-muted;
            margin-top: 1;
        }
        #start {
            margin-top: 1;
            width: 100%;
        }
        #error {
            color: $error;
            height: auto;
        }
        Collapsible {
            margin-top: 1;
        }
        """

        def __init__(self, root: Path, initial_task: Optional[str] = None) -> None:
            super().__init__()
            self._root = root
            self._initial_task = initial_task
            self._tasks = discover_tasks(root)
            self._accounts = discover_oauth_accounts()
            self._prefs = load_prefs()

        # -- helpers -------------------------------------------------------
        def _pref(self, key: str, fallback: Any) -> Any:
            return self._prefs.get(key, fallback)

        @staticmethod
        def _sel(options: List[str], wanted: Any, fallback: str) -> str:
            return wanted if wanted in options else fallback

        # -- UI ------------------------------------------------------------
        def compose(self) -> ComposeResult:
            yield Header()
            task_options = list(self._tasks)
            initial = self._initial_task
            if initial and initial not in task_options:
                task_options.insert(0, initial)
            task_value = initial or self._pref("task", None)
            if task_value not in task_options:
                task_value = task_options[0] if task_options else None

            account_values = [v for _, v in self._accounts]
            account_value = self._sel(
                account_values, self._pref("oauth_account", None),
                account_values[0] if account_values else _OAUTH_NONE)

            with VerticalScroll(id="form"):
                yield Label("Task", classes="field-label")
                yield Select(
                    [(t, t) for t in task_options],
                    value=task_value if task_value else Select.BLANK,
                    prompt="choose a task under input/",
                    id="task",
                )
                yield Label("Agent Backend", classes="field-label")
                yield Select(
                    [(b, b) for b in BACKENDS],
                    value=self._sel(BACKENDS, self._pref("backend", "openclaw"), "openclaw"),
                    allow_blank=False, id="backend",
                )
                yield Label("Model", classes="field-label")
                yield Select(
                    [(m, m) for m in MODEL_CHOICES],
                    value=self._sel(MODEL_CHOICES, self._pref("model", "claude-opus-4.7"),
                                    "claude-opus-4.7"),
                    allow_blank=False, id="model",
                )
                yield Label("OAuth Account (Claude Max subscription)", classes="field-label")
                yield Select(
                    [(label, value) for label, value in self._accounts],
                    value=account_value, allow_blank=False, id="oauth_account",
                )
                yield Label("Parallel Workers  (keep 1 — >1 races mock build / Bedrock throttling)",
                            classes="field-label")
                yield Select(
                    [(str(n), n) for n in (1, 2, 3, 4)],
                    value=int(self._pref("parallel", 1)) if int(self._pref("parallel", 1)) in (1, 2, 3, 4) else 1,
                    allow_blank=False, id="parallel",
                )
                yield Label("Repetitions (reps — sequential runs of the same task, pass@K)",
                            classes="field-label")
                yield Select(
                    [(str(n), n) for n in (1, 2, 3, 4, 5)],
                    value=int(self._pref("reps", 1)) if int(self._pref("reps", 1)) in (1, 2, 3, 4, 5) else 1,
                    allow_blank=False, id="reps",
                )

                with Collapsible(title="Advanced", collapsed=True):
                    with Horizontal(classes="switch-row"):
                        yield Switch(value=bool(self._pref("litellm", True)), id="litellm")
                        yield Label("LiteLLM sidecar routing (Bedrock/OpenAI)")
                    with Horizontal(classes="switch-row"):
                        yield Switch(value=bool(self._pref("mock_stack", True)), id="mock_stack")
                        yield Label("Mock-API stack (shared container)")
                    with Horizontal(classes="switch-row"):
                        yield Switch(value=bool(self._pref("rebuild_mocks", False)), id="rebuild_mocks")
                        yield Label("Force-rebuild mock image")
                    yield Label("Judge council (3-judge Bedrock council)", classes="field-label")
                    yield Select(
                        [("on", ON), ("off", OFF)],
                        value=self._sel([ON, OFF], self._pref("judge_council", ON), ON),
                        allow_blank=False, id="judge_council",
                    )
                    yield Label("Generate + execute pytest channel", classes="field-label")
                    yield Select(
                        [("auto", AUTO), ("on", ON), ("off", OFF)],
                        value=self._pref("generate_tests", AUTO),
                        allow_blank=False, id="generate_tests",
                    )
                    yield Label("Interactive (Mode 2) — auto: on for multi-turn tasks",
                                classes="field-label")
                    yield Select(
                        [("auto", AUTO), ("on", ON), ("off", OFF)],
                        value=self._pref("interactive", AUTO),
                        allow_blank=False, id="interactive",
                    )
                    yield Label("Thinking level", classes="field-label")
                    yield Select(
                        [(lvl, lvl) for lvl in THINKING_LEVELS],
                        value=self._sel(THINKING_LEVELS, self._pref("thinking", "default"), "default"),
                        allow_blank=False, id="thinking",
                    )
                    yield Label("Custom model id (overrides Model dropdown)", classes="field-label")
                    yield Input(value=str(self._pref("custom_model", "") or ""),
                                placeholder="e.g. openrouter/anthropic/claude-sonnet-4.6",
                                id="custom_model")

                yield Static("", id="error")
                yield Button("▶  Start", variant="success", id="start")
                yield Static("Enter/click Start (or Ctrl+S) to run — streaming is always on; "
                             "interactive mode is detected from the task.", id="hint")
            yield Footer()

        # -- collect + validate -------------------------------------------
        def _collect(self) -> Optional[Dict[str, Any]]:
            task = self.query_one("#task", Select).value
            if task in (None, Select.BLANK):
                self.query_one("#error", Static).update(
                    "Pick a task first (no runnable dirs found under input/?)")
                return None
            oauth = self.query_one("#oauth_account", Select).value
            if oauth == _OAUTH_NONE:
                oauth = None
            return {
                "task": str(task),
                "backend": str(self.query_one("#backend", Select).value),
                "model": str(self.query_one("#model", Select).value),
                "oauth_account": oauth,
                "parallel": int(self.query_one("#parallel", Select).value),
                "reps": int(self.query_one("#reps", Select).value),
                "litellm": bool(self.query_one("#litellm", Switch).value),
                "mock_stack": bool(self.query_one("#mock_stack", Switch).value),
                "rebuild_mocks": bool(self.query_one("#rebuild_mocks", Switch).value),
                "judge_council": str(self.query_one("#judge_council", Select).value),
                "generate_tests": str(self.query_one("#generate_tests", Select).value),
                "interactive": str(self.query_one("#interactive", Select).value),
                "thinking": str(self.query_one("#thinking", Select).value),
                "custom_model": str(self.query_one("#custom_model", Input).value or ""),
            }

        def action_start(self) -> None:
            config = self._collect()
            if config is None:
                return
            prefs = dict(config)
            prefs["oauth_account"] = config["oauth_account"] or _OAUTH_NONE
            save_prefs(prefs)
            self.exit(config)

        def on_button_pressed(self, event: "Button.Pressed") -> None:
            if event.button.id == "start":
                self.action_start()

else:  # pragma: no cover

    class LauncherApp:  # type: ignore[no-redef]
        def __init__(self, *a: Any, **kw: Any) -> None:
            raise RuntimeError("textual is not installed — launcher unavailable")


def run_launcher(root: Path, initial_task: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Show the form; returns the selection dict, or None if the user quit."""
    if not _TEXTUAL_AVAILABLE:
        return None
    app = LauncherApp(root, initial_task)
    return app.run()
