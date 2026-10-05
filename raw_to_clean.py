# %%
import os

import pandas as pd


# ============================================================
# 1. 기본 설정
# ============================================================

DATE_BEGIN = "20210101"
DATE_END = "20251231"

# 이 .py 파일이 있는 위치를 기준으로 data/raw, data/clean 사용
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(BASE_DIR, "data", "raw")
CLEAN_DIR = os.path.join(BASE_DIR, "data", "clean")

os.makedirs(CLEAN_DIR, exist_ok=True)

PLAN_RAW_PATH = os.path.join(
    RAW_DIR,
    f"procure_plan_{DATE_BEGIN}_{DATE_END}.csv",
)

NOTICE_RAW_PATH = os.path.join(
    RAW_DIR,
    f"bid_notice_{DATE_BEGIN}_{DATE_END}.csv",
)

RESULT_RAW_PATH = os.path.join(
    RAW_DIR,
    f"bid_result_{DATE_BEGIN}_{DATE_END}.csv",
)

CONTRACT_RAW_PATH = os.path.join(
    RAW_DIR,
    f"contract_info_{DATE_BEGIN}_{DATE_END}.csv",
)

PLAN_CLEAN_PATH = os.path.join(
    CLEAN_DIR,
    f"procure_plan_clean_{DATE_BEGIN}_{DATE_END}.csv",
)

NOTICE_CLEAN_PATH = os.path.join(
    CLEAN_DIR,
    f"bid_notice_clean_{DATE_BEGIN}_{DATE_END}.csv",
)

RESULT_CLEAN_PATH = os.path.join(
    CLEAN_DIR,
    f"bid_result_clean_{DATE_BEGIN}_{DATE_END}.csv",
)

CONTRACT_CLEAN_PATH = os.path.join(
    CLEAN_DIR,
    f"contract_info_clean_{DATE_BEGIN}_{DATE_END}.csv",
)


# ============================================================
# 2. RAW 데이터 로드
# ============================================================


def read_raw_csv(path, name):
    """
    모든 원본 컬럼을 문자열로 읽는다.

    식별번호의 앞자리 0이 사라지거나 숫자로 자동 변환되는 것을
    방지하기 위해 dtype=str을 사용한다.
    """

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{name} RAW 파일을 찾을 수 없습니다:\n{path}"
        )

    df = pd.read_csv(
        path,
        dtype=str,
        low_memory=False,
        encoding="utf-8-sig",
    )

    print(
        f"{name}: {len(df):,}행 / {len(df.columns)}개 원본 컬럼"
    )

    return df


print("=" * 70)
print("RAW 데이터 로드")
print("=" * 70)

plan = read_raw_csv(PLAN_RAW_PATH, "조달계획")
notice = read_raw_csv(NOTICE_RAW_PATH, "입찰공고")
result = read_raw_csv(RESULT_RAW_PATH, "입찰결과")
contract = read_raw_csv(CONTRACT_RAW_PATH, "계약정보")


# ============================================================
# 3. 문자열 기본 정제
# ============================================================


def clean_string_columns(df):
    """
    원본 컬럼은 삭제하지 않고 모든 컬럼의 앞뒤 공백만 제거한다.

    - 컬럼명 앞뒤 공백 제거
    - 값 앞뒤 공백 제거
    - 빈 문자열은 pd.NA로 통일
    - 내부 띄어쓰기, 특수문자, 원본 의미는 변경하지 않음
    """

    df = df.copy()
    df.columns = df.columns.str.strip()

    for col in df.columns:
        df[col] = (
            df[col]
            .astype("string")
            .str.strip()
            .replace("", pd.NA)
        )

    return df


plan = clean_string_columns(plan)
notice = clean_string_columns(notice)
result = clean_string_columns(result)
contract = clean_string_columns(contract)

# 키 추가 전의 원본 컬럼 순서를 보존
PLAN_ORIGINAL_COLUMNS = plan.columns.tolist()
NOTICE_ORIGINAL_COLUMNS = notice.columns.tolist()
RESULT_ORIGINAL_COLUMNS = result.columns.tolist()
CONTRACT_ORIGINAL_COLUMNS = contract.columns.tolist()


# ============================================================
# 4. 완전 동일 중복행 제거
# ============================================================


def remove_exact_duplicates(df, name):
    """
    모든 원본 컬럼 값이 완전히 동일한 행만 제거한다.

    특정 키나 계약번호가 같다는 이유만으로는 제거하지 않는다.
    따라서 의미 있는 1:N / 복수 기록은 그대로 유지한다.
    """

    before = len(df)
    df = df.drop_duplicates().copy()
    after = len(df)

    print(
        f"{name}: {before:,} → {after:,}행 "
        f"| 완전중복 제거 {before - after:,}건"
    )

    return df


print()
print("=" * 70)
print("완전중복 제거")
print("=" * 70)

plan = remove_exact_duplicates(plan, "조달계획")
notice = remove_exact_duplicates(notice, "입찰공고")
result = remove_exact_duplicates(result, "입찰결과")
contract = remove_exact_duplicates(contract, "계약정보")


# ============================================================
# 5. 연결키 생성
# ============================================================


def normalize_key_component(series):
    """
    연결키 생성에만 사용하는 정규화.

    원본 컬럼 값 자체는 바꾸지 않는다.
    예: CSV 처리 과정에서 '1.0'처럼 들어온 차수는 키에서만 '1'로 사용.
    """

    return (
        series
        .astype("string")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )



def make_key(df, columns, new_col):
    """
    여러 원본 컬럼을 '_'로 결합해 연결키를 생성한다.

    구성 항목 중 하나라도 결측이면 불완전한 키를 만들지 않고
    해당 키를 pd.NA로 처리한다.
    """

    missing_columns = [
        col for col in columns
        if col not in df.columns
    ]

    if missing_columns:
        raise KeyError(
            f"{new_col} 생성 불가. 누락 컬럼: {missing_columns}"
        )

    if new_col in df.columns:
        raise ValueError(
            f"RAW 데이터에 이미 '{new_col}' 컬럼이 존재합니다. "
            "원본 컬럼을 덮어쓰지 않기 위해 중단합니다."
        )

    key_source = pd.DataFrame(
        {
            col: normalize_key_component(df[col])
            for col in columns
        },
        index=df.index,
    )

    has_missing = key_source.isna().any(axis=1)

    df = df.copy()
    df[new_col] = (
        key_source
        .fillna("")
        .agg("_".join, axis=1)
        .astype("string")
    )

    df.loc[has_missing, new_col] = pd.NA

    return df


# 문서에서 확정한 키 정의
BUSINESS_KEY_COLUMNS = [
    "demandYear",
    "orntCode",
    "dcsNo",
]

NOTICE_KEY_COLUMNS = [
    "demandYear",
    "orntCode",
    "dcsNo",
    "pblancNo",
    "pblancOdr",
]

CONTRACT_LINK_KEY_COLUMNS = [
    "dcsNo",
    "ornt",
    "cntrctMth",
    "bidMth",
]


# ------------------------------------------------------------
# 조달계획: 원본 + businessKey
# ------------------------------------------------------------
plan = make_key(
    plan,
    BUSINESS_KEY_COLUMNS,
    "businessKey",
)


# ------------------------------------------------------------
# 입찰공고: 원본 + businessKey + noticeKey
# ------------------------------------------------------------
notice = make_key(
    notice,
    BUSINESS_KEY_COLUMNS,
    "businessKey",
)

notice = make_key(
    notice,
    NOTICE_KEY_COLUMNS,
    "noticeKey",
)


# ------------------------------------------------------------
# 입찰결과: 원본 + businessKey + noticeKey + contractLinkKey
# ------------------------------------------------------------
result = make_key(
    result,
    BUSINESS_KEY_COLUMNS,
    "businessKey",
)

result = make_key(
    result,
    NOTICE_KEY_COLUMNS,
    "noticeKey",
)

result = make_key(
    result,
    CONTRACT_LINK_KEY_COLUMNS,
    "contractLinkKey",
)


# ------------------------------------------------------------
# 계약정보: 원본 + contractLinkKey
# ------------------------------------------------------------
contract = make_key(
    contract,
    CONTRACT_LINK_KEY_COLUMNS,
    "contractLinkKey",
)


# ============================================================
# 6. 원본 컬럼 보존 및 추가 컬럼 검증
# ============================================================


def validate_columns(
    df,
    original_columns,
    expected_added_columns,
    name,
):
    """
    원본 컬럼이 전부 같은 순서로 유지되고,
    허용한 키 컬럼만 뒤에 추가되었는지 검증한다.
    """

    current_columns = df.columns.tolist()
    expected_columns = (
        original_columns
        + expected_added_columns
    )

    if current_columns != expected_columns:
        missing = [
            c for c in original_columns
            if c not in current_columns
        ]

        unexpected = [
            c for c in current_columns
            if c not in expected_columns
        ]

        raise AssertionError(
            f"{name} 컬럼 구조가 정의와 다릅니다. "
            f"누락 원본 컬럼={missing}, "
            f"예상 외 컬럼={unexpected}"
        )

    print(
        f"{name}: 원본 {len(original_columns)}개 유지 "
        f"+ 키 {len(expected_added_columns)}개 "
        f"= 총 {len(current_columns)}개 컬럼 : OK"
    )


print()
print("=" * 70)
print("컬럼 구조 검증")
print("=" * 70)

validate_columns(
    plan,
    PLAN_ORIGINAL_COLUMNS,
    ["businessKey"],
    "조달계획",
)

validate_columns(
    notice,
    NOTICE_ORIGINAL_COLUMNS,
    ["businessKey", "noticeKey"],
    "입찰공고",
)

validate_columns(
    result,
    RESULT_ORIGINAL_COLUMNS,
    ["businessKey", "noticeKey", "contractLinkKey"],
    "입찰결과",
)

validate_columns(
    contract,
    CONTRACT_ORIGINAL_COLUMNS,
    ["contractLinkKey"],
    "계약정보",
)


# ============================================================
# 7. 연결키 결측 / 구조 확인
# ============================================================


def print_key_summary(df, key_col, name):
    missing = df[key_col].isna().sum()
    unique = df[key_col].nunique(dropna=True)
    duplicated_rows = df[key_col].duplicated(keep=False).sum()

    print(
        f"{name} - {key_col}: "
        f"결측 {missing:,} / "
        f"고유 {unique:,} / "
        f"복수키 포함행 {duplicated_rows:,}"
    )


print()
print("=" * 70)
print("연결키 확인")
print("=" * 70)

print_key_summary(plan, "businessKey", "조달계획")

print_key_summary(notice, "businessKey", "입찰공고")
print_key_summary(notice, "noticeKey", "입찰공고")

print_key_summary(result, "businessKey", "입찰결과")
print_key_summary(result, "noticeKey", "입찰결과")
print_key_summary(result, "contractLinkKey", "입찰결과")

print_key_summary(contract, "contractLinkKey", "계약정보")


# ============================================================
# 8. CLEAN 데이터 저장
# ============================================================

plan.to_csv(
    PLAN_CLEAN_PATH,
    index=False,
    encoding="utf-8-sig",
)

notice.to_csv(
    NOTICE_CLEAN_PATH,
    index=False,
    encoding="utf-8-sig",
)

result.to_csv(
    RESULT_CLEAN_PATH,
    index=False,
    encoding="utf-8-sig",
)

contract.to_csv(
    CONTRACT_CLEAN_PATH,
    index=False,
    encoding="utf-8-sig",
)


print()
print("=" * 70)
print("CLEAN 데이터 생성 완료")
print("=" * 70)

print(
    f"조달계획 : {len(plan):,}행 / "
    f"{len(plan.columns)}개 컬럼\n{PLAN_CLEAN_PATH}"
)

print(
    f"입찰공고 : {len(notice):,}행 / "
    f"{len(notice.columns)}개 컬럼\n{NOTICE_CLEAN_PATH}"
)

print(
    f"입찰결과 : {len(result):,}행 / "
    f"{len(result.columns)}개 컬럼\n{RESULT_CLEAN_PATH}"
)

print(
    f"계약정보 : {len(contract):,}행 / "
    f"{len(contract.columns)}개 컬럼\n{CONTRACT_CLEAN_PATH}"
)