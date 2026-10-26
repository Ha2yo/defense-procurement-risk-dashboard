# -*- coding: utf-8 -*-
"""분석계획표 A3: 공식 사업규모금액 5분위별 유찰률 분석.

기존 기초금액(bsicExpt) 3구간 분석을 사용하지 않는다.
별도의 KPI/validate 스크립트에 의존하지 않는 독립 실행 파일이다.
프로젝트의 analysis/ 폴더에서 실행한다.
입력: data/clean/ 입찰공고·입찰결과·조달계획·품목명세 CLEAN
출력: data/analysis/bid_amount/ 아래 CSV·PNG
분석기간: 2021~2023 학습, 2024 비교 (공고일 기준); 2025년 결과 사용 안 함.
"""
import os
import glob
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib import font_manager
from scipy.stats import chi2_contingency

# ============================================================
# 1. 경로 설정 (기존 프로젝트의 상대 경로 유지)
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "../..", "data", "clean"))
OUT_DIR = os.path.abspath(os.path.join(BASE_DIR, "../..", "data", "analysis", "bid_amount"))
FILE_PREFIXES = {
    "notice": "bid_notice_clean_20210101_20251231",
    "result": "bid_result_clean_20210101_20251231",
    "plan": "procure_plan_clean_20210101_20251231",
    "item": "bid_item_clean_20210101_20251231",
}
LABELS = ["Q1", "Q2", "Q3", "Q4", "Q5"]
PERIODS = {"2021_2023": (2021, 2023), "2024": (2024, 2024)}
MAX_GAP = 0.10  # 공식 금액 품질 기준: 계획예산 대비 차이율이 10% 초과이면 보류


def locate(prefix):
    direct = os.path.join(DATA_DIR, prefix + ".csv")
    if os.path.exists(direct):
        return direct
    candidates = glob.glob(os.path.join(DATA_DIR, prefix + "*.csv"))
    if len(candidates) == 1:
        return candidates[0]
    raise FileNotFoundError(f"CLEAN 입력 파일을 확인하세요: {direct} (후보 {len(candidates)}개)")


def load_csv(prefix, columns):
    df = pd.read_csv(locate(prefix), dtype="string", encoding="utf-8-sig", low_memory=False)
    missing = set(columns) - set(df.columns)
    if missing:
        raise ValueError(f"{prefix}: 필수 열 누락 {sorted(missing)}")
    for col in columns:
        df[col] = df[col].str.strip().replace("", pd.NA)
    return df


def to_number(series):
    return pd.to_numeric(series.astype("string").str.replace(",", "", regex=False), errors="coerce")


def save(df, filename, index=False):
    os.makedirs(OUT_DIR, exist_ok=True)
    df.to_csv(os.path.join(OUT_DIR, filename), encoding="utf-8-sig", index=index)


def configure_font():
    fonts = {font.name for font in font_manager.fontManager.ttflist}
    for name in ["Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR"]:
        if name in fonts:
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False


# ============================================================
# 2. 공고 단위 결과 판정 및 분석 모집단
# ============================================================
def determine_result(values):
    """유찰 + 비유찰 혼합=판단보류, 낙찰 + 순위확정 혼합=낙찰."""
    labels = set(values.dropna().astype(str).str.strip()) - {""}
    if labels == {"유찰"}:
        return "유찰"
    if labels and labels.issubset({"낙찰", "순위확정"}):
        return "낙찰" if "낙찰" in labels else "순위확정"
    return "판단보류"


def unique_result_method(values):
    values = set(values.dropna().astype(str).str.strip()) - {""}
    if len(values) == 1:
        method = next(iter(values))
        if method in {"총액제", "단가제"}:
            return method
    return pd.NA


def prepare():
    notice = load_csv(FILE_PREFIXES["notice"], [
        "noticeKey", "businessKey", "pblancDate", "pblancSe", "cntrctMth"
    ])
    result = load_csv(FILE_PREFIXES["result"], ["noticeKey", "bidResult", "bidMth"])
    plan = load_csv(FILE_PREFIXES["plan"], ["businessKey", "bidMth", "budgetAmount"])
    item = load_csv(FILE_PREFIXES["item"], ["businessKey", "totlAmnt"])

    if notice["noticeKey"].isna().any() or notice["noticeKey"].duplicated().any():
        raise ValueError("입찰공고 noticeKey 결측/중복 발생")
    if plan["businessKey"].isna().any() or plan["businessKey"].duplicated().any():
        raise ValueError("조달계획 businessKey 결측/중복 발생")

    status = result.groupby("noticeKey")["bidResult"].agg(determine_result).rename("result_status")
    notice = notice.join(status, on="noticeKey")
    notice["date"] = pd.to_datetime(notice["pblancDate"].str[:8], format="%Y%m%d", errors="coerce")
    notice["year"] = notice["date"].dt.year
    valid = (
        notice["year"].between(2021, 2024)
        & notice["result_status"].isin(["유찰", "낙찰", "순위확정"])
        & ~notice["pblancSe"].fillna("").eq("취소공고")
        & ~notice["cntrctMth"].fillna("").str.contains("수의", regex=False)
    )
    data = notice.loc[valid].copy()
    data["fail"] = data["result_status"].eq("유찰").astype(int)
    print("분석 대상 공고:", len(data), "(2021~2023 / 2024)")

    # 품목명세는 행 단위로 공고에 조인하면 공고 중복이 생김: 사업키별 1행 집계.
    item["value"] = to_number(item["totlAmnt"])
    item["valid_row"] = item["value"].notna() & item["value"].ge(0)
    item_sum = item.groupby("businessKey").agg(
        item_rows=("value", "size"),
        valid_item_rows=("valid_row", "sum"),
        item_total=("value", "sum"),
    ).reset_index()
    valid_total = (item_sum["item_rows"] == item_sum["valid_item_rows"]) & item_sum["item_total"].gt(0)
    item_sum.loc[~valid_total, "item_total"] = np.nan

    plan = plan[["businessKey", "bidMth", "budgetAmount"]].rename(columns={"bidMth": "plan_method"})
    plan["budget"] = to_number(plan["budgetAmount"])
    plan.loc[~plan["budget"].gt(0), "budget"] = np.nan
    data = data.merge(plan, on="businessKey", how="left", validate="many_to_one")
    data = data.merge(item_sum, on="businessKey", how="left", validate="many_to_one")

    # 공고 당시 계획 입찰방법 우선. 계획에서 확인 불가 시 과거 공고의 결과로만 복원.
    # 이 파일은 2021~2024 과거 분석 전용이며, 신규·2025 공고에는 적용하지 않는다.
    recovered = result.groupby("noticeKey")["bidMth"].agg(unique_result_method)
    data["method"] = data["plan_method"].where(data["plan_method"].isin(["총액제", "단가제"]))
    data["method_source"] = np.where(data["method"].notna(), "조달계획", "미확인")
    candidates = data["noticeKey"].map(recovered)
    mask = data["method"].isna() & candidates.isin(["총액제", "단가제"])
    data.loc[mask, "method"] = candidates.loc[mask]
    data.loc[mask, "method_source"] = "입찰결과_사후복원"

    # 총액제: 계획예산 우선, 없으면 유효 품목합계. 두 값 차이가 예산의 10% 초과면 보류.
    # 단가제: 계획예산은 사용하지 않고 유효 품목합계만 사용.
    data["amount"] = np.nan
    data["amount_source"] = "미확인"
    total = data["method"].eq("총액제").fillna(False)
    unit = data["method"].eq("단가제").fillna(False)
    budget_ok = total & data["budget"].notna()
    total_fallback = total & data["budget"].isna() & data["item_total"].notna()
    unit_ok = unit & data["item_total"].notna()
    data.loc[budget_ok, "amount"] = data.loc[budget_ok, "budget"]
    data.loc[budget_ok, "amount_source"] = "계획예산"
    data.loc[total_fallback, "amount"] = data.loc[total_fallback, "item_total"]
    data.loc[total_fallback, "amount_source"] = "총액제_품목대체"
    data.loc[unit_ok, "amount"] = data.loc[unit_ok, "item_total"]
    data.loc[unit_ok, "amount_source"] = "단가제_품목합계"

    data["relative_gap"] = np.nan
    both = total & data["budget"].notna() & data["item_total"].notna()
    data.loc[both, "relative_gap"] = (
        (data.loc[both, "budget"] - data.loc[both, "item_total"]).abs() / data.loc[both, "budget"]
    )
    disagreement = both & data["relative_gap"].gt(MAX_GAP)
    data.loc[disagreement, "amount"] = np.nan
    data.loc[disagreement, "amount_source"] = "총액제_불일치10pct초과"

    if data["noticeKey"].duplicated().any():
        raise AssertionError("결합 후 공고 중복: 1행=공고 1건 원칙 위반")
    return data


# ============================================================
# 3. 학습기간 경계(20/40/60/80%) 및 금액 품질
# ============================================================
def assign_quintiles(data):
    train = data.loc[data["year"].between(2021, 2023), "amount"].dropna()
    edges = train.quantile([.2, .4, .6, .8]).to_numpy()
    if np.unique(edges).size != 4:
        raise ValueError("학습기간 분위수 경계가 중복됨: 별도 구간 정의 필요")
    data["amount_bin"] = pd.cut(
        data["amount"], bins=[-np.inf, *edges, np.inf], labels=LABELS, right=True
    )
    data["period"] = np.where(data["year"] == 2024, "2024", "2021_2023")
    save(pd.DataFrame({"경계": ["20%", "40%", "60%", "80%"], "금액_원": edges}), "00_학습_5분위_경계.csv")
    return data


def amount_quality(data):
    sources = data.groupby(["period", "amount_source"], dropna=False).size().rename("공고수").reset_index()
    save(sources, "01_금액출처별_공고수.csv")
    detail = data.loc[data["amount"].isna(), [
        "noticeKey", "businessKey", "year", "method", "method_source", "budget", "item_total", "relative_gap", "amount_source"
    ]]
    save(detail, "08_금액미확인_불일치_점검.csv")
    desc_rows = []
    for period, group in data.groupby("period"):
        a = group["amount"].dropna()
        q1, q3 = a.quantile([.25, .75]); iqr = q3 - q1
        desc_rows.append({
            "기간": period, "전체공고": len(group), "금액유효": len(a), "금액미확인": len(group)-len(a),
            "평균_원": a.mean(), "중앙값_원": a.median(), "표준편차_원": a.std(),
            "최솟값_원": a.min(), "Q1_원": q1, "Q3_원": q3, "최댓값_원": a.max(),
            "IQR_원": iqr, "IQR상한_원": q3+1.5*iqr,
            "IQR초과": int((a > q3+1.5*iqr).sum()),
            "1000원미만양수": int(((a > 0) & (a < 1000)).sum()),
        })
    desc = pd.DataFrame(desc_rows)
    save(desc, "02_사업규모금액_기술통계.csv")
    return desc


# ============================================================
# 4. 5분위별 유찰률, 연도별 추이, 카이제곱·Cramér's V
# ============================================================
def analyze(data):
    usable = data.dropna(subset=["amount_bin"]).copy()
    group = usable.groupby(["period", "amount_bin"], observed=False)["fail"].agg(
        공고수="size", 유찰건수="sum"
    ).reset_index()
    group["비유찰건수"] = group["공고수"] - group["유찰건수"]
    group["유찰률_pct"] = group["유찰건수"] / group["공고수"] * 100
    save(group, "03_사업규모_5분위_유찰률.csv")

    test_rows = []
    for period in PERIODS:
        g = group.loc[group["period"].eq(period)].set_index("amount_bin").reindex(LABELS)
        observed = g[["유찰건수", "비유찰건수"]].to_numpy(dtype=float)
        if (observed.sum(axis=1) == 0).any():
            raise ValueError(f"{period}: 빈 금액 5분위 구간")
        chi2, p, df, expected = chi2_contingency(observed, correction=False)
        n = observed.sum()
        v = np.sqrt(chi2/(n*min(observed.shape[0]-1, observed.shape[1]-1)))
        test_rows.append({"기간": period, "검정공고수": int(n), "chi2": chi2,
                          "df": df, "p_value": p, "Cramers_V": v,
                          "최소기대빈도": expected.min(), "기대빈도5미만_셀수": int((expected < 5).sum())})
    test = pd.DataFrame(test_rows)
    save(test, "04_카이제곱_CramersV.csv")

    yearly = usable.groupby(["year", "amount_bin"], observed=False)["fail"].agg(
        공고수="size", 유찰건수="sum"
    ).reset_index()
    yearly["유찰률_pct"] = yearly["유찰건수"] / yearly["공고수"] * 100
    save(yearly, "07_연도별_5분위_유찰률.csv")

    p = group.pivot(index="amount_bin", columns="period", values="유찰률_pct").reindex(LABELS)
    p["차이_pctp"] = p["2024"] - p["2021_2023"]
    save(p.reset_index(), "05_기간별_5분위_비교.csv")
    return group, test, yearly


# ============================================================
# 5. 분석 계획표 3-3용 시각화
# ============================================================
def draw_charts(group, yearly, data):
    configure_font()
    for period in PERIODS:
        g = group.loc[group["period"].eq(period)].set_index("amount_bin").reindex(LABELS)
        fig, ax = plt.subplots(figsize=(9, 5))
        bars = ax.bar(LABELS, g["유찰률_pct"])
        ax.bar_label(bars, labels=[f"{v:.2f}%" for v in g["유찰률_pct"]], padding=3)
        ax.set(title=f"{period} 사업규모금액 5분위별 유찰률", ylabel="유찰률 (%)")
        ax.set_ylim(0, max(1, float(g["유찰률_pct"].max())*1.2))
        ax.grid(axis="y", alpha=.2); fig.tight_layout()
        fig.savefig(os.path.join(OUT_DIR, f"05_{period}_5분위_유찰률.png"), dpi=160)
        plt.close(fig)

        values = data.loc[data["period"].eq(period), "amount"].dropna()
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.hist(np.log10(values[values > 0]), bins=35)
        ax.set(title=f"{period} 사업규모금액 분포", xlabel="사업규모금액 log10(원)", ylabel="공고 수")
        ax.grid(axis="y", alpha=.2); fig.tight_layout()
        fig.savefig(os.path.join(OUT_DIR, f"02_{period}_금액분포.png"), dpi=160)
        plt.close(fig)

    pivot = yearly.pivot(index="year", columns="amount_bin", values="유찰률_pct").reindex(columns=LABELS)
    ax = pivot.plot(kind="line", marker="o", figsize=(9, 5))
    ax.set(title="연도별 사업규모금액 5분위 유찰률 (학습 기준 경계 고정)", xlabel="공고 연도", ylabel="유찰률 (%)")
    ax.set_xticks([2021, 2022, 2023, 2024]); ax.grid(alpha=.2)
    fig = ax.get_figure(); fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "06_연도별_5분위_유찰률.png"), dpi=160)
    plt.close(fig)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    data = assign_quintiles(prepare())
    save(data[["noticeKey", "businessKey", "year", "fail", "method", "method_source",
               "budget", "item_total", "relative_gap", "amount", "amount_source", "amount_bin"]],
         "notice_amounts.csv")
    descriptive = amount_quality(data)
    rates, chi, yearly = analyze(data)
    draw_charts(rates, yearly, data)
    print("\n[금액 유효 및 품질]")
    print(descriptive[["기간", "전체공고", "금액유효", "금액미확인"]].to_string(index=False))
    print("\n[사업규모금액 5분위별 유찰률]")
    print(rates.to_string(index=False))
    print("\n[카이제곱 및 Cramér's V]")
    print(chi.to_string(index=False))
    print("\n저장 위치:", OUT_DIR)
    print("※ A3 단변량 통계 검정 결과는 예측력의 증거가 아니며, KPI 평가는 별도 A6에서 수행합니다.")


if __name__ == "__main__":
    main()
