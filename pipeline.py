# -*- coding: utf-8 -*-
"""
pipeline.py — 법인 4계좌(JYN901~904) 결산 데이터 파이프라인 (단일 진입점)

[구조 원칙]
 1) 원본 이력(거래내역/실현손익/환율)은 report_data/history/*.json 에 저장해 둔다.
 2) 평소에는 API로 "최근 14일"만 조회해 저장소의 해당 구간을 교체한다. (history_cache.py)
    전체 재조회는 저장소가 없을 때, 수동 초기화(--full 또는 history_cache.py --reset), 90일 안전망일 때만.
 3) 모든 계산(RP 잔고, 이자, 손익, 원금, 주별 분해)은 저장소의 행에서 이 파일의 함수로만 한다.
    계산 규칙이 여기 한 곳에만 있으므로 다른 스크립트가 다른 값을 내는 일이 없다.
 4) 산출물: weekly_backfill.json / weekly_fx.json / weekly_history.json (make_dashboard.py 입력)

[RP 세후이자 보정 - 절대 빼먹지 말 것]
 외화RP 환매도 행: FcurrAmt = 원금 + "세후 이자", FcurrTrdAmt = 원금 + "세전 이자" (차이 = 원천세).
 RP 잔고를 "조건부매수 - 취소 - 환매도(FcurrAmt)"로 역산하면 환매도 금액에 섞인 이자만큼
 RP 잔고가 작게 계산되고(현금에는 이자가 들어와 합계에서 상쇄되어 사라짐) 총자산·손익에서
 RP이자가 통째로 빠진다. (2026-09 발견: 누적 약 $2.1천, 총자산의 0.45%)
 보정: 행마다 세후이자 = (Ictax+Ihtax) x (1/0.154 - 1) / (TrdAmt/FcurrTrdAmt)   [15.4% = 소득세14% + 지방세1.4%]
       RP잔고(보정) = 조건부매수 - 취소 - 환매도 + 누적 세후이자
 검증: DB증권 화면 [1623]/[1001] 대조 오차 0.02% 이내. 이 보정은 summarize() 한 곳에서만 처리한다.

[검산 잔차 감시]
 (실측 총자산) - (환전 원금 + 누적 손익) = 미실현손익 + 예탁금이용료 등 소액($30 안팎).
 RESIDUAL_WARN_USD를 넘으면 계산에서 무언가 빠졌다는 뜻이므로 경고를 남기고 화면에도 표시한다.

[통화 원칙] 총자산·원금·손익 합산은 원화, 계좌별 매매손익·배당은 USD, RP세금은 원화 (API가 주는 통화 그대로).
[원금 정의] 원금 = 외화매수(원화->USD 환전)에 사용된 원화 누적. 계좌간 외화대체는 합산 시 상쇄.
[손익 정의] 매매손익(AstkBnsplAmt)은 수수료·제세금을 이미 차감한 순손익이다. 수수료를 또 빼지 않는다.

사용법:  python3 pipeline.py          (일상: 최근 14일만 갱신)
         python3 pipeline.py --full   (저장소 전체 재조회)
"""
from __future__ import annotations
import os, sys, json, time
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from dotenv import load_dotenv
load_dotenv(os.path.join(HERE, ".env"))

import requests
import db_client as C
import report as R
import history_cache as HC

RP_TAX_RATE = 0.154
RESIDUAL_WARN_USD = 150.0
OV_HISTORY_PATH = "/api/v1/trading/overseas-stock/inquiry/trade-history"  # CAZCQ01600 (환율용)


# ───────────────────────── API 조회 (저장소가 호출) ─────────────────────────
def fetch_daily_fx_rates(acc, qry_srt: str, qry_end: str) -> dict:
    """해외주식 거래내역(CAZCQ01600)의 AstkAppXchrat(그날 적용환율). 반환: {YYYYMMDD: 환율}"""
    rates = {}
    cont_yn, cont_key = "N", ""
    for _ in range(60):
        time.sleep(R.REQUEST_DELAY)
        headers = C._headers(acc)
        headers["cont_yn"] = cont_yn
        headers["cont_key"] = cont_key
        body = {"In": {"QryTpCode": "0", "StnlnTpCode": "1", "AstkIsuNo": "",
                       "QrySrtDt": qry_srt, "QryEndDt": qry_end, "DpntBalTpCode": "0"}}
        resp = requests.post(C.BASE_URL + OV_HISTORY_PATH, headers=headers, json=body, timeout=15)
        if resp.status_code != 200:
            raise C.DBSecError(f"[{acc.id}] 해외주식거래내역(환율용) 조회 실패: {resp.status_code} {resp.text[:300]}")
        data = resp.json()
        for row in (data.get("Out") or []):
            d, rate = row.get("TrdDt"), row.get("AstkAppXchrat")
            if d and rate and float(rate) > 0:
                rates[d] = float(rate)
        cont_yn = resp.headers.get("cont_yn") or resp.headers.get("Cont_yn") or "N"
        cont_key = resp.headers.get("cont_key") or resp.headers.get("Cont_key") or ""
        if cont_yn != "Y" or not cont_key:
            break
    return rates


def fetch_realized_pnl_rows(acc, qry_srt: str, qry_end: str) -> list:
    """해외주식 실현손익(CAZCQ00300) 원본 행 (OrdDt 포함)."""
    all_rows = []
    body_in = {"TrxTpCode": "2", "QrySrtDt": qry_srt, "QryEndDt": qry_end,
               "AstkIsuNo": "", "WonFcurrTpCode": "2", "EvrprcYn": "Y", "DpntBalTpCode": "0"}
    cont_yn, cont_key = "N", ""
    for _ in range(60):
        time.sleep(R.REQUEST_DELAY)
        headers = C._headers(acc)
        headers["cont_yn"] = cont_yn
        headers["cont_key"] = cont_key
        resp = requests.post(C.BASE_URL + R.RLZPNL_PATH, headers=headers, json={"In": body_in}, timeout=15)
        if resp.status_code != 200:
            raise C.DBSecError(f"[{acc.id}] 실현손익 조회 실패: {resp.status_code} {resp.text[:300]}")
        data = resp.json()
        all_rows.extend(data.get("Out2") or [])
        cont_yn = resp.headers.get("cont_yn") or resp.headers.get("Cont_yn") or "N"
        cont_key = resp.headers.get("cont_key") or resp.headers.get("Cont_key") or ""
        if cont_yn != "Y" or not cont_key:
            break
    return all_rows


# ───────────────────────── 계산 규칙 (저장소의 행만 사용, API 호출 없음) ─────────────────────────
def rp_net_interest_usd(row: dict) -> float:
    """환매도 1건에 포함된 세후 RP이자(USD) 추정. 위 [RP 세후이자 보정] 참고."""
    tax = float(row.get("Ictax") or 0) + float(row.get("Ihtax") or 0)
    usd_t = float(row.get("FcurrTrdAmt") or 0)
    krw_t = float(row.get("TrdAmt") or 0)
    if tax <= 0 or usd_t <= 0 or krw_t <= 0:
        return 0.0
    return tax * (1 / RP_TAX_RATE - 1) / (krw_t / usd_t)


def summarize(rows: list, pnl_rows: list, up_to: str) -> dict:
    """up_to(YYYYMMDD)까지의 계좌 누적 요약. RP 보정은 여기서만 한다."""
    buy = cancel = sell = net_int = 0.0
    div_in = div_tax = rp_tax_krw = fee = 0.0
    for r in rows:
        if (r.get("TrdDt") or "") > up_to:
            continue
        nm = r.get("SmryNm", "")
        if nm == R.RP_BUY_NAME:
            buy += float(r["FcurrAmt"])
        elif nm == R.RP_BUY_CANCEL_NAME:
            cancel += float(r["FcurrAmt"])
        elif nm == R.RP_SELL_NAME:
            sell += float(r["FcurrAmt"])
            net_int += rp_net_interest_usd(r)
            rp_tax_krw += float(r["Ictax"]) + float(r["Ihtax"])
        elif nm == R.DIV_IN_NAME:
            div_in += float(r["FcurrAmt"])
        elif nm == R.DIV_TAX_NAME:
            div_tax += float(r["FcurrAmt"])
        elif nm == "주식매도대금입금(외화)":     # 입금액 = 체결금액 - 수수료
            fee += float(r["FcurrTrdAmt"]) - float(r["FcurrAmt"])
        elif nm == "주식매수대금출금(외화)":     # 출금액 = 체결금액 + 수수료
            fee += float(r["FcurrAmt"]) - float(r["FcurrTrdAmt"])
    trading = sum(float(p["AstkBnsplAmt"]) for p in pnl_rows if (p.get("OrdDt") or "") <= up_to)
    ledger = buy - cancel - sell
    return dict(rp_ledger_usd=ledger, rp_net_interest_usd=net_int, rp_balance_usd=ledger + net_int,
                dividend_net_usd=div_in - div_tax, dividend_tax_usd=div_tax,
                rp_tax_krw=rp_tax_krw, rp_interest_krw=rp_tax_krw / RP_TAX_RATE,
                trading_pnl_usd=trading, fee_usd=fee)


def conversions(rows: list) -> list:
    """환전 이력 [(날짜, USD, KRW)]. 외화매수(+) / 외화매도(-)."""
    out = []
    for r in rows:
        nm = r.get("SmryNm", "")
        if nm not in ("외화매수", "외화매도"):
            continue
        if "취소" in (r.get("CancTpNm") or ""):
            print(f"[경고] 취소건 제외: {r.get('TrdDt')} {nm}")
            continue
        sign = 1 if nm == "외화매수" else -1
        if sign == -1:
            print(f"[경고] 외화매도 발견: {r.get('TrdDt')} USD={r['FcurrAmt']} KRW={r['TrdAmt']}")
        out.append((r["TrdDt"], sign * float(r["FcurrAmt"]), sign * float(r["TrdAmt"])))
    return out


def week_ends(start: date, end: date) -> list:
    """시작일+7n일 ... 마지막은 오늘."""
    out, d = [], start + timedelta(days=7)
    while d < end:
        out.append(d)
        d += timedelta(days=7)
    out.append(end)
    return out


def rate_as_of(table: dict, up_to: str):
    c = [d for d in table if d <= up_to]
    return table[max(c)] if c else None


def avg_fx(tables: dict, up_to: str):
    vals = [v for t in tables.values() if (v := rate_as_of(t, up_to)) is not None]
    return sum(vals) / len(vals) if vals else None


def write_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ───────────────────────── 메인 ─────────────────────────
def main():
    t0 = time.time()
    if "--full" in sys.argv:
        os.environ["FORCE_FULL"] = "1"
    today = date.today()
    today_str = today.strftime("%Y%m%d")
    accounts = {a.id: a for a in C.load_accounts() if a.id in R.REPORT_ACCOUNTS}

    # 1) 이력 저장소 갱신 (평소 최근 14일만 API 조회)
    rows, pnl, fx = {}, {}, {}
    for aid, acc in accounts.items():
        rows[aid] = HC.cached_rows("trade", acc, R.fetch_trade_history, "TrdDt", R.INITIAL_START_DATE, today_str)
        pnl[aid] = HC.cached_rows("pnl", acc, fetch_realized_pnl_rows, "OrdDt", R.INITIAL_START_DATE, today_str)
        fx[aid] = HC.cached_fx(acc, fetch_daily_fx_rates, R.INITIAL_START_DATE, today_str)

    conv = []
    for aid in accounts:
        conv += conversions(rows[aid])

    # 2) 주별 재구성
    start = datetime.strptime(R.INITIAL_START_DATE, "%Y%m%d").date()
    bf_weekly, fx_weekly = [], []
    prev = dict(P=0.0, K=0.0, G=0.0, RP=0.0, fx=None, eq=0.0)
    print(f"{'주':10} {'환율':>7} {'원금(백만)':>9} {'재구성총자산':>11} {'원화손익':>9} "
          f"{'주간매매·배당':>11} {'주간RP':>7} {'주간환차':>8} {'주간순입금':>9}")
    for w in week_ends(start, today):
        ws, disp = w.strftime("%Y%m%d"), w.strftime("%Y-%m-%d")
        fxw = avg_fx(fx, ws)
        if fxw is None:
            print(f"[경고] {disp} 적용환율 없음 - 주차 건너뜀")
            continue
        sums = [summarize(rows[a], pnl[a], ws) for a in accounts]
        trading = sum(s["trading_pnl_usd"] for s in sums)
        div = sum(s["dividend_net_usd"] for s in sums)
        RP = sum(s["rp_net_interest_usd"] for s in sums)
        rp_bal = sum(s["rp_balance_usd"] for s in sums)
        G = trading + div
        P = sum(u for d, u, k in conv if d <= ws)
        K = sum(k for d, u, k in conv if d <= ws)
        eq_usd = P + G + RP
        eq_krw = eq_usd * fxw
        pnl_krw = eq_krw - K
        dP, dK = P - prev["P"], K - prev["K"]
        trading_k = (G - prev["G"]) * fxw
        rp_k = (RP - prev["RP"]) * fxw
        fx_pl = (prev["eq"] * (fxw - prev["fx"]) if prev["fx"] else 0.0) + (dP * fxw - dK)
        bf_weekly.append(dict(week_end=disp, fx_rate=fxw, rp_balance_usd=rp_bal,
                              cum_trading_pnl_usd=trading, cum_dividend_usd=div,
                              cum_gain_usd=G, cum_gain_krw=G * fxw))
        fx_weekly.append(dict(week_end=disp, fx=fxw, principal_usd=P, principal_krw=K,
                              gain_td_usd=G, rp_cum_usd=RP, equity_usd=eq_usd, equity_krw=eq_krw,
                              pnl_krw=pnl_krw, weekly_trading_krw=trading_k, weekly_rp_krw=rp_k,
                              weekly_invest_krw=trading_k + rp_k, weekly_fx_krw=fx_pl, weekly_net_in_krw=dK))
        print(f"{disp:10} {fxw:7.1f} {K/1e6:9.1f} {eq_krw/1e6:11.1f} {pnl_krw/1e6:9.1f} "
              f"{trading_k/1e6:11.2f} {rp_k/1e6:7.2f} {fx_pl/1e6:8.2f} {dK/1e6:9.1f}")
        prev = dict(P=P, K=K, G=G, RP=RP, fx=fxw, eq=eq_usd)

    # 3) 오늘 스냅샷 (잔고 API는 오늘 값만 필요 - 계좌당 2회)
    fx_now = avg_fx(fx, today_str)
    snap_accounts = []
    for aid, acc in accounts.items():
        s = summarize(rows[aid], pnl[aid], today_str)
        cash = R.get_usd_cash(acc)
        stock = R.get_stock_eval_usd(acc)
        snap_accounts.append(dict(
            id=aid, label=getattr(acc, "label", aid), cash_usd=cash, stock_eval_usd=stock,
            rp_balance_usd=s["rp_balance_usd"], total_usd=cash + stock + s["rp_balance_usd"],
            trading_pnl_usd=s["trading_pnl_usd"], dividend_net_usd=s["dividend_net_usd"],
            dividend_tax_usd=s["dividend_tax_usd"], rp_interest_krw=s["rp_interest_krw"],
            rp_tax_krw=s["rp_tax_krw"], rp_net_usd=s["rp_net_interest_usd"],
            rp_ledger_usd=s["rp_ledger_usd"], fee_usd=s["fee_usd"]))
    cash_t = sum(a["cash_usd"] for a in snap_accounts)
    stock_t = sum(a["stock_eval_usd"] for a in snap_accounts)
    rp_t = sum(a["rp_balance_usd"] for a in snap_accounts)
    total_usd = cash_t + stock_t + rp_t
    total_krw = total_usd * fx_now
    rp_net_t = sum(a["rp_net_usd"] for a in snap_accounts)
    fees_t = sum(a["fee_usd"] for a in snap_accounts)

    # 4) 검산 잔차 감시
    residual = total_usd - fx_weekly[-1]["equity_usd"]
    check = dict(residual_usd=residual, threshold_usd=RESIDUAL_WARN_USD, ok=abs(residual) <= RESIDUAL_WARN_USD)
    summary = dict(rp_net_total_usd=rp_net_t, rp_net_by_account={a["id"]: a["rp_net_usd"] for a in snap_accounts},
                   fees_usd=fees_t, total_usd_corrected=total_usd, total_krw_corrected=total_krw, check=check)

    # 5) 산출물 저장 (make_dashboard.py 입력, 기존과 같은 형식)
    D = R.DATA_DIR
    write_json(os.path.join(D, "weekly_backfill.json"), dict(
        generated_at=str(today), weekly=bf_weekly,
        today_snapshot=dict(cash_usd=cash_t, stock_eval_usd=stock_t, rp_balance_usd=rp_t,
                            fx_rate=fx_now, total_usd=total_usd, total_krw=total_krw, rp_corrected=True)))
    write_json(os.path.join(D, "weekly_fx.json"), dict(generated_at=str(today), summary=summary, weekly=fx_weekly))
    hist_path = os.path.join(D, "weekly_history.json")
    try:
        with open(hist_path, encoding="utf-8") as f:
            hist = json.load(f)
        if not isinstance(hist, list):
            hist = []
    except Exception:
        hist = []
    entry = dict(date=today.strftime("%Y-%m-%d"), fx_rate=fx_now, total_assets_usd=total_usd,
                 total_assets_krw=total_krw, accounts=snap_accounts)
    hist = ([h for h in hist if h.get("date") != entry["date"]] + [entry])[-400:]
    write_json(hist_path, hist)

    # 6) 요약 출력 (run_dashboard.sh 로그에 마지막 줄들이 남는다)
    chk = sum(r["weekly_trading_krw"] + r["weekly_rp_krw"] + r["weekly_fx_krw"] for r in fx_weekly) - fx_weekly[-1]["pnl_krw"]
    print(f"\n[검증1] 주간(매매+RP+환차) 누적합 − 최종 원화손익 = {chk:,.0f}원 (0에 가까워야 함)")
    print(f"누적 매매·배당 {sum(r['weekly_trading_krw'] for r in fx_weekly):,.0f}원 / "
          f"RP이자 {sum(r['weekly_rp_krw'] for r in fx_weekly):,.0f}원 / 환차 {sum(r['weekly_fx_krw'] for r in fx_weekly):,.0f}원")
    print(f"[검증2] 실측 총자산 ${total_usd:,.2f} = {total_krw:,.0f}원 (환율 {fx_now:.1f}) / 재구성 ${fx_weekly[-1]['equity_usd']:,.2f} / 잔차 ${residual:,.2f}")
    if not check["ok"]:
        print(f"[경고] 검산 잔차가 임계값(${RESIDUAL_WARN_USD:,.0f})을 넘었습니다 - 계산 항목 누락 점검 필요")
    print(f"RP 세후이자 누적 ${rp_net_t:,.2f} / 수수료·제세금 참고(매매손익에 이미 반영) ${fees_t:,.2f}")
    print(f"완료: 소요 {time.time() - t0:.0f}초")


if __name__ == "__main__":
    main()
