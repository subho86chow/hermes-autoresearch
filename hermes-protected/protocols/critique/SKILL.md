# PROTOCOL: Critique Agent
version: 1.1
location: ~/.hermes-protected/protocols/critique/SKILL.md
access: READ-ONLY (managed by runner, not editable by agents)

## Rubric (all criteria scored pass/fail)

| # | Criterion | Checked How |
|---|-----------|-------------|
| 1 | task_type match | output content matches task_type in envelope |
| 2 | model integrity | actual_model == assigned_model (runner-provided, NOT agent-claimed) |
| 3 | quality threshold | content meets tier-appropriate standard (see below) |
| 4 | delegation correctness | no evidence of tier skipping (L0->L2 without L1) |
| 5 | envelope completeness | all required output fields present |
| 6 | iteration limit | iteration_count <= 3 for L2, <= 5 for L1 |

### Quality Threshold by Tier
- L2 output: coherent, on-brief, no placeholder text, >150 words for content_writing
- L1 output: all child task_ids accounted for, critique_verdict present per task
- L0 output: all L1 tracks present, campaign_ref matches

### Model Integrity Note
The critique request includes TWO model fields:
- actual_model: provided by the runner — the model the runner actually called. TRUSTED.
- agent_claimed: the model the agent reports in its output envelope. INFORMATIONAL ONLY.
Criterion 2 passes only if actual_model == assigned_model. agent_claimed is logged but never trusted.

## CRITIQUE_RESULT Schema
{
  "critique_id": "<uuid>",
  "task_id": "<from request>",
  "tier_evaluated": "L2 | L1 | L0",
  "timestamp": "<iso>",
  "criteria": {
    "task_type_match": "pass | fail",
    "model_integrity": "pass | fail",
    "quality_threshold": "pass | fail",
    "delegation_correctness": "pass | fail",
    "envelope_completeness": "pass | fail",
    "iteration_limit": "pass | fail"
  },
  "overall": "pass | fail",
  "issues": ["<description of each fail>"],
  "escalate_to": "L0 | L1 | none",
  "retry_recommended": true | false
}

## CRITIQUE_REQUEST Schema (built by runner, never by agents)
{
  "request_type": "CRITIQUE_REQUEST",
  "critique_version": "1.1",
  "requesting_tier": "L1 | L0",
  "task_id": "<task_id being critiqued>",
  "assigned_model": "<from original envelope>",
  "actual_model": "<from runner model registry, TRUSTED>",
  "agent_claimed_model": "<from output envelope, INFORMATIONAL>",
  "original_envelope_summary": { ... },
  "output_envelope_summary": { ... }
}

## Invocation Rules
- Critique Agent is invoked by the runner, NEVER by agents
- Critique Agent receives NO file paths, NO access to calling agent's skill directory
- Critique Agent has NO file write tools, NO skill_manage, NO terminal access
- Critique Agent is text-in, text-out only
- The runner appends results to CRITIQUE_LOG, not the Critique Agent

## CRITIQUE_LOG Format
Tab-separated, append only. Runner writes, Critique Agent never touches this file.
Header: critique_id \t task_id \t tier \t overall \t model_integrity \t issues \t timestamp
