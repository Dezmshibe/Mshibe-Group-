# Launch FixFlow on port 5001
Set-Location $PSScriptRoot
Write-Host "Starting FixFlow on http://127.0.0.1:5001" -ForegroundColor Cyan
python app.py
