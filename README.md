# Hermes Autoresearch

Security-hardened multi-tier agent system based on the [Karpathy autoresearch](https://github.com/karpathy/autoresearch) pattern, built for [Hermes](https://github.com/nicholasgriffintn/hermes).

## Architecture

```
Human → L0 (Meta-Orchestrator) → L1 (Sub-Orchestrators) → L2 (Workers) → Critique
                ↑                         ↑                       ↑              ↑
            minimax-m2.5             minimax-m2.5          kimi-k2.5 /      minimax-m2.5
                                     minimax-m2.5          minimax-m2.5         ↑
                                                                                   │
                                                                        Runner (plugin) calls
                                                                        Critique — agents never do
```

## Security Model

The **runner** is the only trusted component. All agent outputs are verified, not followed.

| Attack Vector | Fix |
|---|---|
| Agent edits PROTOCOL | Files in `hermes-protected/protocols/` — external read-only skill dir |
| Agent edits IDENTITY | IDENTITY is `SOUL.md` — `chmod 444`, not a skill so `skill_manage` can't touch it |
| Agent spoofs `model_used` | Runner tracks actual model via `critique_gate.py` model registry |
| Agent constructs own critique | Critique gate lives in runner plugin — agents never call it |
| Prompt injection via payload | Payload sanitized before passing to critique (metadata + 300-char preview only) |
| CRITIQUE_LOG tampering | Runner appends, critique agent has no file write tools |
| Running with tampered files | SHA-256 hash verification before every agent invocation |

## Deploy

```bash
# 1. Copy to VPS home directory
scp -r hermes-* launch_system.sh monitor.sh user@vps:~/

# 2. SSH in and launch
ssh user@vps
chmod +x launch_system.sh monitor.sh
./launch_system.sh
```

## Monitor

```bash
# Single snapshot
./monitor.sh

# Live refresh (every 5s)
watch -n 5 ./monitor.sh

# Tail critique log
tail -f ~/.hermes-protected/CRITIQUE_LOG.tsv
```

## File Layout

```
~/.hermes-protected/              # Outside all agent HERMES_HOMEs
├── protocols/                    # External skill dir (read-only to agents)
│   ├── l0-meta/SKILL.md
│   ├── l1-content/SKILL.md
│   ├── l1-research/SKILL.md
│   ├── l2-writer/SKILL.md
│   ├── l2-researcher/SKILL.md
│   ├── l2-trend-analyst/SKILL.md
│   └── critique/SKILL.md
├── envelope_schema.json
└── .integrity_manifest.json      # Generated at startup

~/.hermes-l0/                     # L0 profile
├── SOUL.md  [chmod 444]          # IDENTITY (not a skill)
├── config.yaml                   # Points to external protocols
└── skills/state-l0/SKILL.md      # STATE (agent writable)

~/.hermes-l1-content/             # L1 content profile
~/.hermes-l1-research/            # L1 research profile
~/.hermes-l2-writer/              # L2 writer (model: kimi-k2.5)
~/.hermes-l2-researcher/          # L2 researcher
~/.hermes-l2-trend-analyst/       # L2 trend analyst
~/.hermes-critique/               # Critique (tools disabled)
~/.hermes-runner/                 # Orchestration layer
└── plugins/critique_gate.py      # The enforcer
```
