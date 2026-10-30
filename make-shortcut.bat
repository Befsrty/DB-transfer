@echo off
rem Создать на рабочем столе ярлык «DB-transfer», который запускает start-ui.bat.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop') + '\DB-transfer.lnk');" ^
  "$s.TargetPath = '%~dp0start-ui.bat'; $s.WorkingDirectory = '%~dp0'; $s.Description = 'DB-transfer UI'; $s.Save()"
if errorlevel 1 (
    echo Ярлык создать не удалось.
) else (
    echo Ярлык DB-transfer появился на рабочем столе.
)
pause
