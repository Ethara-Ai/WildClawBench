#!/usr/bin/env python3
"""
extract_actual_usage.py — Extract, verify, and FIX token counts + costs from WildClawBench task runs.

=== CONTEXT FOR OTHER LLMs / AGENTS ===

This script exists because WildClawBench's kensei delivery fork has multiple known token
counting and cost calculation defects that can inflate reported costs by 2-5x. The script:

1. ANALYZES usage.json files from completed task runs
2. DIAGNOSES which inflation mechanism is active (if any)
3. CREATES a corrected `usage_fixed.json` alongside the original (never overwrites)

=== KNOWN DEFECTS THIS SCRIPT CORRECTS ===

DEFECT 1 — Sub-agent double-count (openclaw backend, master branch only):
    When the openclaw runner spawns sub-agents, those sub-agent LLM calls go through the
    SAME LiteLLM sidecar as the parent agent. The time-window extractor at
    `grading.py:1842` (extract_usage_from_litellm_log) captures ALL rows in the ±2s window
    — both parent AND sub-agent traffic. Then `runner.py:918-960` reads spawn_tree.jsonl
    and ADDS sub-agent tokens into the usage dict again. Result: sub-agent tokens counted
    TWICE. Cost is ~4x on the sub-agent portion (sidecar at $5/$25/MTok + fold at $15/$75/MTok).
    
    FIX: Subtract subagent_tokens_in and subagent_tokens_out from the totals. Recalculate
    cost using only the corrected (de-duplicated) token counts.
    
    NOTE: In native subagent mode (the current default), spawn_tree.jsonl is EMPTY and the
    fold is INERT. This defect only fires on legacy `subagent_director` mode runs where
    spawn_tree.jsonl has actual rows.

DEFECT 2 — Credential entanglement (RESOLVED, affects runs Jul 8 - Aug 18 2026 only):
    Commit cdff015 (Jul 8, sachin) passed Bedrock credentials unconditionally into the
    sidecar alongside OAuth config. This caused duplicate usage.jsonl rows (same request
    counted on both Bedrock AND OAuth paths). Incomplete fix at 1d9bdb7 (Jul 30, Akshita)
    addressed only 1 of 4 coupling points. Fully fixed at c49ac85 (Aug 18, akshatgharpure).
    
    FIX: Detect inflated ratios (>1.5x) and flag. Cannot programmatically de-duplicate
    without raw usage.jsonl (only aggregated usage.json retained in output dir). The fix
    is to re-run the task on current code.

DEFECT 3 — LiteLLM #26807 cache double-billing:
    Sidecar sets both `input_cost_per_token` AND `cache_read_input_token_cost` on Bedrock
    opus/sonnet routes, which can trigger double-billing of cached tokens in
    `litellm.completion_cost()`. Signature: cost_usd - expected == cache_read * 0.000005.
    
    FIX: Recalculate cost from token counts directly (bypass litellm.completion_cost).

=== PRICING REFERENCE ===

Bedrock Opus (as configured in litellm_sidecar.py:200-207):
    Input (non-cached):     $5.00 / MTok   = 5e-6 per token
    Output:                $25.00 / MTok   = 25e-6 per token
    Cache read:             $0.50 / MTok   = 0.5e-6 per token
    Cache write:            $6.25 / MTok   = 6.25e-6 per token

These are also the values in oauth_pricing.py OPUS_RATES (byte-identical by design).
The sub-agent director uses 3x rates ($15/$75) — those are for container-internal
accounting only, not for Bedrock billing.

=== USAGE ===

    python3 extract_actual_usage.py <task_dir_or_name> [--fix] [--output-dir <dir>]

    # Analyze only (no changes):
    python3 extract_actual_usage.py greg_hargrove_54cef347-1262-4fe3-a124-b3c6c6ea347e

    # Analyze AND write corrected usage_fixed.json alongside each usage.json:
    python3 extract_actual_usage.py greg_hargrove_54cef347-1262-4fe3-a124-b3c6c6ea347e --fix

    # Specify a different base directory to search:
    python3 extract_actual_usage.py Data/run_1 --fix

    # Point at a specific output directory:
    python3 extract_actual_usage.py /path/to/task/trajectories/claude --fix

=== OUTPUT ===

When --fix is used, creates `usage_fixed.json` in the SAME directory as each usage.json.
The fixed file contains:
    - Corrected token counts (sub-agent tokens subtracted if double-counted)
    - Recalculated cost from corrected tokens at Bedrock Opus rates
    - A `_fix_metadata` section documenting what was changed and why
    - All original fields preserved (unchanged ones passed through)

The original usage.json is NEVER modified.

=== ADAPTING FOR OTHER TASKS ===

To use this for a different task or model:
1. If the model is NOT Opus, change OPUS_RATES to match the model's actual rates
2. The script auto-detects the usage.json schema (both old format with top-level keys
   and new format with sources.agent nested dict)
3. For tasks on the `main` branch (no OAuth features), the script will likely report
   ratio≈1.0 and no fixes needed — that branch has simpler correct pipeline
"""
from __future__ import annotations

import json
import os
import sys
import copy
from datetime import datetime, timezone
from pathlib import Path

# ============================================================================
# PRICING CONSTANTS
# ============================================================================
# Bedrock Opus rates per TOKEN (not per million tokens).
# Source: litellm_sidecar.py:200-207 and oauth_pricing.py:63-68
# These are the rates the harness SHOULD use for cost calculation.
OPUS_RATES = {
    "input": 5e-6,         # $5.00 per million tokens (non-cached input)
    "output": 25e-6,       # $25.00 per million tokens
    "cache_read": 0.5e-6,  # $0.50 per million tokens (10x cheaper than input)
    "cache_write": 6.25e-6,  # $6.25 per million tokens (1.25x input)
}

# Sub-agent director rates (3x Bedrock baseline) — for reference/detection only.
# These are used INSIDE the agent container by subagent_director.py:234.
# They should NOT appear in usage.json (that's a bug if they do).
SUBAGENT_RATES = {
    "input": 15e-6,        # $15.00 per million tokens
    "output": 75e-6,       # $75.00 per million tokens
    "cache_read": 1.5e-6,  # $1.50 per million tokens
    "cache_write": 18.75e-6,  # $18.75 per million tokens
}

# Inflation ratio thresholds for diagnosis
RATIO_OK = 0.01        # Within 1% = no issue
RATIO_MINOR = 0.10     # Within 10% = minor (embedding contamination, rounding)
RATIO_INFLATED = 1.5   # >1.5x = definite inflation


# ============================================================================
# FILE DISCOVERY
# ============================================================================
def find_usage_files(target: str) -> list[Path]:
    """
    Find all usage.json files under the target path.
    
    Searches multiple standard WildClawBench output layouts:
    - Direct path to usage.json
    - Recursive search under a directory
    - Standard output layout: output/openclaw/<task>/trajectories/claude/run_N/usage.json
    - Data directory layout: Data/run_N/usage.json
    - Custom path with trajectories/claude/ convention
    
    Returns sorted list of Path objects pointing to usage.json files.
    """
    target_path = Path(target)

    # Direct file reference
    if target_path.is_file() and target_path.name == "usage.json":
        return [target_path]

    # Direct directory — search recursively for usage.json
    if target_path.is_dir():
        return sorted(target_path.rglob("usage.json"))

    # Try common WildClawBench output directory patterns
    candidates = [
        Path(target),
        Path("output/openclaw") / target / "trajectories/claude",
        Path("Data") / target,
        Path(target) / "trajectories/claude",
    ]

    for candidate in candidates:
        if candidate.is_dir():
            found = sorted(candidate.rglob("usage.json"))
            if found:
                return found

    # Last resort: glob the name pattern anywhere under output/
    if Path("output").is_dir():
        found = sorted(Path("output").rglob(f"*{target}*/usage.json"))
        if found:
            return found

    return []


# ============================================================================
# TOKEN EXTRACTION
# ============================================================================
def extract_run_info(usage: dict) -> dict:
    """
    Extract key metrics from a usage.json file.
    
    Handles two schemas:
    - NEW format (master branch): usage.sources.agent.{input_tokens, output_tokens, ...}
    - OLD format (main branch): top-level {input_tokens, total_prompt_tokens, ...}
    
    Returns a normalized dict with consistent field names regardless of source schema.
    
    IMPORTANT: The `input_tokens` field in usage.json represents NON-CACHED input only.
    The formula is: total_billable_input = input_tokens + cache_read_tokens + cache_write_tokens
    This is because litellm_usage_callback.py:166 computes:
        non_cached = prompt_tokens_raw - cache_read - cache_write
    before writing to usage.jsonl.
    """
    sources = usage.get("sources", {})
    agent = sources.get("agent", {})

    # Try sources.agent first (new format on master branch)
    input_tokens = agent.get("input_tokens", 0)
    output_tokens = agent.get("output_tokens", 0)
    cache_read = agent.get("cache_read_tokens", 0)
    cache_write = agent.get("cache_write_tokens", 0)
    cost = agent.get("cost_usd", 0.0)
    requests = agent.get("request_count", 0)
    elapsed = agent.get("elapsed_s", usage.get("elapsed_seconds", 0))

    # Fallback to top-level keys (old format on main branch)
    if not any([input_tokens, output_tokens, cache_read, cache_write]):
        input_tokens = usage.get("input_tokens", usage.get("total_prompt_tokens", 0))
        output_tokens = usage.get("output_tokens", usage.get("total_completion_tokens", 0))
        cache_read = usage.get("cache_read_tokens", usage.get("total_cache_read_tokens", 0))
        cache_write = usage.get("cache_write_tokens", usage.get("total_cache_write_tokens", 0))
        cost = usage.get("cost_usd", usage.get("total_cost", 0.0))
        requests = usage.get("request_count", usage.get("num_calls", 0))
        elapsed = usage.get("elapsed_s", usage.get("elapsed_seconds", usage.get("total_duration_s", 0)))

    # Sub-agent metadata (present only when spawn_tree fold ran)
    # These fields exist ONLY when openclaw runner's spawn_tree fold at runner.py:918-960
    # added sub-agent tokens into the usage dict. Their presence is the signal that
    # double-counting MAY have occurred.
    subagent_tokens_in = agent.get("subagent_tokens_in", usage.get("subagent_tokens_in"))
    subagent_tokens_out = agent.get("subagent_tokens_out", usage.get("subagent_tokens_out"))
    subagent_count = usage.get("subagent_count", agent.get("subagent_count"))

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "cost_usd": cost,
        "request_count": requests,
        "elapsed_s": elapsed,
        "subagent_tokens_in": subagent_tokens_in,
        "subagent_tokens_out": subagent_tokens_out,
        "subagent_count": subagent_count,
    }


# ============================================================================
# COST CALCULATION
# ============================================================================
def compute_expected_cost(tokens: dict) -> float | None:
    """
    Compute expected cost from token counts using Opus Bedrock rates.
    
    Formula: cost = (input * $5 + output * $25 + cache_read * $0.50 + cache_write * $6.25) / 1M
    
    Where:
        input = non-cached input tokens (already excludes cache_read and cache_write)
        output = completion tokens (includes thinking tokens, which are billed at same rate)
        cache_read = tokens read from Bedrock prompt cache
        cache_write = tokens written to Bedrock prompt cache
    
    This is the formula that SHOULD produce the reported cost. If the ratio
    (reported / expected) deviates from 1.0, there's a billing discrepancy.
    """
    input_tokens = tokens.get("input_tokens", 0)
    output_tokens = tokens.get("output_tokens", 0)
    cache_read = tokens.get("cache_read_tokens", 0)
    cache_write = tokens.get("cache_write_tokens", 0)

    if not any([input_tokens, output_tokens, cache_read, cache_write]):
        return None

    expected = (
        input_tokens * OPUS_RATES["input"]
        + output_tokens * OPUS_RATES["output"]
        + cache_read * OPUS_RATES["cache_read"]
        + cache_write * OPUS_RATES["cache_write"]
    )
    return expected


def compute_cost_from_tokens(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
) -> float:
    """
    Pure cost calculation from individual token fields.
    Same formula as compute_expected_cost but takes explicit params.
    """
    return (
        input_tokens * OPUS_RATES["input"]
        + output_tokens * OPUS_RATES["output"]
        + cache_read_tokens * OPUS_RATES["cache_read"]
        + cache_write_tokens * OPUS_RATES["cache_write"]
    )


# ============================================================================
# FIX LOGIC
# ============================================================================
def compute_fixed_usage(usage: dict, info: dict) -> tuple[dict, list[str]]:
    """
    Compute corrected token counts and cost.
    
    Returns:
        (fixed_usage_dict, list_of_corrections_applied)
    
    The fixed dict is a DEEP COPY of the original with corrections applied.
    The corrections list documents what was changed (for audit trail).
    
    === CORRECTION LOGIC ===
    
    1. SUB-AGENT DOUBLE-COUNT:
       If subagent_tokens_in/out are present and non-zero, subtract them from
       input_tokens/output_tokens. This undoes the spawn_tree fold that double-counted
       sub-agent traffic already captured by the sidecar's time-window extractor.
       
       Condition: subagent_tokens_in > 0 AND input_tokens > subagent_tokens_in
       (sanity check prevents going negative)
    
    2. COST RECALCULATION:
       Always recalculate cost from (possibly corrected) token counts using Opus rates.
       This fixes both the double-count cost AND any litellm #26807 cache double-billing.
       The recalculated cost is the AUTHORITATIVE figure.
    
    3. TOTAL_TOKENS RECOMPUTE:
       total_tokens = input + output + cache_read + cache_write
       (matches the formula at run_batch.py:274-282 recompute_combined)
    """
    fixed = copy.deepcopy(usage)
    corrections = []

    # Get current values
    input_tokens = info["input_tokens"]
    output_tokens = info["output_tokens"]
    cache_read = info["cache_read_tokens"]
    cache_write = info["cache_write_tokens"]
    original_cost = info["cost_usd"]

    # --- CORRECTION 1: Sub-agent double-count ---
    # The spawn_tree fold at runner.py:918-960 reads spawn_tree.jsonl and adds
    # sub-agent tokens into the parent's usage dict. But those same tokens already
    # exist in usage.jsonl (captured by the sidecar). Subtract to de-duplicate.
    subagent_in = info.get("subagent_tokens_in") or 0
    subagent_out = info.get("subagent_tokens_out") or 0

    if subagent_in > 0 and input_tokens > subagent_in:
        input_tokens -= subagent_in
        corrections.append(
            f"Subtracted {subagent_in:,} subagent input tokens "
            f"(spawn_tree double-count fix, Defect #1)"
        )

    if subagent_out > 0 and output_tokens > subagent_out:
        output_tokens -= subagent_out
        corrections.append(
            f"Subtracted {subagent_out:,} subagent output tokens "
            f"(spawn_tree double-count fix, Defect #1)"
        )

    # --- CORRECTION 2: Recalculate cost from corrected token counts ---
    # This bypasses litellm.completion_cost() entirely, using the known Opus rates.
    # Fixes both sub-agent cost inflation AND litellm #26807 cache double-billing.
    recalculated_cost = compute_cost_from_tokens(
        input_tokens, output_tokens, cache_read, cache_write
    )

    cost_delta = original_cost - recalculated_cost
    if abs(cost_delta) > 0.01:  # Only note if delta > 1 cent
        corrections.append(
            f"Recalculated cost: ${recalculated_cost:.6f} "
            f"(was ${original_cost:.6f}, delta=${cost_delta:.6f})"
        )
    else:
        corrections.append(
            f"Cost verified correct: ${recalculated_cost:.6f} (delta=${cost_delta:.6f})"
        )

    # --- CORRECTION 3: Recompute total_tokens ---
    total_tokens = input_tokens + output_tokens + cache_read + cache_write

    # --- Apply corrections to the fixed dict ---
    # Write into sources.agent if it exists, otherwise top-level
    sources = fixed.get("sources", {})
    agent = sources.get("agent", {})

    if agent:
        # New format (master branch)
        agent["input_tokens"] = input_tokens
        agent["output_tokens"] = output_tokens
        agent["cache_read_tokens"] = cache_read
        agent["cache_write_tokens"] = cache_write
        agent["total_tokens"] = total_tokens
        agent["cost_usd"] = recalculated_cost
        # Preserve the original cost for reference
        agent["cost_usd_original"] = original_cost
        sources["agent"] = agent
        fixed["sources"] = sources
    else:
        # Old format (main branch)
        fixed["input_tokens"] = input_tokens
        fixed["output_tokens"] = output_tokens
        fixed["cache_read_tokens"] = cache_read
        fixed["cache_write_tokens"] = cache_write
        fixed["total_tokens"] = total_tokens
        fixed["cost_usd"] = recalculated_cost
        fixed["cost_usd_original"] = original_cost
        # Also update legacy aliases if present
        if "total_prompt_tokens" in fixed:
            fixed["total_prompt_tokens"] = input_tokens
        if "total_completion_tokens" in fixed:
            fixed["total_completion_tokens"] = output_tokens
        if "total_cost" in fixed:
            fixed["total_cost"] = recalculated_cost

    # Also update top-level combined fields if present (recompute_combined output)
    if "combined_cost" in fixed:
        # combined_cost includes judge + testgen + preflight; we only fix agent portion
        judge_cost = sources.get("judge", {}).get("cost_usd", 0.0)
        testgen_cost = sources.get("testgen", {}).get("cost_usd", 0.0)
        preflight_cost = sources.get("preflight", {}).get("cost_usd", 0.0)
        fixed["combined_cost"] = recalculated_cost + judge_cost + testgen_cost + preflight_cost

    # --- Add fix metadata ---
    # This section documents what was done, for audit trail and reproducibility.
    fixed["_fix_metadata"] = {
        "script": "extract_actual_usage.py",
        "fixed_at": datetime.now(timezone.utc).isoformat(),
        "corrections_applied": corrections,
        "rates_used": {
            "input_per_mtok": "$5.00",
            "output_per_mtok": "$25.00",
            "cache_read_per_mtok": "$0.50",
            "cache_write_per_mtok": "$6.25",
            "source": "litellm_sidecar.py:200-207 (Bedrock Opus)",
        },
        "original_cost_usd": original_cost,
        "fixed_cost_usd": recalculated_cost,
        "cost_delta_usd": cost_delta,
        "ratio_before_fix": original_cost / recalculated_cost if recalculated_cost > 0 else 0,
    }

    return fixed, corrections


# ============================================================================
# DISPLAY HELPERS
# ============================================================================
def format_tokens(n: int | float | None) -> str:
    """Format token count with K/M suffix for human-readable display."""
    if n is None:
        return "N/A"
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def format_cost(cost: float | None) -> str:
    """Format cost with appropriate precision."""
    if cost is None:
        return "N/A"
    if cost >= 100:
        return f"${cost:.0f}"
    if cost >= 1:
        return f"${cost:.2f}"
    return f"${cost:.4f}"


# ============================================================================
# MAIN
# ============================================================================
def main():
    """
    Main entry point. Parses args, finds usage files, analyzes, and optionally fixes.
    
    Exit codes:
        0 = success (all files analyzed, fixes written if --fix)
        1 = no usage files found
        2 = usage error (bad arguments)
    """
    # Parse arguments
    args = sys.argv[1:]
    do_fix = "--fix" in args
    if do_fix:
        args.remove("--fix")

    if not args:
        print(__doc__)
        sys.exit(2)

    target = args[0]
    usage_files = find_usage_files(target)

    if not usage_files:
        print(f"ERROR: No usage.json files found for: {target}")
        print(f"  Searched: {target}, output/openclaw/*/trajectories/claude, Data/")
        print(f"\n  Hint: Provide a path containing usage.json files, e.g.:")
        print(f"    python3 {sys.argv[0]} /path/to/task/trajectories/claude --fix")
        print(f"    python3 {sys.argv[0]} Data/run_1 --fix")
        sys.exit(1)

    print(f"\n{'=' * 90}")
    print(f"  Token & Cost Analysis: {target}")
    print(f"  Found {len(usage_files)} usage.json file(s)")
    if do_fix:
        print(f"  MODE: FIX — will create usage_fixed.json alongside each file")
    else:
        print(f"  MODE: ANALYZE ONLY — use --fix to create corrected files")
    print(f"{'=' * 90}\n")

    # Table header
    print(
        f"{'Run':<12} {'Reqs':>6} {'Input':>8} {'Output':>8} {'CacheRd':>10} "
        f"{'CacheWr':>10} {'Reported':>10} {'Expected':>10} {'Ratio':>7} {'Time':>7} {'Sub':>4}"
    )
    print("-" * 110)

    total_cost = 0.0
    total_expected = 0.0
    total_fixed_cost = 0.0
    total_requests = 0
    runs_data = []
    all_corrections = []
    fixed_files_written = []

    for i, usage_file in enumerate(usage_files, 1):
        try:
            with open(usage_file) as f:
                usage = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ERROR reading {usage_file}: {e}")
            continue

        info = extract_run_info(usage)
        expected = compute_expected_cost(info)

        # Determine run label from directory name
        run_label = usage_file.parent.name
        if run_label in ("claude", "trajectories"):
            run_label = usage_file.parent.parent.name

        ratio = info["cost_usd"] / expected if expected and expected > 0 else 0.0
        elapsed_min = info["elapsed_s"] / 60 if info["elapsed_s"] else 0
        subagent_str = str(info["subagent_count"]) if info["subagent_count"] else "-"

        print(
            f"{run_label:<12} {info['request_count']:>6} "
            f"{format_tokens(info['input_tokens']):>8} "
            f"{format_tokens(info['output_tokens']):>8} "
            f"{format_tokens(info['cache_read_tokens']):>10} "
            f"{format_tokens(info['cache_write_tokens']):>10} "
            f"${info['cost_usd']:>9.2f} "
            f"${expected:>9.2f} " if expected else "      N/A "
            f"{ratio:>6.3f}x "
            f"{elapsed_min:>5.1f}m "
            f"{subagent_str:>4}"
        )

        total_cost += info["cost_usd"]
        total_expected += expected if expected else 0
        total_requests += info["request_count"]
        runs_data.append(info)

        # === APPLY FIX ===
        if do_fix:
            fixed_usage, corrections = compute_fixed_usage(usage, info)
            fixed_cost = fixed_usage.get("_fix_metadata", {}).get("fixed_cost_usd", 0)
            total_fixed_cost += fixed_cost
            all_corrections.extend(corrections)

            # Write usage_fixed.json alongside the original
            fixed_path = usage_file.parent / "usage_fixed.json"
            with open(fixed_path, "w") as f:
                json.dump(fixed_usage, f, indent=2)
            fixed_files_written.append(fixed_path)

    # === SUMMARY ===
    print("-" * 110)
    avg_ratio = total_cost / total_expected if total_expected > 0 else 0
    print(
        f"\n  TOTALS: {len(usage_files)} runs | {total_requests:,} requests | "
        f"${total_cost:.2f} reported | ${total_expected:.2f} expected | "
        f"ratio={avg_ratio:.4f}x"
    )

    if do_fix and total_fixed_cost > 0:
        savings = total_cost - total_fixed_cost
        print(
            f"  FIXED:  ${total_fixed_cost:.2f} corrected cost | "
            f"${savings:.2f} savings ({savings / total_cost * 100:.1f}% reduction)"
        )

    # === DIAGNOSIS ===
    print(f"\n{'=' * 90}")
    print("  DIAGNOSIS")
    print(f"{'=' * 90}")

    if abs(avg_ratio - 1.0) < RATIO_OK:
        print("  RESULT: Cost math is consistent — reported matches expected (within 1%)")
        print("     No token inflation or double-billing detected.")
        print("     Token counts and costs appear accurate.")
    elif avg_ratio > RATIO_INFLATED:
        print(f"  RESULT: INFLATION DETECTED — reported is {avg_ratio:.2f}x expected")
        print("     Possible causes (check in order):")
        print("     1. Credential entanglement (pre-Aug 18 runs with duplicate usage.jsonl rows)")
        print("     2. Sub-agent double-count (spawn_tree fold + sidecar, openclaw master only)")
        print("     3. LiteLLM #26807 cache double-billing (signature: delta == cache_read * 5e-6)")
        print("     4. Stale artifacts from pre-fix code (re-run on current commit c057b6b+)")
        if not do_fix:
            print("\n     Run with --fix to create corrected usage_fixed.json files.")
    elif avg_ratio < 0.5:
        print(f"  RESULT: UNDERCOUNTING — reported is only {avg_ratio:.2f}x expected")
        print("     Possible causes:")
        print("     - OAuth repricing not firing (config.use_claude_oauth predicate divergence)")
        print("     - Mock/embedding responses priced at $0 mixed into aggregate")
        print("     - usage_oauth.jsonl reader has no callers (write-only audit trail)")
    elif abs(avg_ratio - 1.0) < RATIO_MINOR:
        print(f"  RESULT: Minor discrepancy: ratio={avg_ratio:.4f}x (within 10%)")
        print("     Likely causes: embedding contamination (~0.02%), preflight offset, rounding")
    else:
        print(f"  RESULT: Moderate discrepancy: ratio={avg_ratio:.4f}x")
        print("     May indicate partial inflation or model-rate mismatch.")
        print("     Check if task used non-Opus model (rates would differ).")

    # === PER-REQUEST STATS ===
    if runs_data:
        print(f"\n  Per-request averages (cache_read/req indicates context size):")
        for i, info in enumerate(runs_data, 1):
            if info["request_count"] > 0:
                cr_per_req = info["cache_read_tokens"] / info["request_count"]
                cost_per_req = info["cost_usd"] / info["request_count"]
                print(f"    Run {i}: {cr_per_req:,.0f} cache_read/req, ${cost_per_req:.4f}/req")

    # === SUB-AGENT SUMMARY ===
    has_subagents = any(r.get("subagent_count") for r in runs_data)
    if has_subagents:
        print(f"\n  Sub-agent info (potential double-count source):")
        for i, info in enumerate(runs_data, 1):
            if info["subagent_count"]:
                sa_in = format_tokens(info["subagent_tokens_in"])
                sa_out = format_tokens(info["subagent_tokens_out"])
                print(
                    f"    Run {i}: {info['subagent_count']} subagents, "
                    f"tokens_in={sa_in}, tokens_out={sa_out}"
                )
                if info["subagent_tokens_in"] and info["input_tokens"]:
                    pct = info["subagent_tokens_in"] / info["input_tokens"] * 100
                    print(f"           Sub-agent portion of input: {pct:.1f}%")

    # === FIX SUMMARY ===
    if do_fix and fixed_files_written:
        print(f"\n{'=' * 90}")
        print(f"  FIX RESULTS")
        print(f"{'=' * 90}")
        print(f"  Created {len(fixed_files_written)} corrected file(s):")
        for fp in fixed_files_written:
            print(f"    {fp}")
        print(f"\n  Corrections applied:")
        # De-duplicate correction messages for display
        seen = set()
        for c in all_corrections:
            # Normalize the message for dedup (remove specific numbers)
            if c not in seen:
                print(f"    - {c}")
                seen.add(c)
        print(f"\n  NOTE: Original usage.json files are UNCHANGED.")
        print(f"        Fixed versions are in usage_fixed.json alongside each original.")
        print(f"        To use fixed values downstream, point consumers at usage_fixed.json")
        print(f"        or rename: mv usage.json usage_original.json && mv usage_fixed.json usage.json")

    # === LiteLLM #26807 SPECIFIC CHECK ===
    # Signature: if (reported - expected) ≈ cache_read * 5e-6, then #26807 is active.
    # This check only makes sense when ratio > 1.0 (inflation, not undercounting).
    if avg_ratio > 1.05:
        print(f"\n  LiteLLM #26807 check (cache double-billing signature):")
        for i, info in enumerate(runs_data, 1):
            expected_cost = compute_expected_cost(info)
            if expected_cost and info["cost_usd"] > expected_cost:
                delta = info["cost_usd"] - expected_cost
                signature = info["cache_read_tokens"] * 5e-6  # The exact double-bill amount
                if signature > 0 and abs(delta - signature) / signature < 0.05:
                    print(f"    Run {i}: MATCHES #26807 signature "
                          f"(delta=${delta:.4f} ≈ cache_read*5e-6=${signature:.4f})")
                else:
                    print(f"    Run {i}: Does NOT match #26807 "
                          f"(delta=${delta:.4f}, signature=${signature:.4f})")

    print()


if __name__ == "__main__":
    main()
