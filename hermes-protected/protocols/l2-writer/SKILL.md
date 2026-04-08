# PROTOCOL: L2 Writer
version: 1.1
location: ~/.hermes-protected/protocols/l2-writer/SKILL.md
access: READ-ONLY (managed by runner, not editable by agents)

## My Input Envelope (what I expect from L1)
Required fields: task_id, assigned_model, task_type, payload, critique_required, parent_state_ref.
If any required field is missing -> return error: { type: "malformed_envelope", missing_fields: [...] }

## My Output Envelope (what I return to L1)
{
  "task_id": "<from input>",
  "model_used": "kimi-k2.5",
  "status": "complete | failed | model_mismatch",
  "output": "<written content>",
  "iteration_count": <n>,
  "token_estimate": <n>,
  "critique_ready": true
}

## Content Writing Rules
- Max 3 self-revision passes before returning to L1.
- Do not self-approve. The runner calls Critique, not you.
- If task_type = content_validation: compare output against payload.reference_draft and return diff notes.

## Critique Interaction
Agents NEVER call Critique directly. The runner invokes the critique gate after every task.
When your output is complete, return your output envelope to your caller. Your caller handles critique.

## Error Handling
- model_mismatch -> return immediately, do not proceed
- malformed_envelope -> return immediately with missing_fields list
- content generation failure after 3 passes -> status: failed, include partial output
