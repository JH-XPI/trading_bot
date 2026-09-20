"""결산 실행 결과를 텔레그램으로 알린다. 사용: notify_report.py ok | fail <단계명>"""
import json, os, re, sys
from datetime import datetime, timedelta

D = os.path.dirname(os.path.abspath(__file__))
os.chdir(D)

# .env 로드 (cron 환경 대비)
try:
    for line in open(".env", encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
except FileNotFoundError:
    pass

import notify as N

def tail(path, n):
    try:
        return "".join(open(path, encoding="utf-8", errors="ignore").readlines()[-n:])
    except Exception:
        return ""

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
    now = (datetime.utcnow() + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M KST")
    if mode == "fail":
        step = sys.argv[2] if len(sys.argv) > 2 else "알 수 없음"
        msg = (f"🔴 결산 대시보드 갱신 실패 ({now})\n단계: {step}\n"
               f"기존 페이지는 유지됩니다.\n\n" + tail("report_data/pipeline.out", 8)[-700:])
        N.send_telegram(msg)
        return
    S = json.load(open("report_data/weekly_fx.json", encoding="utf-8")).get("summary", {})
    chk = S.get("check", {}) or {}
    total = S.get("total_krw_corrected", 0)
    res = chk.get("residual_usd")
    thr = chk.get("threshold_usd", 150)
    out = tail("report_data/pipeline.out", 200)
    pl = ""
    m = re.search(r"누적 매매[^\n]*", out)
    if m:
        pl = "\n" + m.group(0)
    head = "🟢 결산 대시보드 갱신 완료" if chk.get("ok", True) else "⚠️ 결산 갱신 완료 (검산 잔차 경고)"
    msg = f"{head} ({now})\n총자산 {total:,.0f}원{pl}"
    if res is not None:
        msg += f"\n검산 잔차 ${res:,.2f} (기준 ${thr:,.0f})"
    msg += "\nhttps://soxl.riskandeconomy.com/report/"
    N.send_telegram(msg)

try:
    main()
except Exception as e:
    print("[notify_report] 발송 실패:", e)
