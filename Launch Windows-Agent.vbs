' Double-click to launch the desktop interface without a terminal window.
Option Explicit
Dim shell, files, root, python, localApp, fallbackPy
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
python = files.BuildPath(root, ".venv\Scripts\pythonw.exe")
If Not files.FileExists(python) Then
    localApp = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%")
    fallbackPy = files.BuildPath(localApp, "Programs\Python\Python312\pythonw.exe")
    If files.FileExists(fallbackPy) Then
        python = fallbackPy
    Else
        MsgBox "Create the project .venv and install requirements.txt first, or ensure Python is installed. See README.md.", vbExclamation, "Windows-Agent setup"
        WScript.Quit 1
    End If
End If
shell.CurrentDirectory = root
shell.Run """" & python & """ -m windows_agent.desktop", 1, False
