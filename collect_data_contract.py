import os
import time

import pandas as pd
import requests
import xml.etree.ElementTree as ET
from dotenv import load_dotenv


# ============================================================
# 1. 기본 설정
# ============================================================

load_dotenv()

API_KEY = os.getenv("API_KEY_DATA_PORTAL")

if not API_KEY:
    raise RuntimeError(
        "API_KEY_DATA_PORTAL을 .env에서 읽지 못했습니다."
    )


# 국내 계약정보 목록
URL_CONTRACT = (
    "https://apis.data.go.kr/1690000/"
    "CntrctInfoService/getDmstcCntrctInfoList"
)


# 수집 기간
DATE_BEGIN = "20210101"
DATE_END = "20251231"

# 페이지당 요청 개수
CHUNK_SIZE = 5000

# 정상 요청 사이 대기시간
REQUEST_DELAY = 0.2

# 페이지 요청 최대 시도 횟수
MAX_RETRIES = 3

# 재시도 기본 대기시간
RETRY_DELAY = 2

# 저장 폴더
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(BASE_DIR, "data", "raw")
os.makedirs(RAW_DIR, exist_ok=True)


# HTTP 연결 재사용
SESSION = requests.Session()


# ============================================================
# 2. XML 응답 파싱
# ============================================================

def parse_xml_response(content):
    """
    공공데이터포털 XML 응답을 파싱합니다.

    반환:
        items       : 현재 페이지 데이터
        total_count : 전체 데이터 건수
    """

    root = ET.fromstring(content)

    result_code = root.findtext(".//resultCode")
    result_msg = root.findtext(".//resultMsg")

    if result_code and result_code != "00":
        raise RuntimeError(
            f"API 오류 "
            f"{result_code} / "
            f"{result_msg or ''}"
        )

    total_count_text = root.findtext(
        ".//totalCount"
    )

    total_count = (
        int(total_count_text)
        if total_count_text
        else 0
    )

    item_elements = root.findall(".//item")

    items = []

    for item in item_elements:
        row = {
            child.tag: (
                child.text.strip()
                if child.text
                else ""
            )
            for child in item
        }
        items.append(row)

    return items, total_count


# ============================================================
# 3. 공통 목록 수집 함수
# ============================================================

def fetch_data(name, url, extra_params):
    all_items = []
    page = 1

    print()
    print("=" * 60)
    print(f"{name} 수집 시작")
    print("=" * 60)

    while True:
        params = {
            "serviceKey": API_KEY,
            "pageNo": page,
            "numOfRows": CHUNK_SIZE,
            **extra_params,
        }

        page_success = False
        last_error = None

        for attempt in range(
            1,
            MAX_RETRIES + 1,
        ):
            try:
                request_start = time.time()

                response = SESSION.get(
                    url,
                    params=params,
                    timeout=(10, 60),
                )

                response.raise_for_status()

                request_time = (
                    time.time()
                    - request_start
                )

                parse_start = time.time()

                items, total_count = (
                    parse_xml_response(
                        response.content
                    )
                )

                parse_time = (
                    time.time()
                    - parse_start
                )

                page_success = True
                break

            except Exception as e:
                last_error = e

                print()
                print(
                    f"[Page {page}] "
                    f"요청 실패 "
                    f"({attempt}/{MAX_RETRIES})"
                )
                print(f"오류: {e}")

                if attempt < MAX_RETRIES:
                    wait_time = (
                        RETRY_DELAY
                        * attempt
                    )

                    print(
                        f"{wait_time}초 후 "
                        f"Page {page} 재시도..."
                    )

                    time.sleep(wait_time)

        if not page_success:
            print()
            print("=" * 60)
            print(
                f"Page {page} "
                f"{MAX_RETRIES}회 요청 실패"
            )
            print("=" * 60)

            raise RuntimeError(
                f"{name} 수집 실패 "
                f"| Page {page} "
                f"| 마지막 오류: {last_error}"
            )

        if not items:
            print(
                f"[Page {page}] "
                f"데이터가 없습니다."
            )
            break

        all_items.extend(items)

        print(
            f"[Page {page}] "
            f"{len(items):,}건 수집 "
            f"| 누적 {len(all_items):,}건 "
            f"| 전체 {total_count:,}건 "
            f"| 요청 {request_time:.2f}초 "
            f"| 파싱 {parse_time:.2f}초"
        )

        if (
            total_count > 0
            and len(all_items) >= total_count
        ):
            break

        if len(items) < CHUNK_SIZE:
            break

        page += 1
        time.sleep(REQUEST_DELAY)

    df = pd.DataFrame(all_items)

    print()
    print(
        f"{name} 수집 완료: "
        f"{len(df):,}건"
    )

    return df


# ============================================================
# 4. 실행
# ============================================================

def main():
    # 국내 계약정보 목록
    contract_df = fetch_data(
        name="국내 계약정보 목록",
        url=URL_CONTRACT,
        extra_params={
            "cntrctDateBegin": DATE_BEGIN,
            "cntrctDateEnd": DATE_END,
        },
    )

    # 계약정보 CSV 저장 경로
    contract_path = os.path.join(
        RAW_DIR,
        f"contract_info_{DATE_BEGIN}_{DATE_END}.csv",
    )

    contract_df.to_csv(
        contract_path,
        index=False,
        encoding="utf-8-sig",
    )

    print(
        f"CSV 저장 완료 → {contract_path}"
    )

    # 기본 결과 확인
    print()
    print("=" * 60)
    print("최종 수집 결과")
    print("=" * 60)

    print(
        f"계약정보 목록 : {len(contract_df):,}건"
    )

    print()
    print("계약정보 CSV:")
    print(contract_path)

    if "cntrctSttus" in contract_df.columns:
        print()
        print("계약상태(cntrctSttus) 종류")
        print("-" * 40)
        print(
            contract_df["cntrctSttus"]
            .value_counts(
                dropna=False
            )
        )

    if "cntrctMth" in contract_df.columns:
        print()
        print("계약방법(cntrctMth) 종류")
        print("-" * 40)
        print(
            contract_df["cntrctMth"]
            .value_counts(
                dropna=False
            )
        )


if __name__ == "__main__":
    main()
