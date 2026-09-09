#!/bin/bash
# 一键启动「多模型圆桌讨论」——双击即可运行，无需在终端输入命令。
# 以脚本所在目录作为项目目录（脚本放在项目里双击即可；
# 如需桌面快捷方式，可右键本脚本 -> 制作替身，把替身放到桌面）。

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

echo "正在启动，浏览器将自动打开…"
echo "（关闭本窗口即可退出程序）"
# 等 Streamlit 起来后再打开浏览器
( sleep 3; open "http://localhost:8501" ) &
./venv/bin/streamlit run app.py
