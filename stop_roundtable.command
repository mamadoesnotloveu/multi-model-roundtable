#!/bin/bash
# 停止「多模型圆桌讨论」后台程序（关闭 8501 端口上的进程）。

PIDS=$(lsof -ti :8501 2>/dev/null)
if [ -z "$PIDS" ]; then
  echo "程序没有在运行（8501 端口空闲）。"
else
  echo "$PIDS" | xargs kill
  echo "已停止后台程序。"
fi
read -r -p "按回车键退出…"
