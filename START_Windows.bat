@echo off
rem First run creates a private Python environment in your home folder (%USERPROFILE%\.vaider\envs\VAIDER-mask) and installs pydicom + numpy.
set ENV=%USERPROFILE%\.vaider\envs\VAIDER-mask
cd /d "%~dp0scripts"
if not exist "%ENV%\Scripts\python.exe" (
  echo First run: setting up environment, needs internet once...
  python -m venv "%ENV%" || (echo Could not create environment. Is Python 3 installed? & pause & exit /b 1)
  "%ENV%\Scripts\python.exe" -m pip install --quiet pydicom numpy || (echo Install failed. & rmdir /s /q "%ENV%" & pause & exit /b 1)
)
"%ENV%\Scripts\python.exe" server.py
pause
