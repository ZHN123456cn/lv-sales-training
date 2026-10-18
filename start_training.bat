@echo off
chcp 65001 >nul
echo ========================================
echo   LV 销售训练系统 - 一键启动
echo ========================================
echo.

cd /d "%~dp0project_class_all_ragplus"

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 虚拟环境不存在，请先运行：
    echo   cd project_class_all_ragplus
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo [启动] 训练编排器...
echo.
".venv\Scripts\python.exe" "scripts\run_training.py"

pause
