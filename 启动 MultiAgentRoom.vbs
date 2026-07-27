' 源码启动 Web UI（浏览器）；旧 tk 界面请用: python -m multi_agent_room --tk
Option Explicit
Dim sh, fso, root
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = root
sh.Environment("PROCESS")("PYTHONPATH") = root & "\src"

On Error Resume Next
Err.Clear
sh.Run "python -m multi_agent_room", 1, False
If Err.Number = 0 Then WScript.Quit 0
Err.Clear
sh.Run "pythonw -m multi_agent_room", 0, False
If Err.Number = 0 Then WScript.Quit 0

MsgBox "启动失败：请安装 Python 3.11+，然后在项目目录执行：" & vbCrLf & _
  "$env:PYTHONPATH=.\src; python -m multi_agent_room", 16, "MultiAgentRoom"
