# environment/skills — CONNECTOR + MEDIA SKILL FLEET

Skill dirs injected into task containers alongside the mock-API fleet. 54 subdirs total:
50 `<api>-connector/` skills (one per `environment/<api>-api/` mock) + 4 media/meta skills.

## LAYOUT
```
<api>-connector/          # 50 dirs; one per mock in environment/<api>-api/
  SKILL.md                # skill description the agent reads
  references/             # API reference material
  scripts/                # helper scripts the skill invokes
audio-extract/            # media skill: pull audio from video
pdf-extract/              # media skill: text/layout from PDF
video-frames/             # media skill: sample frames from video
self-improving-agent-3.0.5/  # meta-skill with its own hooks/references/.learnings
```

## CONVENTIONS
- `<api>-connector/` slug MUST match the paired mock service: `github-api-connector` ↔ `environment/github-api/`.
- Each connector is self-contained: `SKILL.md` + `references/` + `scripts/`. The agent discovers skills by reading `SKILL.md`.
- `SKILL.md` / scripts comments still say "Kensei2"/"Kensei3" — vendored heritage, not a dependency. Do NOT "fix" them (root convention #15).

## SNAPSHOT / BUNDLE INTERACTION
- `environment/scripts/` (fleet-level, NOT these per-skill `scripts/`) and `self-improving-agent-*` are EXCLUDED from bundle snapshots by `src/utils/env_overlay_snapshot.py` (`_SKIP_TOPLEVEL_NAMES`/`_SKIP_TOPLEVEL_PREFIXES`) in lock-step with `script/repackage_to_bundle.py:1864-1881`. Do NOT let internal R&D (`self-improving-agent-*`) leak into delivered bundles.

## ANTI-PATTERNS
- Don't add a connector without its paired `<api>-api/` mock — the connector count MUST equal the API count (currently 50/50).
- Don't put runtime secrets in `SKILL.md` or `references/`; these are shipped into the agent container.
