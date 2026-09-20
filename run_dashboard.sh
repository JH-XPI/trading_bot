#!/bin/bash
# 결산 대시보드 일일 갱신: pipeline.py(증분 이력 갱신+집계) -> make_dashboard.py -> 배포
cd "$HOME/trading_bot" || exit 1
WEBROOT=/home/master/applications/zburctrrtu/public_html
LOG=report_data/run_dashboard.log
OUT=report_data/pipeline.out
mkdir -p report_data

exec 9>report_data/.run.lock
flock -n 9 || { echo "$(date '+%F %T') 이미 실행 중, 건너뜀" >> "$LOG"; exit 0; }

# 로그 1MB 초과 시 최근 3000줄만 유지
if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG")" -gt 1048576 ]; then
  tail -n 3000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

echo "=== $(date '+%F %T') 시작 (pipeline)" >> "$LOG"

if ! python3 pipeline.py > "$OUT" 2>&1; then
  echo "pipeline 실패 - 기존 페이지 유지" >> "$LOG"
  tail -15 "$OUT" >> "$LOG"
  exit 1
fi
grep -E "history|검증|RP 세후|수수료|완료|경고" "$OUT" >> "$LOG"

if ! python3 make_dashboard.py --out report_data/dashboard_new.html >> "$LOG" 2>&1; then
  echo "대시보드 생성 실패 - 기존 페이지 유지" >> "$LOG"
  exit 1
fi

mkdir -p "$WEBROOT/report"
cp report_data/dashboard_new.html "$WEBROOT/report/index.html.tmp" \
  && mv "$WEBROOT/report/index.html.tmp" "$WEBROOT/report/index.html" \
  && echo "배포 완료" >> "$LOG"
