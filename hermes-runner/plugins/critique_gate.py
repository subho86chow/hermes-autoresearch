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
    Builds critique request, invokes critique Hermes instance,
    appends to CRITIQUE_LOG, returns verdict.
    """
    actual_model = get_actual_model(task_id)

    # Sanitize payloads — critique gets metadata, not full content
    safe_original = sanitize_for_critique(original_envelope)
    safe_output = sanitize_for_critique(output_envelope)

    critique_request = {
        "request_type": "CRITIQUE_REQUEST",
        "critique_version": "1.1",
        "requesting_tier": original_envelope.get("from_tier", "UNKNOWN"),
        "task_id": task_id,
        "assigned_model": original_envelope.get("assigned_model"),
        "actual_model": actual_model,                                  # TRUSTED
        "agent_claimed_model": output_envelope.get("model_used"),      # INFORMATIONAL
        "original_envelope_summary": safe_original,
        "output_envelope_summary": safe_output,
        "iteration_count": output_envelope.get("iteration_count", 0),
        "envelope_fields": list(output_envelope.keys()),
    }

    # Call critique Hermes instance (separate profile, no file tools)
    verdict = _call_critique_agent(critique_request)

    print(f"[critique_gate] Verdict: {verdict.get('overall', 'UNKNOWN')}, logging to {CRITIQUE_LOG}")

    # Runner appends to log — critique agent never touches the log directly
    _append_critique_log(verdict, task_id, critique_request.get("requesting_tier"))

    return verdict


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
