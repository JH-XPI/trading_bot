# -*- coding: utf-8 -*-
"""
DB증권(DB금융투자) Open API 클라이언트 — 계좌/전략별 설정(accounts.yaml)을 읽어
토큰 발급, 해외주식 시세조회, 해외주식 주문을 수행한다.

안전장치:
  - accounts.yaml의 enabled=false 인 계좌는 주문을 절대 실제로 전송하지 않는다
    (계산 결과만 로그로 남기고 dry-run 표시). enabled=true 로 바꿔야 실주문이 나간다.
  - mode=demo(모의투자) / production(실전) 은 계좌 설정에서 명시적으로 지정해야 하며
    기본값은 demo. 반드시 demo에서 충분히 검증 후에만 production으로 바꿀 것.
  - 계좌 자격증명(API 키/시크릿/계좌번호)은 이 코드나 accounts.yaml에 직접 적지 않고
    전부 환경변수(.env)에서 읽는다.

참고(DB증권 공식 오픈API SDK 리서치 기준):
  - 토큰 발급: POST {base_url}/oauth2/token, x-www-form-urlencoded,
      body: grant_type=client_credentials, appkey=..., appsecretkey=..., scope=oob
      응답: access_token, token_type, expires_in(초)
  - 인증 헤더: Authorization: Bearer <token>, Content-Type: application/json
  - 해외주식 현재가조회: POST {base_url}/api/v1/quote/overseas-stock/inquiry/price
      body.In: {InputIscd1: 종목코드, InputCondMrktDivCode: FY/FN/FA}
  - 해외주식 주문: POST {base_url}/api/v1/trading/overseas-stock/order
      body.In: {AstkIsuNo, AstkBnsTpCode(1매도/2매수), AstkOrdprcPtnCode(1지정가/2시장가/5LOC/6MOC 등),
                AstkOrdCndiTpCode(1FAS/2IOC/3FOK), AstkOrdQty, AstkOrdPrc(시장가면 0),
                OrdTrdTpCode(0주문/1정정/2취소), OrgOrdNo(신규주문이면 0)}
      -- 계좌번호 필드는 공식 예제에 명시적으로 나타나지 않았음(별도 확인 필요).
         첫 실제 호출 시 에러 메시지로 필수 필드를 다시 확인할 것.
  - base_url은 실전/모의 동일하며, "앱키 쌍"만 다르다(모의: vtl_*, 운영: prd_* 접두어 관례).
    본 코드는 accounts.yaml에서 명시적으로 별도 env var(app_key_env/app_secret_env)를
    지정하도록 해 실전/모의 키를 계좌 설정 단위로 명확히 분리한다.
"""
from __future__ import annotations
import os, json, time, logging
from dataclasses import dataclass
from typing import Optional
import requests
import yaml

BASE_URL = "https://openapi.dbsec.co.kr:8443"
TOKEN_PATH = "/oauth2/token"
QUOTE_PATH = "/api/v1/quote/overseas-stock/inquiry/price"
ORDER_PATH = "/api/v1/trading/overseas-stock/order"
BALANCE_PATH = "/api/v1/trading/overseas-stock/inquiry/balance-margin"

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "trading.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("dbsec")


class DBSecError(RuntimeError):
    pass


@dataclass
class Account:
    id: str
    label: str
    broker: str
    strategy: str
    symbol: str
    market: str
    start_date: str     # 이 계좌로 전략을 실제 시작한 날짜 'YYYY-MM-DD' (sniper_lab project_from 기준일)
    start_cap: float    # 그 시점 투입 원금(USD) — 계좌마다 다를 수 있음
    mode: str          # demo | production
    enabled: bool       # False면 실주문 전송 안 함(dry-run)
    account_no: str
    app_key: str
    app_secret: str


def _env(name: str) -> str:
    v = os.environ.get(name, "")
    if not v:
        raise DBSecError(f"환경변수 {name} 가 설정되어 있지 않습니다 (.env 확인).")
    return v


def load_accounts(path: str = None) -> list[Account]:
    path = path or os.path.join(HERE, "accounts.yaml")
    if not os.path.exists(path):
        raise DBSecError(f"{path} 가 없습니다. accounts.example.yaml 을 복사해서 만드세요.")
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    out = []
    for a in cfg.get("accounts", []):
        if "start_date" not in a or "start_cap" not in a:
            raise DBSecError(
                f"계좌 '{a.get('id')}' 에 start_date/start_cap 이 없습니다 — 이 계좌를 실제로 시작한 "
                f"날짜와 투입 원금을 accounts.yaml에 명시하세요 (계좌마다 다를 수 있어 기본값을 두지 않음)."
            )
        out.append(Account(
            id=a["id"], label=a.get("label", a["id"]), broker=a.get("broker", "dbsec"),
            strategy=a["strategy"], symbol=a.get("symbol", "SOXL"), market=a.get("market", "FN"),
            start_date=str(a["start_date"]), start_cap=float(a["start_cap"]),
            mode=a.get("mode", "demo"), enabled=bool(a.get("enabled", False)),
            account_no=_env(a["account_no_env"]),
            app_key=_env(a["app_key_env"]),
            app_secret=_env(a["app_secret_env"]),
        ))
    return out


# ── 토큰 캐시 (계좌별로 별도 파일, 24시간 유효) ──
def _token_cache_path(acc: Account) -> str:
    return os.path.join(HERE, f".token_{acc.id}_{acc.mode}.json")


def get_token(acc: Account, force: bool = False) -> str:
    cache_path = _token_cache_path(acc)
    if not force and os.path.exists(cache_path):
        try:
            data = json.load(open(cache_path, encoding="utf-8"))
            if data.get("expires_at", 0) > time.time() + 60:
                return data["access_token"]
        except Exception:
            pass

    resp = requests.post(
        BASE_URL + TOKEN_PATH,
        headers={"content-type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "appkey": acc.app_key,
            "appsecretkey": acc.app_secret,
            "scope": "oob",
        },
        timeout=10,
    )
    if resp.status_code != 200:
        raise DBSecError(f"[{acc.id}] 토큰 발급 실패: {resp.status_code} {resp.text[:300]}")
    data = resp.json()
    access_token = data.get("access_token")
    if not access_token:
        raise DBSecError(f"[{acc.id}] 토큰 응답에 access_token 없음: {data}")
    expires_in = int(data.get("expires_in", 86400))
    json.dump(
        {"access_token": access_token, "expires_at": time.time() + expires_in, "mode": acc.mode},
        open(cache_path, "w", encoding="utf-8"),
    )
    log.info(f"[{acc.id}] 토큰 발급 완료 (mode={acc.mode}, {expires_in}초 유효)")
    return access_token


def _headers(acc: Account) -> dict:
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {get_token(acc)}",
        "cont_yn": "N",   # 연속조회 여부(공식 예제 기준 필요 헤더) - 첫 조회는 N
        "cont_key": "",   # 연속조회 키 - 첫 조회는 빈 값
    }


def get_quote(acc: Account) -> dict:
    """해외주식 현재가 조회. 반환: DB증권 응답 그대로(dict). 실패 시 DBSecError."""
    body = {"In": {"InputIscd1": acc.symbol, "InputCondMrktDivCode": acc.market}}
    resp = requests.post(BASE_URL + QUOTE_PATH, headers=_headers(acc), json=body, timeout=15)
    if resp.status_code != 200:
        raise DBSecError(f"[{acc.id}] 시세조회 실패: {resp.status_code} {resp.text[:300]}")
    data = resp.json()
    log.info(f"[{acc.id}] {acc.symbol} 시세조회 응답: {json.dumps(data, ensure_ascii=False)[:500]}")
    return data


def get_balance(acc: Account) -> dict:
    """해외주식 잔고/증거금 조회(TR: CAZCQ00400). 반환: DB증권 응답 그대로(dict, Out/Out2/Out3 포함).
    계좌번호는 별도 필드로 안 넘긴다 — 토큰(appkey)에 이미 계좌가 귀속되어 있음(공식 예제 기준)."""
    body = {"In": {
        "WonFcurrTpCode": "2",   # 1:원화잔고 2:외화잔고 — 외화 기준으로 조회
        "TrxTpCode": "2",        # 1:외화잔고 2:주식잔고상세 3:주식잔고(국가별) 9:당일실현손익
        "CmsnTpCode": "2",       # 0:제비용 미포함 1:매수제비용만 2:매수+매도 제비용 포함
        "DpntBalTpCode": "1",    # 0:전체 1:일반 2:소수점 — 일반(정수주) 잔고만
    }}
    resp = requests.post(BASE_URL + BALANCE_PATH, headers=_headers(acc), json=body, timeout=15)
    if resp.status_code != 200:
        raise DBSecError(f"[{acc.id}] 잔고조회 실패: {resp.status_code} {resp.text[:300]}")
    data = resp.json()
    log.info(f"[{acc.id}] 잔고조회 응답: {json.dumps(data, ensure_ascii=False)[:800]}")
    return data


def get_holding_qty(acc: Account) -> int:
    """get_balance() 응답(Out2: 종목별 보유내역)에서 acc.symbol 의 "지금 추가로 매도 주문 넣어도
    되는 수량"을 합산. AstkOrdAbleQty(주문가능수량)를 쓴다 — 이미 다른(수동 포함) 주문에 묶여있는
    수량은 제외되므로, 이걸 기준으로 해야 자동매매가 이미 나가있는 주문과 중복으로 매도 주문을
    또 넣는 걸 막을 수 있다. (총 보유수량은 AstkExecBaseQty이지만, 안전장치 목적엔 부적합 —
    이미 매도 주문이 걸려있는 물량까지 "또 팔아도 되는 것"으로 착각하게 됨)"""
    data = get_balance(acc)
    rows = data.get("Out2") or []
    qty = 0
    for r in rows:
        sym = str(r.get("SymCode") or r.get("AstkIsuNo") or "").strip().upper()
        if sym == acc.symbol.upper() or sym == f"{acc.symbol.upper()}.US":
            try:
                able_qty = float(r.get("AstkOrdAbleQty") or 0)
                exec_qty = float(r.get("AstkExecBaseQty") or 0)
                if exec_qty != able_qty:
                    log.info(f"[{acc.id}] {acc.symbol} 참고: 총 보유={exec_qty:.0f}주, "
                             f"주문가능(=매도가능)={able_qty:.0f}주 — 차이는 이미 걸려있는 주문 등으로 "
                             f"묶여있는 물량. 안전장치는 주문가능 기준으로 판단.")
                qty += int(able_qty)
            except (TypeError, ValueError):
                pass
    return qty


def place_order(acc: Account, side: str, qty: int, price: float = 0, order_type: str = "market") -> dict:
    """
    side: 'buy' | 'sell'
    price: 지정가일 때 가격, 시장가(market)면 0
    order_type: 'market'(시장가) | 'limit'(지정가) | 'loc'(장마감지정가) | 'moc'(장마감시장가, 매도전용)

    acc.enabled == False 이면 실제 전송하지 않고 dry-run 결과만 반환한다.
    """
    bns = {"buy": "2", "sell": "1"}[side]
    ptn = {"market": "2", "limit": "1", "loc": "5", "moc": "6"}[order_type]
    body_in = {
        "AstkIsuNo": acc.symbol,
        "AstkBnsTpCode": bns,
        "AstkOrdprcPtnCode": ptn,
        "AstkOrdCndiTpCode": "1",  # FAS(일반)
        "AstkOrdQty": int(qty),
        "AstkOrdPrc": 0 if order_type == "market" else price,
        "OrdTrdTpCode": "0",  # 신규주문
        "OrgOrdNo": 0,
    }

    if not acc.enabled:
        log.warning(f"[{acc.id}] DRY-RUN (enabled=false, 실주문 전송 안 함): {side} {qty}주 {acc.symbol} "
                    f"({order_type}, price={price}) — In={body_in}")
        return {"dry_run": True, "ok": None, "In": body_in}

    if acc.mode != "demo":
        log.warning(f"[{acc.id}] !!! 실전투자(production) 주문 전송 !!! {side} {qty}주 {acc.symbol}")

    resp = requests.post(BASE_URL + ORDER_PATH, headers=_headers(acc), json={"In": body_in}, timeout=15)
    http_ok = resp.status_code == 200
    data = _safe_json(resp)
    # 주의: DB증권 API는 주문이 거부돼도 HTTP 200을 반환하고, 실패 사유는 응답 본문의
    # rsp_cd/rsp_msg에만 담는다. HTTP 상태코드만으로 "성공"을 판단하면 안 되고, 반드시
    # rsp_cd == "00000" 인지까지 확인해야 실제 체결/접수 성공 여부를 알 수 있다.
    rsp_cd = data.get("rsp_cd") if isinstance(data, dict) else None
    biz_ok = http_ok and rsp_cd == "00000"
    data["ok"] = biz_ok
    status_kr = "성공" if biz_ok else "실패"
    log.info(f"[{acc.id}] 주문 전송 ({status_kr}): {side} {qty}주 {acc.symbol} "
             f"status={resp.status_code} rsp_cd={rsp_cd} resp={json.dumps(data, ensure_ascii=False)[:500]}")
    if not http_ok:
        raise DBSecError(f"[{acc.id}] 주문 전송 실패(HTTP): {resp.status_code} {resp.text[:300]}")
    if not biz_ok:
        log.warning(f"[{acc.id}] 주문 거부됨(브로커 응답): rsp_cd={rsp_cd} {data.get('rsp_msg')}")
    return data


def _safe_json(resp) -> dict:
    try:
        return resp.json()
    except Exception:
        return {"raw_text": resp.text[:500]}
