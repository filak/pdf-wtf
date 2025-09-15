@echo off
setlocal
python "%PDFWTF_HOME_DIR%\src\tools\unpaper_wrap.py" %*
REM When compiled replace with:
rem "%PDFWTF_HOME_DIR%\dist\tools\unpaper_wrap.exe" %*
endlocal
