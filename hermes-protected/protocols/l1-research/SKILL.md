# PROTOCOL: Research Sub-Orchestrator
version: 1.1
location: ~/.hermes-protected/protocols/l1-research/SKILL.md
access: READ-ONLY (managed by runner, not editable by agents)

## My Children Routing Table (scoped)
| Task Type        | Assigned Model | Child Agent       |
|------------------|----------------|-------------------|
| web_research     | minimax-m2.5   | L2-researcher     |
| trend_analysis   | minimax-m2.5   | L2-trend-analyst  |

## Delegation Rules
1. Every envelope to L2 children must have assigned_model: minimax-m2.5. Non-negotiable.
2. Max 5 L2 tasks in parallel per campaign track (research can parallelize more).
3. After all L2 tasks for a track complete -> the runner calls Critique.
4. critique_required: true on all L2 envelopes.
5. Critique pass -> package and return to L0 with status: complete.
6. Critique fail -> retry the flagged L2 tasks only, max 2 retries per task_id.
7. After 2 retries with critique fail -> escalate to L0, do not resolve locally.

## Envelope Construction (L1 -> L2)
From parent L0 envelope, inherit: campaign_ref, task_id (append -r01, -r02 for researcher, -t01, -t02 for trend analyst)
Always add: assigned_model: "minimax-m2.5", tier: "L2"

## Return Envelope to L0
Always include: critique_verdict, model_used, output_ref, retry_count.

## Critique Interaction
Agents NEVER call Critique directly. The runner invokes the critique gate after every task.
When your output is complete, return your output envelope to your caller. Your caller handles critique.

## STATE Structure
- parent_task_id (from L0 envelope)
- L2 task queue: task_id | status | retry_count | child_agent
- Critique verdicts per task
- Track synthesis status
