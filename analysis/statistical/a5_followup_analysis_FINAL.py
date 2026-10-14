# -*- coding: utf-8 -*-
"""
A5. 유찰 이후 후속공고·재공고·추가 소요일 분석 (프로젝트 문서 기준)
======================================================================
[근거 문서]
- 데이터 항목정의서: 3.1~3.4 (사업키/공고키/판정/집계), 4.2~4.3 (결과 매칭, 후속공고)
- 프로젝트 정의: 2-4~2-5 (유찰 결과 및 후속공고/추가 소요일)
- KPI 정의서: 유찰 영향은 예측 KPI와 구분하는 별도 사후분석

[분석 관측 단위]
1) 공고 단위: noticeKey (사업키+공고번호+차수) 1개 = 공고 1건.
2) 사업 단위: businessKey (요구년도+기관코드+판단번호)로 동일 사업 묶기.
3) 입찰결과는 품목(iemNo)으로 인해 noticeKey가 여러 행일 수 있으므로 집계 후 병합.

[기간·정의]
- 유찰 모집단: 실제 pblancDate가 2021-01-01 ~ 2025-12-31인 유찰 공고.
- 후속 탐색: 같은 businessKey의 '이후 개찰 시점'에 위치한 최초 유효 공고.
- 후속 유형: 후보의 pblancSe가 '재공고'인지 여부 별도 표시.
- 소요일: 후속 재공고 개찰일 - 직전 유찰 공고 개찰일.
- 공식 현상분석 수치 비교: 후속 재공고의 결과까지 확정된 대상을 중심으로 집계.
- 기본 탐색에 임의의 90일 제한을 두지 않음; 90일은 민감도 비교에만 사용.
- 공고/결과 미확인을 0 또는 미재공고로 간주하지 않음.

[주의: 해석상 불확실성]
'시간상 다음 유효 공고'의 구체적 동일 시각 처리·정정/연기공고 제외 기준은
문서에 완전히 정해져 있지 않습니다. 본 파일은 기본적으로 '취소공고만 제외'하고,
동일 개찰시각은 공고일→차수→공고키 순으로 정렬합니다.
이러한 구현상의 결정을 변경 가능하도록 설정하며, 문서 기준값과 차이를 출력합니다.
동일시각 공고는 검토 대상이며 자동 연결은 분석용 연결이지 법적 인과확정이 아닙니다.

[실행]
1. VS Code에서 이 파일을 data/clean 안 또는 프로젝트 scripts/ 폴더에 저장
2. pip install pandas numpy
3. python a5_followup_analysis_project.py
   (다른 경로면 python a5_followup_analysis_project.py --data-dir "C:\\...\\clean")
4. data/clean/a5_output_project/ 에 결과 저장
"""

from __future__ import annotations

import argparse
from pathlib import Path
import numpy as np
import pandas as pd

# =============================================================================
# STEP 0. 모두가 합의하여 변경할 수 있는 설정
# =============================================================================
# 프로젝트 최상위 폴더
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 입력 데이터가 있는 폴더
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "clean"
# 분석 결과를 저장할 폴더
DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT / "data" / "analysis" / "a5_output_project"
)
START_DATE = pd.Timestamp("2021-01-01")
END_DATE = pd.Timestamp("2025-12-31")
LAST_QUARTER_START = pd.Timestamp("2025-10-01")
OBSERVATION_END = pd.Timestamp("2025-12-31")
SENSITIVITY_DAYS = 90
# '유효 후속공고'에 해당하지 않는 유형. 정의서는 취소 후속 여부를 구체적으로 지정하지 않아
# 취소만 제외하는 구현 선택을 문서화함. 정정/연기도 제외하려면 아래 집합에 추가.
EXCLUDED_FOLLOWUP_TYPES = {"취소공고"}
# 시간상 다음 공고: 동일 개찰시각은 독립적인 순서를 확정하기 어려우므로
# 기본적으로 연결하지 않고 '동일시각 검토 후보'로 집계함.
ALLOW_EQUAL_OPENING_TIME = False

# 데이터 정의서·프로젝트 정의서의 CLEAN 기준 재현 목표 (계산에 사용되지 않음)
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


def load_one(folder: Path, prefix: str) -> pd.DataFrame:
    """파일 접두사로 정확히 1개만 읽음. 문자열 dtype으로 코드의 선행 0을 보존."""
    matches = sorted(folder.glob(prefix + "*.csv"))
    if len(matches) != 1:
        raise FileNotFoundError(f"{folder} : {prefix}*.csv 파일이 1개여야 합니다. 발견: {[p.name for p in matches]}")
    print(f"[입력] {matches[0].name}")
    return pd.read_csv(matches[0], dtype="string", encoding="utf-8-sig", low_memory=False)


def assert_columns(df: pd.DataFrame, expected: list[str], dataset: str) -> None:
    missing = sorted(set(expected) - set(df.columns))
    if missing:
        raise ValueError(f"[{dataset}] 필수 열 없음: {missing}; 실제 열: {list(df.columns)}")


def clean_text(s: pd.Series) -> pd.Series:
    """값을 정규화하되 식별코드의 앞자리 0은 지우지 않음."""
    return s.astype("string").str.strip().replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})


def parse_datetime(s: pd.Series) -> pd.Series:
    """202101291300(분)·20210129(일) 등의 문자열을 안전하게 날짜로 변환."""
    x = clean_text(s).str.replace(r"\D", "", regex=True)
    # 분초까지 있는 자료도 지원. 자릿수로 처리하여 Pandas의 임의 날짜 추정을 막음.
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    for digits, fmt in [(14, "%Y%m%d%H%M%S"), (12, "%Y%m%d%H%M"), (8, "%Y%m%d")]:
        mask = x.str.len().eq(digits).fillna(False)
        if mask.any():
            out.loc[mask] = pd.to_datetime(x.loc[mask], format=fmt, errors="coerce")
    return out


# =============================================================================
# STEP 1. 원본 식별자 검증 + 입찰결과 공고 단위 요약
# =============================================================================
def prepare(notices: pd.DataFrame, results: pd.DataFrame):
    n_required = ["businessKey", "noticeKey", "pblancDate", "opengDt", "pblancSe", "pblancOdr"]
    r_required = ["businessKey", "noticeKey", "bidResult"]
    assert_columns(notices, n_required, "입찰공고")
    assert_columns(results, r_required, "입찰결과")
    n = notices.copy()
    r = results.copy()
    for df in (n, r):
        for c in ("noticeKey", "businessKey"):
            df[c] = clean_text(df[c])
    # 누락키를 서로 같은 사업으로 자동 연결하는 위험 방지
    if n.noticeKey.isna().any() or n.noticeKey.duplicated().any():
        raise ValueError("입찰공고 noticeKey가 유일하지 않거나 결측입니다. 원천 데이터 확인 필요.")
    n["notice_date"] = parse_datetime(n.pblancDate)
    n["opening_at"] = parse_datetime(n.opengDt)
    n["notice_round"] = pd.to_numeric(n.pblancOdr, errors="coerce")
    r["bidResult"] = clean_text(r.bidResult)
    r = r.dropna(subset=["noticeKey"])

    # 문서 3.2: 낙찰+순위확정은 '낙찰', 유찰+비유찰 혼재는 '판단보류'.
    def classify(values) -> str:
        vs = set(values.dropna().tolist())
        if "유찰" in vs and len(vs) > 1:
            return "판단보류"
        if vs == {"유찰"}:
            return "유찰"
        if vs and vs.issubset({"낙찰", "순위확정"}) and "낙찰" in vs:
            return "낙찰"
        if vs == {"순위확정"}:
            return "순위확정"
        return "판단보류" if vs else "결과 미확인"

    by_notice = r.groupby("noticeKey", dropna=False)["bidResult"].agg(classify)
    # '공고키가 결과에 직접 존재하는지'는 결과 확정 여부와 별도 개념.
    n["direct_result"] = n.noticeKey.isin(by_notice.index)
    n["result_status"] = n.noticeKey.map(by_notice).fillna("결과 미확인")
    matched_business = set(n.loc[n.direct_result & n.businessKey.notna(), "businessKey"])

    # 문서 4.2: 상호배타 순서 ① 직접 ② 취소 ③ 같은사업 다른결과 ④ 미확인
    n["connection_status"] = np.select(
        [n.direct_result, n.pblancSe.eq("취소공고"), n.businessKey.isin(matched_business)],
        ["결과 직접매칭", "취소공고", "동일사업 다른 공고 결과 존재"],
        default="결과 미확인",
    )
    return n, r


# =============================================================================
# STEP 2. 유찰 공고 추출 + 후속공고를 1:1로 연결
# =============================================================================
def build_followup(n: pd.DataFrame):
    # 전체 공고가 후속 후보. 분석모집단(2021~2025)은 유찰 원공고에만 적용.
    subjects = n.loc[
        n.notice_date.between(START_DATE, END_DATE)
        & n.result_status.eq("유찰")
    ].copy()
    subjects = subjects.sort_values(["opening_at", "noticeKey"], kind="stable")

    # 후속 후보 집합. 결측 사업키·개찰일은 순서 비교가 불가능.
    eligible = n.loc[
        n.businessKey.notna()
        & n.opening_at.notna()
        & ~n.pblancSe.isin(EXCLUDED_FOLLOWUP_TYPES)
    ].copy()

    # 공고차수가 선후관계의 조건은 아님. 개찰 순서가 문서상 우선 원칙.
    eligible = eligible.sort_values(
        ["businessKey", "opening_at", "notice_date", "notice_round", "noticeKey"],
        na_position="last", kind="stable"
    )
    # 동일 사업의 직후 공고를 shift로 연결 (자기 자신을 다른 공고로 연결하지 않음).
    # 동일 개찰시각의 다른 공고는 연결 모호성이 있으므로 검토 플래그 부여.
    g = eligible.groupby("businessKey", dropna=False, sort=False)
    keep = ["noticeKey", "opening_at", "notice_date", "pblancSe", "result_status", "notice_round"]
    for c in keep:
        eligible["next_" + c] = g[c].shift(-1)
    eligible["same_time_next"] = eligible.opening_at.eq(eligible.next_opening_at).fillna(False)

    # 원공고가 유효 공고 목록에서 빠지는 경우도 누락시키지 않도록 subject에 일괄 left join.
    follow_cols = ["noticeKey"] + ["next_" + c for c in keep] + ["same_time_next"]
    detail = subjects.merge(
        eligible[follow_cols], on="noticeKey", how="left", validate="one_to_one"
    )
    # 기본은 '개찰시각 엄격 증가'. 같은 개찰시각의 연결은 확정하지 않음.
    if not ALLOW_EQUAL_OPENING_TIME:
        for c in ["next_" + k for k in keep]:
            detail.loc[detail.same_time_next.fillna(False), c] = pd.NA

    detail["followup_found"] = detail.next_noticeKey.notna()
    detail["followup_reannounce"] = detail.followup_found & detail.next_pblancSe.eq("재공고").fillna(False)
    detail["followup_result_known"] = detail.followup_found & detail.next_result_status.isin(["유찰", "낙찰", "순위확정"])
    detail["followup_reannounce_result_known"] = detail.followup_reannounce & detail.followup_result_known

    # 문서 4.3: 개찰일과 개찰일의 차이. 시간이 포함되어 있으므로 '날짜'만 추출해 정수 일수.
    detail["delay_days"] = (
        detail.next_opening_at.dt.normalize() - detail.opening_at.dt.normalize()
    ).dt.days
    # [A5 원문 요구사항] 후속공고만 존재하고 개찰일이 유효하면 소요일 계산 가능.
    # [프로젝트 정의서] 결과확정 후속 '재공고'에서만 공식 추가 소요일 산출.
    # 두 분모는 다르므로 플래그와 보고서를 분리한다.
    detail["duration_any_valid"] = detail.followup_found & detail.delay_days.ge(0).fillna(False)
    detail["duration_valid"] = detail.followup_reannounce_result_known & detail.delay_days.ge(0).fillna(False)
    detail["delay_zero_day"] = detail.duration_valid & detail.delay_days.eq(0)
    detail["last_quarter_excluded"] = detail.notice_date.lt(LAST_QUARTER_START)
    detail["complete_90d_observation"] = (
        detail.opening_at.notna() &
        (detail.opening_at.dt.normalize() + pd.Timedelta(days=SENSITIVITY_DAYS) <= OBSERVATION_END)
    )
    return detail, eligible


# =============================================================================
# STEP 3. 통계: 각각의 분모를 명시적으로 분리
# =============================================================================
def summarize(df: pd.DataFrame, label: str) -> dict:
    total = len(df)                             # 후속 확인 비율의 분모
    found = int(df.followup_found.sum())
    reann = int(df.followup_reannounce.sum())
    result_known = int(df.followup_result_known.sum())
    confirmed_reann = int(df.followup_reannounce_result_known.sum())
    valid = df.loc[df.duration_valid, "delay_days"].dropna().astype(float)  # 문서 기준 기간 통계 분모
    any_valid = df.loc[df.duration_any_valid, "delay_days"].dropna().astype(float)  # A5 요구 원문 기준
    return {
        "구간": label, "추적 대상 건수": total,
        "후속공고 확인 건수": found, "후속공고 미확인 건수": total-found,
        "후속공고 확인 비율(%)": round(100*found/total, 2) if total else np.nan,
        "후속 재공고 확인 건수": reann,
        "후속공고 결과확정 건수": result_known,
        "후속공고 결과미확인 건수": found-result_known,
        "결과확정 후속 재공고 건수": confirmed_reann,
        "기간 계산 가능 건수": len(valid),
        "후속공고 전체 기간 계산 가능 건수": len(any_valid),
        "후속공고 전체 평균(일)": round(any_valid.mean(), 2),
        "후속공고 전체 중앙값(일)": round(any_valid.median(), 2),
        "후속공고 전체 Q1(일)": round(any_valid.quantile(.25), 2),
        "후속공고 전체 Q3(일)": round(any_valid.quantile(.75), 2),
        "0일 기간 건수": int((valid == 0).sum()),
        "평균(일)": round(valid.mean(), 2),
        "중앙값(일)": round(valid.median(), 2),
        "Q1(일)": round(valid.quantile(.25), 2),
        "Q3(일)": round(valid.quantile(.75), 2),
    }


def make_validation(n: pd.DataFrame, detail: pd.DataFrame, summary: pd.DataFrame) -> pd.DataFrame:
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
    v = pd.DataFrame([{
        "검증 항목": key, "문서 기준": expected, "실행값": actual[key],
        "차이": actual[key]-expected, "일치": actual[key] == expected
    } for key, expected in REFERENCE.items()])
    return v


def export(df: pd.DataFrame, folder: Path, filename: str):
    df.to_csv(folder / filename, encoding="utf-8-sig", index=False)
    print(f"  저장: {filename} ({len(df):,}행)")


def main():
    parser = argparse.ArgumentParser(description="A5 프로젝트 정의 기준 후속 재공고/추가 지연 분석")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    args = parser.parse_args()
    folder = args.data_dir
    out = DEFAULT_OUTPUT_DIR
    out.mkdir(exist_ok=True, parents=True)

    notices = load_one(folder, "bid_notice_clean_")
    results = load_one(folder, "bid_result_clean_")
    n, _ = prepare(notices, results)
    detail, history = build_followup(n)

    # 문서 기준 '전체 유찰 9,845건'과 본 분석기간 유찰 9,836건의
    # 차이가 어디서 발생했는지: 범위 밖 원공고를 별도 파일로 확인 가능하게 보존.
    out_of_period_failures = n.loc[
        n.result_status.eq("유찰")
        & ~n.notice_date.between(START_DATE, END_DATE)
    ].copy()
    print(f"[기간 점검] 분석 범위 밖 유찰공고: {len(out_of_period_failures):,}건")

    summary = pd.DataFrame([
        summarize(detail, "2021~2025 전체"),
        summarize(detail.loc[detail.last_quarter_excluded], "2025년 4분기 제외"),
        summarize(detail.loc[detail.complete_90d_observation], "90일 완전 관측 가능"),
    ])
    validation = make_validation(n, detail, summary)
    # A5 보고서 필수 항목만 별도로 구성하여 팀원이 쉽게 비교하도록 출력한다.
    # 후속공고 확인 비율의 분모 = 추적 대상 유찰 공고 전체
    # 평균·중앙값·사분위수의 분모 = 기간 계산 가능 건수
    report_columns = [
        "구간", "추적 대상 건수", "후속공고 확인 건수", "후속공고 미확인 건수",
        "기간 계산 가능 건수", "후속공고 확인 비율(%)",
        "평균(일)", "중앙값(일)", "Q1(일)", "Q3(일)",
    ]
    a5_report = summary.loc[:, report_columns].copy()
    print("\n=== A5 보고서 필수 지표 ===")
    print(a5_report.to_string(index=False))
    export(a5_report, out, "a5_required_metrics.csv")
    # A5 원문의 "연결 + 유효 날짜" 조건으로 구한 별도 요약:
    # 결과확정/재공고 제한은 하지 않으며, 공식 지연일과 혼용하지 않는다.
    broad = summary[["구간", "추적 대상 건수", "후속공고 확인 건수",
                     "후속공고 미확인 건수", "후속공고 확인 비율(%)",
                     "후속공고 전체 기간 계산 가능 건수", "후속공고 전체 평균(일)",
                     "후속공고 전체 중앙값(일)", "후속공고 전체 Q1(일)",
                     "후속공고 전체 Q3(일)"]].copy()
    export(broad, out, "a5_all_followup_durations.csv")

    # 집계분모가 유찰 기준(전체 결과 확정 경쟁성 공고 기준 아님)임을 명시.
    print("\n=== A5 통계 (후속공고 미확인 != 미재공고) ===")
    print(summary.to_string(index=False))
    print("\n=== 프로젝트 문서 기준값 대조 (차이 = 정의 또는 구현 검토 필요) ===")
    print(validation.to_string(index=False))

    # 개별 공고에 연결 근거를 남겨 수동 검증할 수 있도록 보관.
    export(out_of_period_failures, out, "a5_out_of_period_failures.csv")
    export(summary, out, "a5_summary.csv")
    export(validation, out, "a5_validation_report.csv")
    export(detail, out, "a5_followup_detail.csv")
    export(detail.loc[detail.followup_found], out, "a5_followup_review.csv")
    export(detail.loc[~detail.followup_found], out, "a5_unconfirmed_review.csv")
    export(detail.loc[detail.same_time_next.fillna(False)], out, "a5_same_time_review.csv")
    # 기간 계산 가능 원공고의 근거 행을 별도로 제공
    export(detail.loc[detail.duration_valid], out, "a5_official_duration_rows.csv")
    export(history, out, "a5_business_notice_history.csv")

    # 대시보드 연계: 그룹별 기간은 검증된 후속재공고에 한해 계산.
    for c in ("orntCode", "cntrctMth", "busiDivs"):
        if c in detail.columns:
            grouped = [summarize(g, f"{c}={name}") for name, g in detail.groupby(c, dropna=False)]
            export(pd.DataFrame(grouped), out, f"a5_by_{c}.csv")

    print(f"\n완료: {out}")
    print("※ 문서 기준값 불일치 항목은 조용히 보정하지 않았습니다. 검증 CSV를 확인하세요.")
    print("※ 미래의 재공고 정보는 사전 유찰 위험도 산정 변수로 사용하면 안 됩니다.")


if __name__ == "__main__":
    main()
