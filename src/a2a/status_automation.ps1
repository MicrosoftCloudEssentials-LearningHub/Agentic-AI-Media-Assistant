# Check A2A Automation Framework Status
Write-Host "Checking A2A Automation Framework status..."
 = Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object { .CommandLine -like "*automated_main*" }
if () {
  Write-Host "A2A Automation Framework is RUNNING"
  Write-Host "Processes: 0"
   | Format-Table Id,ProcessName,StartTime
} else {
  Write-Host "A2A Automation Framework is STOPPED"
}

# Check automation endpoint
try {
   = Invoke-RestMethod -Uri "https://zava-507f5a99-app.azurewebsites.net/a2a/automation/status" -TimeoutSec 5
  Write-Host "Automation Status: "
} catch {
  Write-Host "Automation endpoint not accessible"
}
