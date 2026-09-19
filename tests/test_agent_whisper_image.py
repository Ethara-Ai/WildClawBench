"""Parity + wiring coverage for the agent runtime image's whisper layer.

`environment/skills/audio-extract/scripts/transcribe.sh` falls back to a LOCAL
openai-whisper install when the harness sidecar advertises no whisper route
(OAuth/Bedrock-only batches — the route is registered only with a whisper key,
`src/agents/openclaw/runner.py:601`). That fallback is only real if the image
the agent runs actually HAS whisper and its weights.

Delivered bundles get it from `src/utils/harbor/dockerfile.py`. Our own runtime
image gets it from `docker/agent-whisper.Dockerfile`, which layers the same
install onto the prebuilt `wildclawbench-ubuntu:v1.3` tarball. Two independent
recipes for one behavior drift silently — an agent would then hit a different
whisper in a bundle than in a trajectory run — so the first half of this file
pins them to the same four semantic facts:

    pip package      openai-whisper
    model name       small
    download_root    /opt/wb_whisper_models
    cache symlink    /root/.cache/whisper -> /opt/wb_whisper_models

The second half pins the wiring: run.sh must RUN the whisper image while still
ACQUIRING the plain tarball base, and the python-side default must agree with
run.sh, because that default — not run.sh's constant — is what actually selects
the container image (`src/utils/docker_utils.py:16`).

Everything here is static text analysis: no docker, no network.

Comment lines are stripped before any assertion. The Dockerfile's header
documents all four tokens in prose, so a substring check against the raw file
would pass even if the RUN instruction were deleted outright.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.utils.harbor.dockerfile import generate_harbor_dockerfile  # noqa: E402

_AGENT_DOCKERFILE = _REPO_ROOT / "docker" / "agent-whisper.Dockerfile"
_RUN_SH = _REPO_ROOT / "script" / "run.sh"
_TRANSCRIBE_SH = (
    _REPO_ROOT / "environment" / "skills" / "audio-extract" / "scripts" / "transcribe.sh"
)

_BASE_IMAGE = "wildclawbench-ubuntu:v1.3"
# HarnessV2: the default agent image stays the base; the whisper image is opt-in
# via DOCKER_IMAGE=wildclawbench-ubuntu:v1.4.
_AGENT_IMAGE = "wildclawbench-ubuntu:v1.4"
_DEFAULT_IMAGE = _BASE_IMAGE
_BASE_IMAGE_SHA = "88cd069e8ead5f4497093671a6d9e39e80259d75d4524f230b52ce439fcb2870"
_MODEL_DIR = "/opt/wb_whisper_models"


def _directives(dockerfile_text: str) -> list[str]:
    """Comment-free logical Dockerfile instructions (continuations joined)."""
    kept = [
        ln for ln in dockerfile_text.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    joined = "\n".join(kept).replace("\\\n", " ")
    return [re.sub(r"\s+", " ", ln).strip() for ln in joined.splitlines() if ln.strip()]


def _whisper_directive(dockerfile_text: str) -> str:
    matches = [d for d in _directives(dockerfile_text) if "openai-whisper" in d]
    assert len(matches) == 1, f"expected exactly one whisper RUN, got {len(matches)}"
    return matches[0]


@pytest.fixture(scope="module")
def agent_whisper_run() -> str:
    return _whisper_directive(_AGENT_DOCKERFILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def harbor_whisper_run() -> str:
    return _whisper_directive(generate_harbor_dockerfile())


class TestWhisperRecipeParity:
    """The runtime image's whisper layer must behave like the bundle's."""

    def test_installs_the_same_pip_package(self, agent_whisper_run, harbor_whisper_run):
        # Unversioned in both: v1.3's pip 22.0.2 cannot read the current
        # sdist's metadata and resolves an older release than harbor's pip
        # does, so the PACKAGE is the contract, never a pinned version.
        for run in (agent_whisper_run, harbor_whisper_run):
            assert re.search(r"pip install [^&]*\bopenai-whisper\b", run), run
            assert "--no-cache-dir" in run

    def test_preloads_the_same_model_into_the_same_root(
        self, agent_whisper_run, harbor_whisper_run
    ):
        pattern = r"load_model\(\s*'([^']+)'\s*,\s*download_root='([^']+)'\s*\)"
        agent = re.search(pattern, agent_whisper_run)
        harbor = re.search(pattern, harbor_whisper_run)
        assert agent is not None, agent_whisper_run
        assert harbor is not None, harbor_whisper_run
        assert agent.groups() == harbor.groups()
        assert agent.groups() == ("small", _MODEL_DIR)

    def test_creates_the_same_cache_symlink(self, agent_whisper_run, harbor_whisper_run):
        link = "ln -sfn %s /root/.cache/whisper" % _MODEL_DIR
        assert link in agent_whisper_run
        assert link in harbor_whisper_run

    @pytest.mark.skip(reason="HarnessV2's audio-extract/transcribe.sh uses whisper.cpp "
                             "(whisper-cli + ggml model), not the openai-whisper model dir")
    def test_model_dir_matches_the_skill_default(self, agent_whisper_run):
        # transcribe.sh gates its local rung on this directory existing and
        # passes it as download_root; a renamed path silently disables the rung.
        assert 'WCB_WHISPER_MODEL_DIR:-%s' % _MODEL_DIR in _TRANSCRIBE_SH.read_text(
            encoding="utf-8"
        )
        assert _MODEL_DIR in agent_whisper_run

    def test_break_system_packages_divergence_is_deliberate(
        self, agent_whisper_run, harbor_whisper_run
    ):
        # The one intentional difference. harbor's ubuntu:24.04 base ships an
        # EXTERNALLY-MANAGED marker and needs the flag; v1.3 is 22.04 with pip
        # 22.0.2, where the option does not exist and passing it is a usage
        # error that fails the build. Pinned so neither side is "harmonized"
        # into breaking.
        assert "--break-system-packages" in harbor_whisper_run
        assert "--break-system-packages" not in agent_whisper_run

    def test_build_reaches_the_network_past_the_baked_proxy(self, agent_whisper_run):
        # v1.3 bakes an unroutable corporate proxy into its image env
        # (src/utils/docker_utils.py:525). Without neutralizing it for the
        # build, both the pip install and the weight download die against it.
        assert re.search(r"unset\b[^&]*\bhttp_proxy\b", agent_whisper_run)
        assert "https_proxy" in agent_whisper_run

    def test_layers_onto_the_base_without_mutating_its_runtime_env(self):
        directives = _directives(_AGENT_DOCKERFILE.read_text(encoding="utf-8"))
        assert directives[0] == "FROM %s" % _BASE_IMAGE
        # Exactly one instruction total: the image must be v1.3 + whisper and
        # nothing else, so a rollback is a pure tag flip. A persisted ENV here
        # would also un-fix the proxy override the harness relies on at RUN time.
        assert len(directives) == 2, directives
        assert not [d for d in directives if d.startswith(("ENV ", "CMD ", "ENTRYPOINT ", "WORKDIR "))]


class TestRunShImageWiring:
    @pytest.fixture(scope="class")
    def run_sh(self) -> str:
        return _RUN_SH.read_text(encoding="utf-8")

    def test_agent_image_defaults_to_the_base_image(self, run_sh):
        assert 'readonly AGENT_IMAGE_DEFAULT="$BASE_IMAGE"' in run_sh

    def test_agent_image_is_resolved_from_the_python_side_variable(self, run_sh):
        # Preflight and run_batch have to read ONE channel. docker_utils reads
        # DOCKER_IMAGE from the environment, so run.sh resolves that same name
        # and exports the answer; whichever value wins, both sides see it.
        assert 'AGENT_IMAGE="${DOCKER_IMAGE:-$(env_file_value DOCKER_IMAGE)}"' in run_sh
        assert 'AGENT_IMAGE="${AGENT_IMAGE:-$AGENT_IMAGE_DEFAULT}"' in run_sh
        assert 'export DOCKER_IMAGE="$AGENT_IMAGE"' in run_sh

    def test_env_file_is_read_rather_than_sourced(self, run_sh):
        # .env holds live credentials; sourcing it to get one tag would put all
        # of them in the runner's environment.
        body = run_sh.split("env_file_value() {", 1)[1].split("\n}", 1)[0]
        assert "sed -n" in body
        assert "source" not in body
        assert "eval" not in body

    def test_no_version_tag_is_hardcoded_as_the_agent_image(self, run_sh):
        # The v1.3 literals that remain are the base tarball's, not the
        # runtime's: the tag, its SHA pin and the tar filename.
        for line in run_sh.splitlines():
            if "wildclawbench-ubuntu:v1.3" not in line:
                continue
            assert any(k in line for k in
                       ("BASE_IMAGE", "AGENT_TAR_PATH", "#")), line

    def test_base_image_named_once_and_still_acquired(self, run_sh):
        assert 'readonly BASE_IMAGE="%s"' % _BASE_IMAGE in run_sh
        # The tarball is still the distributed artifact — v1.4 has none.
        assert 'AGENT_TAR_PATH="Images/wildclawbench-ubuntu_v1.3.tar"' in run_sh

    def test_sha_pin_matches_the_real_base_content_id(self, run_sh):
        # The previous pin matched no layer set on either production host, so
        # the retag rung silently no-op'd and every tag-table loss degraded
        # into a 28GB re-load.
        assert 'readonly BASE_IMAGE_SHA="%s"' % _BASE_IMAGE_SHA in run_sh
        assert "60eec8752cb597e180780ff08d7569c1892c169521f1f2b069c2efeb006a4078" not in run_sh

    def test_preflight_builds_the_whisper_image_from_the_dockerfile(self, run_sh):
        assert 'readonly AGENT_WHISPER_DOCKERFILE="docker/agent-whisper.Dockerfile"' in run_sh
        assert 'docker build --platform linux/amd64 -f "$AGENT_WHISPER_DOCKERFILE" -t "$AGENT_IMAGE" .' in run_sh
        assert "ensure_base_image() {" in run_sh
        assert "preflight_agent_image() {" in run_sh

    def test_preflight_acquires_base_before_building(self, run_sh):
        body = run_sh.split("preflight_agent_image() {", 1)[1].split("\n}", 1)[0]
        assert body.index("ensure_base_image") < body.index("docker build")

    def test_recovery_rebuilds_rather_than_retagging_a_built_image(self, run_sh):
        # v1.4 is built locally, so its content ID is unknown at write time and
        # a lost tag cannot be recovered by SHA the way the base can.
        body = run_sh.split("attempt_docker_recovery() {", 1)[1].split("\n}", 1)[0]
        assert "ensure_base_image" in body
        assert "docker build" in body
        assert "AGENT_IMAGE_SHA" not in body


_DOCKER_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$DOCKER_LOG"
case "$1 $2" in
  "image inspect")
    for ref in $PRESENT_IMAGES; do [[ "$3" == "$ref" ]] && exit 0; done
    exit 1 ;;
esac
[[ "$1" == "build" ]] && exit "${BUILD_RC:-0}"
exit 0
"""

# run.sh ends in a bare `main "$@"`; drop that one line and the rest is a
# sourceable library of its functions.
_HARNESS = """#!/usr/bin/env bash
set -u
source "$(dirname "$0")/run_lib.sh"
cd "$REPO_ROOT"
preflight_agent_image
echo "rc=$?"
"""


def _run_preflight(tmp_path, present_images: str, build_rc: str = "0",
                   docker_image: str | None = None, env_file: str | None = None):
    """Drive the REAL preflight_agent_image against a stubbed docker CLI."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "docker"
    stub.write_text(_DOCKER_STUB, encoding="utf-8")
    stub.chmod(0o755)

    # run.sh resolves lib/log.sh against `dirname $0`, which for a sourced file
    # is the SOURCING script — so the harness has to live in a directory shaped
    # like script/ or the real log:: functions silently go undefined.
    script_dir = tmp_path / "script"
    script_dir.mkdir(exist_ok=True)
    (script_dir / "lib").symlink_to(_REPO_ROOT / "script" / "lib")

    stripped = script_dir / "run_lib.sh"
    stripped.write_text(
        "\n".join(_RUN_SH.read_text(encoding="utf-8").splitlines()[:-1]),
        encoding="utf-8",
    )
    harness = script_dir / "harness.sh"
    harness.write_text(_HARNESS, encoding="utf-8")

    # run.sh cd's to `dirname $0/..` at source time, which for a sourced file is
    # the sourcing script's directory — so tmp_path is the repo root while the
    # tag is being resolved, and a .env written here is the one it reads.
    if env_file is not None:
        (tmp_path / ".env").write_text(env_file, encoding="utf-8")

    docker_log = tmp_path / "docker.log"
    docker_log.touch()
    import os
    import subprocess

    env = {
        **os.environ,
        "PATH": "%s:%s" % (bin_dir, os.environ["PATH"]),
        "DOCKER_LOG": str(docker_log),
        "PRESENT_IMAGES": present_images,
        "BUILD_RC": build_rc,
        "REPO_ROOT": str(_REPO_ROOT),
        "NO_COLOR": "1",
    }
    # The tag is now resolved from the environment, so the caller states it
    # rather than inheriting whatever this developer's shell happens to export.
    env.pop("DOCKER_IMAGE", None)
    if docker_image is not None:
        env["DOCKER_IMAGE"] = docker_image
    proc = subprocess.run(
        ["bash", str(harness)], env=env, capture_output=True, text=True, timeout=120
    )
    return proc, docker_log.read_text(encoding="utf-8").splitlines()


class TestPreflightBehavior:
    """Exercises the real function; only the docker CLI is stubbed."""

    def test_present_image_is_a_fast_path_with_no_build(self, tmp_path):
        proc, calls = _run_preflight(tmp_path, present_images=_DEFAULT_IMAGE)
        assert "rc=0" in proc.stdout
        assert not [c for c in calls if c.startswith("build")]

    def test_default_never_builds_the_whisper_layer_onto_the_base_tag(self, tmp_path):
        proc, calls = _run_preflight(tmp_path, present_images="sha256:%s" % _BASE_IMAGE_SHA)
        assert "rc=0" in proc.stdout
        assert [c for c in calls if c.startswith("tag ")] == [
            "tag sha256:%s %s" % (_BASE_IMAGE_SHA, _BASE_IMAGE)]
        assert not [c for c in calls if c.startswith("build")]

    def test_missing_image_builds_from_the_dockerfile(self, tmp_path):
        proc, calls = _run_preflight(tmp_path, present_images=_BASE_IMAGE,
                                     docker_image=_AGENT_IMAGE)
        assert "rc=0" in proc.stdout
        builds = [c for c in calls if c.startswith("build")]
        assert len(builds) == 1
        assert "-f docker/agent-whisper.Dockerfile" in builds[0]
        assert "-t %s" % _AGENT_IMAGE in builds[0]
        assert "--platform linux/amd64" in builds[0]

    def test_missing_base_tag_is_retagged_from_sha_before_building(self, tmp_path):
        proc, calls = _run_preflight(tmp_path, present_images="sha256:%s" % _BASE_IMAGE_SHA,
                                     docker_image=_AGENT_IMAGE)
        assert "rc=0" in proc.stdout
        tags = [c for c in calls if c.startswith("tag ")]
        assert tags == ["tag sha256:%s %s" % (_BASE_IMAGE_SHA, _BASE_IMAGE)]
        assert [c for c in calls if c.startswith("build")]

    def test_build_failure_fails_loud_with_an_actionable_command(self, tmp_path):
        proc, _ = _run_preflight(tmp_path, present_images=_BASE_IMAGE, build_rc="1",
                                 docker_image=_AGENT_IMAGE)
        assert "rc=1" in proc.stdout
        combined = proc.stdout + proc.stderr
        assert "docker build --platform linux/amd64 -f docker/agent-whisper.Dockerfile" in combined
        assert "internet" in combined


class TestPreflightAndRunnerAgreeOnOneTag:
    """The image preflight verifies is the image run_batch is handed.

    Previously each side resolved the tag for itself: run.sh from a literal,
    docker_utils from $DOCKER_IMAGE with a literal default. 2f52a8d moved both
    literals to v1.4 while `DOCKER_IMAGE=wildclawbench-ubuntu:v1.3` sat in
    .env, which only docker_utils read — so preflight verified and reported
    v1.4 and the tasks ran on v1.3.
    """

    def test_override_moves_preflight_onto_the_same_tag(self, tmp_path):
        proc, calls = _run_preflight(
            tmp_path, present_images=_AGENT_IMAGE, docker_image=_AGENT_IMAGE)
        assert "rc=0" in proc.stdout
        inspects = [c for c in calls if c.startswith("image inspect")]
        assert inspects == ["image inspect %s" % _AGENT_IMAGE]
        assert not [c for c in calls if c.startswith("build")]

    def test_stale_env_file_pin_moves_preflight_too(self, tmp_path):
        # The exact 2026-09-18 configuration: the tag came from .env, which
        # only the python side used to read. Preflight now lands on v1.3 with
        # it instead of verifying a v1.4 that nothing would run.
        proc, calls = _run_preflight(
            tmp_path, present_images="wildclawbench-ubuntu:v1.3",
            env_file="KENSEI_MODEL=claude-opus-5\nDOCKER_IMAGE=wildclawbench-ubuntu:v1.3\n")
        assert "rc=0" in proc.stdout
        assert [c for c in calls if c.startswith("image inspect")] == [
            "image inspect wildclawbench-ubuntu:v1.3"]

    def test_commented_env_file_pin_is_not_a_pin(self, tmp_path):
        proc, calls = _run_preflight(
            tmp_path, present_images=_DEFAULT_IMAGE,
            env_file="# DOCKER_IMAGE=wildclawbench-ubuntu:v1.4\n")
        assert "rc=0" in proc.stdout
        assert [c for c in calls if c.startswith("image inspect")] == [
            "image inspect %s" % _DEFAULT_IMAGE]

    def test_process_env_beats_the_env_file(self, tmp_path):
        # load_dotenv() does not override an already-set variable, so the shell
        # has to resolve it the same way round or the two sides diverge again.
        proc, calls = _run_preflight(
            tmp_path, present_images=_AGENT_IMAGE,
            docker_image=_AGENT_IMAGE,
            env_file="DOCKER_IMAGE=wildclawbench-ubuntu:v1.3\n")
        assert "rc=0" in proc.stdout
        assert [c for c in calls if c.startswith("image inspect")] == [
            "image inspect %s" % _AGENT_IMAGE]

    def test_override_is_reported_rather_than_applied_silently(self, tmp_path):
        proc, _ = _run_preflight(
            tmp_path, present_images=_AGENT_IMAGE, docker_image=_AGENT_IMAGE)
        combined = proc.stdout + proc.stderr
        assert "overrides the default %s" % _DEFAULT_IMAGE in combined

    def test_default_run_says_nothing_about_an_override(self, tmp_path):
        proc, _ = _run_preflight(tmp_path, present_images=_DEFAULT_IMAGE)
        assert "overrides the default" not in proc.stdout + proc.stderr

    def test_docker_utils_reads_the_exported_tag(self, monkeypatch):
        # The export is the whole mechanism: load_dotenv() does not overwrite a
        # variable already in the environment, so run.sh's answer wins over the
        # .env line that used to decide this alone.
        #
        # Imported by name rather than reloaded a held reference: other suites
        # evict this module from sys.modules, which makes reload() raise.
        import importlib
        import sys

        def _fresh():
            sys.modules.pop("src.utils.docker_utils", None)
            return importlib.import_module("src.utils.docker_utils")

        monkeypatch.setenv("DOCKER_IMAGE", "wildclawbench-ubuntu:v1.3")
        try:
            assert _fresh().DOCKER_IMAGE == "wildclawbench-ubuntu:v1.3"
        finally:
            monkeypatch.delenv("DOCKER_IMAGE", raising=False)
            _fresh()

    def test_docker_utils_default_matches_run_sh(self):
        from src.utils import docker_utils

        assert docker_utils.DOCKER_IMAGE == _DEFAULT_IMAGE

    def test_config_default_matches_run_sh(self):
        from src.utils.config import Config

        assert Config().docker_image == _DEFAULT_IMAGE

    def test_env_example_does_not_ship_a_pinned_tag(self):
        # A live pin here is how the stale tag reached every deployment: the
        # file is copied to .env verbatim, so whatever it sets, operators set.
        body = (_REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        live = [ln for ln in body.splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]
        assert not [ln for ln in live if ln.startswith("DOCKER_IMAGE=")]
        assert not [ln for ln in live if "wildclawbench-ubuntu:v1.3" in ln]
