@echo off
cd /d "%~dp0"
rem Runs this checkout from source like the packaged exe: live INTent bridge, floating panel and in-client HUD,
rem every queue except ARAM/Arena. Team comps (Cian's Discord choices) arrive through the same team room as the
rem installed app when TEAM_SYNC points at its team-sync.json.
set INTENT_LIVE=1
if "%TEAM_SYNC%"=="" set TEAM_SYNC=%USERPROFILE%\Downloads\FlexersDraftCoach (3)\FlexersDraftCoach\team-sync.json
set SYNCARGS=
if exist "%TEAM_SYNC%" (
  for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "$j = Get-Content -Raw '%TEAM_SYNC%' | ConvertFrom-Json; '--cloud-sync-mode auto --cloud-sync-url ' + $j.relay + ' --cloud-sync-key ' + $j.room_key"`) do set SYNCARGS=%%A
)
echo Flexers Draft Coach (source): coaching every queue except ARAM. Coach page: http://127.0.0.1:8765/
python run.py --bridge --window --hud --no-update --load "loadfiles\flexers-team.json" %SYNCARGS%
pause
