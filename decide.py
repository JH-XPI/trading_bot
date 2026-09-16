# -*- coding: utf-8 -*-
"""
2단계: DB증권 주문 판단.

계좌마다 시작일(start_date)/시작금액(start_cap)이 다를 수 있으므로, 정적 JSON을 미리
만들어두는 대신 sniper_lab(soxl_bot)의 엔진 코드를 그 자리에서 직접 재사용해서
계좌별로 각자의 project_from(reset_date, reset_cap)을 따로 계산한다. 로직이 soxl_bot
한 곳에만 있어서, 전략을 고치면 상태판과 trading_bot이 항상 같은 계산을 쓴다.

원칙(서정호 형님 확인, 2026-09-16):
  - 매수: sniper_lab 계산치(트리거가·배정금액)를 그대로 신뢰해서 LOC 매수 주문을 만든다.
  - 매도(LOC 매도 / MOC 강제청산): 반드시 DB증권 잔고조회로 실제 보유수량을 먼저 확인하고,
    이번 사이클 매도수량 합계가 실제 보유수량을 넘지 않을 때만 주문을 만든다. 넘으면
    (전략 계산과 실제 계좌가 어긋난 상태) 그 계좌의 매도는 전부 스킵하고 경고만 남긴다.
  - 계좌별 secret은 한 .env 파일 안에서 변수명으로만 구분(accounts.yaml의 *_env 필드).

실주문 여부는 항상 accounts.yaml의 enabled 값을 따른다 (false=dry-run, place_order()가 처리).

실행: python3 decide.py
사전 준비:
  - accounts.yaml(계좌마다 start_date/start_cap 필수), .env
  - 같은 서버에 soxl_bot이 최신 데이터로 이미 실행되어 있어야 함(data/soxl_us_d.csv가 최신)
    (경로가 다르면 환경변수 SOXL_BOT_DIR 으로 직접 지정 가능, 기본값 ~/soxl_bot)
"""
import os, sys, math

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from dotenv import load_dotenv
load_dotenv(os.path.join(HERE, ".env"))

import db_client as C
import notify as N

SOXL_BOT_DIR = os.environ.get("SOXL_BOT_DIR") or os.path.expanduser("~/soxl_bot")
if not os.path.isdir(SOXL_BOT_DIR):
    raise SystemExit(
        f"{SOXL_BOT_DIR} 를 찾을 수 없습니다. soxl_bot이 같은 서버의 다른 경로에 있다면 "
        f"환경변수 SOXL_BOT_DIR 로 지정하세요."
    )
sys.path.insert(0, SOXL_BOT_DIR)
import status as S           # noqa: E402  (soxl_bot의 상태판 엔진 — 동일 코드를 그대로 재사용)
import predict as PR         # noqa: E402
from engine import Engine, Params  # noqa: E402
import pandas as pd          # noqa: E402

FEE = 1.0004  # 수수료 반영 배수 (매수 예상수량 계산용, sniper_lab predict.describe_pending과 동일 로직)


def build_base():
    """전체 이력 df + $10,000 기준 실행의 mode 이력(tr) — 계좌별 재계산에 공통으로 쓰임
    (모드 판정은 투입자본 크기와 무관하므로 한 번만 계산해 모든 계좌에 재사용한다)."""
    df, log, src = S.build_input()
    eng = Engine(Params(fee=0.0004, decimals=2, cap=10000.0))
    res = eng.run(df)
    tr = pd.DataFrame(res.trace)
    tr["date"] = pd.to_datetime(tr["date"])
    tr = tr.set_index("date")
    return df, tr


def orders_for_account(df, tr, acc):
    """계좌 고유의 start_date/start_cap 기준으로 오늘의 목표 주문을 계산."""
    res = PR.project_from(df, tr, reset_date=acc.start_date, reset_cap=acc.start_cap)
    orders = []
    for o in res["pending"]:
        if o[0] == "b":
            _, alloc, trig, lab = o
            orders.append(dict(kind="매수", tier=lab, trig=float(trig), alloc=float(alloc), qty=None))
        elif o[0] == "t":
            _, qty, trig, L = o
            orders.append(dict(kind="LOC 매도", tier=L["tier"], trig=float(trig), alloc=None, qty=int(qty)))
        elif o[0] == "m":
            _, qty, _, L = o
            orders.append(dict(kind="MOC 청산", tier=L["tier"], trig=None, alloc=None, qty=int(qty)))
    mode = res["mode"]; lots = res["trace"][-1]["lots"]
    return dict(mode=mode, lots=lots, orders=orders)


def build_buy_orders(predict):
    """'매수' 목표 -> 주문 인자 리스트. 계산치(트리거가/배정금액)를 그대로 신뢰한다."""
    out = []
    for o in predict["orders"]:
        if o["kind"] != "매수":
            continue
        trig, alloc = o.get("trig"), o.get("alloc")
        if not trig or not alloc:
            continue
        qty = math.floor(alloc / (trig * FEE))
        if qty <= 0:
            continue
        out.append(dict(tier=o["tier"], side="buy", qty=qty, price=trig, order_type="loc"))
    return out


def build_sell_orders(predict, real_qty_held):
    """'LOC 매도'/'MOC 청산' 목표 -> 실제 보유수량과 대조 후 (주문 인자 리스트, 스킵사유 또는 None).
    합계가 실제 보유수량을 넘으면 전부 스킵(안전장치)."""
    sells = []
    for o in predict["orders"]:
        if o["kind"] == "LOC 매도":
            sells.append(dict(tier=o["tier"], side="sell", qty=o["qty"], price=o["trig"], order_type="loc"))
        elif o["kind"] == "MOC 청산":
            sells.append(dict(tier=o["tier"], side="sell", qty=o["qty"], price=0, order_type="moc"))

    total = sum(s["qty"] for s in sells)
    if total == 0:
        return [], None
    if total > real_qty_held:
        reason = (f"매도 전부 스킵: 계산된 매도수량 합계({total}주)가 실제 주문가능 잔고"
                  f"({real_qty_held}주)보다 많음 — 전략 계산과 실제 계좌가 어긋난 것으로 보여 보류")
        C.log.warning(f"[SAFETY] {reason}")
        return [], reason
    return sells, None


DATA_FRESHNESS_MARKER = os.path.join(HERE, "data", ".decide_last_seen")


def check_data_freshness(df) -> tuple:
    """오늘 daily_update.py가 실제로 새 데이터를 반영했는지 확인.
    df의 최신 날짜를, 이 스크립트가 직전 실행 때 본 최신 날짜와 비교한다 —
    두 번 연속 같은 날짜면(=최근 실행 이후 새 거래일 데이터가 한 번도 안 늘었으면)
    daily_update 갱신이 안 된 것으로 보고 오늘은 전체 주문을 건너뛴다.
    (주말/공휴일로 실제 새 데이터가 없는 날도 이 조건에 걸리지만, 그 경우 어차피
    새 종가 없이 주문을 내는 게 더 위험하므로 건너뛰는 게 안전한 쪽이다.)
    반환: (fresh: bool, latest_date_str: str, prev_seen_str: str or None)
    """
    latest = str(df["date"].max().date()) if hasattr(df["date"].max(), "date") else str(df["date"].max())
    os.makedirs(os.path.dirname(DATA_FRESHNESS_MARKER), exist_ok=True)
    prev = None
    if os.path.exists(DATA_FRESHNESS_MARKER):
        with open(DATA_FRESHNESS_MARKER) as f:
            prev = f.read().strip() or None
    fresh = (prev is None) or (latest != prev)
    with open(DATA_FRESHNESS_MARKER, "w") as f:
        f.write(latest)
    return fresh, latest, prev


def main():
    accounts = C.load_accounts()
    if not accounts:
        print("accounts.yaml에 등록된 계좌가 없습니다.")
        return

    df, tr = build_base()

    fresh, latest_date, prev_date = check_data_freshness(df)
    if not fresh:
        reason = (f"데이터 최신일이 지난 실행 때와 동일합니다({latest_date}) — "
                  f"daily_update.py 갱신이 오늘 반영되지 않은 것으로 보여, 오래된 데이터로 "
                  f"잘못된 주문이 나가는 걸 막기 위해 오늘은 모든 계좌의 주문을 전부 건너뜁니다.")
        print(f"[SAFETY] {reason}")
        C.log.warning(f"[SAFETY] {reason}")
        N.send_telegram(f"⚠️ SOXL Dual Sniper 자동주문 건너뜀\n\n{reason}\n"
                        f"(soxl_bot의 daily_update.py 로그를 확인해주세요)")
        return

    msg_lines = []  # 텔레그램으로 보낼 전체 요약

    for acc in accounts:
        tag = "실전" if acc.mode != "demo" else "모의"
        live = "실주문" if acc.enabled else "DRY-RUN"
        header = f"[{acc.label}] ({tag}/{live})"
        print(f"\n--- {header} 시작 {acc.start_date} ${acc.start_cap:,.0f} ---")
        acc_lines = [f"<b>{header}</b>"]

        predict = orders_for_account(df, tr, acc)
        print(f"  전략 모드: {predict['mode']}, 보유로트: {len(predict['lots'])}개")

        try:
            real_qty = C.get_holding_qty(acc)
            print(f"  실제 보유수량({acc.symbol}): {real_qty:,}주")
        except C.DBSecError as e:
            print(f"  잔고조회 실패: {e}")
            print("  -> 이 계좌는 이번 사이클 매도를 전부 건너뜁니다(잔고 확인 불가).")
            real_qty = 0
            acc_lines.append(f"⚠️ 잔고조회 실패({e}) — 매도 전부 건너뜀")

        buys = build_buy_orders(predict)
        sells, skip_reason = build_sell_orders(predict, real_qty)
        if skip_reason:
            acc_lines.append(f"⚠️ {skip_reason}")

        if not buys and not sells:
            print("  오늘 조건에 맞는 주문 없음")
            acc_lines.append("오늘 조건에 맞는 주문 없음")
        else:
            for o in buys + sells:
                side_kr = "매수" if o["side"] == "buy" else "매도"
                try:
                    result = C.place_order(acc, o["side"], o["qty"], price=o["price"], order_type=o["order_type"])
                except C.DBSecError as e:
                    print(f"  [{o['side']}/{o['order_type']}] {o['tier']} {o['qty']:,}주 @ {o['price']} -> 전송 실패: {e}")
                    acc_lines.append(f"❌ {side_kr} {o['tier']} {o['qty']:,}주 @ ${o['price']:.2f} — 전송 실패(HTTP): {e}")
                    continue

                print(f"  [{o['side']}/{o['order_type']}] {o['tier']} {o['qty']:,}주 @ {o['price']} -> {result}")
                if result.get("dry_run"):
                    acc_lines.append(f"🧪 {side_kr} {o['tier']} {o['qty']:,}주 @ ${o['price']:.2f} ({o['order_type']}) — DRY-RUN, 실전송 안 함")
                elif result.get("ok"):
                    acc_lines.append(f"✅ {side_kr} {o['tier']} {o['qty']:,}주 @ ${o['price']:.2f} ({o['order_type']}) — 주문 접수 성공")
                else:
                    acc_lines.append(f"❌ {side_kr} {o['tier']} {o['qty']:,}주 @ ${o['price']:.2f} ({o['order_type']}) — "
                                      f"주문 거부됨: {result.get('rsp_msg') or result.get('rsp_cd')}")

        msg_lines.append("\n".join(acc_lines))

    full_msg = "🎯 SOXL Dual Sniper 자동주문 결과\n\n" + "\n\n".join(msg_lines)
    print("\n[알림] 텔레그램 전송 시도...")
    ok = N.send_telegram(full_msg)
    print(f"[알림] {'전송 완료' if ok else '전송 생략/실패 (로그 확인)'}")


if __name__ == "__main__":
    main()
