@echo off
setlocal
python "%PDFWTF_HOME%\src\tools\unpaper_wrap.py" %*
REM When compiled replace with:
rem "%PDFWTF_HOME%\dist\tools\unpaper_wrap.exe" %*
endlocal & exit /b %ERRORLEVEL%
