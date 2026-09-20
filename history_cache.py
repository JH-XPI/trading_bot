# -*- coding: utf-8 -*-
"""DB증권 조회 결과 저장소: 원본 이력을 JSON으로 저장해 두고, 최근 구간만 API로 갱신한다.

- 평소: 마지막 조회일 OVERLAP_DAYS(14일) 전부터만 API 조회 -> 저장된 행 중 그 구간을 교체
- 전체 초기화: `python3 history_cache.py --reset` (다음 실행이 전체 재조회) 또는 `python3 pipeline.py --full`
- 저장소가 없거나 깨졌으면 자동으로 전체 조회
- 안전망: FULL_REFRESH_DAYS(기본 90일)마다 전체를 다시 받는다 (0이면 끔)
"""
import os, sys, json, time, glob
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "report_data", "history")
OVERLAP_DAYS = 14
FULL_REFRESH_DAYS = 90
os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)


def _path(kind, acc_id):
    return os.path.join(CACHE_DIR, f"{kind}_{acc_id}.json")


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _plan(cache, init_start):
    """(조회 시작일, 전체조회 여부)"""
    if os.environ.get("FORCE_FULL") == "1" or not cache or "fetched_to" not in cache or "full_at" not in cache:
        return init_start, True
    if FULL_REFRESH_DAYS > 0 and (datetime.now() - datetime.fromtimestamp(cache["full_at"])).days >= FULL_REFRESH_DAYS:
        return init_start, True
    start = datetime.strptime(cache["fetched_to"], "%Y%m%d") - timedelta(days=OVERLAP_DAYS)
    return max(start.strftime("%Y%m%d"), init_start), False


def cached_rows(kind, acc, fetch_fn, date_field, init_start, end):
    """fetch_fn(acc, 시작일, 종료일) -> 행 리스트. date_field 기준으로 재조회 구간만 교체."""
    path = _path(kind, acc.id)
    cache = _load(path)
    start, full = _plan(cache, init_start)
    new_rows = fetch_fn(acc, start, end)
    if full:
        rows, full_at = new_rows, time.time()
    else:
        keep = [r for r in cache["rows"] if (r.get(date_field) or "") < start]
        rows, full_at = keep + new_rows, cache["full_at"]
    _save(path, {"fetched_to": end, "full_at": full_at, "rows": rows})
    print(f"[history] {acc.id} {kind}: {'전체' if full else '증분 ' + start + '~'} 신규 {len(new_rows)}건 / 총 {len(rows)}건")
    return rows


def cached_fx(acc, fetch_fn, init_start, end):
    """fetch_fn(acc, 시작일, 종료일) -> {YYYYMMDD: 환율}"""
    path = _path("fx", acc.id)
    cache = _load(path)
    start, full = _plan(cache, init_start)
    new = fetch_fn(acc, start, end)
    if full:
        rates, full_at = new, time.time()
    else:
        rates, full_at = dict(cache["rates"]), cache["full_at"]
        rates.update(new)
    _save(path, {"fetched_to": end, "full_at": full_at, "rates": rates})
    print(f"[history] {acc.id} fx: {'전체' if full else '증분 ' + start + '~'} 신규 {len(new)}일 / 총 {len(rates)}일")
    return rates


def reset():
    n = 0
    for f in glob.glob(os.path.join(CACHE_DIR, "*.json")):
        os.remove(f)
        n += 1
    print(f"저장소 초기화: {n}개 파일 삭제 (다음 실행이 전체 재조회)")


if __name__ == "__main__":
    if "--reset" in sys.argv:
        reset()
    else:
        print("사용법: python3 history_cache.py --reset")
