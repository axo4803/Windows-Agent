# Windows-Agent

A local Windows desktop assistant powered by Ollama. Describe a task in the terminal, and the agent uses Windows UI Automation and bounded tools to inspect applications, navigate controls, and perform supported desktop and file actions.

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
- Read system, GPU, storage, and network diagnostic information.
- List directories and read bounded text files.
- Create directories and text files, and copy files to new destinations.
- Inspect image dimensions and create resized PNG copies.
- Change wallpaper or configure a slideshow with explicit approval.
- Look up documentation on allowlisted official sites when online lookup is enabled.
- Save conversation history, durable preferences, and local system snapshots.

Available actions depend on the selected mode and the target application's accessible controls. Use `/capabilities` for the current tool and approval settings.

## Controls and limits

Expanded mode is the default and permits supported UI and file changes. `--read-only` restricts UI and file actions. Wallpaper changes, document replacement, and ambiguous confirmations request approval. File creation and copy tools do not overwrite existing files. UI label checks are not a secure sandbox.

Stop a running task with **Ctrl+C** or **Ctrl+Alt+F12**. You can also create `desktop_agent.stop` in the project folder; remove it before the next run. An action already dispatched may finish before the stop takes effect.

Default task limits are 24 tool calls, 20 model turns, and 300 seconds. Adjust them with `--max-tools`, `--max-turns`, and `--max-seconds`. Terminal diagnostics accept a fixed list of commands; the agent does not expose an arbitrary shell tool.

Model requests go to the local Ollama server. Online documentation searches use the network unless `--offline` is set. Logs and saved conversations in `logs/` and `memory/` can contain inspected file and screen text.

## Project layout

| Path | Purpose |
| --- | --- |
| `desktop_agent.py` | Main terminal assistant and desktop tools |
| `agent_runtime.py`, `agent_context.py` | Task runtime and conversation context |
| `app_catalog.py` | Windows application discovery |
| `file_actions.py`, `wallpaper_tools.py`, `terminal_tools.py` | File, wallpaper, and diagnostic tools |
| `system_profile.py`, `storage_info.py` | Local system information |
| `screen_use/` | Inherited desktop automation SDK and MCP server |
| `agent.py` | Earlier Calculator-only agent |
| `tests/` | Automated tests |

[Desktop Agent Guide](Desktop%20Agent%20Guide.md) contains additional details and historical implementation notes.

## SDK and MCP server

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
