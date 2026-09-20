# -*- coding: utf-8 -*-
"""
5/4(INITIAL_START_DATE) ~ 오늘까지, 4계좌 합산 기준 "주차별 성장 추이"를 만든다.

report.py와 달리 이건 "지금 이 순간의 잔고"가 아니라 "그 주 시점까지 누적된 손익"을
재구성하는 스크립트다 — 주식 평가액(현재 잔고 API로만 조회 가능)은 과거 시점을
재현할 수 없으므로 주간 추이에서는 제외하고, 재구성 가능한 항목만 담는다:

    RP잔고(누적)        - 계좌거래내역에서 그 날짜까지의 RP매수/취소/환매도 누적
    누적 매매손익(USD)  - 해외주식 실현손익 조회에서 그 날짜까지의 체결분 합산
    누적 배당순액(USD)  - 계좌거래내역에서 그 날짜까지의 배당입금-배당세 누적

주차별 환율은 "해외주식 거래내역 조회"(CAZCQ01600)의 AstkAppXchrat(적용환율) 필드를
그대로 쓴다 — 외부 환율 API 없이, 이미 하는 매매 자체에 찍히는 그날의 실제 환율이다.

API는 계좌당 3번(거래내역 전체, 실현손익 전체, 환율용 해외주식거래내역 전체)만 호출하고,
그 결과를 로컬에서 주차별로 잘라가며 계산한다 — 주마다 API를 다시 부르지 않는다.

사용법:
    python3 weekly_backfill.py
"""
from __future__ import annotations
import os, sys, json, time
from datetime import date, timedelta, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from dotenv import load_dotenv
load_dotenv(os.path.join(HERE, ".env"))

import requests
import db_client as C
import report as R   # fetch_trade_history, RP_*_NAME, INITIAL_START_DATE 등 재사용

OUT_PATH = os.path.join(R.DATA_DIR, "weekly_backfill.json")
OV_HISTORY_PATH = "/api/v1/trading/overseas-stock/inquiry/trade-history"  # CAZCQ01600


def fetch_daily_fx_rates(acc, qry_srt: str, qry_end: str) -> dict:
    """해외주식 거래내역 조회(CAZCQ01600) - 이 API의 모든 행에는 그날 실제 적용된
    환율(AstkAppXchrat)이 찍혀 나온다. 반환: {YYYYMMDD: 환율} (거래가 있었던 날짜만)."""
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
            d = row.get("TrdDt")
            rate = row.get("AstkAppXchrat")
            if d and rate and float(rate) > 0:
                rates[d] = float(rate)
        cont_yn = resp.headers.get("cont_yn") or resp.headers.get("Cont_yn") or "N"
        cont_key = resp.headers.get("cont_key") or resp.headers.get("Cont_key") or ""
        if cont_yn != "Y" or not cont_key:
            break
    return rates


def rate_as_of(rate_table: dict, up_to: str):
    """rate_table(YYYYMMDD->환율)에서 up_to 이전(포함) 중 가장 최근 날짜의 환율(실측값)."""
    candidates = [d for d in rate_table if d <= up_to]
    if not candidates:
        return None
    return rate_table[max(candidates)]


def fetch_realized_pnl_rows(acc, qry_srt: str, qry_end: str) -> list:
    """report.fetch_realized_pnl_usd()는 합계만 반환하므로, 주차별로 자르려면
    OrdDt(주문일자)가 붙은 원본 행이 필요해 별도로 만든다(같은 API, 반환만 다름)."""
    all_rows = []
    body_in = {
        "TrxTpCode": "2", "QrySrtDt": qry_srt, "QryEndDt": qry_end,
        "AstkIsuNo": "", "WonFcurrTpCode": "2", "EvrprcYn": "Y", "DpntBalTpCode": "0",
    }
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


def week_end_dates(start: date, end: date) -> list:
    """start~end 사이, 매주 일요일을 YYYYMMDD로 나열. 마지막엔 end 자체도 포함."""
    dates = []
    d = start
    d += timedelta(days=(6 - d.weekday() + 1) % 7 or 7) if d.weekday() != 6 else timedelta(0)
    while d <= end:
        dates.append(d.strftime("%Y%m%d"))
        d += timedelta(days=7)
    if not dates or dates[-1] != end.strftime("%Y%m%d"):
        dates.append(end.strftime("%Y%m%d"))
    return dates


def cumulative_at(rows: list, date_field: str, up_to: str) -> dict:
    subset = [r for r in rows if (r.get(date_field) or "") <= up_to]
    d = R.compute_deltas_from_rows(subset)
    return dict(rp_balance_usd=d["rp_delta"], dividend_net_usd=d["div_net_usd"])


def cumulative_pnl_at(pnl_rows: list, up_to: str) -> float:
    return sum(float(r.get("AstkBnsplAmt") or 0) for r in pnl_rows if (r.get("OrdDt") or "") <= up_to)


def main():
    accounts = {a.id: a for a in C.load_accounts() if a.id in R.REPORT_ACCOUNTS}
    today = date.today()
    start = datetime.strptime(R.INITIAL_START_DATE, "%Y%m%d").date()
    weeks = week_end_dates(start, today)
    today_str = today.strftime("%Y%m%d")

    acct_rows, acct_pnl_rows, acct_fx = {}, {}, {}
    for aid, acc in accounts.items():
        print(f"[{aid}] 전체이력 조회 중...")
        acct_rows[aid] = R.fetch_trade_history(acc, R.INITIAL_START_DATE, today_str)
        acct_pnl_rows[aid] = fetch_realized_pnl_rows(acc, R.INITIAL_START_DATE, today_str)
        acct_fx[aid] = fetch_daily_fx_rates(acc, R.INITIAL_START_DATE, today_str)

    # 계좌마다 환율이 살짝 다를 수 있으니(같은 날이라도), 4계좌 중 그 시점 가장 최근값들의 평균을 씀
    def week_fx(up_to):
        vals = [v for aid in accounts if (v := rate_as_of(acct_fx[aid], up_to)) is not None]
        return sum(vals) / len(vals) if vals else None

    today_extra = {}
    for aid, acc in accounts.items():
        today_extra[aid] = dict(cash_usd=R.get_usd_cash(acc), stock_eval_usd=R.get_stock_eval_usd(acc))

    weekly_records = []
    for w in weeks:
        w_disp = f"{w[:4]}-{w[4:6]}-{w[6:]}"
        total_rp = total_pnl = total_div = 0.0
        for aid in accounts:
            c = cumulative_at(acct_rows[aid], "TrdDt", w)
            p = cumulative_pnl_at(acct_pnl_rows[aid], w)
            total_rp += c["rp_balance_usd"]
            total_pnl += p
            total_div += c["dividend_net_usd"]
        fx = week_fx(w)
        gain_usd = total_pnl + total_div
        weekly_records.append(dict(
            week_end=w_disp, fx_rate=fx,
            rp_balance_usd=total_rp, cum_trading_pnl_usd=total_pnl, cum_dividend_usd=total_div,
            cum_gain_usd=gain_usd, cum_gain_krw=(gain_usd * fx) if fx else None,
        ))

    today_cash = sum(v["cash_usd"] for v in today_extra.values())
    today_stock = sum(v["stock_eval_usd"] for v in today_extra.values())
    today_fx = weekly_records[-1]["fx_rate"]
    today_total_usd = weekly_records[-1]["rp_balance_usd"] + today_cash + today_stock

    result = dict(
        generated_at=today.strftime("%Y-%m-%d"),
        weekly=weekly_records,
        today_snapshot=dict(
            cash_usd=today_cash, stock_eval_usd=today_stock,
            rp_balance_usd=weekly_records[-1]["rp_balance_usd"],
            fx_rate=today_fx, total_usd=today_total_usd,
            total_krw=(today_total_usd * today_fx) if today_fx else None,
        ),
    )
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n{'주(일요일)':>12} {'적용환율':>9} {'RP잔고(USD)':>13} {'누적손익(USD)':>13} {'누적손익(원화)':>15} {'주간증감(원화)':>15}")
    prev_krw = 0.0
    for r in weekly_records:
        krw = r["cum_gain_krw"] or 0.0
        delta = krw - prev_krw
        fx_disp = f"{r['fx_rate']:.1f}" if r["fx_rate"] else "-"
        print(f"{r['week_end']:>12} {fx_disp:>9} {r['rp_balance_usd']:13,.2f} "
              f"{r['cum_trading_pnl_usd']+r['cum_dividend_usd']:13,.2f} {krw:15,.0f} {delta:+15,.0f}")
        prev_krw = krw

    print(f"\n오늘 총자산: ${result['today_snapshot']['total_usd']:,.2f} "
          f"= {result['today_snapshot']['total_krw']:,.0f}원 (환율 {today_fx})")
    print(f"저장됨: {OUT_PATH}")


if __name__ == "__main__":
    main()
