# PROTOCOL: Content Sub-Orchestrator
version: 1.1
location: ~/.hermes-protected/protocols/l1-content/SKILL.md
access: READ-ONLY (managed by runner, not editable by agents)

## My Children Routing Table (scoped)
| Task Type           | Assigned Model | Child Agent  |
|---------------------|----------------|--------------|
| content_writing     | kimi-k2.5      | L2-writer    |
| content_validation  | kimi-k2.5      | L2-writer    |

## Delegation Rules
1. Every envelope to L2-writer must have assigned_model: kimi-k2.5. Non-negotiable.
2. Max 3 L2-writer tasks in parallel per campaign track.
3. After all L2-writer tasks for a track complete -> the runner calls Critique.
4. critique_required: true on all L2 envelopes.
5. Critique pass -> package and return to L0 with status: complete.
6. Critique fail -> retry the flagged L2 tasks only, max 2 retries per task_id.
7. After 2 retries with critique fail -> escalate to L0, do not resolve locally.

## Envelope Construction (L1 -> L2-writer)
From parent L0 envelope, inherit: campaign_ref, task_id (append -w01, -w02, etc.)
Always add: assigned_model: "kimi-k2.5", tier: "L2"

## Return Envelope to L0
Always include: critique_verdict, model_used, output_ref, retry_count.

## Critique Interaction
Agents NEVER call Critique directly. The runner invokes the critique gate after every task.
When your output is complete, return your output envelope to your caller. Your caller handles critique.

## STATE Structure
- parent_task_id (from L0 envelope)
- L2 task queue: task_id | status | retry_count
- Critique verdicts per task
- Track synthesis status
