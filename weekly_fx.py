"""주별 원화 원금 기반 손익 분해 (매매·배당 / RP이자 / 환차손익). 기존 파일 수정 없음."""
import json
from datetime import date
import report as R
import db_client as C

RP_TAX_RATE = 0.154   # 이자소득세 14% + 지방소득세 1.4%
bf = json.load(open("report_data/weekly_backfill.json"))
weekly = bf["weekly"]
snap = bf.get("today_snapshot", {})
today = date.today().strftime("%Y%m%d")
accounts = [a for a in C.load_accounts() if a.id in R.REPORT_ACCOUNTS]

conv = []      # (날짜, usd, krw) 환전
rp_rows = []   # (날짜, 세후이자 usd, 계좌id)
fee_sell = fee_buy = 0.0
for acc in accounts:
    for r in R.fetch_trade_history(acc, R.INITIAL_START_DATE, today):
        nm = r.get("SmryNm", "")
        if nm in ("외화매수", "외화매도"):
            if "취소" in (r.get("CancTpNm") or ""):
                print(f"[경고] 취소건 제외: {acc.id} {r['TrdDt']} {nm}")
                continue
            sign = 1 if nm == "외화매수" else -1
            if sign == -1:
                print(f"[경고] 외화매도 발견: {acc.id} {r['TrdDt']}")
            conv.append((r["TrdDt"], sign * float(r["FcurrAmt"]), sign * float(r["TrdAmt"])))
        elif nm == R.RP_SELL_NAME:
            tax = float(r["Ictax"]) + float(r["Ihtax"])
            usd_t, krw_t = float(r["FcurrTrdAmt"]), float(r["TrdAmt"])
            if tax > 0 and usd_t > 0 and krw_t > 0:
                rate = krw_t / usd_t
                rp_rows.append((r["TrdDt"], tax * (1 / RP_TAX_RATE - 1) / rate, acc.id))
        elif nm == "주식매도대금입금(외화)":
            fee_sell += float(r["FcurrTrdAmt"]) - float(r["FcurrAmt"])
        elif nm == "주식매수대금출금(외화)":
            fee_buy += float(r["FcurrAmt"]) - float(r["FcurrTrdAmt"])

def prin_at(w):
    ws = w.replace("-", "")
    return (sum(u for d, u, k in conv if d <= ws), sum(k for d, u, k in conv if d <= ws))

def rp_cum(w, acc_id=None):
    ws = w.replace("-", "")
    return sum(u for d, u, a in rp_rows if d <= ws and (acc_id is None or a == acc_id))

out = []
prev = dict(P=0.0, K=0.0, Gtd=0.0, RP=0.0, fx=None, eq_usd=0.0)
print(f"{'주':10} {'환율':>7} {'원금(백만)':>9} {'재구성총자산':>11} {'원화손익':>9} "
      f"{'주간매매·배당':>11} {'주간RP':>7} {'주간환차':>8} {'주간순입금':>9}")
for w in weekly:
    fx = w["fx_rate"]
    P, K = prin_at(w["week_end"])
    Gtd = w["cum_gain_usd"]                 # 매매손익(순, 수수료 반영) + 배당
    RP = rp_cum(w["week_end"])              # 누적 세후 RP이자
    eq_usd = P + Gtd + RP
    eq_krw = eq_usd * fx
    pnl_krw = eq_krw - K

    dP, dK = P - prev["P"], K - prev["K"]
    trading = (Gtd - prev["Gtd"]) * fx
    rpk = (RP - prev["RP"]) * fx
    fx_pl = (prev["eq_usd"] * (fx - prev["fx"]) if prev["fx"] else 0.0) + (dP * fx - dK)
    out.append(dict(week_end=w["week_end"], fx=fx, principal_usd=P, principal_krw=K,
                    gain_td_usd=Gtd, rp_cum_usd=RP, equity_usd=eq_usd, equity_krw=eq_krw,
                    pnl_krw=pnl_krw, weekly_trading_krw=trading, weekly_rp_krw=rpk,
                    weekly_invest_krw=trading + rpk, weekly_fx_krw=fx_pl, weekly_net_in_krw=dK))
    print(f"{w['week_end']:10} {fx:7.1f} {K/1e6:9.1f} {eq_krw/1e6:11.1f} {pnl_krw/1e6:9.1f} "
          f"{trading/1e6:11.2f} {rpk/1e6:7.2f} {fx_pl/1e6:8.2f} {dK/1e6:9.1f}")
    prev = dict(P=P, K=K, Gtd=Gtd, RP=RP, fx=fx, eq_usd=eq_usd)

rp_total = rp_cum(today)
rp_by_acc = {a.id: rp_cum(today, a.id) for a in accounts}
tot_usd_c = snap.get("total_usd", out[-1]["equity_usd"]) + rp_total
tot_krw_c = tot_usd_c * snap.get("fx_rate", out[-1]["fx"])
summary = dict(rp_net_total_usd=rp_total, rp_net_by_account=rp_by_acc,
               fees_usd=fee_sell + fee_buy, total_usd_corrected=tot_usd_c,
               total_krw_corrected=tot_krw_c)

chk = sum(r["weekly_trading_krw"] + r["weekly_rp_krw"] + r["weekly_fx_krw"] for r in out) - out[-1]["pnl_krw"]
print(f"\n[검증1] 주간(매매+RP+환차) 누적합 − 최종 원화손익 = {chk:,.0f}원 (0에 가까워야 함)")
print(f"누적 매매·배당 {sum(r['weekly_trading_krw'] for r in out):,.0f}원 / RP이자 {sum(r['weekly_rp_krw'] for r in out):,.0f}원 / "
      f"환차 {sum(r['weekly_fx_krw'] for r in out):,.0f}원")
print(f"[검증2] 재구성 USD 자산 {out[-1]['equity_usd']:,.2f} vs 보정 총자산 {tot_usd_c:,.2f} (차이 {tot_usd_c-out[-1]['equity_usd']:,.2f})")
print(f"RP 세후이자 누적 ${rp_total:,.2f} / 계좌별 { {k: round(v,1) for k,v in rp_by_acc.items()} }")
print(f"수수료·제세금 참고(매매손익에 이미 반영) ${fee_sell+fee_buy:,.2f}")

json.dump(dict(generated_at=str(date.today()), summary=summary, weekly=out),
          open("report_data/weekly_fx.json", "w"), ensure_ascii=False, indent=1)
print("저장됨: report_data/weekly_fx.json")
