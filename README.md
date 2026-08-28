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

### Bounded computer control (Phase 4A)

- The main Electron screen has a `Control Off` / `Control On` toggle that
  blends with the Screen and Chat controls. Desktop Control Mode starts off on
  every launch and its state is owned by the Python backend.
- While the mode is off, supported computer requests never prepare or execute
  an action. Elaina can explain what to do and recommend turning on the visible
  Computer Control toggle when it would make the task easier.
- While the mode is on, direct requests such as `Open Discord` can run without
  saying a special authorization word on every turn.
- Applications are discovered locally from Start Menu shortcuts, registered
  application paths, Microsoft Store IDs, and supported registered protocols.
  Names such as `Battle.net`, `battle net`, and `BattleNet` normalize to the
  same catalog lookup. `VSCode` is derived from `Visual Studio Code`.
- Launch descriptors always come from the Windows catalog. Elaina never turns
  model output into a shell command, executable path, or command-line argument.
- `close_app` sends a normal window-close request. `force_quit_app` terminates
  only processes matched to a locally resolved catalog entry and always asks
  for a separate data-loss confirmation, even while Desktop Control Mode is
  on. These mutations use verified native Windows handles and window
  messages rather than model-generated or PowerShell process arguments. System
  shutdown is a different, unsupported action. Desktop Control Mode does not
  remove the separate confirmation for force-quit or Recycle Bin deletion.
- Browser tabs accept only validated HTTP or HTTPS destinations grounded in the
  spoken website. A model-produced URL cannot silently substitute another
  domain. Localhost and private-network destinations are disabled by default.
- Empty files and folders can be created only beneath the configured allowed
  roots (Desktop, Documents, and Downloads by default). Existing files and
  folders can be deleted from the same roots only after a separate confirmation;
  deletion moves the exact resolved item to the Windows Recycle Bin. Paths are
  normalized locally, traversal is blocked, parents must already exist, and
  existing items are never overwritten.
- Every attempt has a trusted state such as opened, closed, force-quit,
  created, not found, already exists, failed, or blocked. A short response
  cannot claim success unless the relevant tool returned a verified result.
- Action and agent-start responses are generated under a seven-word limit, with
  recent-response deduplication and generic closings removed.
- Set `computer_control.enabled` to `false` in `config/config.yaml` for an
  immediate kill switch. Optional spoken aliases can be added under
  `computer_control.aliases`. File roots and local-URL access are controlled by
  `computer_control.allowed_file_roots` and `allow_local_urls`.
- The Phase 4A command set does not include settings changes, generic mouse or
  keyboard control, file contents, overwriting, permanent deletion, arbitrary
  moving or renaming, credentials, arbitrary command arguments, elevation, UAC
  interaction, or system shutdown.

### Scoped native UI control (Phase 4B.2 stabilization)

- Elaina can observe controls exposed through Windows UI Automation and use
  generic focus, click, and text-entry operations. This supports requests such
  as searching for `BTS` in Spotify or typing into an open Notepad window
  without adding a separate Python function for each application.
- The foreground application or browser surface is captured when a request
  begins and frozen for the task. References such as `this page` stay attached
  to that surface; a missing GitHub `Settings` control can never fall through
  to opening the unrelated Windows Settings application.
- UI work uses a bounded action budget with repeated-state detection and one
  controlled recovery attempt. Observations do not consume the action budget,
  and Elaina reports a specific incomplete result when the task cannot make
  progress instead of continuing an unbounded loop.
- Success requires an observable postcondition. A click or typing API returning
  without an exception is not enough for Elaina to claim that the requested
  outcome happened.
- Native control names remain available internally for grounding and audit
  details. When `language.response` is `en`, spoken results describe those
  controls in English and do not pass Korean UI labels directly to the English
  Piper voice.

### Desktop control (Phase 4F, screen-native)

Elaina operates your applications the way you do: she reads a window's live
accessibility tree and moves the **real mouse and keyboard**. "Play
Bang Bang by IVE on Spotify" opens Spotify, searches using both title and
artist, and **double-clicks the exact track title Bang Bang** -- a single
click only opens a track, which is how you end up on an album page instead of
hearing the song. The artist is context, not part of the clickable name;
generic Play, radio/mix/station, and combined `Bang Bang by IVE` labels are
rejected before the pointer moves.

A request that names a track like that is resolved from live state without
asking the model to aim at anything: focus the app, search, find the row whose
name is exactly the title with the artist beside it, double-click, then
confirm playback from what the app itself reports (Spotify renames its window
to the track it is playing). If any of that cannot be proved, she says so
rather than reporting a song she did not start -- measured end to end at
about ten seconds, with no model round trips at all.

This is what makes modern apps operable at all. Spotify, Discord and
Battle.net draw their own interfaces and expose no typable field to Windows,
so the older driver could only click near one and type blind, never able to
confirm the text landed. A real pointer clicks the field itself.

**Sharing the mouse with you.** An explicit desktop request starts
immediately; Elaina does not ask a “can I take over?” question. If you
physically move the mouse or type while she is acting, that remains an
immediate emergency stop. Windows flags synthetic input, so her own clicks do
not trigger that stop.

**Remembering what she did.** Verified actions are recorded, so "stop it"
stops the track she actually started — resolved from that record, not from
the model's recollection. That follow-up is answered deterministically,
without asking the model to find the button.

Switch drivers with `computer_control.driver` in `config/config.yaml`:
`screen` (default) or `uia` (the Phase 4B driver, which never touches your
pointer but cannot type into many apps).

Verify it live:

```
python scripts/live_desktop_control_check.py
```

Limits: apps that expose nothing to UI Automation (some games, custom-drawn
UIs) are not operable and she refuses rather than clicking blind; the window
must be visible and frontmost, so you can always see what she is doing; and
if security software blocks the input hooks she falls back to watching the
pointer only, which cannot see typing, and says so.

### Browser control (Phase 4E, screen-native)

By default Elaina operates **the browser window you already have open**. If no
browser window exists, she launches the registered default browser and binds
that exact window for the workflow. She reads the live page through Windows
UI Automation and moves the real mouse and keyboard, so the visible session,
logins, and profile remain the ones in use.

- One complete page observation costs about **0.1 seconds**, measured against
  a real, already-open browser. The older CDP driver budgets up to 15 seconds
  just to start its own browser before it can look at anything.
- Elements are numbered from a fresh scan before every action. An element
  whose label changed since it was scanned is refused, not clicked.
- A cold new tab or `about:blank` does not need a readable Document node before
  navigation: Elaina focuses the address bar, verifies the landed URL, and
  makes one bounded retry if the page remains blank or unreadable.
- The selected browser window stays bound across multi-step work even when
  another application takes focus. A closed window releases the binding.
- Visible text, headings, controls, dialogs, listing cards, image counts and
  accessible image labels are included in page state. Privacy overlays may be
  dismissed only with a verified Reject/Essential-only choice; sponsored
  results are excluded from ordinal result clicks.
- Combobox/listbox choices use type-to-select and are reported as applied only
  after the accessible value reads back correctly.
- Before any click the browser is brought to the front and the page re-scanned
  (focusing can move the window), and the window owning the target pixel is
  checked -- if something is covering the element, the click is refused.
- If you move the mouse or type mid-run, Elaina stops immediately and leaves
  the pointer where you reclaimed it. Normal runs restore its starting place.
- An action is only reported as done when the page observably changed. A click
  that did nothing is reported as unverified, never as success.

Switch drivers with `browser_control.driver` in `config/config.yaml`:
`screen` (default) or `cdp` (the Phase 4C isolated-profile driver below).

Verify it live:

```
python scripts/live_screen_browser_check.py       # mechanism
python scripts/live_screen_browser_task_check.py  # full planner, real goals
```

Limits of the screen driver: a page whose accessibility tree never becomes
available is not operable (use `driver: "cdp"` for those); canvas-drawn and
closed-shadow-DOM widgets expose nothing to aim at; only the visible page of
a window is readable, so background tabs are not listed; and a custom popup
whose selected value cannot be read back is reported as unverified.

### Browser-page control (Phase 4C, `driver: "cdp"`)

- Websites that Elaina opens are created in an isolated, localhost-only
  CDP-enabled browser profile. This keeps a search, its result page, and a
  follow-up such as `click Images` in one controllable session without reading
  the user's normal browser profile, cookies, or unrelated tabs.
- Elaina identifies the controlled tab by the live foreground browser title or
  by the page she just opened. If neither identifies one tab, she stops rather
  than guessing a background tab.
- Page controls are freshly scanned from the live DOM before every action.
  Elements carry a per-scan fingerprint; an element, URL, or link that changes
  before a confirmed action is refused and re-observed rather than replayed.
- Terse page follow-ups such as `click Images`, `open the first result`, and
  `show pictures` inherit the captured browser page. Spotify Web follows the
  same DOM path; the installed Spotify app remains native UI control.
- Page text is untrusted data, never an instruction. Downloads, sends,
  reservations, account-changing actions, and pasting into message/comment
  fields require confirmation. Password and payment fields/actions are refused.
- Local/private network navigation remains disabled. Browser-page navigation is
  performed through observed links; raw model-generated page URLs are not used.

Current limitations:

- Under `driver: "cdp"`, a page already open in a normal personal browser
  profile cannot be attached for DOM control automatically; ask Elaina to open
  or reopen its public URL in her controlled browser session first. The
  default `screen` driver does not have this limitation -- it operates the
  window you already have open.
- Agent Builder installs reviewed blueprints; it does not generate and execute
  arbitrary Python tools.
- Google Calendar currently supports creating events, but not updating,
  deleting, or inviting attendees.
- Booking, payments, purchasing, and university registration are not yet
  implemented.

## Testing

Run the fast deterministic suite after ordinary code changes:

```powershell
.\.venv\Scripts\python.exe scripts\run_feature_regression.py
```

Run one live Ollama smoke case for every feature, plus live advice and
calculation-response checks:

```powershell
.\.venv\Scripts\python.exe scripts\run_feature_regression.py --mode all
```

Run every natural-language variant in `tests/feature_matrix.json`:

```powershell
.\.venv\Scripts\python.exe scripts\run_feature_regression.py --mode all --exhaustive
```

Run the live semantic cases for native UI requests:

```powershell
.\.venv\Scripts\python.exe scripts\run_feature_regression.py --mode live --exhaustive --feature computer_ui_action
```

List the available feature groups or print every voice phrase without running
Ollama:

```powershell
.\.venv\Scripts\python.exe scripts\run_feature_regression.py --list-features
.\.venv\Scripts\python.exe scripts\run_feature_regression.py --list-cases
```

Use `--list-cases --feature create_folder` to inspect one capability. For real
voice and Windows integration checks, follow
[`REINFORCEMENT_TEST_CASES.md`](REINFORCEMENT_TEST_CASES.md).

The exhaustive run is intentionally slower because each semantic-routing case
calls the configured local model. Write-capable cases stop at classification,
proposal, approval-policy, or mocked-tool boundaries; the regression runner
never edits project files, creates commits, pushes, installs an agent, or adds
a real calendar event. Computer-control tests never open the browser or a user
application; the native force-quit regression creates and removes only its own
temporary hidden test process.

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
