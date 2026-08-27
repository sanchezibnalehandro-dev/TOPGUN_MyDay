@echo off
setlocal
set "PROJECT_DIR=%~dp0"
set "LOCAL_PYTHON=%LocalAppData%\Programs\Python\Python312\python.exe"

if exist "%LOCAL_PYTHON%" (
  "%LOCAL_PYTHON%" "%PROJECT_DIR%app.py"
  goto :end
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 "%PROJECT_DIR%app.py"
  goto :end
)

where python >nul 2>nul
if not errorlevel 1 (
  python "%PROJECT_DIR%app.py"
  goto :end
)

echo Python 3.12 ne naiden.
echo Ustanovite Python 3.12 s Tcl/Tk i povtorite zapusk.
pause

:end
endlocal
