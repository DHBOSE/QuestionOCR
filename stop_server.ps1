$procs = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -match 'api\.py' }
if ($procs) {
    foreach ($p in $procs) {
        Stop-Process -Id $p.ProcessId -Force
        Write-Host "已停止服务进程 PID $($p.ProcessId)"
    }
} else {
    Write-Host "未发现正在运行的服务。"
}
