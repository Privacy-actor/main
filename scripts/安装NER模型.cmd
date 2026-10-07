@echo off
setlocal
cd /d "%~dp0.."
title Moyin NER model setup

if not exist "backend\.venv\Scripts\python.exe" (
  echo The backend environment backend\.venv was not found.
  echo Run the Moyin start script in the project folder once first, then run this file again.
  pause
  exit /b 1
)

echo This installs PyTorch and Transformers into backend\.venv,
echo downloads the multilingual NER model (about 1.1 GB) and turns NER on in backend\.env.
echo It can take a while. You can leave the Moyin windows open.
echo.
backend\.venv\Scripts\python.exe scripts\setup_ner.py
if errorlevel 1 (
  echo.
  echo Setup did not finish. See the messages above, then run this file again.
  pause
  exit /b 1
)
echo.
echo Done. Close the "Moyin backend 8000" window and run the Moyin start script again,
echo then open the deploy page in Moyin: the NER layer shows the model name.
pause
