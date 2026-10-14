<#
.SYNOPSIS
  Add FixFlow as a sub-project inside C:\MshibeGroup\fixflow.

.USAGE
  powershell -ExecutionPolicy Bypass -File .\add_fixflow.ps1
  powershell -ExecutionPolicy Bypass -File .\add_fixflow.ps1 -DryRun
  powershell -ExecutionPolicy Bypass -File .\add_fixflow.ps1 -Port 5001
#>

param(
    [string]$Source   = "C:\FIXFLOW",
    [string]$Parent   = "C:\MshibeGroup",
    [string]$Subname  = "fixflow",
    [int]   $Port     = 5001,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Head($m) { Write-Host ""; Write-Host $m -ForegroundColor Cyan }
function OK  ($m) { Write-Host "  OK  $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  !!  $m" -ForegroundColor Yellow }
function Plan($m) { Write-Host "  >   $m" -ForegroundColor Yellow }

if (-not (Test-Path $Source)) { throw "FixFlow source not found: $Source" }
if (-not (Test-Path $Parent)) { throw "Parent project not found: $Parent" }

$Dest = Join-Path $Parent $Subname

Head "Adding FixFlow into $Dest"
Write-Host "  from : $Source"
Write-Host "  to   : $Dest"
Write-Host "  port : $Port"
if ($DryRun) { Warn "dry run - nothing will be written" }

# ---- 1) back up existing sub-folder if present ----
if (Test-Path $Dest) {
    $stamp  = Get-Date -Format "yyyyMMdd-HHmmss"
    $backup = "$Dest-backup-$stamp"
    Warn "$Dest already exists - backing up to $backup"
    if (-not $DryRun) {
        Copy-Item -Path $Dest -Destination $backup -Recurse -Force
    }
}

# ---- 2) create destination ----
if (-not (Test-Path $Dest)) {
    if (-not $DryRun) { New-Item -ItemType Directory -Path $Dest -Force | Out-Null }
    OK "created $Dest"
}

# ---- 3) copy FixFlow files, excluding junk ----
$skipDirs  = @('__pycache__', '.pytest_cache', 'venv', '.venv', 'env', '.git', '_old_files')
$skipFiles = @('*.pyc', '*.pyo', '*.log', 'Thumbs.db', '.DS_Store')

$copied = 0
$total  = 0

Head "Copying files"
Get-ChildItem -Path $Source -Recurse -Force | ForEach-Object {
    $rel = $_.FullName.Substring($Source.Length).TrimStart('\')

    $parts = $rel -split '\\'
    foreach ($p in $parts) {
        if ($skipDirs -contains $p) { return }
    }
    if (-not $_.PSIsContainer) {
        foreach ($pat in $skipFiles) {
            if ($_.Name -like $pat) { return }
        }
    }

    $target = Join-Path $Dest $rel

    if ($_.PSIsContainer) {
        if (-not (Test-Path $target)) {
            Plan "mkdir  $rel"
            if (-not $DryRun) { New-Item -ItemType Directory -Path $target -Force | Out-Null }
        }
    } else {
        $total++
        if (-not $DryRun) {
            Copy-Item -Path $_.FullName -Destination $target -Force
        }
        $copied++
    }
}
OK "copied $copied files"

# ---- 4) force FixFlow's app to listen on the given port ----
$fixflowApp = Join-Path $Dest "app.py"
if ((Test-Path $fixflowApp) -and (-not $DryRun)) {
    $content = Get-Content -Path $fixflowApp -Raw

    # find the last app.run(...) and rewrite its port
    $pattern = "app\.run\(([^)]*)\)"
    $updated = [regex]::Replace($content, $pattern, {
        param($m)
        $args = $m.Groups[1].Value
        if ($args -match 'port\s*=') {
            $args = [regex]::Replace($args, 'port\s*=\s*\d+', "port=$Port")
        } else {
            $args = $args.TrimEnd(',', ' ')
            $args = "$args, port=$Port"
        }
        "app.run($args)"
    })

    if ($updated -ne $content) {
        Set-Content -Path $fixflowApp -Value $updated -Encoding UTF8
        OK "patched app.py - FixFlow will listen on port $Port"
    } else {
        Warn "could not find app.run(...) in app.py - set the port manually"
    }
}

# ---- 5) write a launcher script inside the sub-folder ----
$launcher = Join-Path $Dest "run_fixflow.ps1"
$launcherContent = @"
# Launch FixFlow on port $Port
Set-Location `$PSScriptRoot
Write-Host "Starting FixFlow on http://127.0.0.1:$Port" -ForegroundColor Cyan
python app.py
"@
if (-not $DryRun) {
    Set-Content -Path $launcher -Value $launcherContent -Encoding ASCII
    OK "wrote run_fixflow.ps1 launcher"
}

# ---- 6) write a launcher at the parent level ----
$parentLauncher = Join-Path $Parent "run_all.ps1"
$parentContent = @"
# Launch both MshibeGroup and FixFlow in separate windows.

Write-Host "Starting MshibeGroup on http://127.0.0.1:5000" -ForegroundColor Cyan
Start-Process powershell -ArgumentList '-NoExit','-Command',"Set-Location '$Parent'; python app.py"

Start-Sleep -Seconds 2

Write-Host "Starting FixFlow on http://127.0.0.1:$Port" -ForegroundColor Cyan
Start-Process powershell -ArgumentList '-NoExit','-Command',"Set-Location '$Dest'; python app.py"

Write-Host ""
Write-Host "Both apps launching in separate windows." -ForegroundColor Green
"@
if (-not $DryRun) {
    Set-Content -Path $parentLauncher -Value $parentContent -Encoding ASCII
    OK "wrote run_all.ps1 at $Parent"
}

Head "Result"
OK "FixFlow added to $Dest"
OK "port set to $Port"
Write-Host ""
Write-Host "Structure:"
Write-Host "  $Parent\"
Write-Host "    app.py                  (MshibeGroup)"
Write-Host "    ai_receptionist.py"
Write-Host "    voice.html"
Write-Host "    ..."
Write-Host "    run_all.ps1             (launches both apps)"
Write-Host "    $Subname\"
Write-Host "      app.py                (FixFlow, port $Port)"
Write-Host "      seed_demo.py"
Write-Host "      run_fixflow.ps1"
Write-Host "      templates\"
Write-Host "      static\"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  cd $Dest"
Write-Host "  pip install -r requirements.txt"
Write-Host "  python seed_demo.py       (only if you want demo data)"
Write-Host "  python app.py"
Write-Host ""
Write-Host "Or launch both at once:"
Write-Host "  cd $Parent"
Write-Host "  .\run_all.ps1"