"""
Hermes Autoresearch Runner — the orchestration loop
====================================================
This is the 'train.py' equivalent. It:
1. Accepts a campaign brief from the human
2. Invokes L0, L1, L2 agents via hermes CLI
3. Runs critique_gate after every task completion
4. Logs all results to CRITIQUE_LOG.tsv + structured JSONL logs
5. Returns the final campaign package

Usage:
    python3 runner.py --brief "Write a blog post about AI agents"
    python3 runner.py --brief @brief.txt
    python3 runner.py --resume <campaign_ref>
"""

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(os.environ.get(
    "HERMES_AUTORESEARCH_BASE",
    Path(__file__).resolve().parent.parent
))

# Set env var BEFORE importing critique_gate so it picks up correct paths
os.environ["HERMES_AUTORESEARCH_BASE"] = str(BASE_DIR)

print(f"[runner] Base directory: {BASE_DIR}")

PROFILES = {
    "L0":                BASE_DIR / "hermes-l0",
    "L1-content":        BASE_DIR / "hermes-l1-content",
    "L1-research":       BASE_DIR / "hermes-l1-research",
    "L2-writer":         BASE_DIR / "hermes-l2-writer",
    "L2-researcher":     BASE_DIR / "hermes-l2-researcher",
    "L2-trend-analyst":  BASE_DIR / "hermes-l2-trend-analyst",
    "critique":          BASE_DIR / "hermes-critique",
}

CRITIQUE_LOG = BASE_DIR / "hermes-protected" / "CRITIQUE_LOG.tsv"
CAMPAIGN_DIR = BASE_DIR / "hermes-protected" / "campaigns"

# Verify paths exist
print(f"[runner] Critique log path: {CRITIQUE_LOG}")
print(f"[runner] Critique log exists: {CRITIQUE_LOG.exists()}")
print(f"[runner] Protected dir exists: {(BASE_DIR / 'hermes-protected').exists()}")

# Add runner dir to path for logging_config, and plugins for critique_gate
sys.path.insert(0, str(BASE_DIR / "hermes-runner"))
sys.path.insert(0, str(BASE_DIR / "hermes-runner" / "plugins"))

# Import logging config
from logging_config import (
    get_system_logger,
    log_stage,
    save_hermes_raw,
    save_timing,
    setup_campaign_logging,
)

# Import critique gate — env var is already set so paths resolve correctly
try:
    import critique_gate as cg
    print(f"[runner] critique_gate loaded OK")
    print(f"[runner] cg.CRITIQUE_LOG = {cg.CRITIQUE_LOG}")
    print(f"[runner] cg.PROTECTED_BASE = {cg.PROTECTED_BASE}")
except ImportError as e:
    print(f"[runner] WARNING: critique_gate import failed: {e}")
    cg = None

# System logger for cross-campaign logging
sys_logger = get_system_logger(BASE_DIR)


# ---------------------------------------------------------------------------
# Hermes CLI Invocation
# ---------------------------------------------------------------------------

def call_hermes(
    profile_path: Path,
    message: str,
    timeout: int = 300,
    *,
    logger=None,
    campaign_ref: str = "",
    seq: int = 0,
    tier: str = "",
) -> dict:
    """
    Call hermes chat with a specific profile.
    Returns parsed JSON output or raw text.
    Optionally logs invocation, response, timing, and raw output.
    """
    profile_name = profile_path.name
    env = os.environ.copy()
    env["HERMES_HOME"] = str(profile_path)

    # Source .env if it exists in the profile
    env_file = profile_path / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                env[key.strip()] = val.strip().strip('"').strip("'")

    cmd = [
        "hermes", "chat",
        "-Q",   # quiet/programmatic mode — suppress banner/spinner
        "-q", message,
    ]

    print(f"[runner] Invoking: {profile_path.name} (timeout: {timeout}s)")

    # Log invocation
    if logger:
        log_stage(logger, "hermes_invocation", tier=tier, profile=profile_name,
                  campaign_ref=campaign_ref, seq=seq,
                  cmd=cmd, timeout=timeout, hermes_home=str(profile_path))

    # Log the envelope being sent
    if logger:
        try:
            envelope = json.loads(message)
            log_stage(logger, "envelope_sent", tier=tier, profile=profile_name,
                      campaign_ref=campaign_ref, seq=seq,
                      task_id=envelope.get("task_id", ""),
                      envelope=envelope)
        except json.JSONDecodeError:
            log_stage(logger, "envelope_sent", tier=tier, profile=profile_name,
                      campaign_ref=campaign_ref, seq=seq,
                      message_preview=message[:500])

    start_time = time.monotonic()
    raw_stdout = ""
    raw_stderr = ""
    returncode = -1

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        raw_stdout = result.stdout or ""
        raw_stderr = result.stderr or ""
        returncode = result.returncode
        duration_ms = int((time.monotonic() - start_time) * 1000)

        # Save raw output to hermes_raw/
        if logger:
            campaign_log_dir = BASE_DIR / "hermes-protected" / "logs" / campaign_ref
            if campaign_log_dir.exists():
                save_hermes_raw(campaign_log_dir, profile_name, seq, raw_stdout, raw_stderr)

        # Extract token usage from top-level hermes wrapper BEFORE unwrapping
        usage_data = {}
        try:
            top_level = json.loads(raw_stdout)
            if isinstance(top_level, dict) and "usage" in top_level:
                usage = top_level["usage"]
                usage_data = {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                }
                # Also grab model from response if present
                if "model" in top_level:
                    usage_data["model"] = top_level["model"]
                if logger:
                    log_stage(logger, "model_usage", tier=tier, profile=profile_name,
                              campaign_ref=campaign_ref, seq=seq, **usage_data)
                if sys_logger:
                    sys_logger.info({
                        "campaign_ref": campaign_ref, "tier": tier,
                        "profile": profile_name, "stage": "model_usage",
                        **usage_data,
                    })
        except json.JSONDecodeError:
            pass

        # Log response
        # Hermes chat() returns plain text (the agent's final response string).
        # Try to parse as JSON envelope first; if that fails, keep full text.
        parse_success = True
        try:
            parsed = json.loads(raw_stdout)
            # Unwrap hermes JSON wrapper: choices[0].message.content
            if isinstance(parsed, dict) and "choices" in parsed:
                for choice in parsed["choices"]:
                    msg = choice.get("message", {})
                    content = msg.get("content", "")
                    if content:
                        try:
                            inner = json.loads(content)
                            if isinstance(inner, dict):
                                if logger:
                                    log_stage(logger, "hermes_response", tier=tier,
                                              profile=profile_name, campaign_ref=campaign_ref,
                                              seq=seq, returncode=returncode,
                                              duration_ms=duration_ms,
                                              raw_length=len(raw_stdout),
                                              parse_success=True,
                                              unwrapped=True)
                                return inner
                        except json.JSONDecodeError:
                            pass
                # If choices exist but no valid inner JSON, return the wrapper
                if logger:
                    log_stage(logger, "hermes_response", tier=tier, profile=profile_name,
                              campaign_ref=campaign_ref, seq=seq, returncode=returncode,
                              duration_ms=duration_ms, raw_length=len(raw_stdout),
                              parse_success=True, unwrapped=False)
                return parsed
            if logger:
                log_stage(logger, "hermes_response", tier=tier, profile=profile_name,
                          campaign_ref=campaign_ref, seq=seq, returncode=returncode,
                          duration_ms=duration_ms, raw_length=len(raw_stdout),
                          parse_success=True, unwrapped=False)
            return parsed
        except json.JSONDecodeError:
            parse_success = False

            # Try to extract JSON envelope embedded in the plain-text response
            extracted = _extract_json_from_text(raw_stdout)
            if extracted:
                if logger:
                    log_stage(logger, "hermes_response", tier=tier, profile=profile_name,
                              campaign_ref=campaign_ref, seq=seq, returncode=returncode,
                              duration_ms=duration_ms, raw_length=len(raw_stdout),
                              parse_success=True, extracted_from_text=True)
                return extracted

            if logger:
                log_stage(logger, "hermes_response", tier=tier, profile=profile_name,
                          campaign_ref=campaign_ref, seq=seq, returncode=returncode,
                          duration_ms=duration_ms, raw_length=len(raw_stdout),
                          parse_success=False, stderr_preview=raw_stderr[:300])
            return {
                "output": raw_stdout,
                "stderr": raw_stderr[:500] if raw_stderr else "",
                "parse_error": True,
            }

    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        if logger:
            log_stage(logger, "hermes_response", tier=tier, profile=profile_name,
                      campaign_ref=campaign_ref, seq=seq, returncode=-1,
                      duration_ms=duration_ms, error="timeout")
        return {"error": "timeout", "profile": str(profile_path)}

    except Exception as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        if logger:
            log_stage(logger, "error", tier=tier, profile=profile_name,
                      campaign_ref=campaign_ref, seq=seq,
                      error_type=type(e).__name__, error_message=str(e),
                      traceback=traceback.format_exc())
        return {"error": str(e), "profile": str(profile_path)}


# ---------------------------------------------------------------------------
# Envelope Helpers
# ---------------------------------------------------------------------------

def _extract_json_from_text(text: str) -> dict | None:
    """
    Try to extract a JSON dict from a plain-text hermes response.
    Handles: raw JSON, ```json blocks, and embedded { ... } objects.
    Returns the first dict that looks like an envelope (has 'task_id' or 'payload').
    """
    if not text:
        return None

    # Try direct parse
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    import re
    for match in re.finditer(r'```(?:json)?\s*\n?(.*?)```', text, re.DOTALL):
        try:
            parsed = json.loads(match.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # Try finding first { ... } that parses as a dict
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    parsed = json.loads(text[start:i+1])
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    pass
                start = None

    return None

def make_envelope(
    from_tier: str,
    to_tier: str,
    task_type: str,
    campaign_ref: str,
    payload: dict,
    assigned_model: str = "",
) -> dict:
    """Create a valid Hermes envelope."""
    return {
        "envelope_version": "1.1",
        "from_tier": from_tier,
        "to_tier": to_tier,
        "task_id": f"{task_type}-{uuid.uuid4().hex[:8]}",
        "assigned_model": assigned_model,
        "task_type": task_type,
        "campaign_ref": campaign_ref,
        "critique_required": True,
        "payload": payload,
    }


def extract_envelope_from_output(output: dict) -> dict | None:
    """
    Try to extract a JSON envelope from hermes output.
    Hermes may wrap the output in various ways.
    """
    # Direct envelope
    if "task_id" in output and "envelope_version" in output:
        return output

    # Wrapped in content field
    for key in ["content", "output", "message", "response", "text"]:
        if key in output:
            val = output[key]
            if isinstance(val, str):
                try:
                    parsed = json.loads(val)
                    if isinstance(parsed, dict) and "task_id" in parsed:
                        return parsed
                except json.JSONDecodeError:
                    pass
            elif isinstance(val, dict) and "task_id" in val:
                return val

    # Nested in choices (OpenAI-style)
    if "choices" in output:
        for choice in output["choices"]:
            msg = choice.get("message", {})
            content = msg.get("content", "")
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict) and "task_id" in parsed:
                    return parsed
            except json.JSONDecodeError:
                pass

    return None


# ---------------------------------------------------------------------------
# Critique Gate Wrapper
# ---------------------------------------------------------------------------

def run_critique(
    task_id: str,
    original_envelope: dict,
    output_envelope: dict,
    tier_label: str = "",
    *,
    logger=None,
    campaign_ref: str = "",
    seq: int = 0,
) -> dict:
    """Run critique gate and log result."""
    if cg is None:
        print("[runner] Critique gate not available, logging directly")
        _direct_log(task_id, original_envelope, output_envelope, "skipped", tier_label=tier_label)
        return {"overall": "pass", "issues": [], "criteria": {}}

    try:
        model = original_envelope.get("assigned_model", "UNKNOWN")
        cg.record_model_call(task_id, model)

        result = cg.run_critique_gate(task_id, original_envelope, output_envelope)
        print(f"[runner] Critique complete: {result.get('overall', 'unknown')}")

        # Structured critique log
        if logger:
            log_stage(logger, "critique_result", tier=tier_label,
                      campaign_ref=campaign_ref, seq=seq, task_id=task_id,
                      verdict=result)

        # Re-log with correct tier label (overwrite what critique_gate wrote)
        if tier_label and CRITIQUE_LOG.exists():
            lines = CRITIQUE_LOG.read_text().strip().split("\n")
            if len(lines) > 1:
                last_line = lines[-1]
                parts = last_line.split("\t")
                if len(parts) >= 3:
                    parts[2] = tier_label  # tier column
                    lines[-1] = "\t".join(parts)
                    CRITIQUE_LOG.write_text("\n".join(lines) + "\n")

        # Verify log was written
        if CRITIQUE_LOG.exists():
            lines = CRITIQUE_LOG.read_text().strip().split("\n")
            print(f"[runner] CRITIQUE_LOG now has {len(lines)} lines")
        else:
            print(f"[runner] WARNING: CRITIQUE_LOG missing after critique!")

        return result
    except Exception as e:
        print(f"[runner] Critique error: {e}")
        traceback.print_exc()
        if logger:
            log_stage(logger, "error", tier=tier_label, campaign_ref=campaign_ref,
                      seq=seq, task_id=task_id,
                      error_type=type(e).__name__, error_message=str(e),
                      traceback=traceback.format_exc())
        _direct_log(task_id, original_envelope, output_envelope, f"error: {e}", tier_label=tier_label)
        return {"overall": "fail", "issues": [str(e)], "criteria": {}}


def _direct_log(task_id: str, original: dict, output: dict, status: str, tier_label: str = "") -> None:
    """Direct write to CRITIQUE_LOG when critique gate is unavailable."""
    try:
        CRITIQUE_LOG.parent.mkdir(parents=True, exist_ok=True)
        if not CRITIQUE_LOG.exists() or CRITIQUE_LOG.stat().st_size == 0:
            CRITIQUE_LOG.write_text(
                "critique_id\ttask_id\ttier\toverall\tmodel_integrity\tissues\ttimestamp\n"
            )
        tier = tier_label or original.get("to_tier", "?")
        row = "\t".join([
            str(uuid.uuid4()),
            task_id,
            tier,
            status,
            "unknown",
            f"runner_direct_log",
            datetime.now(timezone.utc).isoformat(),
        ])
        with open(CRITIQUE_LOG, "a") as f:
            f.write(row + "\n")
        print(f"[runner] Direct-logged to {CRITIQUE_LOG}")
    except Exception as e:
        print(f"[runner] Failed to direct-log: {e}")


# ---------------------------------------------------------------------------
# Main Campaign Runner
# ---------------------------------------------------------------------------

def run_campaign(brief: str) -> dict:
    """
    Execute a full campaign:
    1. Send brief to L0
    2. L0 decomposes into L1 tasks
    3. Each L1 dispatches to L2 workers
    4. Critique runs after every L2 completion
    5. Results propagate back up
    """
    campaign_start_time = time.monotonic()
    campaign_ref = f"camp-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    campaign_id = uuid.uuid4().hex[:8]

    # Setup campaign-scoped logging
    logger = setup_campaign_logging(campaign_ref, BASE_DIR)
    campaign_log_dir = BASE_DIR / "hermes-protected" / "logs" / campaign_ref
    print(f"[runner] Campaign log dir: {campaign_log_dir}")
    print(f"[runner] Campaign log dir exists: {campaign_log_dir.exists()}")
    print(f"[runner] Campaigns dir: {CAMPAIGN_DIR}")
    timing_calls: list[dict] = []
    seq_counter = 0

    def next_seq() -> int:
        nonlocal seq_counter
        seq_counter += 1
        return seq_counter

    print(f"\n{'='*60}")
    print(f"  Campaign: {campaign_ref}")
    print(f"  Brief: {brief[:100]}{'...' if len(brief) > 100 else ''}")
    print(f"{'='*60}\n")

    # Clear CRITIQUE_LOG for this campaign run (fresh results only)
    if CRITIQUE_LOG.exists():
        CRITIQUE_LOG.write_text(
            "critique_id\ttask_id\ttier\toverall\tmodel_integrity\tissues\ttimestamp\n"
        )
        print(f"[runner] Cleared CRITIQUE_LOG for new campaign")

    # Save campaign metadata
    CAMPAIGN_DIR.mkdir(parents=True, exist_ok=True)
    campaign_meta = {
        "campaign_ref": campaign_ref,
        "campaign_id": campaign_id,
        "brief": brief,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "tasks": [],
    }

    # Log campaign start
    log_stage(logger, "campaign_start", campaign_ref=campaign_ref,
              brief=brief, campaign_id=campaign_id)
    sys_logger.info({
        "campaign_ref": campaign_ref, "stage": "campaign_start",
        "brief": brief[:200],
    })

    # ── Step 1: Invoke L0 Meta-Orchestrator ──────────────────────────────
    print("[1] Invoking L0 Meta-Orchestrator...")

    seq = next_seq()
    l0_envelope = make_envelope(
        from_tier="human",
        to_tier="L0",
        task_type="orchestration",
        campaign_ref=campaign_ref,
        payload={"brief": brief},
        assigned_model="minimax-m2.5",
    )

    l0_output = call_hermes(
        PROFILES["L0"], json.dumps(l0_envelope), timeout=300,
        logger=logger, campaign_ref=campaign_ref, seq=seq, tier="L0",
    )
    timing_calls.append({
        "seq": seq, "profile": "hermes-l0", "tier": "L0",
        "task_id": l0_envelope["task_id"],
        "duration_ms": "see_campaign.jsonl",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    print(f"[1] L0 raw output keys: {list(l0_output.keys())}")

    # Record model
    if cg:
        cg.record_model_call(l0_envelope["task_id"], "minimax-m2.5")

    # ── Step 2: Run Critique on L0 output ────────────────────────────────
    print("[2] Running critique on L0 output...")

    l0_critique = run_critique(
        l0_envelope["task_id"],
        l0_envelope,
        l0_output,
        tier_label="L0",
        logger=logger, campaign_ref=campaign_ref, seq=seq,
    )
    print(f"[2] L0 critique: {l0_critique.get('overall', 'unknown')}")

    campaign_meta["tasks"].append({
        "tier": "L0",
        "task_id": l0_envelope["task_id"],
        "critique": l0_critique.get("overall", "unknown"),
    })

    # ── Step 3: Parse L0 output for L1 tasks ─────────────────────────────
    l0_envelope_out = extract_envelope_from_output(l0_output)

    l1_tasks = []
    if l0_envelope_out and "payload" in l0_envelope_out:
        l1_tasks = l0_envelope_out["payload"].get("l1_tasks", [])
    elif isinstance(l0_output.get("payload"), dict):
        l1_tasks = l0_output["payload"].get("l1_tasks", [])

    # If no structured tasks found, try to extract from raw output
    if not l1_tasks:
        print("[3] No structured L1 tasks found in L0 output.")
        print("[3] Attempting single-track pass-through...")

        l1_tasks = [
            {
                "profile": "L1-content",
                "task_type": "content_writing",
                "assigned_model": "kimi-k2.5",
                "payload": {"brief": brief},
            }
        ]

    print(f"[3] L0 dispatched {len(l1_tasks)} L1 task(s)")

    # Log tier transition
    log_stage(logger, "tier_transition", tier="L0", campaign_ref=campaign_ref,
              from_tier="L0", to_tier="L1",
              task_count=len(l1_tasks),
              task_types=[t.get("task_type", "?") for t in l1_tasks])

    # ── Step 4: Execute each L1 track ────────────────────────────────────
    l1_results = []

    for i, task in enumerate(l1_tasks):
        profile_name = task.get("profile", "L1-content")
        profile_path = PROFILES.get(profile_name, PROFILES["L1-content"])

        print(f"\n[4.{i+1}] Invoking {profile_name}...")

        seq = next_seq()
        l1_envelope = make_envelope(
            from_tier="L0",
            to_tier="L1",
            task_type=task.get("task_type", "content_writing"),
            campaign_ref=campaign_ref,
            payload=task.get("payload", {"brief": brief}),
            assigned_model=task.get("assigned_model", "minimax-m2.5"),
        )

        l1_output = call_hermes(
            profile_path, json.dumps(l1_envelope), timeout=300,
            logger=logger, campaign_ref=campaign_ref, seq=seq, tier="L1",
        )
        timing_calls.append({
            "seq": seq, "profile": profile_name, "tier": "L1",
            "task_id": l1_envelope["task_id"],
            "duration_ms": "see_campaign.jsonl",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        if cg:
            cg.record_model_call(l1_envelope["task_id"], l1_envelope["assigned_model"])

        # Critique L1 output
        l1_critique = run_critique(
            l1_envelope["task_id"],
            l1_envelope,
            l1_output,
            tier_label="L1",
            logger=logger, campaign_ref=campaign_ref, seq=seq,
        )
        print(f"[4.{i+1}] {profile_name} critique: {l1_critique.get('overall', 'unknown')}")

        # Parse L1 output for L2 tasks
        l1_envelope_out = extract_envelope_from_output(l1_output)

        l2_tasks = []
        if l1_envelope_out and "payload" in l1_envelope_out:
            l2_tasks = l1_envelope_out["payload"].get("l2_tasks", [])

        # If no structured L2 tasks, run single L2 pass-through
        if not l2_tasks:
            l2_profile_name = "L2-writer"
            if "research" in task.get("task_type", ""):
                l2_profile_name = "L2-researcher"

            l2_tasks = [
                {
                    "profile": l2_profile_name,
                    "task_type": task.get("task_type", "content_writing"),
                    "assigned_model": "kimi-k2.5" if "writer" in l2_profile_name else "minimax-m2.5",
                    "payload": task.get("payload", {"brief": brief}),
                }
            ]

        # Log tier transition L1 -> L2
        log_stage(logger, "tier_transition", tier="L1", campaign_ref=campaign_ref,
                  from_tier="L1", to_tier="L2",
                  task_count=len(l2_tasks),
                  task_types=[t.get("task_type", "?") for t in l2_tasks])

        # ── Step 5: Execute L2 tasks ─────────────────────────────────────
        l2_results = []

        for j, l2_task in enumerate(l2_tasks):
            l2_profile_name = l2_task.get("profile", "L2-writer")
            l2_profile_path = PROFILES.get(l2_profile_name, PROFILES["L2-writer"])

            print(f"[5.{j+1}] Invoking {l2_profile_name}...")

            seq = next_seq()
            l2_envelope = make_envelope(
                from_tier="L1",
                to_tier="L2",
                task_type=l2_task.get("task_type", "content_writing"),
                campaign_ref=campaign_ref,
                payload=l2_task.get("payload", {"brief": brief}),
                assigned_model=l2_task.get("assigned_model", "kimi-k2.5"),
            )

            l2_output = call_hermes(
                l2_profile_path, json.dumps(l2_envelope), timeout=300,
                logger=logger, campaign_ref=campaign_ref, seq=seq, tier="L2",
            )
            timing_calls.append({
                "seq": seq, "profile": l2_profile_name, "tier": "L2",
                "task_id": l2_envelope["task_id"],
                "duration_ms": "see_campaign.jsonl",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            if cg:
                cg.record_model_call(l2_envelope["task_id"], l2_envelope["assigned_model"])

            # Critique L2 output
            l2_critique = run_critique(
                l2_envelope["task_id"],
                l2_envelope,
                l2_output,
                tier_label="L2",
                logger=logger, campaign_ref=campaign_ref, seq=seq,
            )
            print(f"[5.{j+1}] {l2_profile_name} critique: {l2_critique.get('overall', 'unknown')}")

            l2_results.append({
                "profile": l2_profile_name,
                "task_id": l2_envelope["task_id"],
                "output": l2_output,
                "critique": l2_critique,
            })

            campaign_meta["tasks"].append({
                "tier": "L2",
                "profile": l2_profile_name,
                "task_id": l2_envelope["task_id"],
                "critique": l2_critique.get("overall", "unknown"),
            })

        l1_results.append({
            "profile": profile_name,
            "task_id": l1_envelope["task_id"],
            "output": l1_output,
            "critique": l1_critique,
            "l2_results": l2_results,
        })

    # ── Step 6: Synthesize final campaign package ────────────────────────
    print(f"\n[6] Synthesizing campaign package...")

    total_duration_ms = int((time.monotonic() - campaign_start_time) * 1000)
    campaign_meta["status"] = "complete"
    campaign_meta["completed_at"] = datetime.now(timezone.utc).isoformat()

    final_package = {
        "campaign_ref": campaign_ref,
        "campaign_id": campaign_id,
        "brief": brief,
        "l0_output": l0_output,
        "l1_results": l1_results,
        "metadata": campaign_meta,
    }

    # Save campaign
    campaign_file = CAMPAIGN_DIR / f"{campaign_ref}.json"
    campaign_file.write_text(json.dumps(final_package, indent=2, default=str))
    print(f"[runner] Campaign JSON saved: {campaign_file} ({campaign_file.stat().st_size} bytes)")

    # Summary counts
    pass_count = sum(1 for t in campaign_meta["tasks"] if t.get("critique") == "pass")
    fail_count = sum(1 for t in campaign_meta["tasks"] if t.get("critique") == "fail")

    # Save timing data
    campaign_log_dir = BASE_DIR / "hermes-protected" / "logs" / campaign_ref
    save_timing(campaign_log_dir, timing_calls, total_duration_ms)
    print(f"[runner] Timing saved: {campaign_log_dir / 'timing.json'}")

    # List all files in campaign log dir
    if campaign_log_dir.exists():
        for f in sorted(campaign_log_dir.rglob("*")):
            if f.is_file():
                print(f"[runner]   {f.relative_to(campaign_log_dir)} ({f.stat().st_size} bytes)")

    # Log campaign completion
    log_stage(logger, "campaign_complete", campaign_ref=campaign_ref,
              total_tasks=len(campaign_meta["tasks"]),
              pass_count=pass_count, fail_count=fail_count,
              total_duration_ms=total_duration_ms)
    sys_logger.info({
        "campaign_ref": campaign_ref, "stage": "campaign_complete",
        "total_tasks": len(campaign_meta["tasks"]),
        "pass": pass_count, "fail": fail_count,
        "total_duration_ms": total_duration_ms,
    })

    print(f"\n{'='*60}")
    print(f"  Campaign complete: {campaign_ref}")
    print(f"  Saved to: {campaign_file}")
    print(f"  Tasks executed: {len(campaign_meta['tasks'])}")
    print(f"  Critique: {pass_count} pass, {fail_count} fail")
    print(f"  Duration: {total_duration_ms}ms")
    print(f"  Logs: {campaign_log_dir}")
    print(f"{'='*60}\n")

    return final_package


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Hermes Autoresearch Runner")
    parser.add_argument("--brief", required=True, help="Campaign brief (or @filename)")
    parser.add_argument("--base-dir", help="Override base directory")
    args = parser.parse_args()

    if args.base_dir:
        global BASE_DIR
        BASE_DIR = Path(args.base_dir)

    # Read brief from file if @-prefixed
    brief = args.brief
    if brief.startswith("@"):
        brief_file = Path(brief[1:])
        if brief_file.exists():
            brief = brief_file.read_text().strip()
        else:
            print(f"ERROR: Brief file not found: {brief_file}")
            sys.exit(1)

    # Verify hermes is available
    try:
        subprocess.run(["hermes", "--version"], capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("ERROR: 'hermes' CLI not found. Install it first.")
        sys.exit(1)

    # Run campaign
    result = run_campaign(brief)

    # Output summary
    print(json.dumps(result.get("metadata", {}), indent=2))


if __name__ == "__main__":
    main()
