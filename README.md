# Elaina Agent Runtime

Elaina is a local, voice-first desktop companion. Elaina handles normal
conversation and delegates actionable work to constrained specialist agents.

## Included agents

| Agent | Current capability |
| --- | --- |
| Conversation Agent | Personality, normal conversation, memory, and stable knowledge |
| Research Agent | Current web searches, entity corrections, and fact checking |
| Vision Agent | Selected-screen analysis and verified visual identification |
| Coding Agent | Project inspection and approval-gated file changes |
| Git Agent | Approval-gated commits and pushes |
| Agent Builder | Requirement collection and reviewed agent installation |

The Google Calendar Agent is supplied as an inactive blueprint. Elaina creates
its user-specific definition only after collecting settings and receiving
Electron approval.

## Project layout

```text
agents/         agent definitions, registry, coordinator, and specialists
brain/          Elaina conversation engine and semantic routing
config/         validated YAML configuration
core/           events, WebSocket transport, and shared paths
desktop/        Electron, Live2D renderer, approvals, and screen selector
memory/         SQLite and FAISS semantic memory
security/       deterministic permissions and approval storage
tools/          MCP, search, visual search, and Google Calendar tools
vision/         on-demand screen capture
voice/          VAD, STT, TTS, playback, and interruption
runtime/        generated agents, audit records, memories, tokens, and captures
tests/          deterministic regression tests
```

## First setup

From the project root in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

cd desktop
npm install
cd ..
```

Copy `.env.example` to `.env` and enter only the services you use. Review
`config/config.yaml`, especially the Piper paths and Ollama model names.

## Start Elaina

Activate the virtual environment, then run:

```powershell
python main.py
```

Python starts the WebSocket backend and opens Electron. Closing Electron also
stops the Python process. The existing BAT/VBS launchers remain compatible.

To run Python without Electron for diagnostics:

```powershell
$env:ELAINA_OPEN_DESKTOP="0"
python main.py
```

## Google Calendar

Follow [docs/GOOGLE_CALENDAR_SETUP.md](docs/GOOGLE_CALENDAR_SETUP.md) before
testing real event creation.

After setup, say:

> Create an agent that can add events to my Google Calendar.

Elaina asks for the time zone, calendar, default duration, and approval policy.
It then shows an installation proposal. Installing the agent does not create an
event and does not bypass future approvals.

After installation, say:

> Add my calculus review to my calendar tomorrow at 3 PM for 90 minutes.

Elaina prepares the exact event and stops at the approval window. Google
Calendar is written only after approval. The first approved event opens
Google's OAuth authorization page.

## Safety model

- Semantic routing can select an agent, but cannot grant permissions.
- A local policy table owns every action permission.
- User-created agents are YAML definitions, not automatically executed Python.
- Project changes, Git writes, agent installation, and calendar writes require
  approval.
- Calendar OAuth tokens remain under `runtime/secrets/`.
- Unknown tools are rejected when an agent definition is installed.
- Each task and approval transition is recorded under `runtime/audit/`.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The suite covers the original routing fixes plus agent registration,
requirement collection, calendar-event preparation, task states, and reusable
approval prevention.

## Current limitations

- The first user-created action agent is Google Calendar.
- Calendar event creation is implemented; update and deletion are not.
- Completely new executable tools are not generated or installed
  automatically. Elaina explains the missing capability instead.
- Browser purchasing, hotel booking, payments, and university registration are
  intentionally unavailable until separate tools and policies are implemented.
