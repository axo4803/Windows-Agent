# Local Windows desktop agent

## Current setup and behavior

From the project folder in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe .\desktop_agent.py
```

The desktop assistant requires a running local Ollama server at
`http://127.0.0.1:11434` and the `qwen3.5:4b` model (or choose an installed model
with `--model`). Install the model with `ollama pull qwen3.5:4b`.

Expanded mode is the default: it permits supported UI and file changes.
Use `--read-only` for inspection and restricted navigation, and `--offline`
to disable online documentation lookup. Wallpaper changes, document replacement,
and ambiguous confirmations require approval. Existing files are not overwritten
by the file tools. UI label checks are not a secure sandbox.

Enter submits a task; Alt+Enter inserts a line. Use `/exit` to leave, `/new`
to clear conversation history, and `/capabilities` to inspect available actions.
Stop a running task with Ctrl+C or Ctrl+Alt+F12. Default limits are 24 tool calls,
20 model turns, 300 seconds, and eight launches. App searches reuse their catalog
for up to 30 seconds within a task; launching always revalidates it.

Detailed logs and saved conversations are kept locally in `logs/` and `memory/`.
They can include inspected file and screen text.

## Historical implementation notes

The notes below record earlier versions. In particular, descriptions of
read-only defaults and disabled typing apply to the original version, not the
current behavior documented above.


Installed at `C:\Users\kuin3\LocalSysAgent\screen-use\desktop_agent.py`.
The existing `agent.py` was left unchanged. No dependencies were added.
Uses the existing `uiautomation` and `ollama` packages with Qwen `qwen3.5:4b`
at `http://127.0.0.1:11434`. No cloud model, screenshot model, browser service,
or shell execution tool is exposed to the model.

## Run the read-only connection task

In PowerShell:

```powershell
Set-Location 'C:\Users\kuin3\LocalSysAgent\screen-use'
.\.venv\Scripts\python.exe .\desktop_agent.py
```

The agent is read-only across applications and the local filesystem. It discovers
visible windows and Start Menu shortcut names, inspects UI controls and accessible
text, activates windows, and launches only fixed approved applications. It can
open a small whitelist of navigation categories and tabs. It cannot type, edit,
save, toggle settings, or click action buttons. Application launch remains an
explicit process start and may trigger an application's own startup behavior.
The one approved write exception is an explicitly requested wallpaper slideshow:
the agent shows the folder, image count, interval and shuffle setting, then waits
for `APPROVE`. This does not grant general settings-write access.

It can report Windows, CPU, memory, GPU and disk details; list one requested
directory (up to 200 entries); and read one requested text file (up to 64 KB).
Binary files are refused. File inspection is direct, local and read-only; it does
not recurse through folders. Treat requested files as private because their text
is sent to the local Ollama model and shown in the terminal.

Online documentation lookup is available by default. The model chooses to use it
for version-sensitive or obscure facts and when official documentation is requested.
For example: `agent --task "Find the official Ollama documentation for context
length"`. Use `--offline` to disable network lookup for one run. It searches a short allowlist of official
Microsoft, Python, Ollama, and MDN sites through DuckDuckGo, then can fetch only
HTTPS pages on those documentation domains. Search queries leave the PC; local
file and screen contents are not sent as queries. Fetched page text is treated as
untrusted reference material and is limited to 14,000 characters.

The terminal shows compact tool summaries. Full per-run details are written to
`logs\desktop-agent-*.jsonl` beside the agent script, including inspected text and
requested file contents. Keep these local logs private; delete them when no longer
needed. The terminal prints the exact log path at the end of each run.

## Approve an application to launch

```powershell
.\.venv\Scripts\python.exe .\desktop_agent.py --allow-app notepad --task 'Open Notepad and inspect the new window.'
```

The interactive-actions option is disabled and rejected. Click and typing tools
always refuse requests, regardless of the model's instructions.

Launch targets are fixed: Settings, Calculator, Notepad, and Task Manager. Settings
is approved by default; other apps must be explicitly approved with `--allow-app`.
Discovered shortcut names do not confer launch permission. Start Menu discovery
does not enumerate every packaged or portable application. No executable path,
command, arbitrary URI, or launch arguments can be supplied by the model.

## Limits and stopping

- Default: 24 total tool calls, 20 model turns, 300 seconds, and at most 3 launches.
- Override limits with `--max-tools`, `--max-turns`, and `--max-seconds`.
- Stop with Ctrl+C or Ctrl+Alt+F12.
- Alternatively, create `desktop_agent.stop` beside the script. Remove it before
  the next run. `--stop-file` selects another path.
- A stop cancels waiting for the model or approval and blocks further actions.
  An already executing Windows UI Automation call may finish before control returns;
  an already dispatched model request may continue locally until its timeout.

Controls must remain visible, non-password, and inside the selected window.
Element IDs expire on a fresh inspection or activation. If launching an app does
not open a new window, the agent will not select a pre-existing app window.

## Verification on October 12, 2026

The local agent opened Settings and autonomously selected Network & internet.
The UI showed:

- Active connection: **Wi-Fi (SpectrumSetup-9FF5)**
- Status: **Connected, secured**
- Profile: **Private network**
- Band: **5 GHz**

No setting-changing tools were available. This reports Windows' visible connection
status; it does not independently test Internet reachability. The final installed
version was rerun successfully and read the same status. Ten safeguard tests
passed on the earlier version.

The original `agent.py` SHA-256 remained
`001DE60F355E26E6E0ED8198F18C2AEDC885DCC3E487505989916617D9B7ADCB`.

## Conversation and image inspection upgrade

Run `agent` from any PowerShell folder. It stays open until `/exit` or `/quit`.
Recent completed exchanges are saved locally in `memory/conversation.json` and
loaded within a bounded context budget. `/new` clears conversation history while
retaining your system profile and memories. `/capabilities` shows the actual
available actions. `/memories` reviews saved notes and automatic memories;
`/forget-memory` clears those memories separately from conversation history.

The read-only `inspect_images` tool reads stored pixel dimensions, exact aspect
ratios, and whether an image is within 1% of 16:9. It checks up to 100 images in
one folder without recursion or modification. It does not interpret visual
contents or apply EXIF orientation. Pillow is already a project dependency.

Local model requests disable hidden thinking to keep the reply budget available
for answers and tools. Empty answers retry once, then stop with a clear message.
Logs record the completion reason and generated token count for debugging.
Confirmed using Qwen 3.5 4B: the agent measured all 11 images in Captures, reported
zero exact 16:9 matches, and used the previous folder path correctly when asked
to check its empty `16_9` subfolder. Ten images are 1672x941 (near 16:9); the
Shanghai image is 1448x1086 (4:3). No photos were changed.

## Expanded permissions

Expanded mode is now the default. `agent --read-only` restores inspection-only
behavior. All four fixed launch targets (Settings, Calculator, Notepad, Task
Manager) are approved in expanded mode, with at most eight launches per task.
Ordinary UI buttons, tabs, checkboxes and radio buttons can be activated. Search
fields support text through ValuePattern; Notepad replacement requests approval.
Console/script/registry tools are blocked for interaction. Generic confirmation
buttons such as OK, Yes, Apply and Save require the human to approve.

New file tools create folders, copy data files up to 100 MB, and create new
txt/md/csv/json files. Exclusive destination creation prevents overwrites;
copying retains the source. They do not write into Windows, Program Files or
the agent's own project, or copy executable/script/shortcut files.

Deletion, uninstall, wipe, formatting and reset actions are blocked by UI label
checks and prompt rules. UI labels cannot guarantee the behavior of every app;
these safeguards do not make arbitrary desktop interaction a secure sandbox.
There is no unrestricted shell, keyboard-shortcut, executable-launch or deletion
tool. The wallpaper slideshow approval remains in place.

## Storage inspection

`inspect_storage` runs a fixed read-only Windows Storage query and maps drive
letters through partitions to disks. SSD/HDD media types are associated only
when a unique serial number matches the physical disk; otherwise type stays
unknown. No arbitrary model-provided commands are accepted. Successful checks
are cached in the local profile with their own capture time. `/refresh-system`
also refreshes storage. Local system questions should attempt the relevant live
tool before reporting a missing snapshot field as a limitation.

## Registered application launching

In expanded mode, `find_applications` searches the Windows Start application
catalog and returns opaque IDs. `launch_application` accepts these discovered
IDs and validates the entry again before opening it through Windows AppsFolder.
The model cannot supply executable paths, shell commands or launch arguments.
Terminal, Python-console, registry, uninstall, reset and recovery entries are
excluded. Existing deletion/reset interaction checks remain in place. Portable
or unregistered apps may not appear. Discovery returns at most 30 matches;
narrow the query if it is truncated. There are still eight launches per task.
`--read-only` retains the original fixed approved launch targets.

## Basic terminal diagnostics

`run_terminal_command` now supports these exact read-only commands: `hostname`,
`whoami`, `ipconfig`, `ipconfig /all`, `tasklist`, and `systeminfo`. Results go to
the model and the detailed local log; the console shows a compact status. The
tool uses fixed System32 executable paths without a command shell or input.
Each command has a 25-second timeout, responds to the existing emergency stop,
and returns capped output. No extra arguments, pipelines, Python/PowerShell
scripts, deletion, reset, process termination or network-setting changes are
accepted. These diagnostics also work in `--read-only` mode. This does not
enable interactive terminal windows or image-resizing scripts.

## Multiline requests and verified photo actions

The interactive prompt now uses prompt_toolkit. Paste the complete request,
including blank lines, then press **Enter** or **Ctrl+D** once to send it.
**Alt+Enter adds a newline**. Single-line slash commands such as `/exit` still work
with Enter. The input limit is 12000 characters. Ordinary one-task `--task`
invocation is unchanged. Complete submitted task text is saved in bounded local
conversation history; older context shown to the model may be explicitly
shortened. Stopped tasks retain their evidence without claiming completion.

`resize_image` creates a new verified RGB PNG using Lanczos resampling and a
centered crop at exact requested dimensions. This is not neural/AI enhancement.
The original stays intact and an existing destination is never overwritten.
`set_wallpaper` accepts an existing PNG/JPEG/BMP, requests approval, applies it
with Fill through Windows APIs and checks the reported path and fit settings.
It does not execute terminal scripts. It remains disabled in read-only mode.
Wallpaper application was not exercised against the user's desktop during
verification; the approval gate was tested without changing wallpaper.

UI actions now refuse to use controls when their inspected window is no longer
foreground. Discover and activate new dialogs before inspecting and acting.
Standard file-picker filename fields can accept an existing local file path.
Instruction changes require evidence for each requested outcome; this improves
reporting but cannot guarantee that the model never makes a false claim.

## Reliability improvements

Instructions are now maintained in `agent_runtime.py` as a compact policy.
Disabled write/web tools are hidden from model requests in read-only/offline
modes, and the dispatcher enforces the same active tool list. Tool context is
bounded, with explicit omission notices and complete raw results retained in
logs. A compact record of prior actions accompanies subsequent model rounds,
including explicit error/unverified outcomes. Failures for different arguments
no longer count as the same repeated action.

`/last` displays the latest saved task, answer and structured tool evidence.
Long input now shows a visible validation error instead of silently refusing
submission. A live local-model check completed both storage and image
inspection, then rechecked the current folder rather than inventing a 4:3
image when asked a follow-up based on that false assumption.


## Persistence and completion checks

The desktop assistant now checks a proposed final answer against every part of
the original request before displaying it. It can continue with tools rather than
ask whether to attempt an already requested investigation. This costs an additional
model turn per proposed answer and shares the existing time and turn budgets.
The check is model-based, not a guarantee of correctness.

Two identical failed actions block that action for the task rather than stopping
the whole task. Changed error text does not reset this limit. Different supported
approaches remain available. Explicit approval denial blocks immediate retries;
existing restrictions and approval gates remain in force. Unverified results,
false success flags and reported errors trigger recovery. At budget exhaustion,
recent evidence is shown and saved without claiming completion.
