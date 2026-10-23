Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
bat = root & "\启动.bat"

Function ServiceRunning()
    On Error Resume Next
    Dim http
    Set http = CreateObject("MSXML2.XMLHTTP")
    http.Open "GET", "http://127.0.0.1:8000/health", False
    http.Send
    ServiceRunning = (Err.Number = 0 And http.Status = 200)
    Err.Clear
    On Error GoTo 0
End Function

' 服务未运行时才启动（隐藏窗口，无黑框）
If Not ServiceRunning() Then
    sh.Run """" & bat & """ hidden", 0, False
    ' 等待服务就绪，最多约 30 秒
    Dim i
    For i = 1 To 30
        WScript.Sleep 1000
        If ServiceRunning() Then Exit For
    Next
End If

' 打开浏览器页面（无论本次是否新启动，都给用户可见反馈）
sh.Run "http://localhost:8000", 1, False
