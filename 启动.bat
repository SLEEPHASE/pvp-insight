@echo off
chcp 65001 >nul
cd /d "%~dp0"
title PVP Insight 本地网页
python -m pvp_web
pause
