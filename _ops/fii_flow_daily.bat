@echo off
REM Daily FII/DII + option-chain PCR collector (NSE EOD). Run after ~19:15 IST.
REM Registered as Windows Task "KHAZANA_FII_Flow_Daily".
cd /d "%~dp0"
set PY="C:\Program Files\Python314\python.exe"
echo ==== %date% %time% ==== >> "%~dp0fii_flow_daily_run.log"
%PY% "%~dp0fii_flow.py" --daily   >> "%~dp0fii_flow_daily_run.log" 2>&1
%PY% "%~dp0chain_pcr.py" --daily  >> "%~dp0fii_flow_daily_run.log" 2>&1
echo. >> "%~dp0fii_flow_daily_run.log"
