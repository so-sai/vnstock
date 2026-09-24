@echo off
REM PTCK_SBV_FIXTURE wrapper - run SBV fixture snapshot daily.
REM Avoids schtasks /TR nested-quote issues by pointing at this .bat.
"C:\Python314\python.exe" "D:\04. Software\opensource_contrib\PTCK_VNSTOCK\ptck.py" sbv-update --save-fixture
exit /b %ERRORLEVEL%
