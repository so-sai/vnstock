@echo off
setlocal
REM ============================================================================
REM run_eod_flowmap.cmd — chuỗi "eod-run -> flow-map" dùng cho Task Scheduler.
REM
REM [WHY]: schtasks /Create /TR tu choi chuoi dai hon 261 ky tu (do that
REM   2026-11-11: lenh noi truc tiep dai 314 ky tu sau khi goc du an chuyen sang
REM   "D:\04. Software\..." dai hon goc E: cu). Task khong duoc tao/cap nhat,
REM   im lang giu nguyen lenh tro ve checkout cu => vi pham "Missing > Fake".
REM [ROOT CAUSE]: hai lenh Python bi noi bang "&&" trong mot chuoi /TR duy nhat.
REM [INVARIANT]: flow-map CHI chay khi eod-run thanh cong (giu dung ngu nghia
REM   "&&" cu). Thieu interpreter => dung ngay (exit 2), khong doan, khong lay
REM   python tu PATH (tranh chay sai bo phu thuoc).
REM ============================================================================
set "PYTHON=%~1"
if "%PYTHON%"=="" (
    echo [EOD] Thieu tham so interpreter: run_eod_flowmap.cmd "<python.exe>"
    exit /b 2
)

set "PTCK=%~dp0..\..\ptck.py"

"%PYTHON%" "%PTCK%" eod-run
if errorlevel 1 (
    echo [EOD] eod-run that bai - khong chay flow-map.
    exit /b 1
)

"%PYTHON%" "%PTCK%" flow-map
exit /b %ERRORLEVEL%
