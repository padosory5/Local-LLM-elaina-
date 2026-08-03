# Elaina

## About the Project

Elaina is a local, voice-first AI desktop companion with a Live2D Electron
interface. You can speak to her naturally for ordinary conversation, then ask
her to use specialized agents for research, screen analysis, project work, Git
operations, and approved external actions.

The main language model runs locally through Ollama. Speech recognition, voice
activity detection, text-to-speech, memory, routing, and most assistant logic
also run on the user's computer. Optional services such as Google Cloud Vision,
Google Calendar, and ElevenLabs require their own credentials only when those
features are enabled.

Elaina separates conversation from actions. A normal comment stays a normal
conversation. A direct request or an accepted agent offer can start an agent,
but changes to files, Git repositories, installed agents, and external services
still stop at an Electron approval window before anything is written.

The project is organized into the following main components:

```text
agents/         specialist agents, consent, coordination, and blueprints
brain/          conversation engine, personality, routing, and response logic
config/         application configuration
core/           events, shared paths, and WebSocket communication
desktop/        Electron application, Live2D renderer, and approval windows
memory/         SQLite and FAISS semantic memory
security/       permissions and approval policies
tools/          project MCP, search, visual search, and calendar integrations
vision/         on-demand screen capture
voice/          microphone input, VAD, STT, TTS, and interruption
runtime/        generated agents, memories, captures, tokens, and audit records
tests/          automated regression tests
```

## Features

### Voice conversation

- Faster-Whisper speech recognition with GPU support and automatic CPU
  fallback.
- Silero voice activity detection.
- One persistent microphone stream that remains open while Elaina is running,
  preventing wireless microphones from repeatedly entering an idle state.
- Voice interruption support so the current response can be stopped when the
  user begins speaking.
- Local Piper text-to-speech and real-time lip-sync events.
- Short, speech-friendly responses controlled by Elaina's personality files.

### Conversation and memory

- Local conversation through an Ollama model.
- English and Korean personality files.
- SQLite and FAISS semantic memory.
- Topic-shift detection that prevents unrelated old context from leaking into
  a new conversation.
- Spoken-response filtering that removes markdown, URLs, repeated greetings,
  and other text that sounds unnatural through TTS.

### Agent system

| Agent | Capability |
| --- | --- |
| Conversation Agent | Handles normal conversation and general knowledge |
| Research Agent | Searches the web and verifies current information |
| Vision Agent | Analyzes a selected screen region and identifies visual entities |
| Coding Agent | Inspects the selected project and proposes file changes |
| Git Agent | Prepares commits and pushes for approval |
| Agent Builder | Installs agents from reviewed capability blueprints |
| Google Calendar Agent | Prepares and creates approved calendar events |

Agent selection uses semantic routing rather than a fixed trigger-word list.
Elaina can understand contextual approvals such as "yeah, let's try that," but
an unrelated answer cannot approve an old offer. Direct requests are treated as
permission to prepare the requested work, while consequential changes still
require approval.

### Desktop and vision

- Transparent always-on-top Electron window.
- Live2D avatar with lip sync and mouse tracking.
- Pin, chat-history, and screen-selection controls.
- Adjustable transparent screen-selection rectangle across multiple monitors.
- On-demand Qwen3-VL analysis for translation, code explanation, and other
  direct visual questions.
- Optional Google Cloud Vision web detection for identifying people, games,
  products, landmarks, logos, and similar entities.

### Project actions and safety

- Read-only project inspection through a local MCP server.
- Editable file-change proposals displayed before applying changes.
- Approval-gated Git commits and pushes.
- Approval-gated agent installation and Google Calendar writes.
- Tool allowlists and deterministic local permission policies.
- Audit records for tasks and approval decisions under `runtime/audit/`.
- Secrets and OAuth tokens kept out of source files.

Current limitations:

- Agent Builder installs reviewed blueprints; it does not generate and execute
  arbitrary Python tools.
- Google Calendar currently supports creating events, but not updating,
  deleting, or inviting attendees.
- Booking, payments, purchasing, and university registration are not yet
  implemented.

## Installation

### 1. Install the prerequisites

Install the following on Windows:

- Python 3.11
- Node.js and npm
- [Ollama](https://ollama.com/)
- [Piper](https://github.com/rhasspy/piper) with an English voice model
- Git, if you want Elaina to commit and push project changes
- An NVIDIA GPU with CUDA support is recommended for faster speech recognition
  and local model inference

### 2. Create the Python environment

Open PowerShell in the project folder and run:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks virtual-environment activation, run this once in the same
window and activate it again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### 3. Install the Electron dependencies

```powershell
cd desktop
npm install
cd ..
```

### 4. Install the Ollama models

The default configuration uses Qwen3 for conversation and Qwen3-VL for screen
analysis:

```powershell
ollama pull qwen3:8b
ollama pull qwen3-vl:8b
```

Model names can be changed under `llm.ollama` and `vision` in
`config/config.yaml`.

### 5. Configure Piper and the microphone

Open `config/config.yaml` and update these Piper paths for your computer:

```yaml
tts:
  piper:
    executable: "C:/path/to/piper/piper.exe"
    model: "C:/path/to/piper/en_US-amy-medium.onnx"
```

The microphone is selected by name rather than a fragile Windows device index.
Update the VAD section if your microphone has a different name:

```yaml
vad:
  silero:
    device_index: null
    device_name: "PRO X 2 LIGHTSPEED"
    preferred_host_api: "Windows WASAPI"
    capture_sample_rate: null
```

Elaina opens the microphone at its supported native sample rate, converts the
audio to 16 kHz internally, and keeps the stream open until shutdown.

### 6. Configure optional services

Copy `.env.example` to `.env`:

```powershell
Copy-Item .env.example .env
```

Only fill in the services you intend to use:

```dotenv
ELEVENLABS_API_KEY=
GOOGLE_APPLICATION_CREDENTIALS=C:/path/to/google-vision-credentials.json
GOOGLE_CALENDAR_CREDENTIALS=C:/path/to/google-calendar-oauth-client.json
```

Do not commit `.env`, credential JSON files, or anything under
`runtime/secrets/`. Calendar setup is explained in
[docs/GOOGLE_CALENDAR_SETUP.md](docs/GOOGLE_CALENDAR_SETUP.md).

### 7. Review project access

By default, Elaina can inspect this project. To use another project, update
`project_access.project_root` in `config/config.yaml`:

```yaml
project_access:
  enabled: true
  project_root: "C:/Users/YourName/Projects/YourProject"
```

### 8. Start Elaina

From the project root with the virtual environment activated:

```powershell
python main.py
```

This starts the Python backend and opens the Electron application. Closing the
Electron window also stops the backend. To launch Elaina without a visible
terminal window, double-click `start_elaina_hidden.vbs`.

To run the Python backend without Electron for diagnostics:

```powershell
$env:ELAINA_OPEN_DESKTOP="0"
python main.py
```

### 9. Verify the installation

Run the automated tests:

```powershell
python -m unittest discover -s tests -v
```

Run the live semantic-router check against your configured Ollama model:

```powershell
python scripts/live_router_check.py
```

When Elaina starts successfully, the console should include:

```text
[Microphone] Persistent input stream is active.
Elaina is ready.
Microphone mode active.
```
