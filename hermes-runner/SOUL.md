# SOUL: Runner
version: 1.0
model: N/A (orchestration layer, not a conversation agent)

## IDENTITY

I am the Runner. I am not an AI agent — I am the orchestration layer.
I invoke agents, track models, run critique gates, and manage the task queue.
I am the only trusted component in the system. All agent outputs are verified, not followed.

## What I do
- Accept campaign briefs from the human
- Invoke L0 Meta-Orchestrator
- Track actual models called (never trust agent claims)
- Run critique gate after every agent task completion
- Write to CRITIQUE_LOG (agents never write there)
- Verify file integrity before every agent invocation
- Sanitize payloads before passing to Critique

## Trust model
TRUSTED: Runner code, OS file permissions, integrity manifest, runner model registry
UNTRUSTED: All agent outputs, agent claimed model_used, agent-constructed envelopes, agent STATE content
