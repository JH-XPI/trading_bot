#!/bin/bash
# 결산 대시보드 일일 갱신 (매매봇 decide.py 등은 건드리지 않음)
cd /home/master/trading_bot || exit 1
WEBROOT=/home/master/applications/zburctrrtu/public_html
LOG=report_data/run_dashboard.log
TMP=report_data/dashboard_new.html
mkdir -p report_data
[ -f $LOG ] && [ "$(stat -c%s $LOG)" -gt 1000000 ] && { tail -n 3000 $LOG > $LOG.tmp && mv $LOG.tmp $LOG; }
exec 9>/tmp/run_dashboard.lock
flock -n 9 || { echo "$(date '+%F %T') 이미 실행 중" >> $LOG; exit 0; }
{
  echo "=== $(date '+%F %T') 시작"
  python3 weekly_backfill.py > /dev/null \
  && FX=$(python3 -c "import json;print(json.load(open('report_data/weekly_backfill.json'))['today_snapshot']['fx_rate'])") \
  && python3 report.py --fx $FX > /dev/null \
  && python3 weekly_fx.py | tail -5 \
  && python3 make_dashboard.py --out $TMP \
  && cp $TMP $WEBROOT/report/index.html.tmp \
  && mv $WEBROOT/report/index.html.tmp $WEBROOT/report/index.html \
  && echo "배포 완료" || echo "!! 실패 - 기존 화면 유지"
} >> $LOG 2>&1
