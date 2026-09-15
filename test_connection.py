# -*- coding: utf-8 -*-
"""
1단계 연결 테스트: 토큰 발급 + SOXL 시세조회만 한다 (주문 없음).
accounts.yaml에 등록된 모든 계좌에 대해 순서대로 실행한다.

실행: python3 test_connection.py
사전 준비: accounts.yaml, .env 파일이 이 폴더에 있어야 함 (각각 .example 파일 참고)
"""
import os, sys
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))
sys.path.insert(0, HERE)

import db_client as C


def main():
    accounts = C.load_accounts()
    if not accounts:
        print("accounts.yaml에 등록된 계좌가 없습니다.")
        return

    for acc in accounts:
        print(f"\n=== [{acc.id}] {acc.label} (mode={acc.mode}, enabled={acc.enabled}) ===")
        try:
            token = C.get_token(acc)
            print(f"  토큰 발급 OK ({token[:12]}...)")
        except C.DBSecError as e:
            print(f"  토큰 발급 실패: {e}")
            continue

        try:
            quote = C.get_quote(acc)
            out = quote.get("Out") or quote.get("out") or quote
            print(f"  {acc.symbol} 시세조회 OK: {out}")
        except C.DBSecError as e:
            print(f"  시세조회 실패: {e}")


if __name__ == "__main__":
    main()
