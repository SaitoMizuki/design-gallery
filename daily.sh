#!/bin/sh
# GitHub のスケジュール実行は遅延・不発が多いため、Mac が起動している日は
# ここから定刻に更新を促す。trigger.txt を1行書き換えて push すると、
# push イベントでワークフローが即座に走る（schedule と違い遅延しない）。
# Mac が落ちていても GitHub 側の cron が保険として残る。
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
D=/Users/apple/gallery-digest
L="$D/logs"
mkdir -p "$L"
LOG="$L/$(date +%Y-%m-%d).log"
{
  echo "===== START $(date '+%Y-%m-%d %H:%M:%S') ====="
  cd "$D" || exit 1

  # 作業中の変更があるときは触らない。勝手に巻き込んで壊さないため。
  if [ -n "$(git status --porcelain -- . ':!trigger.txt')" ]; then
    echo "!! 未コミットの変更があるため中止"
    git status --short | head
    exit 1
  fi

  git pull --rebase --quiet || { echo "!! pull 失敗"; exit 1; }

  # 既に本日分が終わっていれば何もしない
  if [ -f last-run.txt ] && [ "$(cut -c1-10 last-run.txt)" = "$(date +%F)" ]; then
    echo "本日分は更新済み（$(cat last-run.txt)）。何もしません。"
    echo "===== DONE  $(date '+%Y-%m-%d %H:%M:%S') ====="
    exit 0
  fi

  date '+%Y-%m-%dT%H:%M:%S%z' > trigger.txt
  git add trigger.txt
  git -c user.name="Mizuki Saito" -c user.email="tackerdomingo@gmail.com" \
      commit -q -m "chore: 定刻トリガー $(date +%F)" || { echo "変更なし"; exit 0; }
  git push --quiet || { echo "!! push 失敗"; exit 1; }
  echo "トリガーを送信。GitHub Actions が取得・ビルド・公開を行います。"
  echo "===== DONE  $(date '+%Y-%m-%d %H:%M:%S') ====="
} >> "$LOG" 2>&1
