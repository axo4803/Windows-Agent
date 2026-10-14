# Windows-Agent MCP server

This server exposes the project's inspection tools to MCP hosts such as OpenCode
and Pi. It uses the existing Python tools and does not load Ollama or run the
terminal assistant's decision loop. The inherited `screen_use` MCP server remains
a separate interface with a different tool set and action behavior.

## Run

From the repository folder in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m windows_agent.mcp_server
```

The server uses stdin/stdout for MCP messages. Running it alone waits for a host;
it does not show an interactive prompt. Diagnostic logs go to stderr. The code
requires MCP Python SDK 2.3 or newer within the 2.x series. When installed as a
package, the entry point is `windows-agent-mcp`.

## Tools

| Tool | Purpose |
| --- | --- |
| `get_capabilities` | Server tool list, limits, and available mode |
| `inspect_wifi` | Current network SSID and adapter label, separately |
| `inspect_gpu` | Live GPU/VRAM and supported telemetry |
| `inspect_system` | Live Windows, CPU, memory, GPU, and free space |
| `inspect_storage` | Disk types, drive letters, supported health counters |
| `find_applications` | Registered application names and IDs; no launching |
| `list_windows` | Visible window titles and IDs; no activation |
| `inspect_window` | Accessible controls/text in one listed window; no activation |
| `list_directory` | Up to 200 directory entries |
| `read_text_file` | Up to 64 KB of UTF-8 text at a byte offset; no writes |
| `search_files` | Up to 100 name/glob matches with bounded recursion |
| `inspect_images` | Dimensions/aspect ratios; no image-content interpretation |
| `read_system_profile` | Dated saved inventory; not live verification |

Failed or unverified inspections are MCP tool errors, not successful completion.
Partial error details remain in the error content. Windows access restrictions,
driver support, and localized command output can limit the available telemetry.

The server exposes no actions: no clicking, typing, launching, window activation,
wallpaper changes, file writes, or arbitrary shell commands. It does not refresh
or write the saved system profile. Files, UI labels, and saved snapshots are
untrusted reference data. Requested local file paths are not limited to the
project directory; the connected host receives those contents, so use a trusted
host. A cloud-backed host may send tool results to its model provider.

Requests have a 30-second deadline and up to two inspection workers. Cancellation
stops cooperative loops and subprocess polling. An already-running native Windows
call may finish before cancellation takes effect. The server has no idle lifetime
limit and initializes UI Automation on the thread performing the inspection.

## Generic stdio configuration

Replace the paths with your checkout and environment. `PYTHONPATH` makes module
discovery independent of the host's working directory.

```json
{
  "mcpServers": {
    "windows-agent": {
      "command": "C:/Users/kuin3/LocalSysAgent/screen-use/.venv/Scripts/python.exe",
      "args": ["-m", "windows_agent.mcp_server"],
      "env": {
        "PYTHONPATH": "C:/Users/kuin3/LocalSysAgent/screen-use"
      }
    }
  }
}
```

Hosts differ in configuration syntax; this is the common `mcpServers` form.

## OpenCode

Merge this into `opencode.json`, adjusting the path if necessary:

```json
{
  "mcp": {
    "windows-agent": {
      "type": "local",
      "command": [
        "C:/Users/kuin3/LocalSysAgent/screen-use/.venv/Scripts/python.exe",
        "-m",
        "windows_agent.mcp_server"
      ],
      "environment": {
        "PYTHONPATH": "C:/Users/kuin3/LocalSysAgent/screen-use"
      },
      "enabled": true
    }
  }
}
```

For Pi, use its supported MCP integration to register the same stdio command and
environment. This change does not install or modify a host's configuration.

Try: "Using windows-agent, which Wi-Fi network am I currently connected to?"

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_windows_mcp.py -q
```

These tests use controlled files, mocked hardware/UI calls, and actual MCP client
requests, including a subprocess stdio connection and legacy-client mode.

Future action tools should adapt approval to the MCP host explicitly. The current
terminal `APPROVE` prompt must not be used to block an unattended stdio server.
