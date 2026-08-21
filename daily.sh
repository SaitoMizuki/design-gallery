#!/bin/sh
# Design gallery 日次更新: 取得→ビルド→Netlify公開
# Claude のセッションに依存せず単独で完結する
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
D=/Users/apple/gallery-digest
L="$D/logs"
mkdir -p "$L"
LOG="$L/$(date +%Y-%m-%d).log"
{
  echo "===== START $(date '+%Y-%m-%d %H:%M:%S') ====="
  python3 "$D/build.py" || { echo "!! build 失敗 rc=$?"; exit 1; }
  echo "----- deploy -----"
  sh "$D/publish.sh" || { echo "!! deploy 失敗 rc=$?"; exit 1; }
  echo "===== DONE  $(date '+%Y-%m-%d %H:%M:%S') ====="
} >> "$LOG" 2>&1
