# Agent test cases

Run these after the original conversation, search, screen, coding, and Git
tests.

## Routing

1. “I’m continuing my project tonight.”
   - Expected: Conversation Agent; no project tools.
2. “What should I add to my project?”
   - Expected: Coding Agent in read-only question mode.
3. “Add a test button next to the Screen button.”
   - Expected: Coding Agent; editable project proposal.
4. “Search for the latest Qwen release.”
   - Expected: Research Agent and current web evidence.
5. Select a screen region and ask “Translate this.”
   - Expected: Vision Agent and only the selected image.

## Calendar Agent creation

1. “Create an agent that can add schedules to Google Calendar.”
   - Expected: Elaina asks for missing setup information.
2. “Asia/Seoul, primary, 60 minutes, and ask before every write.”
   - Expected: Agent installation approval.
3. Reject the proposal.
   - Expected: No file under `runtime/agents/`.
4. Repeat and approve.
   - Expected: `runtime/agents/google_calendar_agent.yaml`.

## Calendar event

1. “Add a meeting to my calendar.”
   - Expected: asks for title/date/time.
2. “Project review tomorrow at 4 PM for 90 minutes.”
   - Expected: exact event approval.
3. Reject.
   - Expected: nothing written to Google Calendar.
4. Repeat and approve.
   - Expected: OAuth on first use, then one created event.
5. Click Approve twice.
   - Expected: the second decision is rejected; no duplicate event.

## Missing capability

1. “Create an agent that buys concert tickets.”
   - Expected: Elaina explains that no reviewed purchasing tool exists.
   - It must not generate or execute arbitrary browser-control code.
