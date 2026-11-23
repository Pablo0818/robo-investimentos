#!/usr/bin/env bash
# Inicia o robô no Linux/Mac sem Docker
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; }
[ -f .env ] || cp .env.example .env
exec .venv/bin/python main.py
