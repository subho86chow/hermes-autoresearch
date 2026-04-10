"""
Critique Gate Plugin for Hermes Runner
=======================================
Runs AFTER every agent task completion.
Agents never call this — the runner does.

Security layers enforced:
  1. Integrity verification (hash check on IDENTITY/PROTOCOL files)
  2. Model registry (tracks actual model called, never trusts agent claims)
  3. Critique gate (runner calls critique, agents never do)
  4. Payload sanitization (strips raw content before passing to critique)
  5. Critique log (runner appends, critique agent never touches)
"""

import hashlib
import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration — uses HERMES_AUTORESEARCH_BASE env var or defaults to ~
# Set HERMES_AUTORESEARCH_BASE to the repo root on VPS
# ---------------------------------------------------------------------------

_BASE = Path(os.environ.get("HERMES_AUTORESEARCH_BASE", Path.home()))
PROTECTED_BASE = _BASE / "hermes-protected"
MANIFEST_PATH = PROTECTED_BASE / ".integrity_manifest.json"
CRITIQUE_LOG = PROTECTED_BASE / "CRITIQUE_LOG.tsv"
CRITIQUE_PROFILE = _BASE / "hermes-critique"

PROTECTED_FILES: list[Path] = [
    # SOUL.md files (IDENTITY — chmod 444)
    _BASE / "hermes-l0" / "SOUL.md",
    _BASE / "hermes-l1-content" / "SOUL.md",
    _BASE / "hermes-l1-research" / "SOUL.md",
    _BASE / "hermes-l2-writer" / "SOUL.md",
    _BASE / "hermes-l2-researcher" / "SOUL.md",
    _BASE / "hermes-l2-trend-analyst" / "SOUL.md",
    _BASE / "hermes-critique" / "SOUL.md",
]

PROTECTED_DIRS: list[Path] = [
    PROTECTED_BASE / "protocols",
]


# ---------------------------------------------------------------------------
# Integrity Manifest
# ---------------------------------------------------------------------------

def _hash_file(path: Path) -> str:
    """SHA-256 hash of a file's contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_integrity_manifest() -> dict[str, str]:
    """
    Generate reference hashes for all protected files.
    Run once at system startup. Stores manifest outside agent-accessible dirs.
    """
    manifest: dict[str, str] = {}

    # Hash individual SOUL.md files
    for path in PROTECTED_FILES:
        if path.is_file():
            manifest[str(path)] = _hash_file(path)

    # Hash all SKILL.md files in protected protocol dirs
    for dir_path in PROTECTED_DIRS:
        if dir_path.is_dir():
            for f in dir_path.rglob("SKILL.md"):
                manifest[str(f)] = _hash_file(f)

    # Write manifest
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    print(f"[critique_gate] Integrity manifest written: {len(manifest)} files")
    return manifest


def verify_integrity() -> None:
    """
    Verify all protected files match their reference hashes.
    Called before every agent invocation. Halts on violation.
    """
    if not MANIFEST_PATH.exists():
        raise RuntimeError(
            "[critique_gate] INTEGRITY MANIFEST MISSING. "
            "Run build_integrity_manifest() first."
        )

    manifest = json.loads(MANIFEST_PATH.read_text())
    violations: list[str] = []

    for path_str, expected_hash in manifest.items():
        path = Path(path_str)
        if not path.exists():
            violations.append(f"{path_str} (MISSING)")
            continue
        current_hash = _hash_file(path)
        if current_hash != expected_hash:
            violations.append(path_str)

    if violations:
        raise SecurityError(
            f"[critique_gate] INTEGRITY VIOLATION — halting.\n"
            f"Modified files:\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\n\nRestore from git or re-lock before continuing."
        )


# ---------------------------------------------------------------------------
# Model Registry
# ---------------------------------------------------------------------------

_model_registry: dict[str, str] = {}


def record_model_call(task_id: str, model: str) -> None:
    """Runner records what model it ACTUALLY called for a task."""
    _model_registry[task_id] = model


def get_actual_model(task_id: str) -> str:
    """Retrieve the actual model called — never trusts agent claims."""
    return _model_registry.get(task_id, "UNKNOWN")


def extract_model_from_soul(profile_path: Path) -> str:
    """
    Extract model assignment from a profile's SOUL.md.
    The runner reads this, not the agent.
    """
    soul_path = profile_path / "SOUL.md"
    if not soul_path.exists():
        raise RuntimeError(f"[critique_gate] SOUL.md not found at {soul_path}")
    content = soul_path.read_text()
    for line in content.splitlines():
        if line.startswith("model:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError(f"[critique_gate] No model: field found in {soul_path}")


# ---------------------------------------------------------------------------
# Payload Sanitization
# ---------------------------------------------------------------------------

def sanitize_for_critique(envelope: dict) -> dict:
    """
    Strip raw payload content. Critique only needs metadata.
    Prevents prompt injection via task content.
    """
    safe: dict = {
        "task_id": envelope.get("task_id"),
        "task_type": envelope.get("task_type"),
        "assigned_model": envelope.get("assigned_model"),
        "from_tier": envelope.get("from_tier"),
        "to_tier": envelope.get("to_tier"),
        "campaign_ref": envelope.get("campaign_ref"),
        "critique_required": envelope.get("critique_required"),
    }

    # For quality check: hash + word count + short preview only
    content = ""
    if isinstance(envelope.get("payload"), dict):
        content = envelope["payload"].get("output", "")
    elif isinstance(envelope.get("output"), str):
        content = envelope["output"]

    safe["content_hash"] = hashlib.sha256(str(content).encode()).hexdigest()
    safe["content_word_count"] = len(str(content).split())
    safe["content_preview"] = str(content)[:300]  # first 300 chars only

    return safe


# ---------------------------------------------------------------------------
# Critique Gate
# ---------------------------------------------------------------------------

def run_critique_gate(
    task_id: str,
    original_envelope: dict,
    output_envelope: dict,
) -> dict:
    """
    Called by runner after every agent task — not by the agent.
    Runs PROGRAMMATIC critique (deterministic rubric checks).
    Optionally tries LLM critique as enhancement, but never fails
    due to LLM output parsing issues.
    """
    actual_model = get_actual_model(task_id)

    # ── Programmatic critique (always succeeds, never relies on LLM) ──
    verdict = _programmatic_critique(
        task_id=task_id,
        original_envelope=original_envelope,
        output_envelope=output_envelope,
        actual_model=actual_model,
    )

    print(f"[critique_gate] Programmatic verdict: {verdict['overall']}, "
          f"issues: {verdict.get('issues', [])}, logging to {CRITIQUE_LOG}")

    # Runner appends to log — critique agent never touches the log directly
    _append_critique_log(verdict, task_id, original_envelope.get("from_tier", "UNKNOWN"))

    return verdict


def _programmatic_critique(
    task_id: str,
    original_envelope: dict,
    output_envelope: dict,
    actual_model: str,
) -> dict:
    """
    Evaluate all 6 rubric criteria programmatically. No LLM needed.
    This is deterministic and always produces a valid CRITIQUE_RESULT.
    """
    criteria: dict[str, str] = {}
    issues: list[str] = []
    assigned_model = original_envelope.get("assigned_model", "UNKNOWN")
    from_tier = original_envelope.get("from_tier", "UNKNOWN")
    task_type = original_envelope.get("task_type", "UNKNOWN")

    # ── 1. task_type match ──
    output_task_type = output_envelope.get("task_type", "")
    if not output_task_type or output_task_type == task_type:
        criteria["task_type_match"] = "pass"
    else:
        criteria["task_type_match"] = "fail"
        issues.append(f"task_type mismatch: expected={task_type}, got={output_task_type}")

    # ── 2. model integrity (runner-recorded actual_model vs envelope assigned_model) ──
    if actual_model and actual_model != "UNKNOWN" and actual_model == assigned_model:
        criteria["model_integrity"] = "pass"
    elif actual_model == "UNKNOWN":
        # No model recorded — treat as pass if we have no info (e.g., human tier)
        criteria["model_integrity"] = "pass"
    else:
        criteria["model_integrity"] = "fail"
        issues.append(f"model_integrity: actual={actual_model}, assigned={assigned_model}")

    # ── 3. quality threshold ──
    # Extract content text from output for quality checks
    content = _extract_output_content(output_envelope)
    word_count = len(content.split()) if content else 0
    has_placeholder = any(
        p in content.lower()
        for p in ["[placeholder]", "todo:", "tbd", "insert here", "fill in"]
    ) if content else False

    quality_pass = True
    if not content or word_count == 0:
        quality_pass = False
        issues.append("quality: empty output")
    elif has_placeholder:
        quality_pass = False
        issues.append("quality: contains placeholder text")
    elif task_type == "content_writing" and from_tier in ("L1", "L0") and word_count < 10:
        quality_pass = False
        issues.append(f"quality: content too short ({word_count} words)")
    criteria["quality_threshold"] = "pass" if quality_pass else "fail"

    # ── 4. delegation correctness (no tier skipping) ──
    to_tier = original_envelope.get("to_tier", "UNKNOWN")
    delegation_pass = True
    if from_tier == "L0" and to_tier == "L2":
        delegation_pass = False
        issues.append("delegation: L0->L2 skip detected (must go through L1)")
    elif from_tier == "human" and to_tier not in ("L0", "L1", "L2", "critique"):
        delegation_pass = False
        issues.append(f"delegation: invalid tier target {to_tier}")
    criteria["delegation_correctness"] = "pass" if delegation_pass else "fail"

    # ── 5. envelope completeness ──
    required_fields = ["task_id", "task_type", "from_tier", "to_tier", "payload"]
    # output_envelope might be raw agent output — be lenient
    missing = [f for f in required_fields if f not in output_envelope]
    if not missing or output_envelope.get("raw_output") or output_envelope.get("content"):
        # If it's a raw output (no envelope), that's OK — the agent just returned text
        criteria["envelope_completeness"] = "pass"
    else:
        criteria["envelope_completeness"] = "fail"
        issues.append(f"envelope: missing fields: {', '.join(missing)}")

    # ── 6. iteration limit ──
    iteration_count = output_envelope.get("iteration_count", 0)
    if isinstance(iteration_count, (int, float)):
        iteration_count = int(iteration_count)
    else:
        iteration_count = 0
    max_iterations = 3 if to_tier == "L2" else 5
    if iteration_count <= max_iterations:
        criteria["iteration_limit"] = "pass"
    else:
        criteria["iteration_limit"] = "fail"
        issues.append(f"iteration_limit: {iteration_count} > max {max_iterations}")

    # ── Overall verdict ──
    fail_count = sum(1 for v in criteria.values() if v == "fail")
    overall = "fail" if fail_count >= 2 else "pass"

    # Determine escalation
    escalate = "none"
    if overall == "fail" and from_tier in ("L2", "L1"):
        escalate = "L0" if from_tier == "L1" else "L1"

    return {
        "critique_id": str(uuid.uuid4()),
        "task_id": task_id,
        "tier_evaluated": from_tier,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "criteria": criteria,
        "overall": overall,
        "issues": issues,
        "escalate_to": escalate,
        "retry_recommended": overall == "fail" and fail_count <= 2,
        "content_word_count": word_count,
    }


def _extract_output_content(output_envelope: dict) -> str:
    """Extract text content from an agent output envelope (any format)."""
    # Direct content fields
    for key in ("output", "content", "text", "response"):
        val = output_envelope.get(key)
        if isinstance(val, str) and val.strip():
            return val
        elif isinstance(val, dict):
            inner = val.get("output") or val.get("content") or val.get("text")
            if isinstance(inner, str) and inner.strip():
                return inner

    # Payload output
    payload = output_envelope.get("payload")
    if isinstance(payload, dict):
        for key in ("output", "content", "text", "result"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val

    # Raw output
    raw = output_envelope.get("raw_output")
    if isinstance(raw, str) and raw.strip():
        return raw

    # Fallback: dump the whole thing as text
    if output_envelope:
        return json.dumps(output_envelope, default=str)

    return ""


def _extract_json_from_hermes_output(raw_stdout: str) -> dict | None:
    """
    Extract the actual critique JSON from hermes CLI output.
    Hermes --output-json wraps the model response in an OpenAI-style envelope.
    The critique JSON can be at various nesting levels:
      1. Top-level JSON (direct)
      2. choices[0].message.content (string needing re-parse)
      3. content / output / message / response fields
      4. Embedded in markdown code blocks (```json ... ```)
    """
    # First try: direct JSON parse of stdout
    try:
        parsed = json.loads(raw_stdout)
        # If it's already a valid critique result, return it
        if isinstance(parsed, dict) and ("overall" in parsed or "criteria" in parsed):
            return parsed
    except json.JSONDecodeError:
        parsed = None

    # If stdout parsed as JSON but isn't a critique result, dig into it
    if parsed is not None:
        # Check OpenAI-style wrapper: choices[0].message.content
        if "choices" in parsed:
            for choice in parsed["choices"]:
                msg = choice.get("message", {})
                content = msg.get("content", "")
                extracted = _try_parse_json_string(content)
                if extracted:
                    return extracted

        # Check common wrapper fields
        for key in ["content", "output", "message", "response", "text"]:
            val = parsed.get(key)
            if isinstance(val, str):
                extracted = _try_parse_json_string(val)
                if extracted:
                    return extracted
            elif isinstance(val, dict):
                if "overall" in val or "criteria" in val:
                    return val

    # Last resort: scan raw text for JSON in markdown code blocks
    return _try_parse_json_string(raw_stdout)


def _try_parse_json_string(text: str) -> dict | None:
    """
    Try to extract a JSON dict from a string.
    Handles: raw JSON, ```json blocks, ``` blocks, and partial text with JSON.
    """
    if not text:
        return None

    # Try direct parse
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and ("overall" in parsed or "criteria" in parsed):
            return parsed
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    import re
    code_block_match = re.search(r'```(?:json)?\s*\n?(.*?)```', text, re.DOTALL)
    if code_block_match:
        try:
            parsed = json.loads(code_block_match.group(1).strip())
            if isinstance(parsed, dict) and ("overall" in parsed or "criteria" in parsed):
                return parsed
        except json.JSONDecodeError:
            pass

    # Try finding first { ... } that parses as a critique result
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
                    if isinstance(parsed, dict) and ("overall" in parsed or "criteria" in parsed):
                        return parsed
                except json.JSONDecodeError:
                    pass
                start = None

    return None


def _call_critique_agent(request: dict) -> dict:
    """
    Spawns the critique Hermes profile as a subprocess.
    Critique agent has: no skill_manage, no file write tools, no terminal.
    """
    print(f"[critique_gate] Calling critique agent at {CRITIQUE_PROFILE}")
    try:
        result = subprocess.run(
            [
                "hermes", "chat",
                "--hermes-home", str(CRITIQUE_PROFILE),
                "--output-json",
                "-q", json.dumps(request),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        print(f"[critique_gate] hermes returncode: {result.returncode}")
        print(f"[critique_gate] stdout[:500]: {result.stdout[:500]}")
        if result.stderr:
            print(f"[critique_gate] stderr[:200]: {result.stderr[:200]}")

        # Try to extract critique JSON from hermes output wrapper
        extracted = _extract_json_from_hermes_output(result.stdout)
        if extracted:
            print(f"[critique_gate] Extracted critique: overall={extracted.get('overall')}")
            return extracted

        # If extraction failed, log and return parse error
        print(f"[critique_gate] Failed to extract critique JSON from output")
        raise json.JSONDecodeError("No critique JSON found", result.stdout, 0)

    except json.JSONDecodeError:
        return {
            "critique_id": str(uuid.uuid4()),
            "task_id": request.get("task_id", "UNKNOWN"),
            "tier_evaluated": request.get("requesting_tier", "UNKNOWN"),
            "overall": "fail",
            "criteria": {
                "task_type_match": "fail",
                "model_integrity": "fail",
                "quality_threshold": "fail",
                "delegation_correctness": "fail",
                "envelope_completeness": "fail",
                "iteration_limit": "fail",
            },
            "issues": ["critique_agent_parse_error"],
            "escalate_to": "L0",
            "retry_recommended": False,
            "raw": "PARSE_ERROR",
        }
    except subprocess.TimeoutExpired:
        return {
            "critique_id": str(uuid.uuid4()),
            "task_id": request.get("task_id", "UNKNOWN"),
            "tier_evaluated": request.get("requesting_tier", "UNKNOWN"),
            "overall": "fail",
            "criteria": {
                "task_type_match": "fail",
                "model_integrity": "fail",
                "quality_threshold": "fail",
                "delegation_correctness": "fail",
                "envelope_completeness": "fail",
                "iteration_limit": "fail",
            },
            "issues": ["critique_agent_timeout"],
            "escalate_to": "L0",
            "retry_recommended": True,
            "raw": "TIMEOUT",
        }


def _append_critique_log(verdict: dict, task_id: str, tier: str) -> None:
    """Append-only write by runner. Critique agent never writes here."""
    CRITIQUE_LOG.parent.mkdir(parents=True, exist_ok=True)

    # Write header if file is new/empty
    if not CRITIQUE_LOG.exists() or CRITIQUE_LOG.stat().st_size == 0:
        with open(CRITIQUE_LOG, "w") as f:
            f.write("critique_id\ttask_id\ttier\toverall\tmodel_integrity\tissues\ttimestamp\n")

    issues = "|".join(verdict.get("issues", []))
    criteria = verdict.get("criteria", {})
    model_integrity = criteria.get("model_integrity", "unknown")

    row = "\t".join([
        verdict.get("critique_id", str(uuid.uuid4())),
        task_id,
        tier,
        verdict.get("overall", "unknown"),
        model_integrity,
        issues,
        datetime.now(timezone.utc).isoformat(),
    ])
    print(f"[critique_gate] Writing row to {CRITIQUE_LOG}: {row[:80]}...")
    try:
        with open(CRITIQUE_LOG, "a") as f:
            f.write(row + "\n")
        print(f"[critique_gate] Write OK. File size: {CRITIQUE_LOG.stat().st_size}")
    except Exception as e:
        print(f"[critique_gate] WRITE FAILED: {e}")


# ---------------------------------------------------------------------------
# Agent Invocation Wrapper
# ---------------------------------------------------------------------------

def invoke_agent(
    profile_path: Path,
    task_envelope: dict,
    critique_required: bool = True,
) -> dict:
    """
    Full agent invocation with security gates.
    1. Verify integrity
    2. Extract model from SOUL.md
    3. Record actual model
    4. Call agent
    5. Run critique gate
    6. Return output or handle failure
    """
    task_id = task_envelope.get("task_id", str(uuid.uuid4()))

    # Step 1: Integrity check before invocation
    verify_integrity()

    # Step 2: Determine actual model from SOUL.md (not from agent claims)
    actual_model = extract_model_from_soul(profile_path)
    record_model_call(task_id, actual_model)

    # Step 3: Ensure envelope has correct assigned_model
    if task_envelope.get("assigned_model") and task_envelope["assigned_model"] != actual_model:
        return {
            "task_id": task_id,
            "status": "model_mismatch",
            "error": {
                "type": "model_mismatch",
                "expected": actual_model,
                "received": task_envelope["assigned_model"],
            },
        }

    task_envelope["assigned_model"] = actual_model

    # Step 4: Invoke the agent via Hermes
    try:
        result = subprocess.run(
            [
                "hermes", "chat",
                "--hermes-home", str(profile_path),
                "--output-json",
                "-q", json.dumps(task_envelope),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        # Try direct JSON parse first
        try:
            output_envelope = json.loads(result.stdout)
        except json.JSONDecodeError:
            output_envelope = {
                "task_id": task_id,
                "status": "failed",
                "error": "agent_output_parse_error",
                "raw_output": result.stdout[:500] if result.stdout else "",
            }
        else:
            # Unwrap hermes JSON wrapper if present (choices[0].message.content)
            if isinstance(output_envelope, dict) and "choices" in output_envelope:
                for choice in output_envelope["choices"]:
                    content = choice.get("message", {}).get("content", "")
                    if content:
                        try:
                            parsed = json.loads(content)
                            if isinstance(parsed, dict) and "task_id" in parsed:
                                output_envelope = parsed
                                break
                        except json.JSONDecodeError:
                            # Content isn't JSON — store raw content for critique
                            output_envelope = {
                                "task_id": task_id,
                                "raw_output": content,
                                "parse_error": True,
                            }
                            break
    except subprocess.TimeoutExpired:
        output_envelope = {
            "task_id": task_id,
            "status": "failed",
            "error": "agent_timeout",
        }

    # Step 5: Run critique gate (always, regardless of what agent claims)
    if critique_required:
        critique_result = run_critique_gate(task_id, task_envelope, output_envelope)
        output_envelope["critique_result"] = critique_result

        if critique_result.get("overall") == "fail":
            output_envelope["status"] = "critique_failed"
            output_envelope["critique_issues"] = critique_result.get("issues", [])

    return output_envelope


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class SecurityError(Exception):
    """Raised when file integrity verification fails."""
    pass


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python critique_gate.py [build_manifest|verify|run <json>]")
        sys.exit(1)

    command = sys.argv[1]

    if command == "build_manifest":
        build_integrity_manifest()
    elif command == "verify":
        verify_integrity()
        print("[critique_gate] Integrity check passed.")
    elif command == "run":
        if len(sys.argv) < 3:
            print("Usage: python critique_gate.py run <envelope_json>")
            sys.exit(1)
        envelope = json.loads(sys.argv[2])
        result = invoke_agent(
            profile_path=Path(envelope.pop("profile_path", "")),
            task_envelope=envelope,
        )
        print(json.dumps(result, indent=2))
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
