# -*- coding: utf-8 -*-
"""분석계획표 3번: 사업규모금액 산정·품질검증·5분위 유찰률·카이제곱.

[보관용] 공식 금액 분석은 scripts/analysis/03_bid_amount_analysis.py 이다.
같은 수치를 내는 이전 버전으로, 03번에 없는
06_계획방법미확인_입찰결과교차검증.csv 점검표 때문에 보관한다.

실행: 프로젝트 루트에서 python archive/amount_validation/validate_amount_values.py
2025년 입찰결과는 분석·구간 경계 산정에 사용하지 않는다.
"""

import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib import font_manager
from scipy.stats import chi2_contingency

# ============================================================
# 1. 기본 설정 및 파일 경로
# ============================================================

# 이 파일은 archive/amount_validation/ 에 있으므로 두 단계 위가 프로젝트 루트다.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))
DATA_DIR = os.path.join(PROJECT_DIR, "data", "clean")
MAPPING_DIR = os.path.join(PROJECT_DIR, "data", "analysis", "ornt_code")
OUT_DIR = os.path.join(PROJECT_DIR, "data", "analysis", "bid_amount_4vars")

# 입력 파일
DATA_SUFFIX = "20210101_20251231.csv"
NOTICE_FILE = os.path.join(DATA_DIR, f"bid_notice_clean_{DATA_SUFFIX}")
RESULT_FILE = os.path.join(DATA_DIR, f"bid_result_clean_{DATA_SUFFIX}")
PLAN_FILE = os.path.join(DATA_DIR, f"procure_plan_clean_{DATA_SUFFIX}")
ITEM_FILE = os.path.join(DATA_DIR, f"bid_item_clean_{DATA_SUFFIX}")
MAPPING_FILE = os.path.join(MAPPING_DIR, "orntCode_codeVld4_all.csv")

# 출력 파일
NOTICE_AMOUNTS_FILE = os.path.join(OUT_DIR, "notice_amounts.csv")
BOUNDARIES_FILE = os.path.join(OUT_DIR, "00_2021_2023_학습_5분위경계.csv")
AMOUNT_SOURCES_FILE = os.path.join(OUT_DIR, "01_금액출처별_공고수.csv")
DESCRIPTIVE_FILE = os.path.join(OUT_DIR, "02_사업규모금액_기술통계.csv")
FAILURE_RATES_FILE = os.path.join(OUT_DIR, "03_사업규모_5분위_유찰률.csv")
CHI_SQUARE_FILE = os.path.join(OUT_DIR, "04_카이제곱_CramersV.csv")
YEARLY_RATES_FILE = os.path.join(OUT_DIR, "07_연도별_5분위_유찰률.csv")
INVALID_AMOUNTS_FILE = os.path.join(OUT_DIR, "08_금액미확인_불일치_점검.csv")
METHOD_AUDIT_FILE = os.path.join(OUT_DIR, "06_계획방법미확인_입찰결과교차검증.csv")
TRAIN_CHART_FILE = os.path.join(OUT_DIR, "05_2021_2023_5분위_유찰률.png")
VALID_CHART_FILE = os.path.join(OUT_DIR, "05_2024_5분위_유찰률.png")
YEARLY_CHART_FILE = os.path.join(OUT_DIR, "06_연도별_5분위_유찰률.png")

# 분석 기준
AMOUNT_DISAGREEMENT_RATE = 0.10
TRAIN_YEARS = (2021, 2023)
VALID_YEAR = 2024
QUANTILES = [0.2, 0.4, 0.6, 0.8]
QUINTILE_LABELS = ["Q1", "Q2", "Q3", "Q4", "Q5"]
YEAR_GROUPS = {"2021_2023": (2021, 2023), "2024": (2024, 2024)}


def find_project_root():
    """기존 KPI 코드와 호환되는 프로젝트 경로 확인 함수."""
    required_files = [NOTICE_FILE, RESULT_FILE, PLAN_FILE, ITEM_FILE, MAPPING_FILE]
    missing_files = [path for path in required_files if not os.path.isfile(path)]

    if missing_files:
        raise FileNotFoundError(
            "필수 CLEAN 데이터 또는 기관그룹 매핑 파일이 없습니다. "
            "프로젝트 루트의 data/clean/ 과 data/analysis/ornt_code/ 경로를 확인하세요.\n"
            + "\n".join(missing_files)
        )

    return PROJECT_DIR


# ============================================================
# read_csv
# ============================================================

def read_csv(path):
    return pd.read_csv(path, dtype=str, encoding="utf-8-sig", low_memory=False)


# ============================================================
# to_numeric
# ============================================================

def to_numeric(series):
    cleaned = series.astype("string").str.replace(",", "", regex=False).str.strip()
    return pd.to_numeric(cleaned, errors="coerce")


# ============================================================
# save_csv
# ============================================================

def save_csv(frame, path):
    frame.to_csv(path, index=False, encoding="utf-8-sig")


# ============================================================
# result_status
# ============================================================

def result_status(values):
    """품목별 입찰결과를 공고별 단일 상태로 합친다."""
    statuses = set(values.dropna().astype(str).str.strip()) - {""}

    if "유찰" in statuses and len(statuses) > 1:
        return "판단보류"
    if statuses == {"유찰"}:
        return "유찰"
    if statuses and statuses.issubset({"낙찰", "순위확정"}):
        return "낙찰" if "낙찰" in statuses else "순위확정"
    return "판단보류"


# ============================================================
# prepare_data
# ============================================================

def prepare_data(data_dir, mapping_path):
    notice = read_csv(NOTICE_FILE)
    results = read_csv(RESULT_FILE)
    plans = read_csv(PLAN_FILE)
    items = read_csv(ITEM_FILE)
    mapping = read_csv(MAPPING_FILE)

    if not notice["noticeKey"].is_unique or not plans["businessKey"].is_unique:
        raise ValueError("공고키 또는 조달계획 사업키가 중복됩니다.")

    statuses = (
        results.groupby("noticeKey")["bidResult"]
        .apply(result_status)
        .rename("result_status")
    )
    notice = notice.join(statuses, on="noticeKey")
    notice["date"] = pd.to_datetime(
        notice["pblancDate"].astype(str).str[:8],
        format="%Y%m%d",
        errors="coerce",
    )
    notice["year"] = notice["date"].dt.year
    notice["fail"] = notice["result_status"].map(
        {"유찰": 1, "낙찰": 0, "순위확정": 0}
    )

    valid_notices = (
        notice["year"].between(2021, 2024)
        & notice["fail"].notna()
        & ~notice["cntrctMth"].fillna("").str.contains("수의")
    )
    notice = notice.loc[valid_notices].copy()
    notice["fail"] = notice["fail"].astype(int)

    org_mapping = mapping[["orntCode", "기관그룹"]].drop_duplicates("orntCode")
    notice = notice.merge(
        org_mapping, on="orntCode", how="left", validate="many_to_one"
    )
    notice["기관그룹"] = notice["기관그룹"].fillna("미분류")

    plans = plans[["businessKey", "bidMth", "budgetAmount"]].copy()
    plans["budget"] = to_numeric(plans["budgetAmount"])
    plans["budget"] = plans["budget"].where(plans["budget"].gt(0))

    items["item_amt"] = to_numeric(items["totlAmnt"])
    items["item_valid"] = items["item_amt"].notna() & items["item_amt"].ge(0)
    item_totals = (
        items.groupby("businessKey")
        .agg(
            rows=("item_amt", "size"),
            valid_rows=("item_valid", "sum"),
            item_sum=("item_amt", "sum"),
        )
        .reset_index()
    )
    complete = (
        item_totals["rows"].eq(item_totals["valid_rows"])
        & item_totals["item_sum"].gt(0)
    )
    item_totals["item_sum"] = item_totals["item_sum"].where(complete)

    data = (
        notice.merge(plans, on="businessKey", how="left", validate="many_to_one")
        .merge(item_totals, on="businessKey", how="left", validate="many_to_one")
    )
    # 조달계획에 입찰방법이 없는 과거 결과확정 공고만 입찰결과로 복원한다.
    # 주의: 2021~2024년 과거 공고만 해당. 신규·2025 공고에는 적용하지 않는다.
    data["plan_method"] = data["bidMth"].astype("string").str.strip()
    data["method"] = data["plan_method"].copy()
    data["method_source"] = "조달계획"

    needs_recovery = ~data["method"].isin(["총액제", "단가제"])
    result_methods = (
        results.groupby("noticeKey")["bidMth"]
        .agg(lambda values: sorted(set(
            values.dropna().astype("string").str.strip()
        ) - {""}))
    )

    # 동일 공고의 복수 결과에 서로 다른 입찰방법이 있으면 임의로 선택하지 않는다.
    unique_method = result_methods.map(
        lambda methods: methods[0]
        if len(methods) == 1 and methods[0] in ("총액제", "단가제")
        else pd.NA
    )
    recovered = data["noticeKey"].map(unique_method)
    recovered_mask = needs_recovery & recovered.isin(["총액제", "단가제"])
    data.loc[recovered_mask, "method"] = recovered.loc[recovered_mask]
    data.loc[recovered_mask, "method_source"] = "입찰결과_사후복원"
    data.loc[needs_recovery & ~recovered_mask, "method_source"] = "미확인"
    data["amount"] = np.nan
    data["amount_source"] = "미확인"

    is_total = data["method"].eq("총액제").fillna(False)
    is_unit = data["method"].eq("단가제").fillna(False)

    # 총액제: 계획예산 우선, 예산이 없을 때만 완전한 품목합계 사용.
    use_budget = is_total & data["budget"].notna()
    data.loc[use_budget, "amount"] = data.loc[use_budget, "budget"]
    data.loc[use_budget, "amount_source"] = "계획예산"

    replace_total = is_total & data["budget"].isna() & data["item_sum"].notna()
    data.loc[replace_total, "amount"] = data.loc[replace_total, "item_sum"]
    data.loc[replace_total, "amount_source"] = "총액제_품목대체"

    # 단가제: 계획예산을 총액으로 간주하지 않고 완전한 품목합계만 사용.
    use_item = is_unit & data["item_sum"].notna()
    data.loc[use_item, "amount"] = data.loc[use_item, "item_sum"]
    data.loc[use_item, "amount_source"] = "단가제_품목합계"

    # 총액제: |계획예산 - 품목합계| / 계획예산 > 10%이면 금액 사용 보류.
    # 분모는 두 금액의 최솟값이 아니라, 우선 금액 출처인 조달계획 예산이다.
    can_compare = is_total & data["budget"].notna() & data["item_sum"].notna()
    data["relative_gap"] = np.nan
    data.loc[can_compare, "relative_gap"] = (
        (data.loc[can_compare, "budget"] - data.loc[can_compare, "item_sum"]).abs()
        / data.loc[can_compare, "budget"]
    )
    disagreement = can_compare & data["relative_gap"].gt(AMOUNT_DISAGREEMENT_RATE)
    data.loc[disagreement, "amount"] = np.nan
    data.loc[disagreement, "amount_source"] = "총액제_불일치10pct초과"

    data["half"] = (
        data["date"].dt.year.astype(str)
        + "H"
        + np.where(data["date"].dt.month.le(6), "1", "2")
    )
    return data, disagreement, is_total, is_unit


# ============================================================
# audit_result_bid_method
# ============================================================

def audit_result_bid_method(data, data_dir, output_dir):
    """조달계획 bidMth 미확인 공고를 입찰결과 bidMth로 조사한다.

    입찰결과 bidMth는 과거 결과확정 공고의 방법 복원에만 활용한다.
    신규 공고 및 2025년 결과를 이용한 복원은 하지 않는다.
    공고키의 결과 행이 여러 개일 수 있어 값 충돌도 구분한다.
    """
    results = read_csv(RESULT_FILE)

    def summarize_methods(values):
        unique_values = sorted({
            value
            for value in values.dropna().astype("string").str.strip()
            if value and value not in ("<NA>", "nan")
        })
        if not unique_values:
            return "입찰결과_방법없음"
        if len(unique_values) > 1:
            return "입찰결과_방법충돌"
        if unique_values[0] not in ("총액제", "단가제"):
            return "입찰결과_기타값"
        return unique_values[0]

    method_by_notice = (
        results.groupby("noticeKey", dropna=False)["bidMth"]
        .apply(summarize_methods)
        .rename("결과입찰방법")
    )

    checked = data[[
        "noticeKey", "businessKey", "date", "year", "plan_method", "method", "method_source",
        "budget", "item_sum", "amount", "amount_source",
    ]].copy()
    checked = checked.join(method_by_notice, on="noticeKey")
    checked["결과입찰방법"] = checked["결과입찰방법"].fillna(
        "입찰결과_방법없음"
    )
    checked["계획방법확인"] = checked["plan_method"].isin(["총액제", "단가제"])
    checked["결과방법확인"] = checked["결과입찰방법"].isin(["총액제", "단가제"])
    checked["방법일치"] = (
        checked["계획방법확인"]
        & checked["결과방법확인"]
        & checked["plan_method"].eq(checked["결과입찰방법"])
    )
    checked["방법불일치"] = (
        checked["계획방법확인"]
        & checked["결과방법확인"]
        & ~checked["방법일치"]
    )

    missing_plan = checked.loc[~checked["계획방법확인"]].copy()
    missing_plan["진단"] = np.select(
        [
            missing_plan["결과방법확인"],
            missing_plan["결과입찰방법"].eq("입찰결과_방법충돌"),
            missing_plan["결과입찰방법"].eq("입찰결과_기타값"),
        ],
        ["결과에서_확인", "결과내_방법충돌", "결과방법_기타값"],
        default="결과에서도_미확인",
    )

    save_csv(
        missing_plan,
        METHOD_AUDIT_FILE,
    )

    print("\n[입찰방법 사후 교차검증 — KPI 계산에는 사용하지 않음]")
    print(f"조달계획 입찰방법 미확인: {len(missing_plan):,}건")
    print(missing_plan["진단"].value_counts().to_string())
    print("\n입찰결과의 총액제/단가제 확인 결과")
    print(missing_plan["결과입찰방법"].value_counts().to_string())
    print(f"계획·결과 방법 양쪽 확인 공고 간 불일치: {checked['방법불일치'].sum():,}건")
    print("※ 입찰방법 복원은 결과확정 과거 공고의 사후 분석에만 적용합니다.")


# ============================================================
# configure_font
# ============================================================

def configure_font():
    available = {font.name for font in font_manager.fontManager.ttflist}
    for name in ("Malgun Gothic", "AppleGothic", "NanumGothic"):
        if name in available:
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False


# ============================================================
# amount_quality_by_period
# ============================================================

def amount_quality_by_period(data):
    summary = (
        data.groupby(["기간", "amount_source"], dropna=False)
        .agg(공고수=("noticeKey", "size"))
        .reset_index()
    )
    summary["금액사용가능"] = ~summary["amount_source"].isin(
        ["미확인", "총액제_불일치10pct초과"]
    )
    return summary


# ============================================================
# descriptive_statistics
# ============================================================

def descriptive_statistics(data):
    rows = []
    for period, group in data.groupby("기간", sort=True):
        values = group["amount"].dropna()
        q1, q3 = values.quantile([0.25, 0.75])
        iqr = q3 - q1
        upper = q3 + 1.5 * iqr
        rows.append({
            "기간": period,
            "분석대상공고": len(group),
            "금액유효": len(values),
            "금액미확인": len(group) - len(values),
            "금액유효율_pct": len(values) / len(group) * 100,
            "평균_원": values.mean(),
            "중앙값_원": values.median(),
            "표준편차_원": values.std(),
            "최솟값_원": values.min(),
            "Q1_원": q1,
            "Q3_원": q3,
            "최댓값_원": values.max(),
            "IQR_원": iqr,
            "IQR상한_원": upper,
            "IQR초과건수": int((values > upper).sum()),
            "1000원미만양수건수": int(((values > 0) & (values < 1_000)).sum()),
        })
    return pd.DataFrame(rows)


# ============================================================
# group_failure_rates
# ============================================================

def group_failure_rates(data):
    valid = data.dropna(subset=["금액5분위"]).copy()
    grouped = (
        valid.groupby(["기간", "금액5분위"], observed=False)["fail"]
        .agg(공고수="size", 유찰건수="sum")
        .reset_index()
    )
    grouped["비유찰건수"] = grouped["공고수"] - grouped["유찰건수"]
    grouped["유찰률_pct"] = grouped["유찰건수"] / grouped["공고수"].replace(0, np.nan) * 100
    return grouped


# ============================================================
# test_association
# ============================================================

def test_association(grouped):
    """카이제곱 독립성 검정. 통계적 연관성은 인과·예측 개선을 뜻하지 않음."""
    results = []
    for period in YEAR_GROUPS:
        part = grouped.loc[grouped["기간"].eq(period)].copy()
        observed = part[["유찰건수", "비유찰건수"]].to_numpy(dtype=float)
        if len(observed) < 2 or (observed.sum(axis=1) == 0).any():
            raise ValueError(f"{period}의 빈 구간 때문에 카이제곱 검정 불가")
        chi2, p_value, dof, expected = chi2_contingency(observed, correction=False)
        n = observed.sum()
        cramers_v = np.sqrt(chi2 / (n * min(observed.shape[0] - 1, observed.shape[1] - 1)))
        results.append({
            "기간": period,
            "검정공고수": int(n),
            "chi2": chi2,
            "df": dof,
            "p_value": p_value,
            "Cramers_V": cramers_v,
            "최소기대빈도": expected.min(),
            "기대빈도5미만_셀수": int((expected < 5).sum()),
        })
    return pd.DataFrame(results)


# ============================================================
# yearly_failure_rates
# ============================================================

def yearly_failure_rates(data):
    grouped = (
        data.dropna(subset=["금액5분위"])
        .groupby(["year", "금액5분위"], observed=False)["fail"]
        .agg(공고수="size", 유찰건수="sum")
        .reset_index()
    )
    grouped["유찰률_pct"] = grouped["유찰건수"] / grouped["공고수"].replace(0, np.nan) * 100
    return grouped


# ============================================================
# draw_charts
# ============================================================

def draw_charts(data, group_rates, yearly, folder):
    configure_font()
    # 단일 그림마다 한 차트만 작성한다.
    for period in YEAR_GROUPS:
        part = group_rates.loc[group_rates["기간"].eq(period)].copy()
        labels = part["금액5분위"].astype(str).tolist()
        fig, ax = plt.subplots(figsize=(9, 4.8))
        bars = ax.bar(labels, part["유찰률_pct"])
        ax.bar_label(bars, labels=[f"{v:.2f}%" for v in part["유찰률_pct"]], padding=3)
        ax.set_title(f"{period.replace('_', '~')} 사업규모 5분위별 유찰률")
        ax.set_ylabel("실제 유찰률 (%)")
        ax.set_ylim(0, max(part["유찰률_pct"].max() * 1.2, 1))
        ax.grid(axis="y", alpha=0.2)
        fig.tight_layout()
        fig.savefig(TRAIN_CHART_FILE if period == "2021_2023" else VALID_CHART_FILE, dpi=160)
        plt.close(fig)

    pivot = yearly.pivot(index="year", columns="금액5분위", values="유찰률_pct")
    pivot = pivot.reindex(columns=QUINTILE_LABELS)
    ax = pivot.plot(kind="line", marker="o", figsize=(9, 5))
    ax.set_title("연도별 사업규모 5분위 유찰률 변화 (학습 기준 경계 고정)")
    ax.set_xlabel("공고 연도")
    ax.set_ylabel("유찰률 (%)")
    ax.set_xticks([2021, 2022, 2023, 2024])
    ax.grid(alpha=0.2)
    fig = ax.get_figure()
    fig.tight_layout()
    fig.savefig(YEARLY_CHART_FILE, dpi=160)
    plt.close(fig)


def prepare_amount_analysis(root):
    """공식 4변수 기준으로 2021~2024 결과확정 경쟁성 공고를 준비한다."""
    data, disagreement, _, _ = prepare_data(DATA_DIR, MAPPING_FILE)

    train = data.loc[data["year"].between(*TRAIN_YEARS)].copy()
    edges = train["amount"].dropna().quantile(QUANTILES).to_numpy()
    if len(np.unique(edges)) != 4:
        raise ValueError("금액 5분위 경계가 중복됩니다.")

    data["금액5분위"] = pd.cut(
        data["amount"],
        bins=[-np.inf, *edges, np.inf],
        labels=QUINTILE_LABELS,
        right=True,
    )
    data["기간"] = np.where(data["year"].eq(VALID_YEAR), "2024", "2021_2023")
    return data, edges, disagreement, DATA_DIR


def main():
    """금액 분석 전체를 수행하고 KPI가 재사용할 공고 단위 금액표를 저장한다."""
    root = find_project_root()
    os.makedirs(OUT_DIR, exist_ok=True)
    print("프로젝트:", PROJECT_DIR)
    print("금액 분석 결과:", OUT_DIR)

    data, edges, disagreement, _ = prepare_amount_analysis(root)
    audit_result_bid_method(data, DATA_DIR, OUT_DIR)

    # 공고별 최종 금액표는 KPI 코드가 동일한 변수 정의를 공유하는 기준 자료다.
    detail_cols = [
        "noticeKey", "businessKey", "date", "year", "half", "기관그룹",
        "busiDivs", "cntrctMth", "fail", "plan_method", "method",
        "method_source", "budget", "rows", "valid_rows", "item_sum",
        "relative_gap", "amount", "amount_source", "금액5분위",
    ]
    save_csv(data[detail_cols], NOTICE_AMOUNTS_FILE)

    source_counts = amount_quality_by_period(data)
    descriptive = descriptive_statistics(data)
    rates = group_failure_rates(data)
    chi_square = test_association(rates)
    yearly = yearly_failure_rates(data)

    save_csv(
        pd.DataFrame({"분위": ["Q20", "Q40", "Q60", "Q80"], "상한금액_원": edges}),
        BOUNDARIES_FILE,
    )
    save_csv(source_counts, AMOUNT_SOURCES_FILE)
    save_csv(descriptive, DESCRIPTIVE_FILE)
    save_csv(rates, FAILURE_RATES_FILE)
    save_csv(chi_square, CHI_SQUARE_FILE)
    save_csv(yearly, YEARLY_RATES_FILE)
    save_csv(
        data.loc[
            data["amount"].isna(),
            ["noticeKey", "year", "method", "method_source", "budget",
             "item_sum", "relative_gap", "amount_source"],
        ],
        INVALID_AMOUNTS_FILE,
    )
    draw_charts(data, rates, yearly, OUT_DIR)
    print("\n[분석 대상 및 금액 유효]")
    print(descriptive[["기간", "분석대상공고", "금액유효", "금액미확인"]].to_string(index=False))
    print("\n[5분위별 유찰률]")
    print(rates.to_string(index=False))
    print("\n[카이제곱·Cramér's V]")
    print(chi_square.to_string(index=False))
    print("\n※ KPI 계산은 scripts/kpi/kpi_2024_validation.py에서 별도로 수행합니다.")


if __name__ == "__main__":
    main()
