# Start A2A Automation Framework
 = 
Set-Location 

Write-Host "Starting A2A Automation Framework..."
# Start in a new window so it persists after the script exits
Start-Process python -ArgumentList "automated_main.py" -WorkingDirectory 
Write-Host "A2A Automation Framework launched in a new window."
