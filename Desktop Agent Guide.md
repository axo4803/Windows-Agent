# Local Windows Desktop Agent

The main entry point is `desktop_agent.py`. It uses a local Ollama server at
`http://127.0.0.1:11434` and defaults to `qwen3.5:4b`.

Supporting modules are organized under `windows_agent/`: runtime, conversation
context, and terminal input live at the package level, while `tools/` groups
applications, files/images, network, system/storage, diagnostics, and wallpaper.
The launch command, project-level `memory/` and `logs/` directories, and stop-file
location are unchanged.

## Setup and launch

From the project folder in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
ollama pull qwen3.5:4b
.\.venv\Scripts\python.exe .\desktop_agent.py
```

Keep Ollama running before starting the assistant. Select another installed model
with `--model`. Use `--task "your request"` for a single task.

Enter submits a task; Alt+Enter inserts a line. Stop a running task with Ctrl+C,
Ctrl+Alt+F12, or a `desktop_agent.stop` file beside the script. Remove the stop
file before restarting. An already dispatched action may finish before stopping.

## Modes and approvals

Expanded mode is the default. `--read-only` disables expanded file and input
operations and restricts clicking to supported navigation controls. `--offline`
disables online documentation lookup.

Ordinary supported UI clicks and wallpaper changes can run directly in expanded
mode. Financial action buttons request approval. Enter and Space activation of
non-text controls request approval, as do editing/save shortcuts such as
Backspace, Ctrl+V, Ctrl+Z, Ctrl+Y, and Ctrl+S. Replacing more than 200 characters,
or replacing text whose existing length cannot be read, also requests approval.

Use `--strict-approval` to additionally confirm structured file writes, wallpaper
changes, typing, scrolling, keyboard navigation, and generic UI confirmations.
Approvals accept `y`, `yes`, or `APPROVE`, case-insensitively.

Keyboard operations validate the focused control, its selected-window ancestry,
and password/destructive/console restrictions. Text fallback escapes shortcut
syntax and verifies focus. Scrolling uses UI Automation ScrollPattern; unsupported
controls return an error without sending arrow keys.

These UI checks are not a secure sandbox. Application behavior and accessible
labels vary; review the active app and requested outcome before approving.

## File and image operations

File search is recursive and bounded. Text reads use 64 KB chunks with optional
byte offsets. Copy and move accept files up to 250 MB and refuse destination
overwrites; `on_conflict='rename'` selects a numbered destination. Moving removes
the source after a successful size-checked copy.

Text creation supports `error`, `rename`, `backup`, and `append` conflict modes.
`backup` saves an existing file before replacing its contents. System, program,
and agent directories are excluded from structured writes and moves; listed
executable/script/shortcut extensions are blocked for copy and move.

Image resizing supports PNG, JPEG, WEBP, and BMP. `crop` gives exact dimensions
with a centered crop; `pad` preserves the full image on a blurred canvas;
`contain` fits within the requested bounds and may produce smaller dimensions;
`stretch` gives exact dimensions with possible distortion. These use resampling,
not neural upscaling. Original images are retained.

## Hardware, diagnostics, and limits

Tool schemas are selected by task and recent conversation context by default;
failures unlock all tools permitted by the current mode. `--no-dynamic-routing`
disables selection. `inspect_wifi` remains available in the initial tool set.

Known file pipelines and other deterministic sequences can use `batch_actions`
with 1 to 8 steps. The wrapper and each attempted step consume the same tool-call
budget. Steps share direct calls' approval, denial, and retry checks, and stop at
the first error. A completed batch is not proof that the full task is complete.

`--fast-review` is optional and skips the second model review only for a single
explicitly verified inspection with no detected multi-part request. It does not
skip review for unverified returns or write pipelines.

Application activation and ordinary clicks return a fresh controls preview when
inspection succeeds. `inspect_screen_visual` can supply an annotated screenshot
to the local Ollama model for custom controls; it does not create clickable IDs,
enable coordinate actions, or invoke a cloud vision provider. Raw screenshot
bytes are omitted from the local task log and saved conversation history.

GPU queries prefer direct NVML calls with the existing diagnostics fallback.
Fixed application/storage/inventory scripts can reuse a lazy PowerShell process.
Each query still checks cancellation and a deadline; values are queried afresh.
The process closes when the server or terminal assistant exits.

Bounded experience records live in `memory/windows-agent-trajectories.json`
(`SCREEN_USE_HOME` can override the storage directory). Only explicitly verified
tool histories are recalled as untrusted hints. They do not grant permission or
establish completion. `/forget-memory` also clears these records.

Use `inspect_wifi` for the current connected Wi-Fi network name (SSID). The saved
network inventory's `Name` field identifies an adapter, not the SSID. The live
tool uses `netsh wlan show interfaces`; restricted access or unsupported output
languages are reported as unavailable, without inventing a network name.

GPU inspection uses `nvidia-smi` when available and a Windows registry fallback.
Storage inspection reports health and reliability counters where supported.
Saved inventory includes physical RAM modules. Missing telemetry is not proof of
an absent device or a healthy drive.

Terminal tools run fixed diagnostic commands without a shell. They include
GPU/Ollama diagnostics, network information, and Windows inventory utilities.
An unavailable executable produces an error; arbitrary scripts are not exposed.

Defaults per task: 40 tool calls, 30 model turns, 600 seconds, and 16 launches.
Use `--max-tools`, `--max-turns`, and `--max-seconds` to adjust the first three.

Useful slash commands: `/capabilities`, `/gpu`, `/storage`, `/system`,
`/refresh-system`, `/new`, `/last`, `/memories`, `/remember TEXT`,
`/forget-memory`, and `/exit`.

Logs and saved conversations live in `logs/` and `memory/` and can include
inspected screen and file text. Online documentation lookup uses allowlisted
sites; searches leave the PC. Use `--offline` to disable it.

## Validation

Run `.\.venv\Scripts\python.exe -m pytest tests -q`. Keyboard regression tests
mock actual input so they do not operate the desktop. Live compatibility still
depends on each application's UI Automation support.
