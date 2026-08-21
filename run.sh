#!/bin/sh
# daily.sh をセッションから切り離して起動し、完了まで待って結果を出す。
# Claude のバックグラウンド実行を使わないこと。セッション終了時に kill されるため。
D=/Users/apple/gallery-digest
LOG="$D/logs/$(date +%Y-%m-%d).log"
mkdir -p "$D/logs"
# 当日分が既に完了していれば何もしない（launchd 側で済んでいる場合）
if [ -f "$LOG" ] && grep -q '===== DONE' "$LOG"; then
  echo "[run.sh] 本日分は完了済み（launchd 実行分）"
  echo "---- $LOG ----"
  tail -20 "$LOG"
  exit 0
fi
if ! pgrep -f "$D/daily.sh" >/dev/null 2>&1; then
  nohup /bin/sh "$D/daily.sh" >/dev/null 2>&1 &
  sleep 2
fi
i=0
while [ $i -lt 170 ]; do
  if [ -f "$LOG" ] && grep -qE '===== DONE|!! ' "$LOG"; then break; fi
  sleep 5
  i=$((i + 1))
done
if [ -f "$LOG" ] && grep -q '===== DONE' "$LOG"; then
  echo "[run.sh] 完了"
elif [ -f "$LOG" ] && grep -q '!! ' "$LOG"; then
  echo "[run.sh] 失敗"
else
  echo "[run.sh] まだ実行中（切り離し済みなので継続中。run.sh を再実行すれば続きから待てます）"
fi
echo "---- $LOG ----"
tail -40 "$LOG" 2>/dev/null
