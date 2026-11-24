Option Explicit
Dim fso, shell, base, launcher, pyw, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
launcher = base & "\launcher.py"
pyw = base & "\.venv\Scripts\pythonw.exe"

If fso.FileExists(pyw) Then
    cmd = """" & pyw & """ """ & launcher & """"
    shell.Run cmd, 0, False
Else
    On Error Resume Next
    shell.Run "pythonw.exe """ & launcher & """", 0, False
    If Err.Number <> 0 Then
        Err.Clear
        shell.Run "pyw.exe -3 """ & launcher & """", 0, False
    End If
    If Err.Number <> 0 Then
        MsgBox "Python was not found. Install Python 3.9+ and try again, or run start.bat for diagnostics.", 16, "DocFormatFix"
    End If
End If
