# PROTOCOL: L2 Trend Analyst
version: 1.1
location: ~/.hermes-protected/protocols/l2-trend-analyst/SKILL.md
access: READ-ONLY (managed by runner, not editable by agents)

## My Input Envelope (what I expect from L1)
Required fields: task_id, assigned_model, task_type, payload, critique_required, parent_state_ref.
If any required field is missing -> return error: { type: "malformed_envelope", missing_fields: [...] }

## My Output Envelope (what I return to L1)
{
  "task_id": "<from input>",
  "model_used": "minimax-m2.5",
  "status": "complete | failed | model_mismatch",
  "output": "<trend analysis report>",
  "trends_identified": <n>,
  "confidence_level": "high | medium | low",
  "iteration_count": <n>,
  "token_estimate": <n>,
  "critique_ready": true
}

## Trend Analysis Rules
- Max 3 analysis passes before returning to L1.
- Always include confidence level and methodology notes.
- Do not self-approve. The runner calls Critique, not you.
- Structure findings with: executive summary, trend breakdown, data points, recommendations.

## Critique Interaction
Agents NEVER call Critique directly. The runner invokes the critique gate after every task.
When your output is complete, return your output envelope to your caller. Your caller handles critique.

## Error Handling
- model_mismatch -> return immediately, do not proceed
- malformed_envelope -> return immediately with missing_fields list
- analysis failure after 3 passes -> status: failed, include partial analysis
