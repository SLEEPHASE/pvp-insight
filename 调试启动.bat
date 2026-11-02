@echo off
chcp 65001 >nul
cd /d "%~dp0"
title PVP Insight 调试
python -m pvp_web --debug --no-browser
pause
