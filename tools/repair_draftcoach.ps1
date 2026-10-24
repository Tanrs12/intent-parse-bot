param(
    [string]$Target = $PSScriptRoot,
    [string]$ArchivePath = '',
    [string]$ExpectedCommit = ''
)

# Independent recovery path when an older EXE cannot update itself.
# Intended to run only while FlexersDraftCoach.exe is closed.
$ErrorActionPreference = 'Stop'
$Target = [IO.Path]::GetFullPath($Target)
$Var = Join-Path $Target 'var'
$Log = Join-Path $Var 'updater.log'
$Failure = Join-Path $Var 'update-failed.json'
$Work = $null

try {
    New-Item -ItemType Directory -Force -Path $Var | Out-Null
    if (Get-Process -Name 'FlexersDraftCoach' -ErrorAction SilentlyContinue) {
        throw 'Close Flexers Draft Coach before running the repair.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Target 'FlexersDraftCoach.exe'))) {
        throw 'Repair must run from the installed Draft Coach folder.'
    }

    $Archive = $ArchivePath
    if ($ArchivePath) {
        if (-not $ExpectedCommit -or $ExpectedCommit -notmatch '^[0-9a-fA-F]{40}$') {
            throw 'Offline archive repair needs a 40-character expected build commit.'
        }
    }
    else {
        $Release = Invoke-RestMethod -Uri 'https://api.github.com/repos/Tanrs12/intent-parse-bot/releases/latest' -TimeoutSec 20 -Headers @{
            'User-Agent' = 'FlexersDraftCoach-Repair/1'
            'Accept' = 'application/vnd.github+json'
        }
        $Body = [string]$Release.body
        if ($Body -notmatch 'Built from\s+([0-9a-fA-F]{40})') {
            throw 'Latest release does not identify its build commit.'
        }
        $ExpectedCommit = $Matches[1].ToLowerInvariant()
        $Asset = $Release.assets | Where-Object { $_.name -eq 'FlexersDraftCoach.zip' } | Select-Object -First 1
        if (-not $Asset -or -not $Asset.browser_download_url) {
            throw 'Latest release is missing FlexersDraftCoach.zip.'
        }
    }
    $ExpectedCommit = $ExpectedCommit.ToLowerInvariant()
    $Work = Join-Path $env:TEMP ('flexers-draft-repair-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $Work | Out-Null
    if (-not $ArchivePath) {
        $Archive = Join-Path $Work 'FlexersDraftCoach.zip'
        Write-Output 'Downloading the latest published Draft Coach build...'
        Invoke-WebRequest -UseBasicParsing -Uri $Asset.browser_download_url -TimeoutSec 120 -OutFile $Archive
    }
    if (-not (Test-Path -LiteralPath $Archive -PathType Leaf)) {
        throw 'The release ZIP is missing.'
    }
    $Extract = Join-Path $Work 'unpacked'
    Expand-Archive -LiteralPath $Archive -DestinationPath $Extract -Force
    $Source = Join-Path $Extract 'FlexersDraftCoach'
    if (-not (Test-Path -LiteralPath (Join-Path $Source 'FlexersDraftCoach.exe'))) {
        throw 'Downloaded package contains no executable.'
    }
    $Info = Get-Content -LiteralPath (Join-Path $Source 'build-info.json') -Raw | ConvertFrom-Json
    if ([string]$Info.commit -ne $ExpectedCommit) {
        throw ('Release/package commit mismatch: expected ' + $ExpectedCommit + ', got ' + $Info.commit)
    }

    # Never replace the running EXE or overwrite player-specific state.
    # /IS /IT also fixes releases whose timestamps match despite differing content.
    & robocopy.exe $Source $Target /E /IS /IT /XD var /XF config.json team-sync.json /R:6 /W:2 /NFL /NDL /NP "/LOG+:$Log" | Out-Null
    $Code = $LASTEXITCODE
    if ($Code -ge 8) {
        throw ('Could not replace installed files (robocopy ' + $Code + ').')
    }
    $Installed = Get-Content -LiteralPath (Join-Path $Target 'build-info.json') -Raw | ConvertFrom-Json
    if ([string]$Installed.commit -ne $ExpectedCommit) {
        throw ('Installed build verification failed: ' + $Installed.commit)
    }
    Remove-Item -LiteralPath $Failure -Force -ErrorAction SilentlyContinue
    Add-Content -LiteralPath $Log -Value ('Independent update repair verified ' + $ExpectedCommit + ' at ' + (Get-Date -Format o))
    Write-Output ('Repair complete. Installed build: ' + $ExpectedCommit.Substring(0, 7))
}
catch {
    $Message = $_.Exception.Message
    Add-Content -LiteralPath $Log -Value ('Independent update repair FAILED at ' + (Get-Date -Format o) + ': ' + $Message) -ErrorAction SilentlyContinue
    [Console]::Error.WriteLine('Draft Coach repair failed: ' + $Message)
    exit 1
}
finally {
    if ($Work -and (Test-Path -LiteralPath $Work)) {
        Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue
    }
}
