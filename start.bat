@echo off
set PYTHONDONTWRITEBYTECODE=1
for /d /r %%i in (__pycache__) do if exist "%%i" rd /s /q "%%i"
py -m uvicorn app:app --reload --port 8000
