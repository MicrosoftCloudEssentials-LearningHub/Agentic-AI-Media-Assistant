# Stop A2A Automation Framework
Write-Host "Stopping A2A Automation Framework..."
Get-Process -Name "python" | Where-Object { .CommandLine -like "*automated_main*" } | Stop-Process -Force
Write-Host "A2A Automation Framework stopped"
