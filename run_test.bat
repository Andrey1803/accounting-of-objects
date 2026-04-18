@echo off
chcp 65001 >nul
echo Starting server...
start /B python app_objects.py > server_output.log 2>&1
timeout /t 3 >nul
echo Running test...
python test_add_worker_detailed.py > test_output.log 2>&1
echo Done! Check server_output.log for server errors.
type server_output.log | findstr /C:"ERROR" /C:"Traceback" /C:"File" /C:"error"
