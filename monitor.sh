#!/bin/bash
# monitor.sh — Real-time dashboard for Hermes Agent System
# Usage: ./monitor.sh          (single snapshot)
#        watch ./monitor.sh    (auto-refresh every 2s)

# Auto-detect base directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$SCRIPT_DIR"

BOLD="\033[1m"
GREEN="\033[32m"
RED="\033[31m"
YELLOW="\033[33m"
CYAN="\033[36m"
DIM="\033[2m"
RESET="\033[0m"

CRITIQUE_LOG="$BASE_DIR/hermes-protected/CRITIQUE_LOG.tsv"
MANIFEST="$BASE_DIR/hermes-protected/.integrity_manifest.json"
LOGS_DIR="$BASE_DIR/hermes-protected/logs"

echo ""
echo "${BOLD}════════════════════════════════════════════════════${RESET}"
echo "${BOLD}  Hermes Agent System - Status Dashboard${RESET}"
echo "${BOLD}════════════════════════════════════════════════════${RESET}"
echo ""

# ── System Time ──────────────────────────────────────────────────────────────
echo "${CYAN}Timestamp:${RESET}  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "${CYAN}Base dir:${RESET}   $BASE_DIR"
echo ""

# ── Profile Status ───────────────────────────────────────────────────────────
echo "${BOLD}Profiles:${RESET}"
for profile in hermes-l0 hermes-l1-content hermes-l1-research hermes-l2-writer hermes-l2-researcher hermes-l2-trend-analyst hermes-critique hermes-runner; do
    dir="$BASE_DIR/$profile"
    if [ -d "$dir" ]; then
        soul="$dir/SOUL.md"
        if [ -f "$soul" ]; then
            model=$(grep "^model:" "$soul" 2>/dev/null | head -1 | sed 's/model: *//')
            perms=$(stat -f "%Lp" "$soul" 2>/dev/null || stat -c "%a" "$soul" 2>/dev/null)
            if [ "$perms" = "444" ]; then
                lock="${GREEN}LOCKED${RESET}"
            else
                lock="${RED}UNLOCKED${RESET}"
            fi
            printf "  %-22s model: %-12s SOUL.md: %b\n" "$profile" "${model:-?}" "$lock"
        else
            printf "  %-22s ${RED}SOUL.md MISSING${RESET}\n" "$profile"
        fi
    else
        printf "  %-22s ${DIM}not found${RESET}\n" "$profile"
    fi
done
echo ""

# ── Integrity Manifest ───────────────────────────────────────────────────────
echo "${BOLD}Integrity Manifest:${RESET}"
if [ -f "$MANIFEST" ]; then
    file_count=$(python3 -c "import json; print(len(json.load(open('$MANIFEST'))))" 2>/dev/null || echo "?")
    manifest_age=$(stat -f "%Sm" -t "%Y-%m-%d %H:%M" "$MANIFEST" 2>/dev/null || stat -c "%y" "$MANIFEST" 2>/dev/null | cut -d. -f1)
    echo "  Files tracked: $file_count"
    echo "  Last built:    $manifest_age"
else
    echo "  ${RED}NOT FOUND - run launch_system.sh first${RESET}"
fi
echo ""

# ── Critique Log ─────────────────────────────────────────────────────────────
echo "${BOLD}Critique Log:${RESET}"
if [ -f "$CRITIQUE_LOG" ] && [ -s "$CRITIQUE_LOG" ]; then
    total=$(tail -n +2 "$CRITIQUE_LOG" | wc -l | tr -d ' ')
    passes=$(tail -n +2 "$CRITIQUE_LOG" | awk -F'\t' '{print $4}' | grep -c "pass" 2>/dev/null || echo "0")
    fails=$(tail -n +2 "$CRITIQUE_LOG" | awk -F'\t' '{print $4}' | grep -c "fail" 2>/dev/null || echo "0")
    model_fails=$(tail -n +2 "$CRITIQUE_LOG" | awk -F'\t' '{print $5}' | grep -c "fail" 2>/dev/null || echo "0")

    echo "  Total evaluations: $total"
    printf "  Passes: ${GREEN}%s${RESET}  Fails: ${RED}%s${RESET}\n" "$passes" "$fails"
    echo "  Model integrity failures: $model_fails"

    if [ "$total" -gt "0" ]; then
        echo ""
        echo "  ${DIM}Last 5 entries:${RESET}"
        tail -5 "$CRITIQUE_LOG" | awk -F'\t' '{
            printf "  %s  %s  %-4s  %-5s  model:%s  [%s]\n", $7, $2, $3, $4, $5, $6
        }'
    fi
else
    echo "  ${DIM}No evaluations yet (empty or missing)${RESET}"
fi
echo ""

# ── Structured Logs ──────────────────────────────────────────────────────────
echo "${BOLD}Structured Logs:${RESET}"
if [ -d "$LOGS_DIR" ]; then
    echo "  Location: $LOGS_DIR"
    if [ -f "$LOGS_DIR/system.log" ]; then
        sys_size=$(du -h "$LOGS_DIR/system.log" | cut -f1 | tr -d ' ')
        sys_lines=$(wc -l < "$LOGS_DIR/system.log" | tr -d ' ')
        echo "  system.log:  ${sys_lines} lines (${sys_size})"
    fi
    if [ -f "$LOGS_DIR/critique.jsonl" ]; then
        cq_lines=$(wc -l < "$LOGS_DIR/critique.jsonl" | tr -d ' ')
        echo "  critique.jsonl: ${cq_lines} entries"
    fi
    # List campaign log directories
    camp_count=$(find "$LOGS_DIR" -maxdepth 1 -name "camp-*" -type d 2>/dev/null | wc -l | tr -d ' ')
    if [ "$camp_count" -gt 0 ]; then
        echo "  Campaign logs:  ${camp_count} campaign(s)"
        find "$LOGS_DIR" -maxdepth 1 -name "camp-*" -type d -exec basename {} \; | sort -r | head -3 | while read camp; do
            echo "    ${DIM}$camp${RESET}"
        done
    fi
else
    echo "  ${DIM}No logs directory yet (created on first campaign)${RESET}"
fi
echo ""

# ── Protocol Files ───────────────────────────────────────────────────────────
echo "${BOLD}Protocol Files (read-only):${RESET}"
proto_count=$(find "$BASE_DIR/hermes-protected/protocols" -name "SKILL.md" 2>/dev/null | wc -l | tr -d ' ')
if [ "$proto_count" -gt 0 ]; then
    find "$BASE_DIR/hermes-protected/protocols" -name "SKILL.md" -exec sh -c '
        perms=$(stat -f "%Lp" "$1" 2>/dev/null || stat -c "%a" "$1" 2>/dev/null)
        parent=$(basename $(dirname "$1"))
        if [ "$perms" = "444" ]; then
            printf "  %-20s ${GREEN}444 OK${RESET}\n" "$parent"
        else
            printf "  %-20s ${RED}%s UNLOCKED${RESET}\n" "$parent" "$perms"
        fi
    ' _ {} \;
else
    echo "  ${DIM}No protocol files found${RESET}"
fi
echo ""

echo "${DIM}──────────────────────────────────────────────────────${RESET}"
echo "${DIM} Base: $BASE_DIR${RESET}"
echo "${DIM} Use: watch -n 5 ./monitor.sh   for live refresh${RESET}"
echo ""
