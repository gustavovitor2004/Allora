@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Allora

cd /d "%~dp0"

REM ------------------------------------------------------------------
REM 1) Python must already be installed - we never download Python itself.
REM ------------------------------------------------------------------
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERRO] Python não encontrado.
    echo Baixe em: https://www.python.org/downloads/
    echo IMPORTANTE: marque "Add python.exe to PATH" durante a instalação.
    start "" "https://www.python.org/downloads/"
    pause
    exit /b 1
)

REM ------------------------------------------------------------------
REM 2) Marker file - if present, every dependency already installed
REM    successfully on a previous run. Skip straight to launching.
REM ------------------------------------------------------------------
set "MARKER=%~dp0.allora_installed"
if exist "%MARKER%" goto :launch

REM ------------------------------------------------------------------
REM 3) First run: install everything. No administrator rights needed -
REM    pip installs into the current user's own packages (it falls back
REM    to that automatically when the system site-packages isn't
REM    writable), and ffmpeg/Poppler are downloaded as portable binaries
REM    into tools/, never touching the system PATH. The app itself already
REM    knows to look inside tools/ as a fallback location.
REM ------------------------------------------------------------------
echo ================================================
echo   Allora - Primeira inicialização
echo   Instalando dependências necessárias...
echo   (isso acontece só uma vez - pode levar alguns minutos)
echo ================================================
echo.

echo [1/4] Atualizando pip...
python -m pip install --upgrade pip --quiet
if %errorlevel% neq 0 (
    echo.
    echo [ERRO] Não foi possível atualizar o pip. Verifique sua conexão
    echo com a internet e rode Allora.bat novamente.
    pause
    exit /b 1
)

echo [2/4] Instalando pacotes Python...
python -m pip install -r "%~dp0requirements.txt" --quiet
if %errorlevel% neq 0 (
    echo.
    echo [ERRO] Falha ao instalar pacotes Python. Verifique sua conexão
    echo com a internet e rode Allora.bat novamente.
    pause
    exit /b 1
)

echo [3/4] Verificando ffmpeg...
where ffmpeg >nul 2>nul
if %errorlevel% equ 0 (
    echo       ffmpeg já está disponível no PATH.
) else if exist "%~dp0tools\ffmpeg\ffmpeg.exe" (
    echo       ffmpeg já está instalado em tools\ffmpeg.
) else (
    echo       ffmpeg não encontrado - baixando versão portátil...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools_installer.ps1" -Component ffmpeg -ToolsDir "%~dp0tools"
    if !errorlevel! neq 0 (
        echo.
        echo [ERRO] Falha ao baixar o ffmpeg. Verifique sua conexão com a
        echo internet e rode Allora.bat novamente.
        pause
        exit /b 1
    )
    echo       ffmpeg instalado em tools\ffmpeg.
)

echo [4/4] Verificando Poppler...
where pdftoppm >nul 2>nul
if %errorlevel% equ 0 (
    echo       Poppler já está disponível no PATH.
) else if exist "%~dp0tools\poppler\pdftoppm.exe" (
    echo       Poppler já está instalado em tools\poppler.
) else (
    echo       Poppler não encontrado - baixando versão portátil...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools_installer.ps1" -Component poppler -ToolsDir "%~dp0tools"
    if !errorlevel! neq 0 (
        echo.
        echo [ERRO] Falha ao baixar o Poppler. Verifique sua conexão com a
        echo internet e rode Allora.bat novamente.
        pause
        exit /b 1
    )
    echo       Poppler instalado em tools\poppler.
)

REM Every step above succeeded - only now is it safe to mark the install
REM as done, so a partial/failed run always gets retried from scratch.
echo. > "%MARKER%"
echo.
echo ================================================
echo   Instalação concluída com sucesso!
echo   Iniciando Allora...
echo ================================================
timeout /t 2 /nobreak >nul
goto :launch

REM ------------------------------------------------------------------
REM 4) Launch
REM ------------------------------------------------------------------
:launch
cd /d "%~dp0"
python src\main.py
if %errorlevel% neq 0 (
    echo.
    echo [ERRO] O Allora encerrou com erro. Código: %errorlevel%
    pause
)
