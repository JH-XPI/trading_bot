# -*- coding: utf-8 -*-
"""
GitHub Actions에서 매일 실행되는 무인 갱신 스크립트.
  1) 서정호 형님의 구글 드라이브 "SOXL시세" 시트에서 최신 일봉을 받아 data/soxl_us_d.csv 에 병합
  2) 상태판 재계산 -> out/status.html, docs/index.html 로 저장
  3) (워크플로에서) 변경사항을 커밋/푸시

실행: python daily_update.py

주의: "SOXL시세" 구글시트를 "링크가 있는 모든 사용자 - 뷰어"로 공유해둬야
      GitHub Actions(구글 계정과 무관한 서버)가 이 URL로 읽을 수 있습니다.
"""
import os, sys, io, re, shutil
import requests
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# "SOXL시세" 구글 시트 (Date,Open,High,Low,Close,Volume 형식으로 자동 갱신되는 시트)
SHEET_ID = "1M_qAf9Ks5whb9pSVs1A_sPLEUX4J70V2jw98RLMEpR4"
SHEET_NAME = "시트1"
SHEET_CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/csv,text/plain,*/*",
}

_DATE_RE = re.compile(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})")


def _parse_kr_date(s):
    """'2026. 9. 14 오후 4:00:00' 같은 구글시트 날짜 문자열 -> 'YYYY-MM-DD'.
    (연도가 맨 앞인 형식을 우선 시도하고, 안 맞으면 pandas 추정으로 폴백)"""
    s = str(s).strip()
    m = _DATE_RE.match(s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        if y > 1900 and 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    try:
        return pd.to_datetime(s).strftime("%Y-%m-%d")
    except Exception:
        return None


def fetch_latest_csv():
    """구글 드라이브 'SOXL시세' 시트에서 최신 일봉을 받아 stooq와 동일한
    Date,Open,High,Low,Close,Volume 형식의 DataFrame으로 변환.
    (시트가 링크 공유로 열려 있어야 함 — 비공개면 401/302 로그인 페이지가 돌아옴)
    """
    r = requests.get(
        SHEET_CSV_URL,
        headers=HEADERS,
        params={"tqx": "out:csv", "sheet": SHEET_NAME},
        timeout=30,
    )
    r.raise_for_status()
    text = r.text.strip()
    if not text or "<html" in text.lower() or "accounts.google.com" in text.lower():
        raise RuntimeError(
            f"구글시트 응답이 비정상입니다 (링크 공유 설정을 확인하세요): {text[:300]}"
        )

    df = pd.read_csv(io.StringIO(text))
    df.columns = [str(c).strip() for c in df.columns]
    required = {"Date", "Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(set(df.columns)):
        raise RuntimeError(f"예상치 못한 시트 형식입니다: {df.columns.tolist()}")

    df = df[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
    df["Date"] = df["Date"].map(_parse_kr_date)
    df = df.dropna(subset=["Date", "Open", "High", "Low", "Close", "Volume"]).reset_index(drop=True)
    if df.empty:
        raise RuntimeError("구글시트에서 유효한 일봉을 받지 못했습니다.")
    df["Volume"] = df["Volume"].astype(int)
    return df


def main():
    import data as D
    import status as S

    before = D.load_real()
    last_before = before["date"].max()

    fresh = fetch_latest_csv()
    tmp_path = os.path.join(HERE, "_sheet_fresh.csv")
    fresh.to_csv(tmp_path, index=False)
    try:
        lo, hi, n = D.update_real(tmp_path)
        print(f"[drive] 병합 완료: {lo} ~ {hi} ({n}행)")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    after = D.load_real()
    last_after = after["date"].max()
    if last_after <= last_before:
        print(f"[skip] 새 데이터 없음 (최신 보유일: {last_before.date()})")
    else:
        print(f"[update] {last_before.date()} -> {last_after.date()} 반영")

    st = S.compute()
    out_dir = os.path.join(HERE, "out")
    os.makedirs(out_dir, exist_ok=True)
    html = S.render(st)
    out_path = os.path.join(out_dir, "status.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    docs_dir = os.path.join(HERE, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    shutil.copyfile(out_path, os.path.join(docs_dir, "index.html"))

    # PUBLIC_HTML_DIR 환경변수가 설정돼 있으면 (예: Cloudways의 public_html 절대경로)
    # 거기에도 바로 복사한다. 서버에서 cron으로 돌릴 때 이 값을 지정해두면
    # git commit/push 없이 즉시 웹에 반영된다.
    public_html_dir = os.environ.get("PUBLIC_HTML_DIR")
    if public_html_dir:
        os.makedirs(public_html_dir, exist_ok=True)
        dest = os.path.join(public_html_dir, "index.html")
        shutil.copyfile(out_path, dest)
        print(f"[deploy] {dest} 로 배포 완료")

    print(f"기준일 {st['asof']} | 모드 {st['mode']} | 투자비중 {st['invested']:.0%} | "
          f"주간RSI {st['wRsi'] and round(st['wRsi'],1)} | 빨간불 {st['red']}")


if __name__ == "__main__":
    main()
