@echo off
chcp 65001 >nul
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 run_pipeline.py
) else (
  python run_pipeline.py
)

set RC=%errorlevel%
echo.
if %RC%==0 (
  echo [成功] 管道全部步骤通过
) else (
  echo [失败] 退出码 %RC%，请查看上方输出与 logs\ 目录
)
echo.
pause
