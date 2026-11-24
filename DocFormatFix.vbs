Option Explicit
Dim fso, shell, base, launcher, pyw, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
launcher = base & "\launcher.py"
pyw = base & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pyw) Then
    pyw = "pythonw.exe"
End If
cmd = """" & pyw & """ """ & launcher & """"
On Error Resume Next
shell.Run cmd, 0, False
If Err.Number <> 0 Then
    pyw = "pyw.exe"
    cmd = """" & pyw & """ -3 """ & launcher & """"
    Err.Clear
    shell.Run cmd, 0, False
    If Err.Number <> 0 Then
        MsgBox "Python was not found. Install Python 3.9+ and try again.", 16, "DocFormatFix"
    End If
End If
