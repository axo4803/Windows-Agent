# Windows-Agent

A local Windows desktop assistant powered by Ollama. Describe a task in the browser or terminal, and the agent uses Windows UI Automation and bounded tools to inspect applications, navigate controls, and perform supported desktop and file actions.

## Requirements

- Windows with an interactive desktop session
- Python 3.10 or newer
- Ollama running locally at `http://127.0.0.1:11434`
- The `qwen3.5:4b` model, or another installed model selected with `--model`

## Quickstart

Run these commands in PowerShell with Python, Git, and Ollama installed:

```powershell
git clone https://github.com/axo4803/Windows-Agent.git
cd Windows-Agent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
ollama pull qwen3.5:4b
.\.venv\Scripts\python.exe .\desktop_agent.py
```

Start Ollama before launching the agent. If its background service is not running, run `ollama serve` in a separate terminal.

### Desktop interface

Double-click **Launch Windows-Agent.vbs** in the project folder. It opens the existing chat interface in a dedicated Microsoft Edge app window, without browser tabs, an address bar, or a terminal. A separate app profile leaves your normal browser session alone. Alternatively:

```powershell
.\.venv\Scripts\python.exe -m windows_agent.desktop
```

The desktop starts its own loopback backend on an automatically assigned port. Closing the app window requests cancellation of its task and stops that backend. An existing browser server on port 8765 stays available. Avoid running desktop-changing tasks in both interfaces simultaneously. Window size is remembered locally, and duplicate desktop launches are blocked. Ollama still runs separately.

When installed as a package, use `windows-agent-desktop`. This first release requires Microsoft Edge, Python and the project environment; a standalone executable/installer is a later packaging step. If launching without a terminal fails, check `logs/desktop-app.log`. Windows must have VBScript available to use the double-click launcher; the Python module works independently of VBScript.

The optional embedded pywebview renderer is experimental on this PC because its .NET bridge failed live startup checks. Edge app-window mode is the default. To try the embedded renderer elsewhere, install `requirements-desktop.txt` and use `--renderer webview`.

### Browser interface

Launch the local interface (opens your browser automatically):

```powershell
.\.venv\Scripts\python.exe -m windows_agent.web
```

Open `http://127.0.0.1:8765` to chat, follow live tool activity, approve or deny guarded actions, and stop a task. Choose an installed Ollama model, enable read-only mode or strict approvals, and adjust task limits in the activity panel. The browser uses the same agent loop, tools, conversation history, and local logs as the terminal.

Keep the launch terminal open; Ctrl+C shuts down the interface. Use `--port 8766` to change the port, `--model NAME` to select the default model, or `--no-browser` to start without opening a browser. After installing the package, `windows-agent-ui` launches the same interface.

Only one task runs at a time. Reloading the page reconnects to its current progress and pending approval. **New chat** clears conversation context, while saved preferences and system snapshots remain available. Stop cancels approval waits and requests cancellation of the agent; an action already dispatched may finish.

The server binds only to `127.0.0.1`. Browser requests stay on your computer; models run through the existing local Ollama server. Online documentation lookup follows the task setting. The UI adds no frontend build step, cloud service, or model process.

### Terminal interface

Enter submits a task; Alt+Enter inserts a line. Use `/capabilities` to see available actions, `/new` to clear conversation history, and `/exit` to quit.

For a single task:

```powershell
.\.venv\Scripts\python.exe .\desktop_agent.py --task "Inspect my system and report CPU, memory, and storage details."
```

For inspection with restricted navigation and online documentation lookup disabled:

```powershell
.\.venv\Scripts\python.exe .\desktop_agent.py --read-only --offline
```

## Supported tasks

- Inspect visible windows, accessible controls, and screen text.
- Find and launch supported applications, activate windows, and navigate supported controls.
- Read GPU VRAM and telemetry, RAM module inventory, storage health, and network diagnostics where supported by Windows and installed utilities.
- Read the current Wi-Fi SSID through `inspect_wifi`; saved adapter labels such as `Wi-Fi` are not network names.
- Search files and folders, list directories, and read text files in bounded chunks.
- Create directories and text files; copy or move files to new destinations. File conflicts can use numbered names, or backups/appending for text updates.
- Inspect image dimensions and create resized PNG, JPEG, WEBP, or BMP copies using crop, blurred padding, contain, or stretch modes.
- Neurally upscale JPEG/JFIF, PNG, or WEBP images through an installed Upscayl engine using Digital Art, Standard, or High Fidelity. Creates a new PNG and verifies the requested 2×, 3×, or 4× dimensions; does not open the GUI.
- Change wallpaper fit or configure a slideshow; use `--strict-approval` to confirm these changes.
- Type into supported editable controls, use guarded keyboard actions, and scroll controls exposing a UI Automation ScrollPattern.
- Look up documentation on allowlisted official sites when online lookup is enabled.
- Save conversation history, durable preferences, and local system snapshots.

Available actions depend on the selected mode and the target application's accessible controls. Use `/capabilities` for the current tool and approval settings.

## Controls and limits

Expanded mode is the default and permits supported UI and file changes. `--read-only` restricts UI and file actions. Purchase controls, keyboard activation and editing shortcuts, and replacement of substantial or unreadable existing text request approval. Use `--strict-approval` to additionally confirm file writes, wallpaper changes, typing, scrolling, and generic UI confirmations. UI label checks are not a secure sandbox.

Copy and move tools refuse to overwrite existing destinations. Text updates can explicitly use `on_conflict='backup'` to retain the original before replacement, or `'append'` to add content. Moving removes the source after a successful size-checked copy. Scrolling requires a supported ScrollPattern and fails rather than sending navigation keys to an unrelated control.

Stop a running task with **Ctrl+C** or **Ctrl+Alt+F12**. You can also create `desktop_agent.stop` in the project folder; remove it before the next run. An action already dispatched may finish before the stop takes effect.

Default task limits are 40 tool calls, 30 model turns, 600 seconds, and 16 app launches. Adjust the first three with `--max-tools`, `--max-turns`, and `--max-seconds`. Terminal diagnostics accept a fixed list of commands, including GPU and Ollama diagnostics; the agent does not expose an arbitrary shell tool.

Use `/gpu` and `/storage` for live hardware reports, or `/refresh-system` to refresh the saved inventory. Some telemetry requires compatible drivers, Windows storage support, or permissions; unavailable values remain unknown.

Model requests go to the local Ollama server. Online documentation searches use the network unless `--offline` is set. Logs and saved conversations in `logs/` and `memory/` can contain inspected file and screen text.

Neural upscaling requires Upscayl installed under the Windows Program Files directory with its bundled engine and models. It uses a 128-pixel tile size to limit GPU memory pressure, supports task cancellation, and limits each engine run to 300 seconds. Its native models are 4×; the engine produces the requested final output scale. Inputs are limited to 5 million pixels. Strict approval also covers neural upscaling.

The task context retains matched file paths and output paths after older model turns are trimmed. Identical successful file lookups may be reused for up to 15 seconds, marked as cached; UI actions and writes invalidate this cache. Hardware, Wi-Fi, and window inspections stay live. Repeated tool calls that produce no new progress trigger a checkpoint and then stop with an incomplete-task report. Registered app launches reuse matching windows and poll startup before returning; a pending launch is not repeated.

## Project layout

| Path | Purpose |
| --- | --- |
| `desktop_agent.py` | Main terminal assistant and desktop tools |
| `windows_agent/runtime.py`, `windows_agent/context.py` | Task runtime and conversation context |
| `windows_agent/terminal.py` | Interactive terminal input |
| `windows_agent/web/` | Local browser UI, task session, and approval controls |
| `windows_agent/powershell.py` | Lazy, cancellable PowerShell inspection process |
| `windows_agent/tools/applications.py` | Windows application discovery |
| `windows_agent/tools/files.py` | File actions, search, and image operations |
| `windows_agent/tools/network.py`, `windows_agent/tools/system.py` | Wi-Fi lookup, hardware/storage inventory, and saved profiles |
| `windows_agent/tools/wallpaper.py`, `windows_agent/tools/diagnostics.py` | Wallpaper and diagnostic tools |
| `windows_agent/tools/visual.py` | Annotated screenshots for the local model |
| `screen_use/` | Inherited desktop automation SDK and MCP server |
| `agent.py` | Earlier Calculator-only agent |
| `tests/` | Automated tests |

[Desktop Agent Guide](Desktop%20Agent%20Guide.md) contains additional details and historical implementation notes.

## SDK and MCP server

Windows-Agent now has a separate **read-only MCP server** for Wi-Fi, hardware,
file, application, and UI inspection. It uses the existing tools without running
a model or the terminal agent loop:

```powershell
.\.venv\Scripts\python.exe -m windows_agent.mcp_server
```

See [MCP setup](docs/MCP.md) for the tool list and host connection examples.

The inherited `screen_use` package remains available independently of the terminal assistant:

```python
from screen_use import ScreenUse

tools = ScreenUse()
elements = tools.list_ui_elements()
```

Launch its MCP server with the project's Python environment:

```powershell
.\.venv\Scripts\python.exe -m screen_use.mcp_server
```

The SDK and MCP server have their own configuration and safety behavior. The terminal assistant's `--read-only` flag applies to `desktop_agent.py`. See `.env.example` for the SDK's optional vision-provider configuration.

## Development

Run the automated tests from the project folder:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

Desktop demos in `examples/` interact with the real desktop.

## Credits and license

Windows-Agent builds on [screen-use by tongriyaotxt](https://github.com/tongriyaotxt/screen-use), which provides the inherited desktop automation SDK, MCP server, and related examples. This repository adds the local Windows assistant and supporting tools.

Distributed under the [MIT License](LICENSE). The original copyright and permission notice are retained in `LICENSE`.
