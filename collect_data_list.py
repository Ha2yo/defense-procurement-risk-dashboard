import calendar
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

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

URL_GONGGO = (
    "https://apis.data.go.kr/1690000/"
    "BidPblancInfoService/getDmstcCmpetBidPblancList"
)

URL_RESULT = (
    "https://apis.data.go.kr/1690000/"
    "BidResultInfoService/getDmstcCmpetBidResultList"
)

# 데이터 수집 기간 설정
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
# 공통 날짜/수집 함수
# ============================================================

def make_month_periods(start_month, end_month):
    """YYYYMM 형식의 시작월~종료월을 월별 YYYYMMDD 구간으로 변환합니다."""
    try:
        start = datetime.strptime(start_month, "%Y%m")
        end = datetime.strptime(end_month, "%Y%m")
    except ValueError as e:
        raise ValueError(
            "START_MONTH와 END_MONTH는 YYYYMM 형식이어야 합니다. 예: 202101"
        ) from e

    if start > end:
        raise ValueError("START_MONTH는 END_MONTH보다 늦을 수 없습니다.")

    periods = []
    year, month = start.year, start.month

    while (year, month) <= (end.year, end.month):
        last_day = calendar.monthrange(year, month)[1]
        begin_date = f"{year:04d}{month:02d}01"
        end_date = f"{year:04d}{month:02d}{last_day:02d}"
        periods.append((begin_date, end_date))

        if month == 12:
            year += 1
            month = 1
        else:
            month += 1

    return periods


def split_period(begin_date, end_date):
    """기간을 서로 겹치지 않는 두 날짜 구간으로 반분합니다."""
    begin = datetime.strptime(begin_date, "%Y%m%d")
    end = datetime.strptime(end_date, "%Y%m%d")

    if begin >= end:
        raise ValueError("하루짜리 기간은 더 이상 분할할 수 없습니다.")

    midpoint = begin + timedelta(days=(end - begin).days // 2)

    return (
        begin.strftime("%Y%m%d"),
        midpoint.strftime("%Y%m%d"),
        (midpoint + timedelta(days=1)).strftime("%Y%m%d"),
        end.strftime("%Y%m%d"),
    )


def parse_xml_response(content):
    """공공데이터포털 XML 응답을 파싱합니다."""
    root = ET.fromstring(content)

    result_code = root.findtext(".//resultCode")
    result_msg = root.findtext(".//resultMsg")

    if result_code and result_code != "00":
        raise RuntimeError(
            f"API 오류 {result_code} / {result_msg or ''}"
        )

    total_count_text = root.findtext(".//totalCount")
    total_count = int(total_count_text) if total_count_text else 0

    items = []

    for item in root.findall(".//item"):
        row = {
            child.tag: child.text.strip() if child.text else ""
            for child in item
        }
        items.append(row)

    return items, total_count


def request_once(name, url, params):
    """
    한 조회구간의 Page 1만 요청합니다.
    실패한 동일 요청만 재시도하며 Page 번호는 증가시키지 않습니다.
    """
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            request_start = time.time()

            response = SESSION.get(
                url,
                params=params,
                timeout=(10, 60),
            )
            response.raise_for_status()

            request_time = time.time() - request_start

            parse_start = time.time()
            items, total_count = parse_xml_response(response.content)
            parse_time = time.time() - parse_start

            return items, total_count, request_time, parse_time

        except Exception as e:
            last_error = e

            print()
            print(
                f"{name} 요청 실패 "
                f"({attempt}/{MAX_RETRIES}) | 오류: {e}"
            )

            if attempt < MAX_RETRIES:
                wait_time = RETRY_DELAY * attempt
                print(f"{wait_time}초 후 같은 요청 재시도...")
                time.sleep(wait_time)

    raise RuntimeError(
        f"{name} 요청 실패 | {MAX_RETRIES}회 모두 실패 "
        f"| 마지막 오류: {last_error}"
    )


def collect_period(
    name,
    url,
    begin_date,
    end_date,
    begin_param,
    end_param,
    depth=0,
):
    """
    페이지네이션 없이 기간을 수집합니다.

    - totalCount <= CHUNK_SIZE: 한 번에 수집 완료
    - totalCount > CHUNK_SIZE: 날짜 구간을 절반으로 자동 분할
    - 하루 자체가 CHUNK_SIZE를 초과하면 안전을 위해 중단

    반환:
        DataFrame, 원래 기간 totalCount, 최종 수집구간 로그
    """
    indent = "  " * depth

    params = {
        "serviceKey": API_KEY,
        "pageNo": 1,  # 항상 Page 1만 사용
        "numOfRows": CHUNK_SIZE,
        begin_param: begin_date,
        end_param: end_date,
    }

    items, total_count, request_time, parse_time = request_once(
        name=f"{name} ({begin_date} ~ {end_date})",
        url=url,
        params=params,
    )

    print(
        f"{indent}[조회] {begin_date} ~ {end_date} "
        f"| totalCount {total_count:,} "
        f"| 응답 {len(items):,} "
        f"| 요청 {request_time:.2f}초 "
        f"| 파싱 {parse_time:.2f}초"
    )

    # 데이터가 없는 구간
    if total_count == 0:
        if items:
            raise RuntimeError(
                f"{name} 응답 이상: totalCount=0인데 item이 존재합니다. "
                f"| {begin_date}~{end_date}"
            )

        return (
            pd.DataFrame(),
            0,
            [{
                "시작일": begin_date,
                "종료일": end_date,
                "API건수": 0,
                "수집건수": 0,
                "완전중복": 0,
                "분할깊이": depth,
            }],
        )

    # 한 번에 전부 받을 수 있는 구간
    if total_count <= CHUNK_SIZE:
        if len(items) != total_count:
            raise RuntimeError(
                f"{name} 단일 요청 건수 불일치 "
                f"| API totalCount={total_count:,} "
                f"| 실제 응답={len(items):,} "
                f"| 기간={begin_date}~{end_date}"
            )

        df = pd.DataFrame(items)
        duplicate_count = int(df.duplicated().sum()) if not df.empty else 0

        print(
            f"{indent}[완료] {begin_date} ~ {end_date} "
            f"| {len(df):,}건 "
            f"| 완전중복 {duplicate_count:,}건"
        )

        return (
            df,
            total_count,
            [{
                "시작일": begin_date,
                "종료일": end_date,
                "API건수": total_count,
                "수집건수": len(df),
                "완전중복": duplicate_count,
                "분할깊이": depth,
            }],
        )

    # 한 달/기간이 너무 크면 Page 2로 가지 않고 날짜를 반분
    begin_dt = datetime.strptime(begin_date, "%Y%m%d")
    end_dt = datetime.strptime(end_date, "%Y%m%d")

    if begin_dt == end_dt:
        raise RuntimeError(
            f"{name}: {begin_date} 하루 데이터가 "
            f"{total_count:,}건으로 CHUNK_SIZE({CHUNK_SIZE:,})를 초과했습니다. "
            "페이지네이션을 사용하지 않도록 설정했기 때문에 수집을 중단합니다. "
            "CHUNK_SIZE를 늘리거나 더 세밀한 API 필터를 사용하세요."
        )

    left_begin, left_end, right_begin, right_end = split_period(
        begin_date,
        end_date,
    )

    print(
        f"{indent}[분할] {total_count:,}건 > CHUNK_SIZE {CHUNK_SIZE:,}"
    )
    print(f"{indent}       ① {left_begin} ~ {left_end}")
    print(f"{indent}       ② {right_begin} ~ {right_end}")

    time.sleep(REQUEST_DELAY)

    left_df, left_total, left_logs = collect_period(
        name=name,
        url=url,
        begin_date=left_begin,
        end_date=left_end,
        begin_param=begin_param,
        end_param=end_param,
        depth=depth + 1,
    )

    time.sleep(REQUEST_DELAY)

    right_df, right_total, right_logs = collect_period(
        name=name,
        url=url,
        begin_date=right_begin,
        end_date=right_end,
        begin_param=begin_param,
        end_param=end_param,
        depth=depth + 1,
    )

    # 분할 전 totalCount와 분할 후 totalCount 합이 같아야 함
    child_total = left_total + right_total

    if child_total != total_count:
        raise RuntimeError(
            f"{name} 기간 분할 검증 실패 "
            f"| 부모 totalCount={total_count:,} "
            f"| 분할 합계={child_total:,} "
            f"| 기간={begin_date}~{end_date}"
        )

    frames = [df for df in (left_df, right_df) if not df.empty]

    combined_df = (
        pd.concat(frames, ignore_index=True, sort=False)
        if frames
        else pd.DataFrame()
    )

    if len(combined_df) != total_count:
        raise RuntimeError(
            f"{name} 분할 병합 검증 실패 "
            f"| API totalCount={total_count:,} "
            f"| 병합 행 수={len(combined_df):,} "
            f"| 기간={begin_date}~{end_date}"
        )

    return combined_df, total_count, left_logs + right_logs


def collect_month_range(
    name,
    url,
    begin_param,
    end_param,
):
    """START_MONTH~END_MONTH를 월 단위로 시작해 자동 분할 수집합니다."""
    month_periods = make_month_periods(
        START_MONTH,
        END_MONTH,
    )

    monthly_frames = []
    all_logs = []
    monthly_total_sum = 0

    print()
    print("=" * 70)
    print(f"{name} 수집 시작")
    print("=" * 70)
    print(f"시작월     : {START_MONTH}")
    print(f"종료월     : {END_MONTH}")
    print(f"총 개월 수 : {len(month_periods):,}")
    print(f"CHUNK_SIZE : {CHUNK_SIZE:,}")
    print("방식       : Page 1만 사용 → 초과 시 날짜 자동 반분")

    for idx, (month_begin, month_end) in enumerate(
        month_periods,
        start=1,
    ):
        print()
        print("#" * 70)
        print(
            f"[월 {idx}/{len(month_periods)}] "
            f"{month_begin} ~ {month_end}"
        )
        print("#" * 70)

        month_df, month_total, leaf_logs = collect_period(
            name=name,
            url=url,
            begin_date=month_begin,
            end_date=month_end,
            begin_param=begin_param,
            end_param=end_param,
        )

        if len(month_df) != month_total:
            raise RuntimeError(
                f"{name} 월 단위 검증 실패 "
                f"| 기간={month_begin}~{month_end} "
                f"| API={month_total:,} "
                f"| 수집={len(month_df):,}"
            )

        monthly_frames.append(month_df)
        all_logs.extend(leaf_logs)
        monthly_total_sum += month_total

        month_duplicates = (
            int(month_df.duplicated().sum())
            if not month_df.empty
            else 0
        )

        print(
            f"[월 완료] {month_begin} ~ {month_end} "
            f"| {len(month_df):,}건 "
            f"| 완전중복 {month_duplicates:,}건"
        )

        if idx < len(month_periods):
            time.sleep(REQUEST_DELAY)

    frames = [df for df in monthly_frames if not df.empty]

    final_df = (
        pd.concat(frames, ignore_index=True, sort=False)
        if frames
        else pd.DataFrame()
    )

    if len(final_df) != monthly_total_sum:
        raise RuntimeError(
            f"{name} 전체 병합 검증 실패 "
            f"| 월별 totalCount 합계={monthly_total_sum:,} "
            f"| 병합 행 수={len(final_df):,}"
        )

    log_df = pd.DataFrame(all_logs)

    return (
        final_df,
        log_df,
        month_periods[0][0],
        month_periods[-1][1],
    )


def save_collection_log(log_df, path):
    log_df.to_csv(
        path,
        index=False,
        encoding="utf-8-sig",
    )


def print_notice_key_stats(df, label):
    """프로젝트의 공고 식별 컬럼 조합 개수를 참고용으로 출력합니다."""
    key_cols = [
        "demandYear",
        "orntCode",
        "dcsNo",
        "pblancNo",
        "pblancOdr",
    ]

    if all(col in df.columns for col in key_cols):
        unique_keys = (
            df[key_cols]
            .fillna("")
            .astype(str)
            .drop_duplicates()
            .shape[0]
        )

        print(
            f"{label} 고유 noticeKey 조합 : "
            f"{unique_keys:,}건"
        )


# ============================================================
# 3. 실행
# ============================================================

def main():
    # --------------------------------------------------------
    # 국내 경쟁입찰공고
    # --------------------------------------------------------
    gonggo_df, gonggo_log_df, full_begin, full_end = collect_month_range(
        name="국내 경쟁입찰공고 목록",
        url=URL_GONGGO,
        begin_param="opengDateBegin",
        end_param="opengDateEnd",
    )

    gonggo_path = os.path.join(
        RAW_DIR,
        f"bid_notice_{full_begin}_{full_end}.csv",
    )

    gonggo_log_path = os.path.join(
        RAW_DIR,
        f"bid_notice_collection_log_{full_begin}_{full_end}.csv",
    )

    gonggo_df.to_csv(
        gonggo_path,
        index=False,
        encoding="utf-8-sig",
    )
    save_collection_log(
        gonggo_log_df,
        gonggo_log_path,
    )

    # --------------------------------------------------------
    # 국내 경쟁입찰결과
    # --------------------------------------------------------
    result_df, result_log_df, result_begin, result_end = collect_month_range(
        name="국내 경쟁입찰결과 목록",
        url=URL_RESULT,
        begin_param="opengDateBegin",
        end_param="opengDateEnd",
    )

    result_path = os.path.join(
        RAW_DIR,
        f"bid_result_{result_begin}_{result_end}.csv",
    )

    result_log_path = os.path.join(
        RAW_DIR,
        f"bid_result_collection_log_{result_begin}_{result_end}.csv",
    )

    result_df.to_csv(
        result_path,
        index=False,
        encoding="utf-8-sig",
    )
    save_collection_log(
        result_log_df,
        result_log_path,
    )

    gonggo_duplicates = (
        int(gonggo_df.duplicated().sum())
        if not gonggo_df.empty
        else 0
    )

    result_duplicates = (
        int(result_df.duplicated().sum())
        if not result_df.empty
        else 0
    )

    print()
    print("=" * 70)
    print("최종 수집 결과")
    print("=" * 70)
    print(f"공고 목록 : {len(gonggo_df):,}건")
    print(f"공고 완전중복 : {gonggo_duplicates:,}건")
    print_notice_key_stats(gonggo_df, "공고")
    print()
    print(f"결과 목록 : {len(result_df):,}건")
    print(f"결과 완전중복 : {result_duplicates:,}건")
    print_notice_key_stats(result_df, "결과")

    print()
    print("공고 CSV:")
    print(gonggo_path)
    print("공고 수집 검증 로그:")
    print(gonggo_log_path)

    print()
    print("결과 CSV:")
    print(result_path)
    print("결과 수집 검증 로그:")
    print(result_log_path)

    if "bidResult" in result_df.columns:
        print()
        print("입찰결과(bidResult) 종류")
        print("-" * 40)
        print(
            result_df["bidResult"].value_counts(
                dropna=False
            )
        )


if __name__ == "__main__":
    main()
