# -*- coding: utf-8 -*-
"""
A5. 유찰 이후 후속공고 및 재공고 분석
==============================================

[분석 질문]
유찰 후 후속공고는 얼마나 확인되며,
재공고까지 관측된 추가 소요일은 어느 정도인가?

[모집단]
2021~2025년에 게시된 유찰 공고

[핵심 기준]
- 원공고: noticeKey
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
import seaborn as sns


# ============================================================
# 0. 설정
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "clean"
OUTPUT_DIR = PROJECT_ROOT / "data" / "analysis" / "a5_output_project"
BASELINE_DIR = PROJECT_ROOT / "data" / "analysis" / "a5_output_before_fix"

START_DATE = pd.Timestamp("2021-01-01")
END_DATE = pd.Timestamp("2025-12-31")
OBSERVATION_END = pd.Timestamp("2025-12-31")
LAST_QUARTER_START = pd.Timestamp("2025-10-01")
SENSITIVITY_DAYS = 90

EXCLUDED_FOLLOWUP_TYPES = {"취소공고"}
CONFIRMED_RESULTS = {"유찰", "낙찰", "순위확정"}

REFERENCE = {
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
         "opengDt", "pblancSe", "pblancOdr"],
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

    # 원공고는 2021~2025년 게시 유찰 공고
    subjects = n.loc[
        n.notice_date.between(START_DATE, END_DATE)
        & n.result_status.eq("유찰")
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
    """기존 프로젝트 문서 참고값과 실제 실행값의 차이를 기록한다."""
    actual = {
        "공고 전체": len(n),
        "공고키 직접매칭": int(
            n.connection_status.eq("결과 직접매칭").sum()
        ),
        "결과 미확인": int(
            n.connection_status.eq("결과 미확인").sum()
        ),
        "취소공고": int(
            n.connection_status.eq("취소공고").sum()
        ),
        "동일사업 다른 공고 결과 존재": int(
            n.connection_status.eq("동일사업 다른 공고 결과 존재").sum()
        ),
        "유찰 공고": len(detail),
        "후속공고 확인": int(detail.followup_found.sum()),
        "후속공고 미확인": int((~detail.followup_found).sum()),
        "후속 재공고 확인": int(detail.followup_reannounce.sum()),
        "후속공고 결과확정": int(detail.followup_result_known.sum()),
        "후속공고 결과미확인": int(
            (detail.followup_found & ~detail.followup_result_known).sum()
        ),
        "후속 재공고 결과확정": int(
            detail.followup_reannounce_result_known.sum()
        ),
        "2025년 4분기 제외 결과확정 후속 재공고": int(
            (
                detail.last_quarter_excluded
                & detail.followup_reannounce_result_known
            ).sum()
        ),
    }

    return pd.DataFrame([
        {
            "검증 항목": key,
            "문서 기준": expected,
            "실행값": actual[key],
            "차이": actual[key] - expected,
            "일치": actual[key] == expected,
        }
        for key, expected in REFERENCE.items()
    ])


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
# 8. PNG 시각화
# ============================================================

def create_a5_plots(detail, output_dir):
    """
    A5 보고서용 시각화 4개를 생성한다.

    1. 후속공고 확인/미확인
    2. 후속공고 → 재공고 → 결과확정 현황
    3. 공식 추가 소요일 히스토그램
    4. 마지막 분기 제외 소요일 상자그림
    """
    system = platform.system()

    if system == "Darwin":
        font = "AppleGothic"
    elif system == "Windows":
        font = "Malgun Gothic"
    else:
        font = "DejaVu Sans"

    plt.rcParams["font.family"] = font
    plt.rcParams["axes.unicode_minus"] = False
    sns.set_theme(style="whitegrid", font=font)

    # --------------------------------------------------------
    # 8-1. 후속공고 확인 현황
    # --------------------------------------------------------
    total = len(detail)
    found = int(detail.followup_found.sum())
    missing = total - found

    labels = ["후속공고 확인", "후속공고 미확인"]
    values = [found, missing]

    fig, ax = plt.subplots(figsize=(9, 6))
    bars = ax.bar(
        labels,
        values,
        color=["#4C78A8", "#BDBDBD"],
        width=0.55,
    )

    for bar, value in zip(bars, values):
        pct = value / total * 100 if total else 0
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value,
            f"{value:,}건\n({pct:.1f}%)",
            ha="center",
            va="bottom",
        )

    ax.set_title("A5. 후속공고 확인 현황", fontsize=15)
    ax.set_ylabel("공고 수(건)")
    ax.set_ylim(0, max(values) * 1.2 if max(values) else 1)

    fig.tight_layout()
    fig.savefig(
        output_dir / "a5_followup_status.png",
        dpi=300, bbox_inches="tight",
    )
    plt.close(fig)

    # --------------------------------------------------------
    # 8-2. 후속 재공고 및 결과확정 현황
    # --------------------------------------------------------
    labels = [
        "후속공고 확인",
        "후속 재공고 확인",
        "결과확정\n후속 재공고",
    ]
    values = [
        int(detail.followup_found.sum()),
        int(detail.followup_reannounce.sum()),
        int(detail.followup_reannounce_result_known.sum()),
    ]

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.bar(
        labels,
        values,
        color=["#4C78A8", "#F58518", "#54A24B"],
        width=0.6,
    )

    for bar, value in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value,
            f"{value:,}건",
            ha="center",
            va="bottom",
        )

    ax.set_title("A5. 후속공고·재공고 확인 단계", fontsize=15)
    ax.set_ylabel("공고 수(건)")
    ax.set_ylim(0, max(values) * 1.2 if max(values) else 1)

    fig.tight_layout()
    fig.savefig(
        output_dir / "a5_reannounce_status.png",
        dpi=300, bbox_inches="tight",
    )
    plt.close(fig)

    # --------------------------------------------------------
    # 8-3. 결과확정 후속 재공고 소요일 분포
    # --------------------------------------------------------
    days = pd.to_numeric(
        detail.loc[detail.duration_valid, "delay_days"],
        errors="coerce",
    ).dropna()

    if not days.empty:
        fig, ax = plt.subplots(figsize=(11, 6))

        sns.histplot(
            days,
            bins=30,
            color="#4C78A8",
            edgecolor="white",
            ax=ax,
        )

        mean_days = days.mean()
        median_days = days.median()

        ax.axvline(
            mean_days,
            color="#D62728",
            linestyle="--",
            linewidth=2,
            label=f"평균 {mean_days:.1f}일",
        )
        ax.axvline(
            median_days,
            color="#2CA02C",
            linestyle="--",
            linewidth=2,
            label=f"중앙값 {median_days:.1f}일",
        )

        ax.set_title(
            "A5. 결과확정 후속 재공고 추가 소요일 분포",
            fontsize=15,
        )
        ax.set_xlabel("추가 소요일(일)")
        ax.set_ylabel("공고 수(건)")
        ax.legend()

        fig.tight_layout()
        fig.savefig(
            output_dir / "a5_duration_distribution.png",
            dpi=300, bbox_inches="tight",
        )
        plt.close(fig)

    # --------------------------------------------------------
    # 8-4. 마지막 분기 제외 소요일 비교
    # --------------------------------------------------------
    full = pd.to_numeric(
        detail.loc[detail.duration_valid, "delay_days"],
        errors="coerce",
    ).dropna()

    excluded = pd.to_numeric(
        detail.loc[
            detail.duration_valid & detail.last_quarter_excluded,
            "delay_days",
        ],
        errors="coerce",
    ).dropna()

    if not full.empty and not excluded.empty:
        plot_df = pd.DataFrame({
            "구간": (
                ["전체"] * len(full)
                + ["2025년 4분기 제외"] * len(excluded)
            ),
            "추가 소요일": pd.concat(
                [full, excluded], ignore_index=True
            ),
        })

        fig, ax = plt.subplots(figsize=(10, 6))

        sns.boxplot(
            data=plot_df,
            x="구간",
            y="추가 소요일",
            color="#76A5D4",
            showfliers=True,
            ax=ax,
        )

        ax.set_title(
            "A5. 마지막 분기 제외 전후 추가 소요일 비교",
            fontsize=15,
        )
        ax.set_xlabel("")
        ax.set_ylabel("추가 소요일(일)")

        fig.tight_layout()
        fig.savefig(
            output_dir / "a5_duration_boxplot.png",
            dpi=300, bbox_inches="tight",
        )
        plt.close(fig)

    print("[시각화] A5 PNG 생성 완료")


# ============================================================
# 9. 메인 분석
# ============================================================

def main():
    """입력부터 통계·검증·PNG 생성까지 전체 파이프라인 실행."""

    parser = argparse.ArgumentParser(
        description="A5 후속공고·재공고·추가 소요일 분석"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="CLEAN CSV 디렉터리",
    )

    args = parser.parse_args()

    data_dir = args.data_dir
    output_dir = OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 65)
    print("A5 후속공고 및 재공고 분석 시작")
    print("=" * 65)
    print(f"입력 폴더: {data_dir}")
    print(f"출력 폴더: {output_dir}")
    print(f"관측 종료일: {OBSERVATION_END.date()}")

    # --------------------------------------------------------
    # 9-1. 입력 데이터
    # --------------------------------------------------------
    notices = load_one(data_dir, "bid_notice_clean_")
    results = load_one(data_dir, "bid_result_clean_")

    n, _ = prepare(notices, results)

    (
        detail,
        history,
        temporal_review,
        same_time_review,
        cutoff_review,
    ) = build_followup(n)

    # 원공고 게시일이 분석기간 밖인 유찰공고
    out_of_period = n.loc[
        n.result_status.eq("유찰")
        & ~n.notice_date.between(START_DATE, END_DATE)
    ].copy()

    # --------------------------------------------------------
    # 9-2. 요약 및 민감도 분석
    # --------------------------------------------------------
    summary = pd.DataFrame([
        summarize(detail, "2021~2025 전체"),
        summarize(
            detail.loc[detail.last_quarter_excluded],
            "2025년 4분기 제외",
        ),
        summarize(
            detail.loc[detail.complete_90d_observation],
            "90일 완전 관측 가능",
        ),
    ])

    validation = make_validation(n, detail, summary)
    metrics_90d = make_90day_metrics(detail)

    # 보고서 필수 요약
    report_columns = [
        "구간", "추적 대상 건수",
        "후속공고 확인 건수", "후속공고 미확인 건수",
        "기간 계산 가능 건수", "후속공고 확인 비율(%)",
        "평균(일)", "중앙값(일)", "Q1(일)", "Q3(일)",
    ]

    report = summary[report_columns].copy()

    # 후속공고 전체 소요일 통계
    broad_columns = [
        "구간", "추적 대상 건수",
        "후속공고 확인 건수", "후속공고 미확인 건수",
        "후속공고 확인 비율(%)",
        "후속공고 전체 기간 계산 가능 건수",
        "후속공고 전체 평균(일)",
        "후속공고 전체 중앙값(일)",
        "후속공고 전체 Q1(일)",
        "후속공고 전체 Q3(일)",
    ]
    broad = summary[broad_columns].copy()

    # --------------------------------------------------------
    # 9-3. 기존 CSV 출력
    # --------------------------------------------------------
    export(report, output_dir, "a5_required_metrics.csv")
    export(broad, output_dir, "a5_all_followup_durations.csv")
    export(out_of_period, output_dir, "a5_out_of_period_failures.csv")
    export(summary, output_dir, "a5_summary.csv")
    export(validation, output_dir, "a5_validation_report.csv")
    export(detail, output_dir, "a5_followup_detail.csv")

    export(
        detail.loc[detail.followup_found],
        output_dir,
        "a5_followup_review.csv",
    )
    export(
        detail.loc[~detail.followup_found],
        output_dir,
        "a5_unconfirmed_review.csv",
    )
    export(
        detail.loc[detail.same_time_next],
        output_dir,
        "a5_same_time_review.csv",
    )
    export(
        detail.loc[detail.duration_valid],
        output_dir,
        "a5_official_duration_rows.csv",
    )
    export(
        history,
        output_dir,
        "a5_business_notice_history.csv",
    )

    # --------------------------------------------------------
    # 9-4. 신규 검토 및 90일 CSV
    # --------------------------------------------------------
    export(
        temporal_review,
        output_dir,
        "a5_temporal_order_review.csv",
    )
    export(
        cutoff_review,
        output_dir,
        "a5_observation_cutoff_review.csv",
    )
    export(
        same_time_review,
        output_dir,
        "a5_same_time_candidate_review.csv",
    )
    export(
        metrics_90d,
        output_dir,
        "a5_90day_metrics.csv",
    )

    # --------------------------------------------------------
    # 9-5. 기존 기관·계약방식·사업유형별 요약
    # --------------------------------------------------------
    for column in ("orntCode", "cntrctMth", "busiDivs"):
        if column in detail.columns:
            rows = [
                summarize(group, f"{column}={name}")
                for name, group in detail.groupby(column, dropna=False)
            ]
            export(
                pd.DataFrame(rows),
                output_dir,
                f"a5_by_{column}.csv",
            )

    # --------------------------------------------------------
    # 9-6. 수정 전후 비교
    # --------------------------------------------------------
    compare_with_baseline(detail, output_dir)

    # --------------------------------------------------------
    # 9-7. 보고서용 PNG 4개 생성
    # --------------------------------------------------------
    create_a5_plots(detail, output_dir)

    # --------------------------------------------------------
    # 9-8. 콘솔 검증 결과
    # --------------------------------------------------------
    print("\n=== A5 보고서 필수 지표 ===")
    print(report.to_string(index=False))

    print("\n=== 기존 문서 수치 비교 ===")
    print(validation.to_string(index=False))

    print("\n=== 90일 민감도 분석 ===")
    print(metrics_90d.to_string(index=False))

    print("\n=== 후속공고 연결 검토 ===")
    print(f"유찰 원공고: {len(detail):,}건")
    print(f"후속공고 확인: {int(detail.followup_found.sum()):,}건")
    print(
        "결과확정 후속 재공고:",
        f"{int(detail.followup_reannounce_result_known.sum()):,}건",
    )
    print(
        "동일 개찰시각 관련 원공고:",
        f"{int(detail.same_time_next.sum()):,}건",
    )
    print(f"동일 개찰시각 후보 행: {len(same_time_review):,}건")
    print(
        "시간 역전 관련 원공고:",
        f"{int(detail.temporal_order_review.sum()):,}건",
    )
    print(f"시간 역전 후보 행: {len(temporal_review):,}건")
    print(
        "후속공고 개찰일 관측기간 초과:",
        f"{int(detail.next_opening_after_cutoff.sum()):,}건",
    )

    print("\n" + "=" * 65)
    print("A5 분석 완료")
    print(f"결과 폴더: {output_dir}")
    print("=" * 65)

    print("※ 미확인 공고를 미재공고로 해석하지 마세요.")
    print("※ 문서 참고값과의 차이는 검증 CSV에서 확인하세요.")
    print("※ 결과확정일 부재에 따른 관측 한계를 보고서에 명시하세요.")


# ============================================================
# 10. VS Code 실행 버튼 지원
# ============================================================

if __name__ == "__main__":
    main()
