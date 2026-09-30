#!/bin/bash
# 一键启动「多模型圆桌讨论」——双击即可运行，无需在终端输入命令。
# 启动后本终端窗口可安全关闭，程序会继续在后台运行。

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$PROJECT_DIR/app.py" ]; then
  echo "没找到 app.py，请把本脚本放在项目目录里再运行。"
  read -r -p "按回车键退出…"
  exit 1
fi

cd "$PROJECT_DIR"

# 首次运行：创建虚拟环境并安装依赖
if [ ! -x "venv/bin/streamlit" ]; then
  echo "=============================================="
  echo "  首次运行：正在创建环境并安装依赖（约 1~2 分钟）"
  echo "=============================================="
  python3 -m venv venv
  ./venv/bin/pip install --upgrade pip >/dev/null 2>&1
  ./venv/bin/pip install -r requirements.txt
  echo "依赖安装完成。"
fi

# 后台启动 Streamlit（脱离本终端，关闭窗口不影响运行）
LOG="$PROJECT_DIR/.streamlit_run.log"
nohup ./venv/bin/streamlit run app.py >"$LOG" 2>&1 &

# 等它起来后自动打开浏览器
sleep 3
open "http://localhost:8501"

echo ""
echo "✅ 已启动，浏览器将自动打开。"
echo "   你现在可以安全关闭这个终端窗口了，程序会在后台继续运行。"
echo "   如需停止：运行项目里的 stop_roundtable.command，或执行：lsof -ti :8501 | xargs kill"
