@echo off
chcp 65001 >nul
title Cazacodigos
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  if exist "%USERPROFILE%\.local\bin\uv.exe" (
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
  ) else (
    echo Instalando uv ^(solo la primera vez^)...
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
  )
)

echo La primera vez tarda un par de minutos en bajar todo.
echo.
rem Cambia las opciones de esta linea a gusto. Ver README.md
uv run cazar.py %*

echo.
pause
