#!/bin/sh
# Design gallery 日次ビルド（公開は GitHub Actions が担当）
# Claude のセッションに依存せず単独で完結する
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
D=/Users/apple/gallery-digest
L="$D/logs"
mkdir -p "$L"
LOG="$L/$(date +%Y-%m-%d).log"
{
  echo "===== START $(date '+%Y-%m-%d %H:%M:%S') ====="
  python3 "$D/build.py" || { echo "!! build 失敗 rc=$?"; exit 1; }
  # Netlify へのデプロイはここでは行わない。
  # 公開は GitHub Actions → GitHub Pages が担当する（デプロイ無制限）。
  # Netlify は本番デプロイ1回で15クレジット消費し、無料枠は月300（=20回）しかない。
  echo "===== DONE  $(date '+%Y-%m-%d %H:%M:%S') ====="
} >> "$LOG" 2>&1
