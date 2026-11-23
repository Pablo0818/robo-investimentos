@echo off
REM Inicia o robô no Windows (necessário para B3 via MetaTrader 5)
cd /d %~dp0
if not exist .venv (
  python -m venv .venv
  call .venv\Scripts\activate
  pip install -r requirements-windows-mt5.txt
) else (
  call .venv\Scripts\activate
)
if not exist .env copy .env.example .env
python main.py
pause
