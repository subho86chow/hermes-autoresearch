"""
Hermes Logging Configuration
=============================
Centralized structured logging for the Hermes multi-tier agent system.

Provides:
  - Campaign-scoped JSONL logs (one file per campaign run)
  - System-wide rotating plain-text log
  - Structured critique log (supplements CRITIQUE_LOG.tsv)
  - Raw hermes CLI output capture
  - Timing data persistence

Log directory structure:
  hermes-protected/logs/
    system.log                  # Rotating plain-text (10MB x 5 backups)
    critique.jsonl              # Structured critique results
    camp-YYYYMMDD-HHMMSS/      # Per-campaign directory
      campaign.jsonl            # All events for this campaign
      hermes_raw/               # Raw hermes CLI stdout/stderr
        <profile>_<seq>_stdout.json
        <profile>_<seq>_stderr.txt
      timing.json               # Per-call latency data
"""

import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

class CampaignJSONFormatter(logging.Formatter):
    """Formats each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
        }
        if isinstance(record.msg, dict):
            entry.update(record.msg)
        else:
            entry["message"] = str(record.msg)
        return json.dumps(entry, default=str, ensure_ascii=False)


class PlainFormatter(logging.Formatter):
    """Human-readable single-line format for system.log."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        campaign = ""
        tier = ""
        if isinstance(record.msg, dict):
            campaign = record.msg.get("campaign_ref", "")
            tier = record.msg.get("tier", "")
            msg = record.msg.get("stage", str(record.msg))
        else:
            msg = str(record.msg)
        prefix = f"[{record.name}] {record.levelname}"
        if campaign:
            prefix += f" [{campaign}]"
        if tier:
            prefix += f" [{tier}]"
        return f"{ts} {prefix} {msg}"


# ---------------------------------------------------------------------------
# Logger Cache
# ---------------------------------------------------------------------------

_campaign_loggers: dict[str, logging.Logger] = {}
_system_logger: logging.Logger | None = None


# ---------------------------------------------------------------------------
# Setup Functions
# ---------------------------------------------------------------------------

def setup_campaign_logging(campaign_ref: str, base_dir: Path) -> logging.Logger:
    """
    Create a campaign-scoped logger that writes to:
      - hermes-protected/logs/<campaign_ref>/campaign.jsonl
      - stdout (for live monitoring)

    Also creates the hermes_raw/ subdirectory.
    """
    if campaign_ref in _campaign_loggers:
        return _campaign_loggers[campaign_ref]

    log_dir = base_dir / "hermes-protected" / "logs" / campaign_ref
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "hermes_raw").mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"hermes.campaign.{campaign_ref}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # JSONL file handler
    jsonl_handler = logging.FileHandler(log_dir / "campaign.jsonl")
    jsonl_handler.setFormatter(CampaignJSONFormatter())
    logger.addHandler(jsonl_handler)

    # Stdout handler (for live monitoring)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(CampaignJSONFormatter())
    logger.addHandler(stream_handler)

    _campaign_loggers[campaign_ref] = logger
    return logger


def get_system_logger(base_dir: Path) -> logging.Logger:
    """
    Get or create the system-wide rotating logger.
    Writes to hermes-protected/logs/system.log (10MB, 5 backups).
    """
    global _system_logger
    if _system_logger is not None:
        return _system_logger

    log_dir = base_dir / "hermes-protected" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("hermes.system")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = RotatingFileHandler(
        log_dir / "system.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
    )
    handler.setFormatter(PlainFormatter())
    logger.addHandler(handler)

    _system_logger = logger
    return logger


# ---------------------------------------------------------------------------
# Convenience Functions
# ---------------------------------------------------------------------------

def log_stage(
    logger: logging.Logger,
    stage: str,
    tier: str = "",
    profile: str = "",
    task_id: str = "",
    campaign_ref: str = "",
    seq: int = 0,
    **kwargs,
) -> None:
    """
    Log a structured event with common fields + stage-specific extras.
    This is the primary API surface for runner.py and critique_gate.py.
    """
    entry = {
        "stage": stage,
        "tier": tier,
        "profile": profile,
        "task_id": task_id,
        "campaign_ref": campaign_ref,
        "seq": seq,
    }
    entry.update(kwargs)
    logger.info(entry)


def save_hermes_raw(
    log_dir: Path,
    profile_name: str,
    seq: int,
    stdout: str,
    stderr: str,
) -> None:
    """
    Persist raw hermes CLI stdout/stderr to hermes_raw/ subdirectory.
    Only writes stderr file if stderr is non-empty.
    """
    raw_dir = log_dir / "hermes_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    stdout_file = raw_dir / f"{profile_name}_{seq:03d}_stdout.json"
    stdout_file.write_text(stdout if stdout else "")

    if stderr and stderr.strip():
        stderr_file = raw_dir / f"{profile_name}_{seq:03d}_stderr.txt"
        stderr_file.write_text(stderr)


def save_timing(log_dir: Path, calls: list[dict], total_duration_ms: float = 0) -> None:
    """
    Write or update timing.json with per-call latency data.
    """
    data = {
        "calls": calls,
        "total_duration_ms": total_duration_ms,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (log_dir / "timing.json").write_text(json.dumps(data, indent=2, default=str))
