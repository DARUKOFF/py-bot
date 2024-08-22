@echo off
call %~dp0MUIV_support_bot\venv\Scripts\activate
cd %~dp0MUIV_support_bot
python main.py
pause