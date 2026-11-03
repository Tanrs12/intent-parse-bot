@echo off
cd /d "%~dp0"
rem Runs this checkout from source: the app ranks drafts itself (Discord drafts optional, read from the team
rem room as a follower that never publishes), and the floating panel,
rem every queue except ARAM/Arena. Team comps (Cian's Discord choices) arrive through the same team room as the
rem installed app when TEAM_SYNC points at its team-sync.json.
if "%TEAM_SYNC%"=="" set TEAM_SYNC=%USERPROFILE%\Downloads\FlexersDraftCoach (3)\FlexersDraftCoach\team-sync.json
set SYNCARGS=
if exist "%TEAM_SYNC%" (
  for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "$j = Get-Content -Raw '%TEAM_SYNC%' | ConvertFrom-Json; '--cloud-sync-mode follow --cloud-sync-url ' + $j.relay + ' --cloud-sync-key ' + $j.room_key"`) do set SYNCARGS=%%A
)
echo Flexers Draft Coach (source): coaching every queue except ARAM. Coach page: http://127.0.0.1:8765/
python run.py --bridge --window --no-update --load "loadfiles\flexers-team.json" %SYNCARGS%
pause
