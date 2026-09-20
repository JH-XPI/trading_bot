"""주별 원화 원금 기반 손익 분해 (투자손익 vs 환차손익). 기존 파일 수정 없음."""
import json
from datetime import date
import report as R
import db_client as C

bf = json.load(open("report_data/weekly_backfill.json"))
weekly = bf["weekly"]
today = date.today().strftime("%Y%m%d")
accounts = [a for a in C.load_accounts() if a.id in R.REPORT_ACCOUNTS]

# 환전 이력 수집: 외화매수(+), 외화매도(-)
conv = []  # (TrdDt, usd, krw)
for acc in accounts:
    for r in R.fetch_trade_history(acc, R.INITIAL_START_DATE, today):
        nm = r.get("SmryNm", "")
        if nm not in ("외화매수", "외화매도"):
            continue
        if "취소" in (r.get("CancTpNm") or ""):
            print(f"[경고] 취소건 제외: {acc.id} {r['TrdDt']} {nm}")
            continue
        sign = 1 if nm == "외화매수" else -1
        if sign == -1:
            print(f"[경고] 외화매도 발견: {acc.id} {r['TrdDt']} USD={r['FcurrAmt']} KRW={r['TrdAmt']}")
        conv.append((r["TrdDt"], sign * float(r["FcurrAmt"]), sign * float(r["TrdAmt"])))

def prin_at(w):
    ws = w.replace("-", "")
    return (sum(u for d, u, k in conv if d <= ws),
            sum(k for d, u, k in conv if d <= ws))

out = []
prev = dict(P=0.0, K=0.0, G=0.0, fx=None, eq_usd=0.0)
print(f"{'주':10} {'환율':>7} {'원금(백만)':>10} {'재구성총자산(백만)':>17} {'원화손익(백만)':>13} "
      f"{'주간투자손익':>11} {'주간환차손익':>11} {'주간순입금':>10}")
for w in weekly:
    fx = w["fx_rate"]
    P, K = prin_at(w["week_end"])
    G = w["cum_gain_usd"]
    eq_usd = P + G
    eq_krw = eq_usd * fx
    pnl_krw = eq_krw - K

    dP, dK, dG = P - prev["P"], K - prev["K"], G - prev["G"]
    invest = dG * fx                                             # 투자손익(원)
    fx_old = prev["eq_usd"] * (fx - prev["fx"]) if prev["fx"] else 0.0  # 기존 자산의 환율변동
    fx_new = dP * fx - dK                                        # 신규 환전분의 환율변동
    fx_pl = fx_old + fx_new
    row = dict(week_end=w["week_end"], fx=fx, principal_usd=P, principal_krw=K,
               gain_usd=G, equity_usd=eq_usd, equity_krw=eq_krw, pnl_krw=pnl_krw,
               weekly_invest_krw=invest, weekly_fx_krw=fx_pl, weekly_net_in_krw=dK)
    out.append(row)
    print(f"{w['week_end']:10} {fx:7.1f} {K/1e6:10.1f} {eq_krw/1e6:17.1f} {pnl_krw/1e6:13.1f} "
          f"{invest/1e6:11.2f} {fx_pl/1e6:11.2f} {dK/1e6:10.1f}")
    prev = dict(P=P, K=K, G=G, fx=fx, eq_usd=eq_usd)

# 검증 1: 분해 합계 = 원화손익 변화
chk = sum(r["weekly_invest_krw"] + r["weekly_fx_krw"] for r in out) - out[-1]["pnl_krw"]
print(f"\n[검증1] 주간(투자+환차) 누적합 − 최종 원화손익 = {chk:,.0f}원 (0에 가까워야 함)")
tot_i = sum(r["weekly_invest_krw"] for r in out)
tot_f = sum(r["weekly_fx_krw"] for r in out)
print(f"누적 투자손익 {tot_i:,.0f}원 / 누적 환차손익 {tot_f:,.0f}원 / 합 {tot_i+tot_f:,.0f}원")

# 검증 2: 재구성 USD 자산 vs 오늘 실제 스냅샷
snap = bf.get("today_snapshot", {})
cands = {k: v for k, v in snap.items() if isinstance(v, (int, float)) and "total" in k.lower()}
print(f"[검증2] 재구성 USD 자산 {out[-1]['equity_usd']:,.2f} vs 스냅샷 후보 {cands if cands else list(snap.keys())}")

json.dump(dict(generated_at=str(date.today()), weekly=out), open("report_data/weekly_fx.json", "w"),
          ensure_ascii=False, indent=1)
print("저장됨: report_data/weekly_fx.json")
