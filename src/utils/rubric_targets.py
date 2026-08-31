from __future__ import annotations

# Canonical rubric `evaluation_target` normalization. Three sites branch on
# file-vs-non-file targets and MUST agree: grading._judge_user_prompt (evidence
# grounding), testgen/generator._derive_task_output_format (test-class shape),
# task_parser._derive_taxonomy_for_native_task (L1 label). Authors/heuristics
# spell the file target several ways; collapse them to ONE canonical label.
#
# Canonical is `workspace_artifact` (already the live file-target label in both
# derivation sites; the current input/ corpus has ZERO file-target rubrics, so
# adoption regresses nothing). `produced_file` is an ALIAS, never a 3rd
# canonical name — a 3rd spelling would be silently missed by sites that only
# know `workspace_artifact`. stdlib-only (imported by grading/task_parser/testgen).

FILE_TARGETS: frozenset[str] = frozenset({"workspace_artifact"})

_ALIASES: dict[str, str] = {
    "file_output": "workspace_artifact",
    "produced_file": "workspace_artifact",
    "workspace": "workspace_artifact",
}


def normalize_target(raw: object) -> str:
    # str-coerce (rubrics are author/LLM JSON — a stray int/None must not raise
    # inside grading, which MUST NEVER fail), strip+lower, then alias-map. The
    # four calibrated targets pass through unchanged (not aliases).
    if raw is None:
        return ""
    s = str(raw).strip().lower()
    if not s:
        return ""
    return _ALIASES.get(s, s)
