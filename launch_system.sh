#!/bin/bash
# launch_system.sh — Hermes Multi-Tier Agent System Launcher
# Runs integrity checks, locks files, builds manifest, starts L0
#
# Usage:
#   chmod +x launch_system.sh
#   ./launch_system.sh
#
# Works from any directory — auto-detects BASE_DIR from script location.

set -uo pipefail

# Ensure local bin dirs are in PATH
export PATH="$HOME/.local/bin:$HOME/.local/share/hermes:$PATH"

# ---------------------------------------------------------------------------
# Auto-detect base directory (wherever this script lives)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

if ! command -v hermes &> /dev/null; then
    echo "ERROR: 'hermes' CLI not found in PATH."
    echo "Found paths checked: $PATH"
    echo "Install: https://github.com/nicholasgriffintn/hermes"
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    echo "ERROR: 'python3' not found. Required for critique_gate plugin."
    exit 1
fi

echo "Preflight: hermes=$(command -v hermes), python3=$(command -v python3)"
echo "Base dir:  $BASE_DIR"
echo ""

# ---------------------------------------------------------------------------
# Verify file structure exists
# ---------------------------------------------------------------------------

required_dirs=(
    "$BASE_DIR/hermes-protected/protocols"
    "$BASE_DIR/hermes-l0"
    "$BASE_DIR/hermes-runner/plugins"
)

missing=0
for d in "${required_dirs[@]}"; do
    if [ ! -d "$d" ]; then
        echo "ERROR: Missing directory: $d"
        missing=$((missing + 1))
    fi
done

if [ "$missing" -gt 0 ]; then
    echo ""
    echo "Expected structure at: $BASE_DIR"
    echo "  hermes-protected/protocols/"
    echo "  hermes-l0/"
    echo "  hermes-runner/plugins/"
    echo "  ..."
    exit 1
fi

# ---------------------------------------------------------------------------
# Configuration (all paths relative to BASE_DIR)
# ---------------------------------------------------------------------------

PROTOCOLS_DIR="$BASE_DIR/hermes-protected/protocols"
CRITIQUE_LOG="$BASE_DIR/hermes-protected/CRITIQUE_LOG.tsv"
RUNNER_PLUGINS="$BASE_DIR/hermes-runner/plugins"

SOUL_FILES=(
    "$BASE_DIR/hermes-l0/SOUL.md"
    "$BASE_DIR/hermes-l1-content/SOUL.md"
    "$BASE_DIR/hermes-l1-research/SOUL.md"
    "$BASE_DIR/hermes-l2-writer/SOUL.md"
    "$BASE_DIR/hermes-l2-researcher/SOUL.md"
    "$BASE_DIR/hermes-l2-trend-analyst/SOUL.md"
    "$BASE_DIR/hermes-critique/SOUL.md"
)

echo "========================================"
echo " Hermes Agent System - Launch Sequence"
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

# Update critique_gate.py paths to match BASE_DIR before import
python3 -c "
import sys, os
os.environ['HERMES_AUTORESEARCH_BASE'] = '$BASE_DIR'
sys.path.insert(0, '$RUNNER_PLUGINS')

# Patch paths before importing
import critique_gate as cg
cg.PROTECTED_BASE = __import__('pathlib').Path('$BASE_DIR/hermes-protected')
cg.MANIFEST_PATH = cg.PROTECTED_BASE / '.integrity_manifest.json'
cg.CRITIQUE_LOG = cg.PROTECTED_BASE / 'CRITIQUE_LOG.tsv'
cg.CRITIQUE_PROFILE = __import__('pathlib').Path('$BASE_DIR/hermes-critique')
cg.PROTECTED_FILES = [
    __import__('pathlib').Path('$BASE_DIR/hermes-l0/SOUL.md'),
    __import__('pathlib').Path('$BASE_DIR/hermes-l1-content/SOUL.md'),
    __import__('pathlib').Path('$BASE_DIR/hermes-l1-research/SOUL.md'),
    __import__('pathlib').Path('$BASE_DIR/hermes-l2-writer/SOUL.md'),
    __import__('pathlib').Path('$BASE_DIR/hermes-l2-researcher/SOUL.md'),
    __import__('pathlib').Path('$BASE_DIR/hermes-l2-trend-analyst/SOUL.md'),
    __import__('pathlib').Path('$BASE_DIR/hermes-critique/SOUL.md'),
]
cg.PROTECTED_DIRS = [cg.PROTECTED_BASE / 'protocols']
manifest = cg.build_integrity_manifest()
print(f'  Manifest: {len(manifest)} files hashed')
"

echo ""

# ---------------------------------------------------------------------------
# Step 4: Verify integrity
# ---------------------------------------------------------------------------

echo "[4/5] Verifying integrity..."

python3 -c "
import sys, os
sys.path.insert(0, '$RUNNER_PLUGINS')
import critique_gate as cg
cg.PROTECTED_BASE = __import__('pathlib').Path('$BASE_DIR/hermes-protected')
cg.MANIFEST_PATH = cg.PROTECTED_BASE / '.integrity_manifest.json'
cg.PROTECTED_FILES = [
    __import__('pathlib').Path('$BASE_DIR/hermes-l0/SOUL.md'),
    __import__('pathlib').Path('$BASE_DIR/hermes-l1-content/SOUL.md'),
    __import__('pathlib').Path('$BASE_DIR/hermes-l1-research/SOUL.md'),
    __import__('pathlib').Path('$BASE_DIR/hermes-l2-writer/SOUL.md'),
    __import__('pathlib').Path('$BASE_DIR/hermes-l2-researcher/SOUL.md'),
    __import__('pathlib').Path('$BASE_DIR/hermes-l2-trend-analyst/SOUL.md'),
    __import__('pathlib').Path('$BASE_DIR/hermes-critique/SOUL.md'),
]
cg.PROTECTED_DIRS = [cg.PROTECTED_BASE / 'protocols']
cg.verify_integrity()
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
echo " Profiles at: $BASE_DIR"
echo ""
echo "   L0:  HERMES_HOME=$BASE_DIR/hermes-l0 hermes chat"
echo "   L1c: HERMES_HOME=$BASE_DIR/hermes-l1-content hermes chat"
echo "   L1r: HERMES_HOME=$BASE_DIR/hermes-l1-research hermes chat"
echo "   L2w: HERMES_HOME=$BASE_DIR/hermes-l2-writer hermes chat"
echo "   L2r: HERMES_HOME=$BASE_DIR/hermes-l2-researcher hermes chat"
echo "   L2t: HERMES_HOME=$BASE_DIR/hermes-l2-trend-analyst hermes chat"
echo "   CQ:  HERMES_HOME=$BASE_DIR/hermes-critique hermes chat"
echo ""
echo " Launching L0..."
echo ""

HERMES_HOME="$BASE_DIR/hermes-l0" hermes chat
