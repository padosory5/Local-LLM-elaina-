# Elaina agent system

## One turn

1. Elaina receives the transcript and any explicit screen selection.
2. The semantic router identifies the intent.
3. The coordinator selects one registered agent.
4. The task manager creates a task with a visible lifecycle state.
5. The existing feature implementation performs read-only research or prepares
   a proposal.
6. The local policy layer checks the proposed action.
7. Consequential actions pause in Electron for approval.
8. The executor performs only the exact approved payload.
9. Elaina verifies the result and records the final task state.

## Agent creation

The Agent Builder supports reviewed capability blueprints. An agent definition
contains:

- a stable id and description;
- semantic intents it accepts;
- an allowlist of existing tools;
- operating instructions;
- non-secret settings.

The definition is installed in `runtime/agents/` only after approval. The
registry rejects tools that are not supplied by a trusted built-in agent.

The current builder does not generate arbitrary executable Python. A capability
that lacks an implemented tool is reported as unavailable. This is intentional:
reasoning instructions are safe to generate dynamically, but computer access
requires code review, tests, and an explicit permission policy.

## Task states

Tasks use these states:

- `working`
- `input_required`
- `waiting_approval`
- `completed`
- `failed`
- `cancelled`

This prevents an agent from claiming that it is still working after a restart,
losing a pending action, or silently continuing after interruption.

## Adding the next capability

To add another action agent:

1. Implement a narrow tool with structured inputs and outputs.
2. Add its action to `security/policy.py`.
3. Add unit tests for validation, failure, approval, and duplicate execution.
4. Add a blueprint under `agents/blueprints/`.
5. Teach Agent Builder which requirements the blueprint needs.
6. Add an Electron preview suitable for the action's consequences.

Prefer official APIs or MCP tools over visual desktop clicking. Use browser
automation only when no structured interface exists, and always stop before
external commitments.
