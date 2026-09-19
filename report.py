# -*- coding: utf-8 -*-
"""
법인(jyn-partners) 4개 계좌 결산 리포트 스크립트.

주문(place_order)은 절대 호출하지 않는다 — 조회 전용.
db_client.py는 수정하지 않고, 필요한 부품(_headers, BASE_URL 등)만 가져다 쓴다.

=== 설계 원칙(2026-09-19 확정, 4계좌 실측 대조로 검증됨) ===

1) 전체 자산(합산) — 원화 1줄
     총자산 = 4계좌(현금 + RP + 주식평가액, 전부 USD) 합계 × 오늘 환율
     환율은 매번 실행 시 CURRENT_FX_RATE로 직접 지정한다(이 API로는 공식환율 조회가
     안 되므로, 결산 시점에 형님이 그날 환율을 알려주면 그 값을 넣는다).

2) 계좌별 지표 — 통화를 억지로 통일하지 않고, 원래 찍히는 통화 그대로 둔다
     매매손익   (USD)  해외주식 실현손익 조회(CAZCQ00300)의 Out2.AstkBnsplAmt 합산
     배당       (USD)  배당금입금 FcurrAmt - 배당세출금 FcurrAmt (세후 순액)
     RP이자     (원화)  RP 관련 세금(Ictax+Ihtax) ÷ 0.154 로 역산한 세전이자
     세금(RP)   (원화)  외화환매채환매도 건의 Ictax+Ihtax 합계
     세금(배당) (USD)   배당세출금의 FcurrAmt 합계

3) 잔고(현금/RP/주식)는 아래 API 조합으로 산출(전부 실측 검증됨, 4계좌 오차 0.5% 이내):
     - 현금  : 잔고조회(CAZCQ00400, TrxTpCode=1)의 Out1[USD].AstkEstiDps
     - 주식  : 잔고조회(CAZCQ00400, TrxTpCode=2)의 Out2[].AstkEvalAmt 합산
     - RP    : 계좌거래내역(CDPCQ04700)에서
                 외화환매채조건부매수        (+)
                 외화환매채조건부매수취소    (-)   ← 902계좌 검증 중 발견, 반드시 반영
                 외화환매채환매도            (-)
               를 시간순 누적

4) 증분 갱신 — 매번 시작일부터 전체를 다시 긁지 않는다.
     report_data/state.json 에 계좌별 "마지막 조회일"과 누적값을 저장해두고,
     다음 실행부터는 (마지막 조회일 다음날) ~ 오늘까지만 API로 새로 가져와서
     누적값에 더한다. 최초 1회만 INITIAL_START_DATE(5/4)부터 전부 조회한다.

=== 사용법 ===
    python3 report.py                     # 오늘 환율 물어봄, 증분 갱신 후 요약 출력
    python3 report.py --fx 1383.8         # 환율 직접 지정(비대화형, cron용)
    python3 report.py --reset             # state.json 삭제하고 처음부터 다시 집계
"""
from __future__ import annotations
import os, sys, json, time, argparse
from datetime import datetime, date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from dotenv import load_dotenv
load_dotenv(os.path.join(HERE, ".env"))

import requests
import db_client as C

# ── 설정 ──────────────────────────────────────────────────────────────
REPORT_ACCOUNTS = ["JYN901", "JYN902", "JYN903", "JYN904"]
INITIAL_START_DATE = "20260504"   # 최초 1회만 쓰이는 시작일(decide.py의 start_date와 무관)

DATA_DIR = os.path.join(HERE, "report_data")
os.makedirs(DATA_DIR, exist_ok=True)
STATE_PATH = os.path.join(DATA_DIR, "state.json")
WEEKLY_PATH = os.path.join(DATA_DIR, "weekly_history.json")

HISTORY_PATH = "/api/v1/trading/kr-stock/inquiry/trading-history"
RLZPNL_PATH = "/api/v1/trading/overseas-stock/inquiry/day-rlzpnl"

RP_BUY_NAME = "외화환매채조건부매수"
RP_BUY_CANCEL_NAME = "외화환매채조건부매수취소"
RP_SELL_NAME = "외화환매채환매도"
DIV_IN_NAME = "배당금입금(외화)"
DIV_TAX_NAME = "배당세출금(외화)"

REQUEST_DELAY = 0.55  # 초당 2건 제한(CDPCQ04700, CAZCQ00300 둘 다 동일 스펙) 대응


# ── state.json 입출력 ─────────────────────────────────────────────────
def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"accounts": {}}


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def default_account_state() -> dict:
    return {
        "last_query_date": None,       # YYYYMMDD, 이 날짜까지는 이미 반영됨
        "rp_balance_usd": 0.0,
        "trading_pnl_usd": 0.0,
        "dividend_net_usd": 0.0,
        "rp_tax_krw": 0.0,
        "dividend_tax_usd": 0.0,
    }


# ── API 호출 (db_client.py 미수정, 부품만 재사용) ─────────────────────
def fetch_trade_history(acc, qry_srt: str, qry_end: str) -> list:
    """계좌거래내역 조회(CDPCQ04700), 연속조회 전부 수집."""
    all_rows = []
    cont_yn, cont_key = "N", ""
    for _ in range(60):
        time.sleep(REQUEST_DELAY)
        headers = C._headers(acc)
        headers["cont_yn"] = cont_yn
        headers["cont_key"] = cont_key
        body = {"In": {"QryTp": "0", "QrySrtDt": qry_srt, "QryEndDt": qry_end,
                       "SrtNo": 0, "IsuNo": ""}}
        resp = requests.post(C.BASE_URL + HISTORY_PATH, headers=headers, json=body, timeout=15)
        if resp.status_code != 200:
            raise C.DBSecError(f"[{acc.id}] 거래내역 조회 실패: {resp.status_code} {resp.text[:300]}")
        data = resp.json()
        rows = data.get("Out1") or []
        all_rows.extend(rows)
        cont_yn = resp.headers.get("cont_yn") or resp.headers.get("Cont_yn") or "N"
        cont_key = resp.headers.get("cont_key") or resp.headers.get("Cont_key") or ""
        if cont_yn != "Y" or not cont_key:
            break
    return all_rows


def fetch_realized_pnl_usd(acc, qry_srt: str, qry_end: str) -> float:
    """해외주식 실현손익 조회(CAZCQ00300) — Out2.AstkBnsplAmt(USD) 전체 합산."""
    total = 0.0
    body_in = {
        "TrxTpCode": "2", "QrySrtDt": qry_srt, "QryEndDt": qry_end,
        "AstkIsuNo": "", "WonFcurrTpCode": "2", "EvrprcYn": "Y", "DpntBalTpCode": "0",
    }
    cont_yn, cont_key = "N", ""
    for _ in range(60):
        time.sleep(REQUEST_DELAY)
        headers = C._headers(acc)
        headers["cont_yn"] = cont_yn
        headers["cont_key"] = cont_key
        resp = requests.post(C.BASE_URL + RLZPNL_PATH, headers=headers, json={"In": body_in}, timeout=15)
        if resp.status_code != 200:
            raise C.DBSecError(f"[{acc.id}] 실현손익 조회 실패: {resp.status_code} {resp.text[:300]}")
        data = resp.json()
        for r in (data.get("Out2") or []):
            total += float(r.get("AstkBnsplAmt") or 0)
        cont_yn = resp.headers.get("cont_yn") or resp.headers.get("Cont_yn") or "N"
        cont_key = resp.headers.get("cont_key") or resp.headers.get("Cont_key") or ""
        if cont_yn != "Y" or not cont_key:
            break
    return total


def get_usd_cash(acc) -> float:
    """TrxTpCode=1(외화잔고)의 AstkEstiDps(USD) — [1001]화면 "외화예수금"과 실측 일치 검증됨."""
    body = {"In": {"WonFcurrTpCode": "2", "TrxTpCode": "1", "CmsnTpCode": "2", "DpntBalTpCode": "1"}}
    resp = requests.post(C.BASE_URL + C.BALANCE_PATH, headers=C._headers(acc), json=body, timeout=15)
    if resp.status_code != 200:
        return 0.0
    data = resp.json()
    for row in data.get("Out1") or []:
        if row.get("CrcyCode") == "USD":
            return float(row.get("AstkEstiDps") or 0)
    return 0.0


def get_stock_eval_usd(acc) -> float:
    """잔고조회(TrxTpCode=2)의 Out2[].AstkEvalAmt 합산 — 보유주식 평가액(USD).
    rsp_cd 2679("조회내역이 없습니다")는 보유 0주의 정상 응답으로 처리."""
    try:
        bal = C.get_balance(acc)
    except C.DBSecError:
        bal = {}
    out2 = bal.get("Out2") or []
    return sum(float(r.get("AstkEvalAmt") or 0) for r in out2)


# ── 증분 계산 로직 ─────────────────────────────────────────────────────
def compute_deltas_from_rows(rows: list) -> dict:
    """새로 받아온 거래내역(rows)에서 이번 구간의 증분값을 계산."""
    rp_delta = 0.0
    rp_tax_krw = 0.0
    div_net_usd = 0.0
    div_tax_usd = 0.0

    for r in rows:
        smry = r.get("SmryNm", "")
        if smry == RP_BUY_NAME:
            rp_delta += float(r.get("FcurrAmt") or 0)
        elif smry == RP_BUY_CANCEL_NAME:
            rp_delta -= float(r.get("FcurrAmt") or 0)
        elif smry == RP_SELL_NAME:
            rp_delta -= float(r.get("FcurrAmt") or 0)
            rp_tax_krw += float(r.get("Ictax") or 0) + float(r.get("Ihtax") or 0)
        elif smry == DIV_IN_NAME:
            div_net_usd += float(r.get("FcurrAmt") or 0)
        elif smry == DIV_TAX_NAME:
            amt = float(r.get("FcurrAmt") or 0)
            div_net_usd -= amt
            div_tax_usd += amt

    return dict(rp_delta=rp_delta, rp_tax_krw=rp_tax_krw,
                div_net_usd=div_net_usd, div_tax_usd=div_tax_usd)


def next_query_range(acc_state: dict) -> tuple[str, str] | None:
    """이번에 새로 조회할 (시작일, 종료일). 더 조회할 게 없으면 None."""
    today_str = date.today().strftime("%Y%m%d")
    last = acc_state.get("last_query_date")
    if last is None:
        return INITIAL_START_DATE, today_str
    if last >= today_str:
        return None  # 오늘 이미 반영됨
    next_day = (datetime.strptime(last, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    return next_day, today_str


def update_account(acc, acc_state: dict) -> dict:
    """계좌 하나를 증분 갱신. acc_state를 in-place로 갱신하고 반환."""
    rng = next_query_range(acc_state)
    today_str = date.today().strftime("%Y%m%d")

    if rng is not None:
        qry_srt, qry_end = rng
        rows = fetch_trade_history(acc, qry_srt, qry_end)
        deltas = compute_deltas_from_rows(rows)

        acc_state["rp_balance_usd"] = acc_state.get("rp_balance_usd", 0.0) + deltas["rp_delta"]
        acc_state["rp_tax_krw"] = acc_state.get("rp_tax_krw", 0.0) + deltas["rp_tax_krw"]
        acc_state["dividend_net_usd"] = acc_state.get("dividend_net_usd", 0.0) + deltas["div_net_usd"]
        acc_state["dividend_tax_usd"] = acc_state.get("dividend_tax_usd", 0.0) + deltas["div_tax_usd"]

        # 매매손익은 증분누적 대신, 시작일~오늘 누계를 실현손익 API로 매번 다시 받는다
        # (해당 API가 이미 자체적으로 기간합계를 정확히 계산해주므로, 구간을 나눠 더하는 것보다
        #  전체기간을 한 번에 물어보는 쪽이 더 정확하고 이 API 자체도 빠른 편이다)
        acc_state["trading_pnl_usd"] = fetch_realized_pnl_usd(acc, INITIAL_START_DATE, today_str)

        acc_state["last_query_date"] = today_str

    return acc_state


def account_snapshot(acc, acc_state: dict) -> dict:
    """오늘 시점 스냅샷(잔고 실측 + 누적 지표)을 합쳐 계좌 요약을 만든다."""
    cash = get_usd_cash(acc)
    stock_eval = get_stock_eval_usd(acc)
    rp = acc_state.get("rp_balance_usd", 0.0)
    total_usd = cash + stock_eval + rp

    rp_tax_krw = acc_state.get("rp_tax_krw", 0.0)
    rp_interest_krw = rp_tax_krw / 0.154 if rp_tax_krw else 0.0

    return dict(
        id=acc.id, label=acc.label,
        cash_usd=cash, stock_eval_usd=stock_eval, rp_balance_usd=rp,
        total_usd=total_usd,
        trading_pnl_usd=acc_state.get("trading_pnl_usd", 0.0),
        dividend_net_usd=acc_state.get("dividend_net_usd", 0.0),
        dividend_tax_usd=acc_state.get("dividend_tax_usd", 0.0),
        rp_interest_krw=rp_interest_krw,
        rp_tax_krw=rp_tax_krw,
    )


# ── 메인 ───────────────────────────────────────────────────────────────
def run(fx_rate: float) -> dict:
    state = load_state()
    accounts = {a.id: a for a in C.load_accounts() if a.id in REPORT_ACCOUNTS}

    summaries = []
    for aid in REPORT_ACCOUNTS:
        if aid not in accounts:
            print(f"[경고] {aid} 가 accounts.yaml에 없어 건너뜁니다.")
            continue
        acc = accounts[aid]
        acc_state = state["accounts"].setdefault(aid, default_account_state())
        try:
            update_account(acc, acc_state)
            summaries.append(account_snapshot(acc, acc_state))
        except C.DBSecError as e:
            print(f"[{aid}] 갱신 실패: {e}")

    save_state(state)

    total_assets_usd = sum(s["total_usd"] for s in summaries)
    total_assets_krw = total_assets_usd * fx_rate

    today_str = date.today().strftime("%Y-%m-%d")
    week_record = {
        "date": today_str,
        "fx_rate": fx_rate,
        "total_assets_usd": total_assets_usd,
        "total_assets_krw": total_assets_krw,
        "accounts": summaries,
    }
    weekly = []
    if os.path.exists(WEEKLY_PATH):
        with open(WEEKLY_PATH, encoding="utf-8") as f:
            weekly = json.load(f)
    weekly.append(week_record)
    with open(WEEKLY_PATH, "w", encoding="utf-8") as f:
        json.dump(weekly, f, ensure_ascii=False, indent=2)

    return week_record


def print_summary(record: dict) -> None:
    print(f"\n{'='*90}")
    print(f"결산일: {record['date']}   적용환율: {record['fx_rate']:,}원")
    print(f"{'='*90}")
    print(f"{'계좌':10} {'현금(USD)':>12} {'RP(USD)':>14} {'주식(USD)':>12} "
          f"{'매매손익(USD)':>14} {'배당순액(USD)':>13} {'RP이자(원)':>13} {'RP세금(원)':>12}")
    for s in record["accounts"]:
        print(f"{s['label']:10} {s['cash_usd']:12,.2f} {s['rp_balance_usd']:14,.2f} "
              f"{s['stock_eval_usd']:12,.2f} {s['trading_pnl_usd']:14,.2f} "
              f"{s['dividend_net_usd']:13,.2f} {s['rp_interest_krw']:13,.0f} {s['rp_tax_krw']:12,.0f}")
    print(f"{'-'*90}")
    print(f"전체 총자산: ${record['total_assets_usd']:,.2f}  =  {record['total_assets_krw']:,.0f}원")
    print(f"{'='*90}\n")


def main():
    parser = argparse.ArgumentParser(description="법인 4계좌 결산 리포트")
    parser.add_argument("--fx", type=float, default=None, help="오늘 적용 환율(원/달러), 예: 1383.8")
    parser.add_argument("--reset", action="store_true", help="state.json을 지우고 처음부터 다시 집계")
    args = parser.parse_args()

    if args.reset and os.path.exists(STATE_PATH):
        os.remove(STATE_PATH)
        print("state.json 삭제됨 — 다음 실행 시 5/4부터 전체 재집계합니다.")

    fx_rate = args.fx
    if fx_rate is None:
        try:
            fx_rate = float(input("오늘 적용할 원/달러 환율을 입력하세요 (예: 1383.8): ").strip())
        except (ValueError, EOFError):
            print("환율 입력이 없어 종료합니다. --fx 옵션으로 다시 실행해주세요.")
            return

    record = run(fx_rate)
    print_summary(record)


if __name__ == "__main__":
    main()
