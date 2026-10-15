' Double-click to launch the desktop interface without a terminal window.
Option Explicit
Dim shell, files, root, python
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
python = files.BuildPath(root, ".venv\Scripts\pythonw.exe")
If Not files.FileExists(python) Then
    MsgBox "Create the project .venv and install requirements.txt and requirements-desktop.txt first. See README.md.", vbExclamation, "Windows-Agent setup"
    WScript.Quit 1
End If
shell.CurrentDirectory = root
shell.Run """" & python & """ -m windows_agent.desktop", 0, False
