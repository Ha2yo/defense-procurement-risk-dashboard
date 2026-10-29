# -*- coding: utf-8 -*-
"""
A5. 유찰 이후 후속공고 및 재공고 분석
==============================================

[분석 질문]
유찰 후 후속공고는 얼마나 확인되며,
재공고까지 관측된 추가 소요일은 어느 정도인가?

[모집단]
2021~2025년에 게시된 결과확정 유찰 공고 중 수의계약을 제외한 경쟁성 공고

[핵심 기준]
- 원공고: noticeKey (수의계약 제외; 계약방법 문자열에 "수의" 포함 시 제외)
- 후속 후보: 계약방식 변경 가능성을 관측하기 위해 수의계약 여부와 관계없이 검색
- 동일 사업: businessKey
- 후속공고: 동일 사업 내 개찰시각이 엄격히 늦은 유효 공고
- 시간 검증: 후속 후보 게시일 >= 원공고 개찰일(날짜 단위)
- 관측 종료일: 2025-12-31
- 추가 소요일: 후속 개찰일 - 원공고 개찰일
- 공식 소요일: 결과확정 후속 재공고 대상

[주의]
후속공고 미확인은 미재공고 확정이 아니다.
결과확정일이 없다면 개찰일은 결과확정일의 대체 기준일 뿐이다.
같은 날짜에 게시된 공고는 게시시각의 선후를 확정할 수 없다.
"""

from __future__ import annotations

import argparse
import platform
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker


# ============================================================
# 0. 설정
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "clean"
OUTPUT_DIR = PROJECT_ROOT / "data" / "analysis" / "rebid_followup"
BASELINE_DIR = PROJECT_ROOT / "data" / "analysis" / "a5_output_before_fix"

START_DATE = pd.Timestamp("2021-01-01")
END_DATE = pd.Timestamp("2025-12-31")
OBSERVATION_END = pd.Timestamp("2025-12-31")
LAST_QUARTER_START = pd.Timestamp("2025-10-01")
SENSITIVITY_DAYS = 90

EXCLUDED_FOLLOWUP_TYPES = {"취소공고"}
CONFIRMED_RESULTS = {"유찰", "낙찰", "순위확정"}

LEGACY_REFERENCE = {
    "공고 전체": 61142,
    "공고키 직접매칭": 38025,
    "결과 미확인": 13769,
    "취소공고": 5578,
    "동일사업 다른 공고 결과 존재": 3770,
    "유찰 공고": 9845,
    "후속공고 확인": 3663,
    "후속공고 미확인": 6182,
    "후속 재공고 확인": 3606,
    "후속공고 결과확정": 3043,
    "후속공고 결과미확인": 620,
    "후속 재공고 결과확정": 3002,
    "2025년 4분기 제외 결과확정 후속 재공고": 2933,
}


# ============================================================
# 1. 공통 함수
# ============================================================

def load_one(folder: Path, prefix: str) -> pd.DataFrame:
    """접두사에 해당하는 CSV를 정확히 하나 읽는다."""
    matches = sorted(folder.glob(prefix + "*.csv"))

    if len(matches) != 1:
        raise FileNotFoundError(
            f"{folder}에서 {prefix}*.csv 파일이 "
            f"정확히 1개여야 합니다. 발견: {[p.name for p in matches]}"
        )

    print(f"[입력] {matches[0].name}")

    return pd.read_csv(
        matches[0],
        dtype="string",
        encoding="utf-8-sig",
        low_memory=False,
    )


def assert_columns(df, required, name):
    """필수 컬럼의 존재 여부를 검증한다."""
    missing = sorted(set(required) - set(df.columns))

    if missing:
        raise ValueError(f"[{name}] 필수 컬럼 누락: {missing}")


def clean_text(series):
    """공백·문자열 결측값을 정리하되 식별코드 선행 0은 유지한다."""
    return (
        series.astype("string")
        .str.strip()
        .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
    )


def parse_datetime(series):
    """
    YYYYMMDD, YYYYMMDDHHMM, YYYYMMDDHHMMSS를 변환한다.
    변환 불가능한 값은 NaT로 처리한다.
    """
    values = clean_text(series).str.replace(r"\D", "", regex=True)
    result = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")

    for digits, fmt in [
        (14, "%Y%m%d%H%M%S"),
        (12, "%Y%m%d%H%M"),
        (8, "%Y%m%d"),
    ]:
        mask = values.str.len().eq(digits).fillna(False)

        if mask.any():
            result.loc[mask] = pd.to_datetime(
                values.loc[mask],
                format=fmt,
                errors="coerce",
            )

    return result


def export(df, folder, filename):
    """한글 Excel 호환을 위해 UTF-8 BOM으로 CSV 저장."""
    folder.mkdir(parents=True, exist_ok=True)
    df.to_csv(folder / filename, index=False, encoding="utf-8-sig")
    print(f"[저장] {filename}: {len(df):,}행")


# ============================================================
# 2. 공고 단위 결과 판정
# ============================================================

def prepare(notices, results):
    """
    입찰결과를 noticeKey 단위로 집계하여 공고와 연결한다.

    품목별 결과가 복수 행일 수 있으므로 공고별로 먼저 판정한다.
    유찰과 비유찰이 혼재하면 판단보류로 처리한다.
    """
    assert_columns(
        notices,
        ["businessKey", "noticeKey", "pblancDate",
         "opengDt", "pblancSe", "pblancOdr", "cntrctMth"],
        "입찰공고",
    )
    assert_columns(
        results,
        ["businessKey", "noticeKey", "bidResult"],
        "입찰결과",
    )

    n = notices.copy()
    r = results.copy()

    for df in (n, r):
        for col in ("noticeKey", "businessKey"):
            df[col] = clean_text(df[col])

    if n.noticeKey.isna().any() or n.noticeKey.duplicated().any():
        raise ValueError("입찰공고 noticeKey에 결측 또는 중복이 있습니다.")

    n["notice_date"] = parse_datetime(n["pblancDate"])
    n["opening_at"] = parse_datetime(n["opengDt"])
    n["notice_round"] = pd.to_numeric(n["pblancOdr"], errors="coerce")
    # 다른 절과 동일한 제외 대상: 수의계약(변형 표기 포함). 결측은 별도 감사 대상으로 남김.
    n["is_private_contract"] = n["cntrctMth"].fillna("").astype("string").str.strip().str.contains("수의", regex=False)
    n["is_private_contract"] = n["is_private_contract"].fillna(False).astype(bool)

    r["bidResult"] = clean_text(r["bidResult"])
    r = r.dropna(subset=["noticeKey"])

    def classify(values):
        """한 공고의 여러 품목 결과를 하나의 공고 결과로 변환한다."""
        v = set(values.dropna().tolist())

        if "유찰" in v and len(v) > 1:
            return "판단보류"
        if v == {"유찰"}:
            return "유찰"
        if v and v.issubset({"낙찰", "순위확정"}) and "낙찰" in v:
            return "낙찰"
        if v == {"순위확정"}:
            return "순위확정"

        return "판단보류" if v else "결과 미확인"

    by_notice = r.groupby("noticeKey", dropna=False)["bidResult"].agg(classify)

    n["direct_result"] = n.noticeKey.isin(by_notice.index)
    n["result_status"] = n.noticeKey.map(by_notice).fillna("결과 미확인")

    matched_business = set(
        n.loc[n.direct_result & n.businessKey.notna(), "businessKey"]
    )

    n["connection_status"] = np.select(
        [
            n.direct_result.fillna(False),
            n.pblancSe.eq("취소공고").fillna(False),
            n.businessKey.isin(matched_business),
        ],
        [
            "결과 직접매칭",
            "취소공고",
            "동일사업 다른 공고 결과 존재",
        ],
        default="결과 미확인",
    )

    return n, r


# ============================================================
# 3. 후속공고 연결
# ============================================================

def build_followup(n):
    """
    유찰 원공고마다 동일 businessKey의 최초 유효 후속공고를 탐색한다.

    [개선 사항]
    - shift(-1)만으로 연결하지 않는다.
    - 동일 개찰시각이 있어도 그 이후 후보를 검색한다.
    - 원공고 이전 게시 후보는 자동 확정하지 않는다.
    - 관측 종료일 이후 게시 후보는 연결하지 않는다.
    - 관측 종료일 이후 개찰 후보는 결과·소요일에서 제외한다.
    """

    # 공식 A5 모집단: 기간 내 경쟁성 유찰 원공고만 사용.
    # 후속 후보는 수의계약으로 전환된 사례도 놓치지 않도록 기존 범위를 유지.
    subjects = n.loc[
        n.notice_date.between(START_DATE, END_DATE)
        & n.result_status.eq("유찰")
        & ~n.is_private_contract
    ].copy()

    subjects = subjects.sort_values(
        ["opening_at", "noticeKey"], kind="stable"
    ).reset_index(drop=True)

    # 후속 후보의 게시일은 반드시 관측 종료일 이내
    eligible = n.loc[
        n.businessKey.notna()
        & n.noticeKey.notna()
        & n.notice_date.notna()
        & n.opening_at.notna()
        & n.notice_date.le(OBSERVATION_END)
        & ~n.pblancSe.isin(EXCLUDED_FOLLOWUP_TYPES).fillna(False)
    ].copy()

    eligible = eligible.sort_values(
        ["businessKey", "opening_at", "notice_date",
         "notice_round", "noticeKey"],
        na_position="last",
        kind="stable",
    )

    # 기존 사업별 공고 이력 CSV 형식 호환용
    # 이 shift 결과는 실제 후속공고 연결에 사용하지 않는다.
    keep = [
        "noticeKey", "opening_at", "notice_date",
        "pblancSe", "result_status", "notice_round",
    ]

    history_group = eligible.groupby("businessKey", sort=False)

    for col in keep:
        eligible["next_" + col] = history_group[col].shift(-1)

    eligible["same_time_next"] = eligible.opening_at.eq(
        eligible.next_opening_at
    ).fillna(False)

    # businessKey별 후보를 묶어 검색 효율 개선
    business_groups = {
        key: group
        for key, group in eligible.groupby("businessKey", sort=False)
    }

    detail = subjects.copy()

    # 기존 next_* 컬럼명 보존
    for col in keep:
        is_datetime = col in ("opening_at", "notice_date")
        detail["next_" + col] = pd.Series(
            pd.NaT if is_datetime else pd.NA,
            index=detail.index,
            dtype="datetime64[ns]" if is_datetime else "object",
        )

    # 검토 플래그
    for col in [
        "same_time_next",
        "temporal_order_review",
        "same_day_order_review",
        "source_opening_after_cutoff",
        "next_opening_after_cutoff",
        "source_opening_missing",
    ]:
        detail[col] = False

    # 후보 단위 검토 자료
    temporal_rows = []
    same_time_rows = []
    cutoff_rows = []

    # 원공고별 후속공고 탐색
    for idx, source in detail.iterrows():
        business_key = source["businessKey"]
        source_key = source["noticeKey"]
        source_opening = source["opening_at"]

        if pd.isna(business_key):
            continue

        if pd.isna(source_opening):
            detail.at[idx, "source_opening_missing"] = True
            continue

        # 원공고 개찰일 자체가 관측 종료일을 넘는 경우
        if source_opening.normalize() > OBSERVATION_END:
            detail.at[idx, "source_opening_after_cutoff"] = True
            cutoff_rows.append({
                "source_noticeKey": source_key,
                "candidate_noticeKey": pd.NA,
                "review_reason": "원공고 개찰일 관측기간 초과",
                "source_opening_at": source_opening,
                "candidate_notice_date": pd.NaT,
                "candidate_opening_at": pd.NaT,
            })
            continue

        group = business_groups.get(business_key)

        if group is None or group.empty:
            continue

        # 자기 자신 제외
        others = group.loc[
            group.noticeKey.ne(source_key).fillna(False)
        ].copy()

        if others.empty:
            continue

        # 동일 개찰시각 후보를 별도 검토 파일에 보존
        same = others.loc[
            others.opening_at.eq(source_opening)
        ]

        if not same.empty:
            detail.at[idx, "same_time_next"] = True

            for _, candidate in same.iterrows():
                same_time_rows.append({
                    "source_noticeKey": source_key,
                    "candidate_noticeKey": candidate["noticeKey"],
                    "businessKey": business_key,
                    "source_opening_at": source_opening,
                    "candidate_opening_at": candidate["opening_at"],
                    "candidate_notice_date": candidate["notice_date"],
                    "candidate_round": candidate["notice_round"],
                    "candidate_type": candidate["pblancSe"],
                    "review_reason": "동일 개찰시각",
                })

        # 반드시 원공고보다 개찰시각이 늦어야 함
        later = others.loc[
            others.opening_at.gt(source_opening)
        ].copy()

        if later.empty:
            continue

        source_date = source_opening.normalize()

        # 개찰은 나중이어도 원공고 유찰 이전에 게시된 후보
        invalid = later.loc[
            later.notice_date.lt(source_date)
        ]

        if not invalid.empty:
            detail.at[idx, "temporal_order_review"] = True

            for _, candidate in invalid.iterrows():
                temporal_rows.append({
                    "source_noticeKey": source_key,
                    "candidate_noticeKey": candidate["noticeKey"],
                    "businessKey": business_key,
                    "source_opening_at": source_opening,
                    "candidate_notice_date": candidate["notice_date"],
                    "candidate_opening_at": candidate["opening_at"],
                    "candidate_round": candidate["notice_round"],
                    "candidate_type": candidate["pblancSe"],
                    "review_reason": "원공고 개찰일 이전 게시",
                })

        # 개찰일과 같은 날 또는 그 이후 게시된 후보만 허용
        valid = later.loc[
            later.notice_date.ge(source_date)
        ].copy()

        if valid.empty:
            continue

        # 최초 유효 후보 선택: 개찰시각 우선
        valid = valid.sort_values(
            ["opening_at", "notice_date", "notice_round", "noticeKey"],
            na_position="last",
            kind="stable",
        )

        chosen = valid.iloc[0]

        # 날짜만 같고 시각은 알 수 없는 후보를 검토 대상으로 표시
        if chosen["notice_date"].normalize() == source_date:
            detail.at[idx, "same_day_order_review"] = True

        # 기존 상세 결과 컬럼명 유지
        for col in keep:
            detail.at[idx, "next_" + col] = chosen[col]

        # 게시 사실은 확인되지만 개찰은 관측기간 이후
        if chosen["opening_at"].normalize() > OBSERVATION_END:
            detail.at[idx, "next_opening_after_cutoff"] = True
            cutoff_rows.append({
                "source_noticeKey": source_key,
                "candidate_noticeKey": chosen["noticeKey"],
                "review_reason": "후속공고 개찰일 관측기간 초과",
                "source_opening_at": source_opening,
                "candidate_notice_date": chosen["notice_date"],
                "candidate_opening_at": chosen["opening_at"],
            })

    # 후속공고 존재 여부
    detail["followup_found"] = detail.next_noticeKey.notna()

    # 후속공고의 유형이 재공고인지 확인
    detail["followup_reannounce"] = (
        detail.followup_found
        & detail.next_pblancSe.eq("재공고").fillna(False)
    )

    # 후속공고 개찰이 관측 종료일 이내에 이루어졌는지
    observable_opening = (
        detail.next_opening_at.notna()
        & detail.next_opening_at.dt.normalize().le(OBSERVATION_END)
    )

    # 개찰일이 종료일 이후인 공고의 결과는 이번 관측에서 제외
    detail["followup_result_known"] = (
        detail.followup_found
        & observable_opening
        & detail.next_result_status.isin(CONFIRMED_RESULTS)
    )

    # 결과확정 후속 재공고
    detail["followup_reannounce_result_known"] = (
        detail.followup_reannounce
        & detail.followup_result_known
    )

    # 추가 소요일 = 후속 개찰일 - 원공고 개찰일
    # 시간 차이가 아닌 날짜 단위 정수 일수
    detail["delay_days"] = (
        detail.next_opening_at.dt.normalize()
        - detail.opening_at.dt.normalize()
    ).dt.days

    # 관측 종료일 이후 개찰은 기간 계산 대상에서 제외
    detail["delay_days"] = detail["delay_days"].where(
        observable_opening
    )

    # 유형·결과확정 제한 없이 기간 계산 가능한 후속공고
    detail["duration_any_valid"] = (
        detail.followup_found
        & detail.delay_days.ge(0).fillna(False)
    )

    # 공식 소요일은 결과확정 후속 재공고만 계산
    detail["duration_valid"] = (
        detail.followup_reannounce_result_known
        & detail.delay_days.ge(0).fillna(False)
    )

    detail["delay_zero_day"] = (
        detail.duration_valid & detail.delay_days.eq(0)
    )

    # 2025년 4분기 원공고 제외
    detail["last_quarter_excluded"] = (
        detail.notice_date.lt(LAST_QUARTER_START)
    )

    # 90일 이상 관측할 기회가 있었는가?
    detail["complete_90d_observation"] = (
        detail.opening_at.notna()
        & (
            detail.opening_at.dt.normalize()
            + pd.Timedelta(days=SENSITIVITY_DAYS)
        ).le(OBSERVATION_END)
    )

    # 실제로 원공고 개찰 후 90일 이내 후속 개찰이 확인되었는가?
    detail["followup_within_90d"] = (
        detail.followup_found
        & observable_opening
        & detail.delay_days.between(0, SENSITIVITY_DAYS).fillna(False)
    )

    temporal_cols = [
        "source_noticeKey", "candidate_noticeKey", "businessKey",
        "source_opening_at", "candidate_notice_date",
        "candidate_opening_at", "candidate_round",
        "candidate_type", "review_reason",
    ]
    same_cols = [
        "source_noticeKey", "candidate_noticeKey", "businessKey",
        "source_opening_at", "candidate_opening_at",
        "candidate_notice_date", "candidate_round",
        "candidate_type", "review_reason",
    ]
    cutoff_cols = [
        "source_noticeKey", "candidate_noticeKey", "review_reason",
        "source_opening_at", "candidate_notice_date",
        "candidate_opening_at",
    ]

    return (
        detail,
        eligible,
        pd.DataFrame(temporal_rows, columns=temporal_cols),
        pd.DataFrame(same_time_rows, columns=same_cols),
        pd.DataFrame(cutoff_rows, columns=cutoff_cols),
    )


# ============================================================
# 4. 기술통계
# ============================================================

def summarize(df, label):
    """
    집단별 후속공고 확인률 및 추가 소요일을 계산한다.

    연결률 분모: 유찰 원공고 수
    공식 소요일 분모: 결과확정 후속 재공고 중 기간 계산 가능 건수
    """
    total = len(df)
    found = int(df.followup_found.sum())
    reann = int(df.followup_reannounce.sum())
    known = int(df.followup_result_known.sum())
    confirmed = int(df.followup_reannounce_result_known.sum())

    official = pd.to_numeric(
        df.loc[df.duration_valid, "delay_days"], errors="coerce"
    ).dropna()

    broad = pd.to_numeric(
        df.loc[df.duration_any_valid, "delay_days"], errors="coerce"
    ).dropna()

    return {
        "구간": label,
        "추적 대상 건수": total,
        "후속공고 확인 건수": found,
        "후속공고 미확인 건수": total - found,
        "후속공고 확인 비율(%)": (
            round(found / total * 100, 2) if total else np.nan
        ),
        "후속 재공고 확인 건수": reann,
        "후속공고 결과확정 건수": known,
        "후속공고 결과미확인 건수": found - known,
        "결과확정 후속 재공고 건수": confirmed,
        "기간 계산 가능 건수": len(official),
        "후속공고 전체 기간 계산 가능 건수": len(broad),
        "후속공고 전체 평균(일)": round(broad.mean(), 2),
        "후속공고 전체 중앙값(일)": round(broad.median(), 2),
        "후속공고 전체 Q1(일)": round(broad.quantile(.25), 2),
        "후속공고 전체 Q3(일)": round(broad.quantile(.75), 2),
        "0일 기간 건수": int((official == 0).sum()),
        "평균(일)": round(official.mean(), 2),
        "중앙값(일)": round(official.median(), 2),
        "Q1(일)": round(official.quantile(.25), 2),
        "Q3(일)": round(official.quantile(.75), 2),
    }


# ============================================================
# 5. 기존 문서 기준값 검증
# ============================================================

def make_validation(n, detail, summary):
    """공식 모집단 정합성을 독립 검사하고, 구 기준값은 감사 참고행으로만 저장."""
    in_period = n.notice_date.between(START_DATE, END_DATE)
    original = n.loc[in_period & n.result_status.eq("유찰")]
    competitive = original.loc[~original.is_private_contract]
    checks = [
        ("공식", "유찰 원공고 정확한 모집단", set(detail.noticeKey) == set(competitive.noticeKey)),
        ("공식", "유찰 원공고 키 유일성", detail.noticeKey.notna().all() and detail.noticeKey.is_unique),
        ("공식", "유찰 원공고 수의계약 제외", not detail.is_private_contract.any()),
        ("공식", "수의계약 제외 건수 보존", len(original) == len(competitive) + int(original.is_private_contract.sum())),
        ("공식", "후속공고 확인/미확인 합계", int(detail.followup_found.sum()) + int((~detail.followup_found).sum()) == len(detail)),
        ("공식", "재공고는 후속공고 부분집합", not (detail.followup_reannounce & ~detail.followup_found).any()),
        ("공식", "공식 소요일은 결과확정 재공고 부분집합", not (detail.duration_valid & ~detail.followup_reannounce_result_known).any()),
        ("공식", "공식 소요일은 0일 이상", not detail.loc[detail.duration_valid, "delay_days"].lt(0).any()),
        ("공식", "관측 종료일 이후 개찰은 공식 소요일 제외", not (detail.duration_valid & detail.next_opening_after_cutoff).any()),
    ]
    rows = [{"검증구분": kind, "검증항목": name, "검증결과": bool(passed), "기준값": "동적 일관성", "실행값": "", "차이": "", "비고": "공식 검증"} for kind,name,passed in checks]
    actual = {
        "공고 전체": len(n),
        "공고키 직접매칭": int(n.connection_status.eq("결과 직접매칭").sum()),
        "결과 미확인": int(n.connection_status.eq("결과 미확인").sum()),
        "취소공고": int(n.connection_status.eq("취소공고").sum()),
        "동일사업 다른 공고 결과 존재": int(n.connection_status.eq("동일사업 다른 공고 결과 존재").sum()),
        "유찰 공고": len(detail),
        "후속공고 확인": int(detail.followup_found.sum()),
        "후속공고 미확인": int((~detail.followup_found).sum()),
        "후속 재공고 확인": int(detail.followup_reannounce.sum()),
        "후속공고 결과확정": int(detail.followup_result_known.sum()),
        "후속공고 결과미확인": int((detail.followup_found & ~detail.followup_result_known).sum()),
        "후속 재공고 결과확정": int(detail.followup_reannounce_result_known.sum()),
        "2025년 4분기 제외 결과확정 후속 재공고": int((detail.last_quarter_excluded & detail.followup_reannounce_result_known).sum()),
    }
    # 과거 수치는 별도 참고행: 기간 외 제외와 수의계약 제외를 하나의 오차로 합치지 않는다.
    # '유찰 공고'의 과거 9,845건과 공식 6,269건 차이는 두 단계로 해석해야 한다.
    all_failed_global = n.loc[n.result_status.eq("유찰")]
    in_period_failed = original
    period_excluded = all_failed_global.loc[~all_failed_global.notice_date.between(START_DATE, END_DATE)]
    for name, old in LEGACY_REFERENCE.items():
        note = "과거 수의계약 포함 참고값; 공식 오류 판정에서 제외"
        if name == "유찰 공고":
            note = (f"서로 다른 모집단: 전체기간 {len(all_failed_global):,}건 → 기간 내 {len(in_period_failed):,}건 "
                    f"(기간 외 {len(period_excluded):,}건 제외) → 경쟁성 {len(competitive):,}건 "
                    f"(수의계약 {int(in_period_failed.is_private_contract.sum()):,}건 제외). "
                    "과거값과 직접 비교한 차이는 오류가 아님")
        rows.append({"검증구분":"과거 수의계약 포함 참고", "검증항목":name,
                     "검증결과":pd.NA, "기준값":old, "실행값":actual[name],
                     "차이":actual[name]-old, "비고":note})
    if not all(bool(value) for _, _, value in checks):
        failed = [name for _, name, passed in checks if not passed]
        raise RuntimeError(f"공식 A5 정합성 검증 실패: {failed}")
    return pd.DataFrame(rows)


def build_conservative_sensitivity(detail, history):
    """원공고 분모 유지. 같은 날 게시된 연결만 배제하고 다음 유효 후보를 재탐색.

    기존 공식 후속공고를 변경하지 않고 독립적인 비교 결과를 산출한다.
    게시일이 원공고 개찰일보다 엄격히 늦고 후보 개찰시각도 늦어야 한다.
    """
    alternative = detail[["noticeKey", "businessKey", "opening_at", "same_day_order_review"]].copy()
    alternative["conservative_next_noticeKey"] = pd.NA
    alternative["conservative_next_pblancSe"] = pd.NA
    alternative["conservative_next_result_status"] = pd.NA
    alternative["conservative_next_opening_at"] = pd.NaT

    # 기존 후속공고가 없거나 날짜가 명백히 늦으면 기존 판정을 그대로 이용.
    safe = detail.followup_found & ~detail.same_day_order_review
    for source, target in [("next_noticeKey", "conservative_next_noticeKey"),
                           ("next_pblancSe", "conservative_next_pblancSe"),
                           ("next_result_status", "conservative_next_result_status"),
                           ("next_opening_at", "conservative_next_opening_at")]:
        alternative.loc[safe, target] = detail.loc[safe, source].to_numpy()

    # 같은 날짜 후보가 선정된 원공고에 대해서만 더 나중에 게시된 후보 검색.
    # history는 개찰시각 기준으로 정렬되어 있으므로 최초 후보는 개찰시각 최소.
    ambiguous = detail.loc[detail.same_day_order_review]
    history_groups = {key: group for key, group in history.groupby("businessKey", sort=False)}
    for idx, source in ambiguous.iterrows():
        group = history_groups.get(source.businessKey)
        if group is None or pd.isna(source.opening_at):
            continue
        candidates = group.loc[
            group.noticeKey.ne(source.noticeKey).fillna(False)
            & group.opening_at.gt(source.opening_at)
            & group.notice_date.gt(source.opening_at.normalize())
        ].sort_values(["opening_at", "notice_date", "notice_round", "noticeKey"], kind="stable")
        if candidates.empty:
            continue
        candidate = candidates.iloc[0]
        for source_col, target_col in [("noticeKey", "conservative_next_noticeKey"),
                                      ("pblancSe", "conservative_next_pblancSe"),
                                      ("result_status", "conservative_next_result_status"),
                                      ("opening_at", "conservative_next_opening_at")]:
            alternative.at[idx, target_col] = candidate[source_col]

    alternative["conservative_followup_found"] = alternative.conservative_next_noticeKey.notna()
    alternative["conservative_reannounce"] = alternative.conservative_followup_found & alternative.conservative_next_pblancSe.eq("재공고").fillna(False)
    opening = pd.to_datetime(alternative.conservative_next_opening_at, errors="coerce")
    observable = opening.notna() & opening.dt.normalize().le(OBSERVATION_END)
    alternative["conservative_result_known"] = alternative.conservative_followup_found & observable & alternative.conservative_next_result_status.isin(CONFIRMED_RESULTS)
    alternative["conservative_delay_days"] = (opening.dt.normalize() - alternative.opening_at.dt.normalize()).dt.days.where(observable)
    alternative["conservative_duration_valid"] = (alternative.conservative_reannounce & alternative.conservative_result_known & alternative.conservative_delay_days.ge(0).fillna(False))
    original = int(detail.followup_found.sum())
    conservative = int(alternative.conservative_followup_found.sum())
    if conservative > original:
        raise RuntimeError("보수적 연결 수가 기존 공식 연결보다 많습니다.")
    n = len(detail)
    duration = alternative.loc[alternative.conservative_duration_valid, "conservative_delay_days"]
    review = pd.DataFrame([
        {"분석구분":"기존 공식 (동일 날짜 후보 허용)", "추적원공고":n, "후속공고확인":original,
         "연결률_pct":100*original/n if n else np.nan,
         "공식소요일표본":int(detail.duration_valid.sum()),
         "공식평균소요일":float(detail.loc[detail.duration_valid,"delay_days"].mean())},
        {"분석구분":"보수적 (개찰일 다음날 이후 게시만 허용)", "추적원공고":n, "후속공고확인":conservative,
         "연결률_pct":100*conservative/n if n else np.nan,
         "공식소요일표본":int(alternative.conservative_duration_valid.sum()),
         "공식평균소요일":float(duration.mean())},
    ])
    return alternative, review


# ============================================================
# 6. 90일 민감도 분석
# ============================================================

def make_90day_metrics(detail):
    """
    아래 두 개념을 명확히 구분한다.

    A. 90일 관측 가능 집단의 전체 관측기간 연결률
    B. 같은 집단에서 실제 90일 이내 개찰된 후속공고 연결률
    """
    observed = detail.loc[detail.complete_90d_observation]
    total = len(observed)

    anytime = int(observed.followup_found.sum())
    within = int(observed.followup_within_90d.sum())

    return pd.DataFrame([
        {
            "지표": "90일 관측 가능 집단의 전체기간 연결률",
            "분모": total,
            "후속공고 확인 건수": anytime,
            "연결률(%)": (
                round(anytime / total * 100, 2) if total else np.nan
            ),
        },
        {
            "지표": "실제 90일 이내 후속공고 연결률",
            "분모": total,
            "후속공고 확인 건수": within,
            "연결률(%)": (
                round(within / total * 100, 2) if total else np.nan
            ),
        },
    ])


# ============================================================
# 7. 수정 전후 연결 비교
# ============================================================

def compare_with_baseline(detail, output_dir):
    """
    백업된 a5_followup_detail.csv가 있으면 비교한다.

    기존 결과:
    data/analysis/a5_output_before_fix/a5_followup_detail.csv

    비교 구분:
    신규 연결 / 연결 해제 / 연결 대상 변경 /
    연결 유지 / 미연결 유지 / 모집단 차이
    """
    baseline_file = BASELINE_DIR / "a5_followup_detail.csv"

    if not baseline_file.exists():
        print("[비교] 이전 결과 백업 없음: 수정 전후 비교 생략")
        return

    old = pd.read_csv(
        baseline_file,
        dtype="string",
        encoding="utf-8-sig",
        low_memory=False,
    )

    assert_columns(
        old,
        ["noticeKey", "next_noticeKey"],
        "수정 전 A5 결과",
    )

    if old.noticeKey.isna().any() or old.noticeKey.duplicated().any():
        raise ValueError("백업 파일의 noticeKey에 결측 또는 중복이 있습니다.")

    previous = old[
        ["noticeKey", "next_noticeKey"]
    ].rename(columns={"next_noticeKey": "old_next_noticeKey"})

    current = detail[
        ["noticeKey", "next_noticeKey"]
    ].rename(columns={"next_noticeKey": "new_next_noticeKey"})

    comparison = previous.merge(
        current,
        on="noticeKey",
        how="outer",
        indicator=True,
        validate="one_to_one",
    )

    old_key = comparison["old_next_noticeKey"]
    new_key = comparison["new_next_noticeKey"]

    conditions = [
        comparison["_merge"].eq("left_only"),
        comparison["_merge"].eq("right_only"),
        old_key.isna() & new_key.notna(),
        old_key.notna() & new_key.isna(),
        (
            old_key.notna()
            & new_key.notna()
            & old_key.ne(new_key).fillna(False)
        ),
        (
            old_key.notna()
            & new_key.notna()
            & old_key.eq(new_key).fillna(False)
        ),
    ]

    comparison["change_type"] = np.select(
        conditions,
        [
            "기존 모집단에만 존재",
            "수정 모집단에만 존재",
            "신규 연결",
            "연결 해제",
            "연결 대상 변경",
            "연결 유지",
        ],
        default="미연결 유지",
    )

    comparison = comparison.drop(columns="_merge")
    export(comparison, output_dir, "a5_connection_changes.csv")

    print("\n[수정 전후 연결 비교]")
    print(comparison.change_type.value_counts().to_string())

    # 공식 추가 소요일 수정 전후 비교
    if {"duration_valid", "delay_days"}.issubset(old.columns):
        old_valid = (
            old["duration_valid"].astype("string")
            .str.lower().eq("true").fillna(False)
        )

        old_days = pd.to_numeric(
            old.loc[old_valid, "delay_days"],
            errors="coerce",
        ).dropna()

        new_days = pd.to_numeric(
            detail.loc[detail.duration_valid, "delay_days"],
            errors="coerce",
        ).dropna()

        rows = []
        for label, values in [
            ("수정 전", old_days),
            ("수정 후", new_days),
        ]:
            rows.append({
                "구분": label,
                "기간 계산 가능 건수": len(values),
                "평균(일)": round(values.mean(), 2),
                "중앙값(일)": round(values.median(), 2),
                "Q1(일)": round(values.quantile(.25), 2),
                "Q3(일)": round(values.quantile(.75), 2),
            })

        export(
            pd.DataFrame(rows),
            output_dir,
            "a5_duration_before_after.csv",
        )



# ============================================================
# 8. 출력 패키지: 요약 CSV · 공고 CSV · 감사 CSV · 개별 PNG (단일 폴더)
# ============================================================

def save_csv_tables(folder: Path, tables: dict[str, pd.DataFrame], prefix: str) -> list[Path]:
    """기존 Excel 시트별 DataFrame을 UTF-8 BOM CSV로 저장한다.

    일부 감사 데이터가 0행이어도 컬럼 헤더를 유지하여 검증 이력을 보존한다.
    """
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for number, (name, table) in enumerate(tables.items(), 1):
        path = folder / f"{prefix}_{number:02d}_{name}.csv"
        table.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"[CSV] {path.name}: {len(table):,}행")
        paths.append(path)
    return paths


class PNGPages:
    """시각화 7종을 단일 출력 폴더에 PNG로 저장하는 어댑터."""
    TITLES = [
        '01_followup_stages', '02_link_rate_sensitivity',
        '03_duration_sensitivity', '04_duration_histogram',
        '05_duration_ecdf', '06_by_business_type',
        '07_observation_90day',
    ]

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.index = 0
        self.folder.mkdir(parents=True, exist_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def savefig(self, figure, **kwargs):
        if self.index >= len(self.TITLES):
            raise RuntimeError('기대 그래프 7개를 초과했습니다.')
        destination = self.folder / f'{self.TITLES[self.index]}.png'
        figure.savefig(destination, dpi=200, facecolor='white', **kwargs)
        print(f'[PNG] {destination.name}')
        self.index += 1


def _chart_font():
    # macOS / Windows / Linux 한글 폰트 검색. 그래프 글자 깨짐 방지.
    from matplotlib import font_manager
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for family in ('NanumGothic', 'AppleGothic', 'Malgun Gothic', 'NanumSquare', 'UnDotum', 'DejaVu Sans'):
        if family in installed:
            plt.rcParams['font.family'] = family
            break
    plt.rcParams['axes.unicode_minus'] = False
    plt.rcParams['pdf.fonttype'] = 42


def _pdf_page(pdf, title, desc, draw):
    fig, ax = plt.subplots(figsize=(11.7, 7.8))
    fig.subplots_adjust(left=.13, right=.96, bottom=.19, top=.81)
    fig.suptitle(title, fontsize=18, weight='bold', y=.95)
    draw(ax)
    fig.text(.08, .07, desc, fontsize=9, color='#444444', wrap=True)
    pdf.savefig(fig, bbox_inches='tight')
    plt.close(fig)


def create_report_pngs(path, detail, summary, conservative_summary, metrics_90d, subgroup):
    """해석 및 분모를 포함한 시각화 7개를 개별 PNG로 저장."""
    _chart_font()
    n = len(detail)
    found = int(detail.followup_found.sum())
    reann = int(detail.followup_reannounce.sum())
    known = int(detail.followup_reannounce_result_known.sum())
    durations = pd.to_numeric(detail.loc[detail.duration_valid, 'delay_days'], errors='coerce').dropna()

    with PNGPages(path) as pdf:
        def draw_flow(ax):
            names = ['경쟁성 유찰', '후속공고 확인', '후속 재공고', '결과확정 재공고']
            vals = [n, found, reann, known]
            y = np.arange(len(vals))
            ax.barh(y, vals, color=['#24476B','#3877A3','#3C9280','#71A98E'])
            ax.set_yticks(y, names)
            ax.invert_yaxis()
            ax.set_xlim(0, n*1.16)
            ax.set_xlabel('공고 수 (건)')
            for i, v in enumerate(vals):
                ax.text(v+max(1,n*.012), i, f'{v:,}건  ({v/n:.1%}, 최초 분모)', va='center')
            ax.grid(axis='x', alpha=.18)
        _pdf_page(pdf, 'A5 | 경쟁성 유찰 이후 후속공고 단계',
                  f'모든 비율은 최초 경쟁성 유찰 원공고 {n:,}건을 기준. 단계 간 부분집합 관계를 나타내며 각각 독립 모집단이 아님.', draw_flow)

        def draw_connect(ax):
            a = conservative_summary.copy()
            names = ['공식: 동일일 후보 허용', '보수적: 다음날 게시']
            vals = pd.to_numeric(a['연결률_pct'],errors='coerce').to_numpy()
            ax.bar(names, vals, color=['#326C98','#E4A24A'], width=.50)
            ax.set_ylabel('후속공고 연결률 (%)')
            ax.set_ylim(0, max(vals)*1.32)
            for i, v in enumerate(vals):
                ax.text(i, v+.5, f'{v:.2f}%\n({int(a.iloc[i]["후속공고확인"]):,}/{n:,})', ha='center')
            ax.grid(axis='y',alpha=.17)
        _pdf_page(pdf,'A5 | 공식·보수적 연결률 비교',
                  f'공식값은 게시일=개찰일 후보를 허용해 시간 순서가 불확실할 수 있음. 보수적 기준은 개찰일 다음 날 이후 게시 후보만 인정·재탐색. 두 방식 모두 경쟁성 원공고 {n:,}건이 분모. 보수적 수치는 공식 KPI의 민감도 비교용.',draw_connect)

        def draw_mean(ax):
            a=conservative_summary
            vals=pd.to_numeric(a['공식평균소요일'], errors='coerce').to_numpy()
            ax.bar(['공식 연결', '보수적 연결'], vals, width=.52, color=['#326C98','#E4A24A'])
            ax.set_ylim(0, max(vals)*1.4)
            ax.set_ylabel('결과확정 후속 재공고 평균 추가 소요일 (일)')
            for i,v in enumerate(vals):
                ax.text(i, v+.3, f'{v:.2f}일  (n={int(a.iloc[i]["공식소요일표본"]):,})',ha='center')
            ax.grid(axis='y',alpha=.17)
        _pdf_page(pdf,'A5 | 추가 소요일: 공식·보수적 비교',
                  '공고별 개찰일 차이이며 예상 절감일수가 아님. 비교 집단이 다르므로 평균 변화는 인과 효과가 아님.',draw_mean)

        def draw_hist(ax):
            if durations.empty: return
            bins=np.arange(durations.min()-.5,durations.max()+1.5,2)
            ax.hist(durations, bins=bins, color='#4C78A8',edgecolor='white',alpha=.88)
            ax.axvline(durations.mean(),color='#D44E42',ls='--',lw=2,label=f'평균 {durations.mean():.2f}일')
            ax.axvline(durations.median(),color='#2A8B71',ls='--',lw=2,label=f'중앙값 {durations.median():.0f}일')
            ax.set_xlabel('개찰일 기준 추가 소요일 (일)')
            ax.set_ylabel('공고 수 (건)')
            ax.legend()
            ax.grid(axis='y',alpha=.18)
        _pdf_page(pdf,'A5 | 결과확정 후속 재공고 소요일 분포',
                  f'공식 소요일 표본 n={len(durations):,}건. 평균·중앙값을 동시에 표시하여 긴 꼬리와 이상값의 영향 구분.',draw_hist)

        def draw_ecdf(ax):
            if durations.empty: return
            values=np.sort(durations.to_numpy(float))
            ax.step(values,np.arange(1,len(values)+1)/len(values)*100,where='post',color='#326C98',lw=2)
            for cutoff in [7,14,30,60]:
                pct=(values<=cutoff).mean()*100
                ax.axvline(cutoff,ls=':',color='#777',alpha=.55)
                ax.text(cutoff+1,min(98,pct+2),f'{cutoff}일 {pct:.1f}%',fontsize=9)
            ax.set_xlim(left=0)
            ax.set_ylim(0,103)
            ax.set_ylabel('누적 비율 (%)')
            ax.set_xlabel('추가 소요일 (일 이하)')
            ax.grid(alpha=.17)
        _pdf_page(pdf,'A5 | 공식 소요일 누적분포 (ECDF)',
                  f'결과확정 후속 재공고 {len(durations):,}건의 누적 소요일. 7·14·30·60일 이내 관측 비율을 보여줌.',draw_ecdf)

        def draw_div(ax):
            groups=subgroup['사업구분'].copy()
            if groups.empty: return
            groups=groups.sort_values('추적 대상 건수',ascending=False).head(8)
            names=groups['구간'].astype(str).str.replace('busiDivs=', '',regex=False).tolist()
            vals=pd.to_numeric(groups['후속공고 확인 비율(%)'],errors='coerce').fillna(0).to_numpy()
            ys=np.arange(len(vals))
            ax.barh(ys,vals,color='#326C98')
            ax.set_yticks(ys,names)
            ax.invert_yaxis()
            ax.set_xlim(0,max(5,vals.max()*1.3))
            for i,v in enumerate(vals):
                ax.text(v+.3,i,f'{v:.1f}% (n={int(groups.iloc[i]["추적 대상 건수"]):,})',va='center')
            ax.set_xlabel('후속공고 연결률 (%)')
        _pdf_page(pdf,'A5 | 사업구분별 연결률',
                  '기술통계 비교이며 원인 또는 인과효과로 해석하지 않음. 각 범주별 원공고 수 표시.',draw_div)

        def draw_observation(ax):
            rows=metrics_90d.copy()
            labels=rows['지표'].astype(str).tolist()
            values=pd.to_numeric(rows['연결률(%)'],errors='coerce').to_numpy()
            ys=np.arange(len(values))
            ax.barh(ys,values,color='#6B91B8')
            ax.set_yticks(ys,labels)
            ax.invert_yaxis()
            ax.set_xlim(0,max(5,np.nanmax(values)*1.33))
            for i,v in enumerate(values):
                ax.text(v+.3,i,f'{v:.2f}% (n={int(rows.iloc[i]["분모"]):,})',va='center')
            ax.set_xlabel('연결률 (%)')
        _pdf_page(pdf,'A5 | 관측기간과 90일 민감도',
                  '완전 관측 가능 집단 연결률과 그중 90일 이내 연결률은 정의가 다름. 미확인을 미재공고로 확정하지 않음.',draw_observation)
    print('[PNG] 개별 시각화 7개:',path)


def build_integrated_outputs(detail, history, summary, validation, conservative_detail,
                             conservative_summary, metrics_90d, cohort_audit,
                             temporal_review, same_time_review, cutoff_review,
                             out_of_period, output_dir, debug_history=False):
    """분석 과정의 DataFrame은 유지하고 CSV와 PNG를 한 출력 폴더에 저장."""
    # 공식/보수적 모두 원공고 noticeKey 1:1 일치하여야 한다.
    if len(detail)!=len(conservative_detail) or detail.noticeKey.duplicated().any():
        raise RuntimeError('원공고/보수적 산출물 1:1 관계 위반')
    if set(detail.noticeKey)!=set(conservative_detail.noticeKey):
        raise RuntimeError('공식/보수적 공고키 불일치')
    reduced=conservative_detail.drop(columns=['businessKey','opening_at','same_day_order_review'],errors='ignore')
    combined=detail.merge(reduced,on='noticeKey',how='left',validate='one_to_one')
    if len(combined)!=len(detail):
        raise RuntimeError('통합 상세 행수 보존 오류')
    if not combined.noticeKey.is_unique:
        raise RuntimeError('통합 상세 공고키 중복 발생')

    subgroup={}
    for name,col in [('사업구분','busiDivs'),('계약방법','cntrctMth'),('기관별','orntCode')]:
        rows=[summarize(g,f'{col}={key}') for key,g in detail.groupby(col,dropna=False)]
        subgroup[name]=pd.DataFrame(rows)

    # 중요: 감사 파일에는 후보 단계 원시 근거를 시트로 보존.
    summary_tabs={
        '핵심요약':summary,
        '보수적민감도':conservative_summary,
        '90일관측':metrics_90d,
        '사업구분':subgroup['사업구분'],
        '계약방법':subgroup['계약방법'],
        '기관별':subgroup['기관별'],
    }
    audit_tabs={
        '공식검증':validation,
        '계약방식모집단':cohort_audit,
        '시간순서후보':temporal_review,
        '동일개찰시각후보':same_time_review,
        '관측종료후보':cutoff_review,
        '기간외유찰':out_of_period[['noticeKey','pblancDate','cntrctMth','result_status']].copy() if len(out_of_period) else pd.DataFrame(),
        '검증건수요약':pd.DataFrame([{'항목':k,'건수':v} for k,v in {
            '공식원공고':len(detail),'공식연결':int(detail.followup_found.sum()),
            '미연결':int((~detail.followup_found).sum()),'공식소요일':int(detail.duration_valid.sum()),
            '동일게시일검토':int(detail.same_day_order_review.sum()),
            '동일개찰시각원공고':int(detail.same_time_next.sum()),
            '시간역전원공고':int(detail.temporal_order_review.sum()),
            '보수적연결':int(conservative_detail.conservative_followup_found.sum()),
            '기간내 유찰(수의계약 포함)':int((detail.shape[0] + cohort_audit.loc[cohort_audit['항목'].eq('[4] 분석기간 내 수의계약 유찰 제외'),'건수'].iloc[0])),
        }.items()])
    }
    # 모든 CSV와 PNG를 하나의 폴더에 평면 저장한다. 분석 중간 DataFrame은 그대로 보존한다.
    # 아웃풋 중 어느 하나라도 실패하면 성공 메시지 출력 금지.
    save_csv_tables(output_dir, summary_tabs, 'a5_summary')
    combined.to_csv(output_dir/'a5_followup_detail.csv',index=False,encoding='utf-8-sig')
    print(f'[CSV] a5_followup_detail.csv: {len(combined):,}행')
    save_csv_tables(output_dir, audit_tabs, 'a5_audit')
    create_report_pngs(output_dir,detail,summary,conservative_summary,metrics_90d,subgroup)
    if debug_history:
        history.to_csv(output_dir/'a5_business_history.csv',index=False,encoding='utf-8-sig')
    print('[완료] 단일 폴더 저장: CSV 14개(요약 6 + 상세 1 + 감사 7), PNG 7개. 디버그 이력:',bool(debug_history))
    return combined


# ============================================================
# 9. 전체 A5 실행
# ============================================================
def main():
    parser=argparse.ArgumentParser(description='A5 경쟁성 유찰 후속공고 분석: CSV 및 개별 PNG 저장')
    parser.add_argument('--data-dir',type=Path,default=DEFAULT_DATA_DIR)
    parser.add_argument('--output-dir',type=Path,default=OUTPUT_DIR)
    parser.add_argument('--debug',action='store_true',help='원시 사업공고 이력 CSV 추가 생성')
    args=parser.parse_args()
    out=args.output_dir
    out.mkdir(parents=True,exist_ok=True)
    notices=load_one(args.data_dir,'bid_notice_clean_')
    results=load_one(args.data_dir,'bid_result_clean_')
    n,_=prepare(notices,results)
    detail,history,temporal_review,same_time_review,cutoff_review=build_followup(n)
    out_of_period=n.loc[n.result_status.eq('유찰') & ~n.notice_date.between(START_DATE,END_DATE)].copy()
    summary=pd.DataFrame([
        summarize(detail,'2021~2025 전체'),
        summarize(detail.loc[detail.last_quarter_excluded],'2025년 4분기 제외'),
        summarize(detail.loc[detail.complete_90d_observation],'90일 완전 관측 가능')
    ])
    validation=make_validation(n,detail,summary)
    conservative_detail,conservative_summary=build_conservative_sensitivity(detail,history)
    metrics_90d=make_90day_metrics(detail)
    within=n.notice_date.between(START_DATE,END_DATE)
    all_failed=n.loc[within & n.result_status.eq('유찰')]
    private_all=n.loc[within & n.is_private_contract]
    private_failed=all_failed.loc[all_failed.is_private_contract]
    all_failed_global = n.loc[n.result_status.eq('유찰')]
    out_of_period_failed = all_failed_global.loc[~within]
    if len(all_failed_global) != len(out_of_period_failed) + len(all_failed):
        raise RuntimeError('A5 감사: 기간 외 유찰 + 기간 내 유찰 합계 불일치')
    if len(all_failed) != len(private_failed) + len(detail):
        raise RuntimeError('A5 감사: 수의계약 제외 모집단 합계 불일치')
    cohort_audit=pd.DataFrame([
        {'항목':'[1] 전체 기간 결과확정 유찰 공고','건수':len(all_failed_global)},
        {'항목':'[2] 분석기간 외 유찰 제외','건수':len(out_of_period_failed)},
        {'항목':'[3] 2021~2025년 유찰 공고(수의계약 포함)','건수':len(all_failed)},
        {'항목':'[4] 분석기간 내 수의계약 유찰 제외','건수':len(private_failed)},
        {'항목':'[5] 공식 경쟁성 유찰 추적 모집단','건수':len(detail)},
        {'항목':'전체 유찰(수의계약 포함)','건수':len(all_failed)},
        {'항목':'전체 수의계약 공고(결과무관)','건수':len(private_all)},
        {'항목':'수의계약 중 결과확정','건수':int(private_all.result_status.isin(CONFIRMED_RESULTS).sum())},
        {'항목':'수의계약 중 유찰','건수':len(private_failed)},
        {'항목':'공식 경쟁성 유찰 원공고','건수':len(detail)},
        {'항목':'공식 후속공고 확인','건수':int(detail.followup_found.sum())},
        {'항목':'공식 결과확정 후속 재공고','건수':int(detail.followup_reannounce_result_known.sum())},
    ])
    if len(all_failed)!=len(detail)+len(private_failed):
        raise RuntimeError('수의계약 제외 모집단 건수 보존 실패')
    combined=build_integrated_outputs(detail,history,summary,validation,conservative_detail,
                                      conservative_summary,metrics_90d,cohort_audit,
                                      temporal_review,same_time_review,cutoff_review,
                                      out_of_period,out,debug_history=args.debug)
    print('[핵심 검증] 원공고',len(combined),'후속 연결',int(detail.followup_found.sum()),
          '평균 소요일',round(float(detail.loc[detail.duration_valid,'delay_days'].mean()),3))
    print('주의: 후속공고 미확인은 미재공고 확정이 아닙니다. 관측 소요일은 절감효과가 아닙니다.')

if __name__ == '__main__':
    main()
