@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/3] 安装构建依赖...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :err
echo [2/3] 打包 Windows 免安装版...
python -m PyInstaller --noconfirm --clean --onedir --console ^
  --name "PVP-Insight" ^
  --icon "assets\icon.ico" ^
  --add-data "pvp_web\static;pvp_web\static" ^
  --add-data "assets;assets" ^
  --hidden-import pvp_web.demo ^
  run_web.py
if errorlevel 1 goto :err
echo [3/3] 复制使用说明...
copy /Y "使用说明.txt" "dist\PVP-Insight\使用说明.txt" >nul
echo.
echo 完成：dist\PVP-Insight\PVP-Insight.exe
echo 把整个 dist\PVP-Insight 文件夹压缩后发给别人即可。
pause
exit /b 0
:err
echo.
echo 构建失败，请检查上方错误。
pause
exit /b 1
