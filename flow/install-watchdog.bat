@echo off
rem Registra o watchdog do Flow no Agendador de Tarefas: roda a cada 2 min na
rem sessao do usuario logado (precisa de sessao interativa p/ bandeja e hotkey),
rem via pythonw = sem janela piscando.
rem   remover:      schtasks /Delete /TN Flow-Watchdog /F
rem   rodar agora:  schtasks /Run /TN Flow-Watchdog
setlocal
set "PYW="
for /f "delims=" %%i in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%i"
if not defined PYW (
  echo [install-watchdog] pythonw nao encontrado no PATH.
  exit /b 1
)
schtasks /Create /TN "Flow-Watchdog" /TR "\"%PYW%\" \"%~dp0watchdog.py\"" /SC MINUTE /MO 2 /F
if errorlevel 1 exit /b 1

rem O schtasks nasce com "nao rodar na bateria" e sem limite de execucao — o
rem watchdog precisa rodar sempre e nunca acumular instancias penduradas.
powershell -NoProfile -Command "Set-ScheduledTask -TaskName Flow-Watchdog -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)) | Out-Null"
