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
load_dotenv(os.path.join(BASE_DIR, ".env"))

API_KEY = os.getenv("API_KEY_DATA_PORTAL")

if not API_KEY:
    raise RuntimeError(
        "API_KEY_DATA_PORTAL을 .env에서 읽지 못했습니다."
    )


URL_PLAN = (
    "https://apis.data.go.kr/1690000/"
    "PrcurePlanInfoService/getDmstcPrcurePlanList"
)


#  데이터 수집 기간 설정
START_MONTH = "202101"
END_MONTH = "202512"


CHUNK_SIZE = 10000

REQUEST_DELAY = 0.2
MAX_RETRIES = 3
RETRY_DELAY = 2


RAW_DIR = os.path.join(BASE_DIR, "data", "raw")
os.makedirs(RAW_DIR, exist_ok=True)

SESSION = requests.Session()


# ============================================================
# 2. 월 목록 생성
# ============================================================

def make_months(start_month, end_month):
    """
    YYYYMM 형식의 시작월부터 종료월까지 월 목록을 생성합니다.

    예:
        202101 ~ 202103
        -> ["202101", "202102", "202103"]
    """

    try:
        start = datetime.strptime(start_month, "%Y%m")
        end = datetime.strptime(end_month, "%Y%m")
    except ValueError as e:
        raise ValueError(
            "START_MONTH와 END_MONTH는 YYYYMM 형식이어야 합니다. "
            "예: 202101"
        ) from e

    if start > end:
        raise ValueError(
            "START_MONTH는 END_MONTH보다 늦을 수 없습니다."
        )

    months = []

    year = start.year
    month = start.month

    while (year, month) <= (end.year, end.month):
        months.append(
            f"{year:04d}{month:02d}"
        )

        if month == 12:
            year += 1
            month = 1
        else:
            month += 1

    return months


# ============================================================
# 3. XML 응답 파싱
# ============================================================

def parse_xml_response(content):
    root = ET.fromstring(content)

    result_code = root.findtext(".//resultCode")
    result_msg = root.findtext(".//resultMsg")

    if result_code and result_code != "00":
        raise RuntimeError(
            f"API 오류 {result_code} / {result_msg or ''}"
        )

    total_count_text = root.findtext(".//totalCount")

    total_count = (
        int(total_count_text)
        if total_count_text
        else 0
    )

    items = []

    for item in root.findall(".//item"):
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
# 4. API 요청 + 재시도
# ============================================================

def request_api(name, params):
    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):
        try:
            request_start = time.time()

            response = SESSION.get(
                URL_PLAN,
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

            return (
                items,
                total_count,
                request_time,
                parse_time,
            )

        except Exception as e:
            last_error = e

            print()
            print(
                f"{name} 요청 실패 "
                f"({attempt}/{MAX_RETRIES})"
            )
            print(
                f"오류: {e}"
            )

            if attempt < MAX_RETRIES:
                wait_time = (
                    RETRY_DELAY * attempt
                )

                print(
                    f"{wait_time}초 후 같은 요청 재시도..."
                )

                time.sleep(
                    wait_time
                )

    raise RuntimeError(
        f"{name} 요청 실패 "
        f"| {MAX_RETRIES}회 모두 실패 "
        f"| 마지막 오류: {last_error}"
    )


# ============================================================
# 5. 한 달 수집
# ============================================================

def fetch_month(month):
    """
    조달계획은 orderPrearngeMt가 YYYYMM 단위이므로
    해당 월을 begin=end로 지정해서 한 달씩 수집합니다.

    페이지네이션은 사용하지 않습니다.

    1) numOfRows=1로 totalCount 확인
    2) totalCount가 허용 범위 이내면
       numOfRows=totalCount로 Page 1에서 전부 수집
    """

    # --------------------------------------------------------
    # 1단계: 해당 월 totalCount 확인
    # --------------------------------------------------------

    probe_params = {
        "serviceKey": API_KEY,
        "pageNo": 1,
        "numOfRows": 1,
        "orderPrearngeMtBegin": month,
        "orderPrearngeMtEnd": month,
    }

    (
        _,
        total_count,
        probe_request_time,
        probe_parse_time,
    ) = request_api(
        name=f"조달계획 {month} 건수 확인",
        params=probe_params,
    )

    print(
        f"[{month}] totalCount {total_count:,}건 "
        f"| 확인요청 {probe_request_time:.2f}초 "
        f"| 파싱 {probe_parse_time:.2f}초"
    )

    # 데이터 없는 월
    if total_count == 0:
        return (
            pd.DataFrame(),
            {
                "월": month,
                "API건수": 0,
                "수집건수": 0,
                "완전중복": 0,
            },
        )

    # 페이지네이션 없이 받을 수 없는 경우
    if total_count > CHUNK_SIZE:
        raise RuntimeError(
            f"{month} 조달계획이 {total_count:,}건으로 "
            f"CHUNK_SIZE({CHUNK_SIZE:,})를 초과했습니다. "
            "조달계획은 월 단위 필터이므로 날짜(일) 단위로 더 쪼개지 않습니다. "
            "CHUNK_SIZE 늘리거나 이 경우에만 별도 처리하세요."
        )

    # --------------------------------------------------------
    # 2단계: Page 1 한 번으로 해당 월 전체 수집
    # --------------------------------------------------------

    data_params = {
        "serviceKey": API_KEY,
        "pageNo": 1,
        "numOfRows": total_count,
        "orderPrearngeMtBegin": month,
        "orderPrearngeMtEnd": month,
    }

    (
        items,
        second_total_count,
        request_time,
        parse_time,
    ) = request_api(
        name=f"조달계획 {month} 데이터 수집",
        params=data_params,
    )

    # 두 요청 사이 totalCount가 바뀌었는지 검증
    if second_total_count != total_count:
        raise RuntimeError(
            f"{month} totalCount가 요청 사이에 변경되었습니다. "
            f"| 처음={total_count:,} "
            f"| 두 번째={second_total_count:,}"
        )

    if len(items) != total_count:
        raise RuntimeError(
            f"{month} 수집 건수 불일치 "
            f"| API totalCount={total_count:,} "
            f"| 실제 응답={len(items):,}"
        )

    df = pd.DataFrame(
        items
    )

    duplicate_count = (
        int(df.duplicated().sum())
        if not df.empty
        else 0
    )

    print(
        f"[{month}] 수집 완료 "
        f"| {len(df):,}건 "
        f"| 완전중복 {duplicate_count:,}건 "
        f"| 요청 {request_time:.2f}초 "
        f"| 파싱 {parse_time:.2f}초"
    )

    return (
        df,
        {
            "월": month,
            "API건수": total_count,
            "수집건수": len(df),
            "완전중복": duplicate_count,
        },
    )


# ============================================================
# 6. 실행
# ============================================================

def main():
    months = make_months(
        START_MONTH,
        END_MONTH,
    )

    monthly_frames = []
    logs = []

    print()
    print("=" * 70)
    print("국내 조달계획 목록 수집 시작")
    print("=" * 70)
    print(
        f"수집 기간 : {START_MONTH} ~ {END_MONTH}"
    )
    print(
        f"총 개월 수: {len(months):,}개월"
    )
    print(
        "수집 방식 : 월별 1페이지 단일 요청 "
        "(페이지네이션 없음)"
    )

    for idx, month in enumerate(
        months,
        start=1,
    ):
        print()
        print("#" * 70)
        print(
            f"[{idx}/{len(months)}] {month}"
        )
        print("#" * 70)

        month_df, log = fetch_month(
            month
        )

        if not month_df.empty:
            monthly_frames.append(
                month_df
            )

        logs.append(
            log
        )

        if idx < len(months):
            time.sleep(
                REQUEST_DELAY
            )

    # --------------------------------------------------------
    # 전체 병합
    # --------------------------------------------------------

    if monthly_frames:
        plan_df = pd.concat(
            monthly_frames,
            ignore_index=True,
            sort=False,
        )
    else:
        plan_df = pd.DataFrame()

    log_df = pd.DataFrame(
        logs
    )

    expected_total = (
        int(log_df["API건수"].sum())
        if not log_df.empty
        else 0
    )

    if len(plan_df) != expected_total:
        raise RuntimeError(
            "최종 병합 건수 불일치 "
            f"| 월별 API건수 합계={expected_total:,} "
            f"| 병합 행 수={len(plan_df):,}"
        )

    # --------------------------------------------------------
    # 저장
    # --------------------------------------------------------

    # 기존 파일명 형식 유지
    full_begin = START_MONTH + "01"

    end_year = int(
        END_MONTH[:4]
    )
    end_month = int(
        END_MONTH[4:]
    )

    # 파일명용 마지막 날짜만 계산
    import calendar

    last_day = calendar.monthrange(
        end_year,
        end_month,
    )[1]

    full_end = (
        f"{END_MONTH}{last_day:02d}"
    )

    plan_path = os.path.join(
        RAW_DIR,
        (
            f"procure_plan_"
            f"{full_begin}_"
            f"{full_end}.csv"
        ),
    )

    log_path = os.path.join(
        RAW_DIR,
        (
            f"procure_plan_collection_log_"
            f"{full_begin}_"
            f"{full_end}.csv"
        ),
    )

    plan_df.to_csv(
        plan_path,
        index=False,
        encoding="utf-8-sig",
    )

    log_df.to_csv(
        log_path,
        index=False,
        encoding="utf-8-sig",
    )

    duplicate_count = (
        int(plan_df.duplicated().sum())
        if not plan_df.empty
        else 0
    )

    print()
    print("=" * 70)
    print("최종 수집 결과")
    print("=" * 70)

    print(
        f"조달계획 목록 : "
        f"{len(plan_df):,}건"
    )

    print(
        f"완전중복     : "
        f"{duplicate_count:,}건"
    )

    print(
        f"월별 API 합계: "
        f"{expected_total:,}건"
    )

    print()
    print("조달계획 CSV:")
    print(plan_path)

    print()
    print("수집 검증 로그 CSV:")
    print(log_path)

    if "progrsSttus" in plan_df.columns:
        print()
        print(
            "진행상태(progrsSttus) 종류"
        )
        print("-" * 40)
        print(
            plan_df[
                "progrsSttus"
            ].value_counts(
                dropna=False
            )
        )

    if "excutTy" in plan_df.columns:
        print()
        print(
            "집행유형(excutTy) 종류"
        )
        print("-" * 40)
        print(
            plan_df[
                "excutTy"
            ].value_counts(
                dropna=False
            )
        )


if __name__ == "__main__":
    main()
