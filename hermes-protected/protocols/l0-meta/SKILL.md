# PROTOCOL: Meta-Orchestrator
version: 1.1
location: ~/.hermes-protected/protocols/l0-meta/SKILL.md
access: READ-ONLY (managed by runner, not editable by agents)

## Global Model Routing Table
This is the authoritative source. All tiers refer down to their own scoped copy.

| Task Type              | Assigned Model   | Tier That Runs It |
|------------------------|------------------|-------------------|
| Orchestration/planning | minimax-m2.5     | L0, L1            |
| Content writing        | kimi-k2.5        | L2-writer         |
| Content validation     | kimi-k2.5        | L2-writer         |
| Web research           | minimax-m2.5     | L2-researcher     |
| Trend analysis         | minimax-m2.5     | L2-trend-analyst  |
| Critique               | minimax-m2.5     | critique          |
| Cheap research tasks   | [fallback chain] | L2-researcher     |

## Delegation Rules
1. One envelope per task. Never bundle two distinct task_types in one envelope.
2. Always set assigned_model before dispatching. Look up this table, not STATE.
3. L1 track assignment:
   - "content_writing" or "content_validation" -> L1-content-sub-orchestrator
   - "web_research" or "trend_analysis" -> L1-research-sub-orchestrator
4. Every campaign package passes through Critique — the runner enforces this, not the agent.
5. If Critique returns escalate_to: "L0", replan the affected tasks only — do not rerun the full campaign.

## Envelope Construction (L0 -> L1)
Refer to envelope_schema.json for field definitions.
Mandatory fields at this tier: task_id, assigned_model, task_type, campaign_ref, critique_required.

## Critique Interaction
Agents NEVER call Critique directly. The runner invokes the critique gate after every task.
When your output is complete, return your output envelope to your caller. Your caller handles critique.

## STATE Structure
STATE must always have:
- Current campaign_ref
- Active L1 task_ids and their status (pending / running / complete / failed)
- Critique verdict (pending / pass / fail) per campaign package
- Iteration count (used for compression trigger)
