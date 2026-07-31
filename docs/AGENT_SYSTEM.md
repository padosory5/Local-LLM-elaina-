# Elaina agent system

## One turn

1. Elaina receives the transcript and any explicit screen selection.
2. The semantic router identifies the intent.
3. Ordinary conversation stays with Elaina; no specialist task is created.
4. If the user describes a problem without delegating work, Elaina may store
   one optional agent offer and ask permission.
5. A dedicated semantic consent classifier runs before the general router and
   interprets the next reply as accepting, rejecting, modifying, not answering,
   or changing topics. It does not use a trigger-word list, and a classifier
   failure is treated as unclear rather than permission.
6. Only a direct action request or accepted offer reaches the coordinator.
7. The task manager creates a task with a visible lifecycle state.
8. The existing feature implementation performs read-only research or prepares
   a proposal.
9. The local policy layer checks the proposed action.
10. Consequential actions pause in Electron for approval.
11. The executor performs only the exact approved payload.
12. Elaina verifies the result and records the final task state.

Pending offers expire after the configured time and are cleared when the user
changes topics. This prevents an unrelated later response from authorizing old
work. Agent permission and Electron approval are separate: permission allows
an agent to prepare work, while Electron approval authorizes the exact write.

Agent Builder cannot be offered as a generic creator. It is available only for
direct requests involving reviewed agent blueprints; it cannot create avatars,
images, UI assets, arbitrary code, or unsupported external capabilities.

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
