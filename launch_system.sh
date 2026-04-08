#!/bin/bash
# launch_system.sh — Hermes Multi-Tier Agent System Launcher
# Runs integrity checks, locks files, builds manifest, starts L0
#
# Usage:
#   chmod +x launch_system.sh
#   ./launch_system.sh

set -uo pipefail

# Ensure local bin dirs are in PATH (common on VPS where /root/.local/bin isn't loaded)
export PATH="$HOME/.local/bin:$HOME/.local/share/hermes:$PATH"

# ---------------------------------------------------------------------------
# Preflight check
# ---------------------------------------------------------------------------

if ! command -v hermes &> /dev/null; then
    echo "ERROR: 'hermes' CLI not found."
    echo "Install it first: npm install -g @anthropic-ai/hermes"
    echo "Or check: https://github.com/nicholasgriffintn/hermes"
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    echo "ERROR: 'python3' not found. Required for critique_gate plugin."
    exit 1
fi

echo "Preflight: hermes=$(command -v hermes), python3=$(command -v python3)"
echo ""

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROTOCOLS_DIR="$HOME/.hermes-protected/protocols"
CRITIQUE_LOG="$HOME/.hermes-protected/CRITIQUE_LOG.tsv"
RUNNER_PLUGINS="$HOME/.hermes-runner/plugins"

SOUL_FILES=(
    "$HOME/.hermes-l0/SOUL.md"
    "$HOME/.hermes-l1-content/SOUL.md"
    "$HOME/.hermes-l1-research/SOUL.md"
    "$HOME/.hermes-l2-writer/SOUL.md"
    "$HOME/.hermes-l2-researcher/SOUL.md"
    "$HOME/.hermes-l2-trend-analyst/SOUL.md"
    "$HOME/.hermes-critique/SOUL.md"
)

echo "========================================"
echo " Hermes Agent System — Launch Sequence"
echo "========================================"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Lock all protected files (chmod 444)
# ---------------------------------------------------------------------------

echo "[1/5] Locking protected files..."

# Lock protocol SKILL.md files
find "$PROTOCOLS_DIR" -name "SKILL.md" -exec chmod 444 {} \; 2>/dev/null
echo "  Protocols locked: $(find "$PROTOCOLS_DIR" -name "SKILL.md" | wc -l | tr -d ' ') files"

# Lock SOUL.md files
for soul in "${SOUL_FILES[@]}"; do
    if [ -f "$soul" ]; then
        chmod 444 "$soul"
        echo "  Locked: $soul"
    else
        echo "  WARNING: Missing $soul"
    fi
done

echo ""

# ---------------------------------------------------------------------------
# Step 2: Initialize CRITIQUE_LOG if needed
# ---------------------------------------------------------------------------

echo "[2/5] Initializing CRITIQUE_LOG..."

mkdir -p "$(dirname "$CRITIQUE_LOG")"
if [ ! -f "$CRITIQUE_LOG" ] || [ ! -s "$CRITIQUE_LOG" ]; then
    printf "critique_id\ttask_id\ttier\toverall\tmodel_integrity\tissues\ttimestamp\n" > "$CRITIQUE_LOG"
    echo "  CRITIQUE_LOG initialized with header"
else
    echo "  CRITIQUE_LOG already exists ($(wc -l < "$CRITIQUE_LOG") rows)"
fi

echo ""

# ---------------------------------------------------------------------------
# Step 3: Build integrity manifest
# ---------------------------------------------------------------------------

echo "[3/5] Building integrity manifest..."

python3 -c "
import sys
sys.path.insert(0, '$RUNNER_PLUGINS')
from critique_gate import build_integrity_manifest
manifest = build_integrity_manifest()
print(f'  Manifest: {len(manifest)} files hashed')
"

echo ""

# ---------------------------------------------------------------------------
# Step 4: Verify integrity
# ---------------------------------------------------------------------------

echo "[4/5] Verifying integrity..."

python3 -c "
import sys
sys.path.insert(0, '$RUNNER_PLUGINS')
from critique_gate import verify_integrity
verify_integrity()
print('  All integrity checks PASSED')
"

echo ""

# ---------------------------------------------------------------------------
# Step 5: Start L0 Meta-Orchestrator
# ---------------------------------------------------------------------------

echo "[5/5] Starting L0 Meta-Orchestrator..."
echo ""
echo "========================================"
echo " System Ready"
echo "========================================"
echo ""
echo " Profiles available:"
echo "   L0:  HERMES_HOME=~/.hermes-l0 hermes chat"
echo "   L1c: HERMES_HOME=~/.hermes-l1-content hermes chat"
echo "   L1r: HERMES_HOME=~/.hermes-l1-research hermes chat"
echo "   L2w: HERMES_HOME=~/.hermes-l2-writer hermes chat"
echo "   L2r: HERMES_HOME=~/.hermes-l2-researcher hermes chat"
echo "   L2t: HERMES_HOME=~/.hermes-l2-trend-analyst hermes chat"
echo "   CQ:  HERMES_HOME=~/.hermes-critique hermes chat"
echo ""
echo " Runner commands:"
echo "   python3 ~/.hermes-runner/plugins/critique_gate.py build_manifest"
echo "   python3 ~/.hermes-runner/plugins/critique_gate.py verify"
echo ""
echo " Launching L0..."
echo ""

HERMES_HOME="$HOME/.hermes-l0" hermes chat
