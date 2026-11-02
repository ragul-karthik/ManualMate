@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Creating a local Python environment...
  py -3 -m venv .venv
  if errorlevel 1 goto failed
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
if errorlevel 1 goto failed
python -m pip install -r requirements.txt
if errorlevel 1 goto failed
python -m streamlit run app.py
goto end
:failed
echo.
echo Setup did not complete. Install Python 3.10 or newer and try again.
pause
:end
