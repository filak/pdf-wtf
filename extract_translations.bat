@echo off
setlocal
echo.
echo Checking Python
echo.
py --version 2>NUL
if errorlevel 1 goto errorNoPython

echo.
choice /C AN /M "Activate Python VENV ?  A/N"
if errorlevel 2 goto:start

echo.
echo Activating VENV
call .venv\Scripts\activate

:start
echo.
call pybabel extract -F babel.cfg -o messages.pot .
if errorlevel 1 goto:errorExtract
echo.
echo Finished - use Poedit
echo.
echo Press any key ...
pause >nul
goto:finished

:errorExtract
echo Translation extraction failed.
endlocal
exit /b 1

:errorNoPython
echo.
echo Python NOT installed !
echo.
goto:eof

:finished

endlocal
