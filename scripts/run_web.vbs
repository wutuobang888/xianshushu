' 隐藏窗口启动 Flask（无黑色控制台，日志写 logs\app.log）
Dim shell, fso, dir
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
If Not fso.FolderExists(dir & "\logs") Then fso.CreateFolder(dir & "\logs")
shell.CurrentDirectory = dir
shell.Run "cmd /c python app.py >> logs\app.log 2>&1", 0, False
