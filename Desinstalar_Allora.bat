@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Allora - Desinstalador

cd /d "%~dp0"
set "APP_DIR=%~dp0"

echo.
echo  ================================================
echo    Allora - Desinstalador
echo.
echo    Este processo ira remover:
echo    - Todos os pacotes Python instalados pelo Allora
echo    - ffmpeg portatil (pasta tools\ffmpeg)
echo    - Poppler portatil (pasta tools\poppler)
echo    - Registro de instalacao (.allora_installed)
echo.
echo    O Python em si NAO sera removido.
echo    Nenhuma alteracao e feita no PATH do sistema, porque o
echo    Allora nunca escreve la em primeiro lugar.
echo  ================================================
echo.

set /p CONFIRM="Deseja continuar com a desinstalacao? (S/N): "
if /i not "%CONFIRM%"=="S" (
    echo.
    echo  Desinstalacao cancelada. Nenhuma alteracao foi feita.
    pause
    exit /b 0
)
echo.

REM ------------------------------------------------------------------
REM Allora.bat never requests administrator rights and never writes
REM to the system PATH - everything it installs lives inside this
REM project folder. Undoing it needs the same, ordinary user rights,
REM so there is no elevation step here either.
REM ------------------------------------------------------------------

echo [1/4] Removendo pacotes Python...
echo.
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo  [AVISO] Python nao encontrado no PATH - pulando remocao de pacotes.
) else (
    python -m pip uninstall -y ^
        yt-dlp ^
        PySide6 PySide6-Addons PySide6-Essentials shiboken6 ^
        requests ^
        opencv-python-headless ^
        numpy ^
        Pillow ^
        pdf2image ^
        pdfplumber ^
        pdf2docx ^
        python-docx ^
        reportlab ^
        docx2pdf ^
        pypdf 2>nul
    echo  [OK] Pacotes Python processados ^(os que nao estavam instalados foram ignorados^).
)
echo.

echo [2/4] Removendo ffmpeg...
if exist "%APP_DIR%tools\ffmpeg" (
    rd /s /q "%APP_DIR%tools\ffmpeg"
    echo  [OK] Pasta tools\ffmpeg removida.
) else (
    echo  [INFO] Pasta tools\ffmpeg nao encontrada ^(ja removida ou nunca baixada^).
)
echo.

echo [3/4] Removendo Poppler...
if exist "%APP_DIR%tools\poppler" (
    rd /s /q "%APP_DIR%tools\poppler"
    echo  [OK] Pasta tools\poppler removida.
) else (
    echo  [INFO] Pasta tools\poppler nao encontrada ^(ja removida ou nunca baixada^).
)

if exist "%APP_DIR%tools" (
    set "FILE_COUNT=0"
    for /f %%i in ('dir /b /a "%APP_DIR%tools" 2^>nul ^| find /c /v ""') do set "FILE_COUNT=%%i"
    if "!FILE_COUNT!"=="0" (
        rd /q "%APP_DIR%tools"
        echo  [OK] Pasta tools\ removida ^(estava vazia^).
    )
)
echo.

echo [4/4] Removendo registro de instalacao...
if exist "%APP_DIR%.allora_installed" (
    del /f /q "%APP_DIR%.allora_installed"
    echo  [OK] Arquivo .allora_installed removido.
) else (
    echo  [INFO] Arquivo de registro nao encontrado ^(ja removido^).
)
echo.

echo  ------------------------------------------------------------------
echo  Remocao opcional da pasta do programa
echo.
echo  Deseja remover TODA a pasta do Allora, incluindo o codigo-fonte
echo  e qualquer arquivo pessoal que voce tenha guardado dentro dela?
echo.
echo  ATENCAO: esta acao e IRREVERSIVEL.
echo  Responda N se quiser manter os arquivos do programa.
echo  ------------------------------------------------------------------
echo.
set /p DELETE_FOLDER="Remover a pasta completa do Allora? (S/N): "
if /i "%DELETE_FOLDER%"=="S" (
    echo.
    echo  A pasta sera removida em segundo plano assim que esta janela
    echo  fechar ^(nao e possivel apagar a pasta enquanto este script
    echo  ainda esta rodando dentro dela^).
    start "" /min cmd /c "timeout /t 2 /nobreak >nul & rd /s /q "%APP_DIR%""
) else (
    echo  [OK] Pasta do programa mantida.
)
echo.

echo  ================================================
echo    Desinstalacao concluida com sucesso!
echo.
echo    - Pacotes Python removidos
echo    - ffmpeg removido
echo    - Poppler removido
echo    - Registro de instalacao removido
echo.
echo    O Python em si permanece instalado.
echo    Seus arquivos pessoais fora desta pasta nao foram afetados.
echo  ================================================
echo.
pause
exit /b 0
