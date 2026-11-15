' 兼容旧入口：打开可见演示终端，便于首次安装者查看错误与正常停止。
Dim shell, fso, dir
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
shell.CurrentDirectory = dir
shell.Run Chr(34) & dir & "\scripts\run_app.bat" & Chr(34), 1, False
