$ErrorActionPreference = 'SilentlyContinue'
$repo = Split-Path -Parent $PSScriptRoot
$repoPattern = [regex]::Escape($repo)
$serverPy = Join-Path $repo '.draftcoach-venv\Scripts\python.exe'
$helperPy = Join-Path $repo '.draftcoach-venv\Scripts\pythonw.exe'
if (-not (Test-Path $serverPy) -or -not (Test-Path $helperPy)) { exit 2 }

# One copy of each helper only. Rat's packaged EXE is not used here.
Get-Process FlexersDraftCoach | Stop-Process -Force
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -match $repoPattern -and (
        $_.CommandLine -match 'run\.py' -or
        $_.CommandLine -match 'intent_live_draft_bridge.py' -or
        $_.CommandLine -match 'intent_comp_overlay.py' -or
        $_.CommandLine -match 'intent_hud_proxy.py' -or
        $_.CommandLine -match 'intent_action_hud.py'
    )
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep -Milliseconds 500

$env:INTENT_LIVE = '1'
Start-Process -FilePath $serverPy -ArgumentList @(
    (Join-Path $repo 'run.py'), '--load', (Join-Path $repo 'loadfiles\flexers-team.json'), '--log-level', 'warning'
) -WorkingDirectory $repo -WindowStyle Hidden

# Do not launch polling overlays until the source server is actually listening.
$ready = $false
for ($i = 0; $i -lt 120; $i++) {
    try {
        Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8765/api/config' -TimeoutSec 1 | Out-Null
        $ready = $true
        break
    } catch {}
    Start-Sleep -Milliseconds 250
}
if (-not $ready) { exit 1 }

Start-Process -FilePath $helperPy -ArgumentList @((Join-Path $repo 'tools\intent_live_draft_bridge.py'), '--port', '8766') -WorkingDirectory $repo -WindowStyle Hidden
for ($i = 0; $i -lt 40; $i++) {
    try {
        Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8766/api/guidance' -TimeoutSec 1 | Out-Null
        break
    } catch {}
    Start-Sleep -Milliseconds 200
}
Start-Process -FilePath $helperPy -ArgumentList @((Join-Path $repo 'tools\intent_hud_proxy.py')) -WorkingDirectory $repo -WindowStyle Hidden
Start-Process -FilePath $helperPy -ArgumentList @((Join-Path $repo 'tools\intent_action_hud.py')) -WorkingDirectory $repo -WindowStyle Hidden
Start-Process -FilePath $helperPy -ArgumentList @((Join-Path $repo 'tools\intent_comp_overlay.py')) -WorkingDirectory $repo -WindowStyle Hidden

