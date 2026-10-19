import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import pandas as pd
import requests
from dotenv import load_dotenv


# ============================================================
# 1. 기본 설정
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, "../..", ".env"))

API_KEY = os.getenv("API_KEY_DATA_PORTAL")

if not API_KEY:
    raise RuntimeError("API_KEY_DATA_PORTAL을 .env에서 읽지 못했습니다.")

URL_ITEM = (
    "https://apis.data.go.kr/1690000/"
    "BidPblancInfoService/getDmstcCmpetBidPblancItem"
)

CLEAN_DIR = os.path.join(BASE_DIR, "../..", "data", "clean")
NOTICE_FILE = os.path.join(
    CLEAN_DIR, "bid_notice_clean_20210101_20251231.csv"
)
RAW_DIR = os.path.join(BASE_DIR, "../..", "data", "raw")
os.makedirs(RAW_DIR, exist_ok=True)

# API 호출 설정
CHUNK_SIZE = 1000
REQUEST_DELAY = 0.2
MAX_RETRIES = 3
RETRY_DELAY = 2

# 완료한 요청을 일정 개수마다 별도 파일에 저장합니다.
SAVE_EVERY = 100
PERIOD_TAG = "20210101_20251231"

CHECKPOINT_DIR = os.path.join(RAW_DIR, f"bid_item_checkpoints_{PERIOD_TAG}")
os.makedirs(CHECKPOINT_DIR, exist_ok=True)

FINAL_DATA_FILE = os.path.join(RAW_DIR, f"bid_item_{PERIOD_TAG}.csv")
FINAL_LOG_FILE = os.path.join(
    RAW_DIR, f"bid_item_collection_log_{PERIOD_TAG}.csv"
)

SESSION = requests.Session()

REQUEST_COLUMNS = ["demandYear", "dcsNo", "orntCode"]
# API 명세의 응답 컬럼을 원문 그대로 보존합니다.
ITEM_COLUMNS = [
    "dmstItnb", "prcmRnum", "fsc", "niin", "stnbTycd", "itemName",
    "apeqName", "specNumb", "specClas", "codeVld1", "partNumb",
    "refcNumb", "bugtDivs", "codeVld2", "unit", "ordrUtpr",
    "dlvrRegn", "dlvrDate", "dlvrQnty", "dlvrPrrk", "codeVld3",
    "apeqCode", "miltDivs", "codeVld4", "aprvDate", "totlAmnt",
]
# 26개 응답 항목 + 3개 요청 항목입니다. 명세에 없는 컬럼은 추가하지 않습니다.
RAW_COLUMNS = REQUEST_COLUMNS + ITEM_COLUMNS
LOG_COLUMNS = REQUEST_COLUMNS + [
    "수집상태", "API 전체행수", "수집건수", "페이지수", "요청시간초", "오류"
]


# ============================================================
# 2. 조회 대상 사업 목록 생성
# ============================================================

def load_targets():
    """공고 CLEAN에서 요구년도·판단번호·기관코드의 고유 조합을 추출합니다."""
    notice = pd.read_csv(
        NOTICE_FILE,
        dtype="string",
        encoding="utf-8-sig",
        usecols=REQUEST_COLUMNS,
        low_memory=False,
        keep_default_na=False,
    )

    # API 요청값 앞뒤 공백만 제거하고 식별자의 앞자리 0은 유지합니다.
    targets = notice[REQUEST_COLUMNS].copy()
    for col in REQUEST_COLUMNS:
        targets[col] = targets[col].str.strip()

    missing = targets[REQUEST_COLUMNS].eq("").any(axis=1)
    print(f"공고 행 수: {len(notice):,}건")
    print(f"요청키 결측·공백 행 수: {missing.sum():,}건")

    targets = (
        targets.loc[~missing]
        .drop_duplicates()
        .sort_values(REQUEST_COLUMNS)
        .reset_index(drop=True)
    )

    print(f"API 조회 대상 고유 사업: {len(targets):,}개")
    return targets


# ============================================================
# 3. XML 응답 파싱 및 API 요청
# ============================================================

def parse_xml_response(content):
    """품목명세서 응답의 문자열 값을 보존하며 item과 totalCount를 반환합니다."""
    root = ET.fromstring(content)
    result_code = root.findtext(".//header/resultCode")
    result_msg = root.findtext(".//header/resultMsg")

    if result_code and result_code != "00":
        raise RuntimeError(f"API 오류 {result_code} / {result_msg or ''}")

    total_text = root.findtext(".//body/totalCount")
    if total_text is None:
        raise RuntimeError("응답에 totalCount가 없습니다.")
    total_count = int(total_text)

    items = []
    for item in root.findall(".//body/items/item"):
        row = {child.tag: child.text if child.text is not None else "" for child in item}
        items.append(row)

    return items, total_count


def request_once(params, name):
    """실패한 동일 페이지를 MAX_RETRIES회 재시도합니다."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            start = time.time()
            response = SESSION.get(
                URL_ITEM,
                params=params,
                timeout=(10, 60),
            )
            response.raise_for_status()
            items, total_count = parse_xml_response(response.content)
            return items, total_count, time.time() - start
        except Exception as e:
            last_error = e
            print(f"{name} 요청 실패 ({attempt}/{MAX_RETRIES}) | 오류: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)

    raise RuntimeError(f"{name} 요청 실패 | 마지막 오류: {last_error}")


def collect_target(target):
    """사업 하나의 모든 페이지를 조회하고 응답 건수를 검증합니다."""
    params = {
        "serviceKey": API_KEY,
        "pageNo": 1,
        "numOfRows": CHUNK_SIZE,
        **target,
    }
    name = f"{target['demandYear']}_{target['orntCode']}_{target['dcsNo']}"

    all_items = []
    expected_total = None
    page_no = 1
    elapsed = 0.0

    while True:
        params["pageNo"] = page_no
        items, total_count, seconds = request_once(params, f"{name} Page {page_no}")
        elapsed += seconds

        if expected_total is None:
            expected_total = total_count
        elif total_count != expected_total:
            raise RuntimeError(
                f"페이지 사이 totalCount 변화: {expected_total} → {total_count}"
            )

        if total_count == 0:
            if items:
                raise RuntimeError("totalCount=0인데 item이 존재합니다.")
            break

        if not items:
            raise RuntimeError(f"Page {page_no}에 item이 없는데 totalCount={total_count}")

        if len(items) > CHUNK_SIZE:
            raise RuntimeError("응답 item 수가 numOfRows를 초과했습니다.")

        all_items.extend(items)
        if len(all_items) >= expected_total:
            break

        page_no += 1
        time.sleep(REQUEST_DELAY)

    if len(all_items) != expected_total:
        raise RuntimeError(
            f"수집 건수 불일치: API {expected_total:,}건 / 실제 {len(all_items):,}건"
        )

    rows = []
    for item in all_items:
        # API의 item 필드는 변형하지 않고 요청 식별값만 보존합니다.
        rows.append({**target, **item})

    return rows, expected_total, page_no if expected_total else 1, elapsed


# ============================================================
# 4. 체크포인트 저장 및 재시작
# ============================================================

def checkpoint_paths(batch_no):
    stem = f"batch_{batch_no:06d}"
    return (
        os.path.join(CHECKPOINT_DIR, stem + "_items.csv"),
        os.path.join(CHECKPOINT_DIR, stem + "_log.csv"),
    )


def save_batch(batch_no, item_rows, log_rows):
    """배치별 RAW/로그를 기록합니다. 로그 파일이 완성된 배치만 재사용합니다."""
    data_path, log_path = checkpoint_paths(batch_no)
    temp_data = data_path + ".tmp"
    temp_log = log_path + ".tmp"

    pd.DataFrame(item_rows).reindex(columns=RAW_COLUMNS).to_csv(
        temp_data, index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(log_rows).reindex(columns=LOG_COLUMNS).to_csv(
        temp_log, index=False, encoding="utf-8-sig"
    )

    os.replace(temp_data, data_path)
    os.replace(temp_log, log_path)  # 로그가 마지막에 기록되면 완료된 배치로 인정
    print(f"[중간 저장] 배치 {batch_no} | 요청 {len(log_rows):,}건 | 품목 {len(item_rows):,}행")


def load_checkpoints():
    """완료된 배치를 조회하고 성공/빈결과 요청키를 재요청하지 않습니다."""
    batch_files = []
    for filename in sorted(os.listdir(CHECKPOINT_DIR)):
        if not filename.endswith("_log.csv"):
            continue
        batch_no = int(filename.split("_")[1])
        data_path, log_path = checkpoint_paths(batch_no)
        if os.path.isfile(data_path):
            batch_files.append((batch_no, data_path, log_path))

    finished = set()
    for _, _, log_path in batch_files:
        logs = pd.read_csv(log_path, dtype="string", keep_default_na=False)
        for _, row in logs.iterrows():
            if row["수집상태"] in ("성공", "데이터없음"):
                finished.add(tuple(row[col] for col in REQUEST_COLUMNS))

    return batch_files, finished


def combine_results(batch_files):
    """배치 RAW와 로그를 요청 순서대로 합쳐 최종 파일을 생성합니다."""
    item_frames = []
    log_frames = []

    for _, data_path, log_path in sorted(batch_files):
        item_frames.append(pd.read_csv(data_path, dtype="string", keep_default_na=False))
        log_frames.append(pd.read_csv(log_path, dtype="string", keep_default_na=False))

    items = (
        pd.concat(item_frames, ignore_index=True)
        if item_frames else pd.DataFrame(columns=RAW_COLUMNS)
    )
    logs = (
        pd.concat(log_frames, ignore_index=True)
        if log_frames else pd.DataFrame(columns=LOG_COLUMNS)
    )

    items.reindex(columns=RAW_COLUMNS).to_csv(
        FINAL_DATA_FILE, index=False, encoding="utf-8-sig"
    )
    logs.reindex(columns=LOG_COLUMNS).to_csv(
        FINAL_LOG_FILE, index=False, encoding="utf-8-sig"
    )

    return items, logs


# ============================================================
# 5. 실행
# ============================================================

def main():
    targets = load_targets()
    batch_files, finished = load_checkpoints()
    remaining = [
        dict(row) for row in targets.to_dict("records")
        if tuple(row[col] for col in REQUEST_COLUMNS) not in finished
    ]

    print("=" * 70)
    print("국내 품목명세서 RAW 수집 시작")
    print(f"완료된 고유 사업: {len(finished):,}개")
    print(f"이번 실행 조회 대상: {len(remaining):,}개")
    print("=" * 70)

    batch_no = max((n for n, _, _ in batch_files), default=0) + 1
    item_rows = []
    log_rows = []

    for idx, target in enumerate(remaining, start=1):
        start = time.time()
        try:
            rows, total_count, page_count, elapsed = collect_target(target)
            item_rows.extend(rows)
            status = "성공" if total_count else "데이터없음"
            error = ""
        except Exception as e:
            rows = []
            total_count = 0
            page_count = 0
            elapsed = time.time() - start
            status = "실패"
            error = str(e)

        log_rows.append({
            **target,
            "수집상태": status,
            "API건수": total_count,
            "수집건수": len(rows),
            "페이지수": page_count,
            "요청시간초": f"{elapsed:.3f}",
            "오류": error,
        })
        print(
            f"[{idx:,}/{len(remaining):,}] "
            f"{target['demandYear']}_{target['orntCode']}_{target['dcsNo']} "
            f"| {status} | 품목 {len(rows):,}건"
        )

        if len(log_rows) >= SAVE_EVERY:
            save_batch(batch_no, item_rows, log_rows)
            batch_files.append((batch_no, *checkpoint_paths(batch_no)))
            batch_no += 1
            item_rows = []
            log_rows = []

        if idx < len(remaining):
            time.sleep(REQUEST_DELAY)

    if log_rows:
        save_batch(batch_no, item_rows, log_rows)
        batch_files.append((batch_no, *checkpoint_paths(batch_no)))

    items, logs = combine_results(batch_files)
    print("\n" + "=" * 70)
    print("최종 수집 결과")
    print("=" * 70)
    print(f"품목명세서 RAW 행 수: {len(items):,}건")
    print(f"성공/데이터없음 요청: {logs['수집상태'].isin(['성공', '데이터없음']).sum():,}건")
    print(f"실패 로그 행 수: {logs['수집상태'].eq('실패').sum():,}건")
    print(f"완전중복 RAW 행 수: {items.duplicated().sum():,}건 (삭제하지 않음)")
    print("\n품목명세서 RAW CSV:\n", FINAL_DATA_FILE)
    print("수집 검증 로그 CSV:\n", FINAL_LOG_FILE)


if __name__ == "__main__":
    main()
