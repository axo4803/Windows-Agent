# DSH workspace permissions

Read this first if the desktop app opens a blank window, or if Python inside this
project suddenly cannot write to `%TEMP%`.

## Symptom

Any of these, appearing together and out of nowhere:

- `Launch Windows-Agent.vbs` opens a window that stays blank white; F5 does not help.
- A Python under `.venv` fails to write `%TEMP%` with `PermissionError [Errno 13]`,
  and `tempfile.gettempdir()` silently returns the project directory instead.
- `ImportError: DLL load failed while importing _ctypes: A dynamic link library (DLL)
  initialization routine failed.`
- `pytest` errors on every test with `No usable temporary directory found`.

## Cause

DeepSeek Harness's Windows ACL sandbox (`dsh-sandbox-windows-acl`) confines child
writes by applying three **standing** edits to a workspace root, which DSH never
revokes:

| Edit | Effect |
|---|---|
| capability-SID allow ACE | lets the confined token write (benign, expected) |
| `Everyone` deny of `FILE_DELETE_CHILD` | blocks write-class access for other processes |
| an **inheritable Low integrity label** | everything under the tree inherits it |

The third is what breaks things. Windows refuses write-class access from a Low
integrity process to a Medium-labelled object. Because the label is inherited, an
executable stored in the tree — `.venv\Scripts\python.exe` — **starts at Low
integrity**, so it cannot write `%TEMP%` and falls back to the current directory.
Medium-integrity programs pointed at the tree (Edge, with its profile under
`memory/`) can be denied access to files inside it.

DSH's documentation is explicit that this is by design: the workspace edits are a
standing reuse cache, `dispose()` keeps them, they outlive DSH, and manual
`icacls` cleanup cannot revoke them (`ERROR_NONE_MAPPED`, 1332). The bundled
`diagnose-windows-sandbox-acl` skill does **not** fix this class — by design it
preserves integrity labels and never removes a deny.

## Detect

```powershell
.\.venv\Scripts\python.exe tools\dsh_workspace_grants.py report .
```

Exits non-zero when the damaging edits are present. Read-only; it inspects the
named paths and every ancestor, reports the integrity label and any deny ACEs,
lists DSH's own grants as expected, and runs a `%TEMP%` canary from the current
interpreter. Add `--tree` to sweep descendants.

Three-line canary if you would rather not run the tool:

```python
import os; d = os.environ["TEMP"]; p = os.path.join(d, "canary.txt")
try: open(p, "w").write("x"); os.remove(p); print("OK - Medium integrity")
except Exception as e: print("BROKEN - Low integrity label is back:", e)
```

Run it with the project's `.venv` interpreter — that is what proves the label.

## Repair

```powershell
.\.venv\Scripts\python.exe tools\dsh_workspace_grants.py revoke . --apply
```

Writes an SDDL backup of the current descriptor first, then removes the `Everyone`
deny ACEs and resets the integrity label from Low to Medium. Without `--apply` it
only prints what it would do. Descendants that inherited the old label may need
the same call on their own root.

`tools\dsh_workspace_grants.py selftest` proves detection and repair end to end on
a throwaway directory, without touching anything real.

## It comes back

The workspace edit is a reuse cache keyed on the exact ACE and label. Once
removed, **the next confined command DSH runs in this workspace re-applies it**,
and the symptoms return. To avoid the cycle, either keep DSH sessions in this
directory unconfined, use a workspace that does not contain programs you run
outside DSH, or re-run the canary after any confined session and repair before
using the desktop app.
