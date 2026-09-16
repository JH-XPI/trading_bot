# -*- coding: utf-8 -*-
"""
텔레그램으로 주문 실행 결과를 알린다. .env에 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID가
없으면(설정 전) 조용히 아무것도 안 하고 넘어간다 — 알림 설정이 필수는 아님.

chat_id 알아내는 법(설정 전 1회):
  1) 봇을 만들고 텔레그램에서 그 봇에게 아무 메시지나 먼저 보낸 뒤
  2) 브라우저로 https://api.telegram.org/bot<봇토큰>/getUpdates 접속
  3) 응답 JSON에서 "chat":{"id": 1234567890, ...} 의 그 숫자가 chat_id
"""
import os, logging
import requests

log = logging.getLogger("dbsec")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.info("[notify] TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID 미설정 — 알림 생략")
        return False
    try:
        resp = requests.post(
            TELEGRAM_API.format(token=token),
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if resp.status_code != 200:
            log.warning(f"[notify] 텔레그램 전송 실패: {resp.status_code} {resp.text[:200]}")
            return False
        return True
    except Exception as e:
        log.warning(f"[notify] 텔레그램 전송 중 오류: {e}")
        return False
