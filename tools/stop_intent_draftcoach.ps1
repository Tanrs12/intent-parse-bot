$ErrorActionPreference = 'SilentlyContinue'
$repo = Split-Path -Parent $PSScriptRoot
$repoPattern = [regex]::Escape($repo)
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

