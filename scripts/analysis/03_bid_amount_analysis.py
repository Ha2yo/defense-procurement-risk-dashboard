import os

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib import font_manager
from scipy.stats import chi2_contingency


# ============================================================
# 1. 경로 설정 (요청한 경로를 그대로 유지)
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "../..", "data", "clean")
OUT_DIR = os.path.join(BASE_DIR, "../..", "data", "analysis", "bid_amount")

os.makedirs(OUT_DIR, exist_ok=True)

NOTICE_FILE = os.path.join(
    DATA_DIR,
    "bid_notice_clean_20210101_20251231.csv",
)
RESULT_FILE = os.path.join(
    DATA_DIR,
    "bid_result_clean_20210101_20251231.csv",
)
# 품목명세서: 기초금액 결측·단가 의심 공고의 보완 가능성 검토에만 사용
ITEM_FILE = os.path.join(
    DATA_DIR,
    "bid_item_clean_20210101_20251231.csv",
)


# ============================================================
# 2. 기초금액 구간 정의
# ============================================================
LABELS = [
    "1천만원 이하",
    "1천만원 초과~1억원",
    "1억원 초과",
]


# ============================================================
# 3. 그래프 한글 폰트 설정
# ============================================================
def configure_font():
    available = {font.name for font in font_manager.fontManager.ttflist}

    for font_name in ["Malgun Gothic", "AppleGothic", "NanumGothic"]:
        if font_name in available:
            plt.rcParams["font.family"] = font_name
            break

    plt.rcParams["axes.unicode_minus"] = False


# ============================================================
# 4. CSV 파일 불러오기
# ============================================================
def load_csv(path, required):
    df = pd.read_csv(
        path,
        dtype="string",
        encoding="utf-8-sig",
        low_memory=False,
    )

    missing = set(required) - set(df.columns)
    if missing:
        # path는 os.path.join()으로 만든 문자열이므로 os.path.basename 사용
        raise ValueError(
            f"{os.path.basename(path)} 필수 컬럼 누락: {sorted(missing)}"
        )

    for col in required:
        df[col] = df[col].str.strip().replace("", pd.NA)

    return df


# ============================================================
# 5. 분석 데이터 준비
# ============================================================
def prepare():
    # 4-1. 데이터 불러오기
    notice = load_csv(
        NOTICE_FILE,
        ["noticeKey", "pblancDate", "pblancSe", "cntrctMth", "bsicExpt"],
    )
    result = load_csv(RESULT_FILE, ["noticeKey", "bidResult"])

    if notice.noticeKey.isna().any() or notice.noticeKey.duplicated().any():
        raise ValueError("공고 noticeKey 결측/중복: 정제 데이터를 확인하세요.")

    # 4-2. 유찰=1, 낙찰·순위확정=0, 그 외=NA
    result["label"] = (
        result.bidResult
        .map({"유찰": 1, "낙찰": 0, "순위확정": 0})
        .astype("Int64")
    )

    # 4-3. 동일 공고키 복수 결과를 공고 단위로 집계
    # 낙찰+순위확정은 비유찰, 유찰+비유찰은 판단보류
    summary = (
        result.groupby("noticeKey", dropna=False)
        .agg(
            n_rows=("bidResult", "size"),
            n_known=("label", "count"),
            n_states=("label", "nunique"),
            fail=("label", "first"),
        )
        .reset_index()
    )

    ambiguous = (
        (summary.n_known != summary.n_rows)
        | (summary.n_states != 1)
    )
    summary.loc[ambiguous, "fail"] = pd.NA

    # 4-4. 입찰공고와 공고 단위 입찰결과 연결
    merged = notice.merge(
        summary[["noticeKey", "fail"]],
        on="noticeKey",
        how="left",
        validate="one_to_one",
    )
    merged["year"] = pd.to_datetime(
        merged.pblancDate,
        format="%Y%m%d",
        errors="coerce",
    ).dt.year

    # 4-5. 취소공고·수의계약·결과 미확인·판단보류 제외
    valid = (
        merged.year.between(2021, 2024)
        & ~merged.pblancSe.fillna("").eq("취소공고")
        & ~merged.cntrctMth.fillna("").str.contains("수의", regex=False)
        & merged.fail.notna()
    )
    filtered = merged.loc[valid].copy()
    filtered["fail"] = filtered.fail.astype(int)

    # 4-6. 금액 숫자형 변환 (음수는 오류, 0원은 보존)
    filtered["amount"] = pd.to_numeric(
        filtered.bsicExpt.str.replace(",", "", regex=False),
        errors="coerce",
    )
    filtered.loc[filtered.amount < 0, "amount"] = np.nan

    # 4-7. 금액 구간화
    filtered["amount_bin"] = pd.cut(
        filtered.amount,
        bins=[-0.001, 10_000_000, 100_000_000, np.inf],
        labels=LABELS,
        include_lowest=True,
    )

    return notice, result, filtered


# ============================================================
# 7. 기술통계 및 카이제곱 검정
# ============================================================
def evaluate(data, period):
    # 5-1. 금액 유효 데이터 추출
    usable = data.dropna(subset=["amount_bin"])
    print(
        f"\n[{period}] 분석 대상 {len(data):,}건 / "
        f"금액 유효 {len(usable):,}건 / "
        f"금액 결측·오류 {len(data) - len(usable):,}건"
    )

    # 5-2. 기초금액 기술통계
    stats = (
        usable.amount.describe(percentiles=[0.25, 0.5, 0.75])
        .rename("amount_krw")
        .to_frame()
    )
    stats.to_csv(
        os.path.join(OUT_DIR, f"amount_descriptive_{period}.csv"),
        encoding="utf-8-sig",
    )

    # 5-2-1. 평균·중앙값·표준편차·사분위수·최솟값·최댓값 출력
    # 기술통계에는 0원도 포함하며, 결측·음수만 제외한다.
    print(f"\n[{period} 기초금액 기술통계 (단위: 원)]")
    stat_names = {
        "count": "유효 건수",
        "mean": "평균",
        "std": "표준편차",
        "min": "최솟값",
        "25%": "제1사분위수 (Q1)",
        "50%": "중앙값 (Q2)",
        "75%": "제3사분위수 (Q3)",
        "max": "최댓값",
    }
    for key, label in stat_names.items():
        value = stats.loc[key, "amount_krw"]
        unit = "건" if key == "count" else "원"
        print(f"{label:<16} : {value:,.2f} {unit}")
    print(
        "사분위범위 (IQR) : "
        f"{stats.loc['75%', 'amount_krw'] - stats.loc['25%', 'amount_krw']:,.2f} 원"
    )

    # 5-2-2. 분포 특성 및 검토 대상 기록 (실제 분석 데이터는 변경하지 않음)
    # IQR 경계 밖은 통계적 이상치일 뿐, 잘못된 금액이라고 단정하지 않는다.
    q1 = float(stats.loc["25%", "amount_krw"])
    median = float(stats.loc["50%", "amount_krw"])
    q3 = float(stats.loc["75%", "amount_krw"])
    mean = float(stats.loc["mean", "amount_krw"])
    iqr = q3 - q1
    lower_fence = q1 - 1.5 * iqr
    upper_fence = q3 + 1.5 * iqr

    lower_outliers = usable["amount"] < lower_fence
    upper_outliers = usable["amount"] > upper_fence
    outlier_mask = lower_outliers | upper_outliers

    # 0원, 매우 작은 양수 금액, 최상위 금액은 별도로 원본 확인 후보로 저장한다.
    # 1,000원 경계는 오류 판정 기준이 아니라 검토용 임시 기준이다.
    zero_mask = usable["amount"].eq(0)
    small_positive_mask = usable["amount"].between(0, 1000, inclusive="neither")

    quality = pd.DataFrame([{
        "period": period,
        "valid_count": len(usable),
        "mean_krw": mean,
        "median_krw": median,
        "q1_krw": q1,
        "q3_krw": q3,
        "iqr_krw": iqr,
        "lower_fence_krw": lower_fence,
        "upper_fence_krw": upper_fence,
        "iqr_outlier_count": int(outlier_mask.sum()),
        "iqr_outlier_rate_pct": round(float(outlier_mask.mean()) * 100, 3),
        "zero_amount_count": int(zero_mask.sum()),
        "small_positive_under_1000_count": int(small_positive_mask.sum()),
    }])
    quality.to_csv(
        os.path.join(OUT_DIR, f"amount_quality_summary_{period}.csv"),
        index=False,
        encoding="utf-8-sig",
    )

    # 전체 이상치를 별도 파일로 남기되, 유찰률 계산에서는 제외하지 않는다.
    review_cols = ["noticeKey", "year", "bsicExpt", "amount", "fail", "amount_bin"]
    outlier_details = usable.loc[outlier_mask, review_cols].copy()
    outlier_details["iqr_outlier_type"] = np.where(
        upper_outliers.loc[outlier_details.index], "상한 초과", "하한 미만"
    )
    outlier_details.sort_values("amount").to_csv(
        os.path.join(OUT_DIR, f"amount_iqr_outliers_{period}.csv"),
        index=False,
        encoding="utf-8-sig",
    )

    # 검토 우선순위: 0원·1,000원 미만 양수 + 큰 금액 상위 20건
    review_indices = usable.loc[zero_mask | small_positive_mask].index.union(
        usable.nlargest(20, "amount").index
    )
    review = usable.loc[review_indices, review_cols].copy()
    review["review_reason"] = np.select(
        [review["amount"].eq(0), review["amount"].between(0, 1000, inclusive="neither")],
        ["0원 확인", "1,000원 미만 양수 확인"],
        default="고액 상위 20건 확인",
    )
    review.sort_values("amount").to_csv(
        os.path.join(OUT_DIR, f"amount_review_candidates_{period}.csv"),
        index=False,
        encoding="utf-8-sig",
    )

    print(f"IQR 기준 상한: {upper_fence:,.2f}원 / 이상치: {int(outlier_mask.sum()):,}건")
    print(f"0원: {int(zero_mask.sum()):,}건 / 0~1,000원 양수: {int(small_positive_mask.sum()):,}건")
    if median > 0 and mean > median:
        print("해석: 평균이 중앙값보다 큽니다. 오른쪽으로 긴 금액 분포를 점검하세요.")
    print("주의: IQR 이상치는 자동 제외하지 않으며, 오류 여부는 원본 자료로 검증합니다.")

    # 5-3. 구간별 공고 수·유찰 건수·비유찰 건수·유찰률
    table = (
        usable.groupby("amount_bin", observed=False)
        .agg(
            notice_count=("fail", "size"),
            failure_count=("fail", "sum"),
        )
        .reindex(LABELS)
    )
    table["nonfailure_count"] = table.notice_count - table.failure_count
    table["failure_rate_pct"] = (
        table.failure_count
        / table.notice_count.replace(0, np.nan)
        * 100
    ).round(3)

    table.to_csv(
        os.path.join(OUT_DIR, f"amount_failure_rate_{period}.csv"),
        encoding="utf-8-sig",
    )
    print(table.to_string())

    # 구간별 공고 수와 유찰률, 전체 금액 분포 시각화
    draw_period_charts(usable, table, period)

    # 5-4. 금액 구간 × 유찰 여부 교차표
    contingency = table[["failure_count", "nonfailure_count"]]
    if (table.notice_count == 0).any():
        print("빈 구간이 있어 카이제곱 검정을 수행하지 않았습니다.")
        return

    # 5-5. 카이제곱 독립성 검정
    chi2, p, dof, expected = chi2_contingency(
        contingency,
        correction=False,
    )

    # 5-6. 효과크기: Cramér's V
    n = contingency.to_numpy().sum()
    min_dim = min(contingency.shape[0] - 1, contingency.shape[1] - 1)
    cramers_v = np.sqrt(chi2 / (n * min_dim))

    # 5-7. 검정 결과 저장
    test = pd.DataFrame([
        {
            "period": period,
            "n": len(usable),
            "chi2": chi2,
            "dof": dof,
            "p_value_raw": p,
            "cramers_v": cramers_v,
            "min_expected": expected.min(),
            "expected_below_5": int((expected < 5).sum()),
        }
    ])
    test.to_csv(
        os.path.join(OUT_DIR, f"amount_chisquare_{period}.csv"),
        index=False,
        encoding="utf-8-sig",
    )

    # 5-8. 결과 출력
    print(
        f"χ²({dof})={chi2:.4f}, p={p:.6g}, "
        f"Cramér's V={cramers_v:.4f}, 최소 기대빈도={expected.min():.2f}"
    )
    if (expected < 5).any():
        print("주의: 기대빈도 5 미만 셀이 있습니다. 검정 적합성을 검토하세요.")
    print("참고: Holm 보정 p값은 다른 A1~A4 검정 p값과 함께 별도로 계산해야 합니다.")


# ============================================================
# 6. 시각화: 기간별 금액 분포, 공고 수, 유찰률
# ============================================================
def draw_period_charts(data, table, period):
    """그래프를 PNG로 저장한다. 0원은 분포 히스토그램에서 별도 집계."""
    # 한 Figure에는 한 그래프만 그려서 읽기 쉽게 한다.
    amounts = data.loc[data["amount"] > 0, "amount"].dropna()

    if not amounts.empty:
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.hist(np.log10(amounts.to_numpy(dtype=float)), bins=35)
        ax.set_title(f"{period} 기초금액 분포 (0원 제외)")
        ax.set_xlabel("기초금액 (원, log10 척도)")
        ax.set_ylabel("공고 수")
        ax.grid(axis="y", alpha=0.25)
        fig.tight_layout()
        fig.savefig(
            os.path.join(OUT_DIR, f"amount_distribution_{period}.png"),
            dpi=160,
        )
        plt.close(fig)

        # 기초금액 상자그림: 금액의 치우침이 크므로 X축을 로그 척도로 표시한다.
        # 로그 척도에서는 0원을 표시할 수 없으므로 그림에서만 제외한다.
        # 기술통계와 유찰률 집계에는 0원이 그대로 포함된다.
        fig, ax = plt.subplots(figsize=(9, 4))
        ax.boxplot(
            amounts.to_numpy(dtype=float),
            vert=False,
            showfliers=True,
        )
        ax.set_xscale("log")
        ax.set_title(f"{period} 기초금액 상자그림 (0원 제외)")
        ax.set_xlabel("기초금액 (원, 로그 척도)")
        ax.set_yticks([1])
        ax.set_yticklabels(["전체 공고"])
        ax.grid(axis="x", alpha=0.25)
        fig.tight_layout()
        fig.savefig(
            os.path.join(OUT_DIR, f"amount_boxplot_{period}.png"),
            dpi=160,
        )
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 5))
    counts = table["notice_count"].fillna(0)
    bars = ax.bar(LABELS, counts)
    ax.bar_label(bars, labels=[f"{int(x):,}건" for x in counts], padding=3)
    ax.set_title(f"{period} 금액 구간별 공고 수")
    ax.set_ylabel("공고 수")
    ax.set_ylim(0, max(float(counts.max()) * 1.17, 1))
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(
        os.path.join(OUT_DIR, f"amount_notice_count_{period}.png"),
        dpi=160,
    )
    plt.close(fig)

    rates = table["failure_rate_pct"].astype(float)
    fig, ax = plt.subplots(figsize=(9, 5))
    bars = ax.bar(LABELS, rates.fillna(0))
    ax.bar_label(
        bars,
        labels=[f"{x:.2f}%" if pd.notna(x) else "자료 없음" for x in rates],
        padding=3,
    )
    ax.set_title(f"{period} 금액 구간별 유찰률")
    ax.set_ylabel("유찰률 (%)")
    ax.set_ylim(0, max(float(rates.max()) * 1.25, 1) if rates.notna().any() else 1)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(
        os.path.join(OUT_DIR, f"amount_failure_rate_{period}.png"),
        dpi=160,
    )
    plt.close(fig)


def draw_period_comparison():
    """학습 기간과 예비 검증 기간의 금액 구간별 유찰률을 비교한다."""
    periods = ["2021_2023", "2024"]
    tables = [
        pd.read_csv(
            os.path.join(OUT_DIR, f"amount_failure_rate_{period}.csv"),
            encoding="utf-8-sig",
            index_col=0,
        ).reindex(LABELS)
        for period in periods
    ]

    positions = np.arange(len(LABELS))
    width = 0.36
    fig, ax = plt.subplots(figsize=(10, 5))

    for i, (period, table) in enumerate(zip(periods, tables)):
        values = table["failure_rate_pct"].astype(float)
        bars = ax.bar(
            positions + (i - 0.5) * width,
            values.fillna(0),
            width,
            label=period.replace("_", "~") if i == 0 else period,
        )
        ax.bar_label(
            bars,
            labels=[f"{x:.2f}%" if pd.notna(x) else "-" for x in values],
            padding=3,
            fontsize=9,
        )

    ax.set_xticks(positions, LABELS)
    ax.set_title("기간별 기초금액 구간 유찰률 비교")
    ax.set_ylabel("유찰률 (%)")
    ax.set_ylim(0, max(float(t["failure_rate_pct"].max()) for t in tables) * 1.25)
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "amount_failure_rate_comparison.png"), dpi=160)
    plt.close(fig)


def draw_yearly_line_chart(filtered):
    """연도별 기초금액 구간 유찰률을 하나의 선그래프로 비교한다."""

    # 1. 금액 구간이 확인되는 공고만 사용
    usable = filtered.dropna(subset=["amount_bin"]).copy()

    # 2. 연도·금액 구간별 공고 수, 유찰 건수, 유찰률 계산
    yearly = (
        usable.groupby(["year", "amount_bin"], observed=False)
        .agg(
            notice_count=("fail", "size"),
            failure_count=("fail", "sum"),
        )
        .reset_index()
    )

    yearly["nonfailure_count"] = (
        yearly["notice_count"] - yearly["failure_count"]
    )
    yearly["failure_rate_pct"] = (
        yearly["failure_count"]
        / yearly["notice_count"].replace(0, np.nan)
        * 100
    )

    yearly.to_csv(
        os.path.join(OUT_DIR, "amount_failure_rate_by_year.csv"),
        index=False,
        encoding="utf-8-sig",
    )

    # 3. 연도를 행으로, 기초금액 구간을 열로 재배치
    yearly_pivot = yearly.pivot(
        index="year",
        columns="amount_bin",
        values="failure_rate_pct",
    ).reindex(index=[2021, 2022, 2023, 2024], columns=LABELS)

    # 4. 수업 자료의 pandas 선그래프(df.plot(kind="line")) 사용
    ax = yearly_pivot.plot(
        kind="line",
        marker="o",
        figsize=(10, 5),
    )

    # 5. 수업 자료의 그래프 제목, 축, 범례, 격자 설정
    ax.set_title("연도별 기초금액 구간 유찰률 추이")
    ax.set_xlabel("연도")
    ax.set_ylabel("유찰률 (%)")
    ax.set_xticks(yearly_pivot.index)
    ax.set_ylim(0, max(float(yearly_pivot.max().max()) * 1.2, 1))
    ax.legend(title="기초금액 구간")
    ax.grid(alpha=0.3)

    # 6. 수업 자료의 annotate로 실제 유찰률 표시
    for label in LABELS:
        for year, rate in yearly_pivot[label].items():
            if pd.notna(rate):
                ax.annotate(
                    f"{rate:.2f}%",
                    (year, rate),
                    xytext=(0, 8),
                    textcoords="offset points",
                    ha="center",
                    fontsize=9,
                )

    # 7. 그래프 PNG 저장
    fig = ax.get_figure()
    fig.tight_layout()
    fig.savefig(
        os.path.join(OUT_DIR, "amount_failure_rate_by_year_line.png"),
        dpi=160,
        bbox_inches="tight",
    )
    plt.close(fig)


# ============================================================
# 9. 기간별 구간 유찰률 비교표 (전체 행·차이 포함)
# ============================================================
PERIODS = {
    "2021_2023": (2021, 2023),
    "2024": (2024, 2024),
}


def compare_periods(filtered):
    """구간별 유찰률에 기간 전체 행과 2024 - 2021~2023 차이(%p)를 붙여 저장한다."""
    usable = filtered.dropna(subset=["amount_bin"])
    parts = {}
    for period, (start, end) in PERIODS.items():
        data = usable[usable.year.between(start, end)]
        table = (
            data.groupby("amount_bin", observed=False)
            .agg(notice_count=("fail", "size"), failure_count=("fail", "sum"))
            .reindex(LABELS)
        )
        table.loc["전체"] = [len(data), int(data.fail.sum())]
        table["failure_rate_pct"] = (
            table.failure_count / table.notice_count * 100
        ).round(2)
        parts[period] = table

    out = pd.concat(parts, axis=1)
    out.columns = [f"{period}_{col}" for period, col in out.columns]
    out["diff_pp"] = (
        out["2024_failure_rate_pct"] - out["2021_2023_failure_rate_pct"]
    ).round(2)
    out.index.name = "amount_bin"
    out.to_csv(
        os.path.join(OUT_DIR, "amount_failure_rate_period_comparison.csv"),
        encoding="utf-8-sig",
    )
    print("\n[기간별 금액 구간 유찰률 비교]")
    print(out.to_string())


# ============================================================
# 10. 품목명세서 금액 검증 및 기초금액 보완 가능성
# ============================================================
def to_amount_bin(amount):
    """4-7과 같은 경계로 금액 구간을 만든다."""
    return pd.cut(
        amount,
        bins=[-0.001, 10_000_000, 100_000_000, np.inf],
        labels=LABELS,
        include_lowest=True,
    )


def load_item():
    item = pd.read_csv(
        ITEM_FILE,
        dtype="string",
        encoding="utf-8-sig",
        usecols=[
            "businessKey", "prcmRnum", "dlvrPrrk",
            "ordrUtpr", "dlvrQnty", "totlAmnt",
        ],
        keep_default_na=False,
    )
    for col in item.columns:
        item[col] = item[col].str.strip().replace("", pd.NA)

    for col in ["ordrUtpr", "dlvrQnty", "totlAmnt"]:
        item[f"{col}_num"] = pd.to_numeric(item[col], errors="coerce")

    print(f"\n품목명세서 {len(item):,}행")
    return item


def check_item_amount(item):
    """단가 x 수량과 총금액을 비교한다. 불일치를 오류로 단정하지 않고 구분만 한다."""
    nums = ["ordrUtpr_num", "dlvrQnty_num", "totlAmnt_num"]
    computable = item[nums].notna().all(axis=1)
    diff = (item.ordrUtpr_num * item.dlvrQnty_num - item.totlAmnt_num).abs()

    status = pd.Series("계산 불가", index=item.index)
    status[computable & diff.eq(0)] = "정확 일치"
    status[computable & diff.gt(0) & diff.lt(1)] = "반올림 오차"
    status[computable & diff.ge(1)] = "불일치"

    labels = ["정확 일치", "반올림 오차", "불일치", "계산 불가"]
    summary = pd.DataFrame({
        "category": labels,
        "criterion": [
            "차이 0", "0 < 차이 < 1원", "차이 >= 1원", "단가·수량·총액 중 결측",
        ],
        "row_count": [int(status.eq(label).sum()) for label in labels],
    })
    summary.loc[len(summary)] = ["합계", "", len(item)]
    summary.to_csv(
        os.path.join(OUT_DIR, "item_amount_check_summary.csv"),
        index=False,
        encoding="utf-8-sig",
    )

    # 고유해야 하는 사업키·조달요구번호·납품순위가 겹치면 합산 전 검토 대상
    dup_keys = ["businessKey", "prcmRnum", "dlvrPrrk"]
    dup = item[item.duplicated(subset=dup_keys, keep=False)]
    dup[dup_keys + ["ordrUtpr", "dlvrQnty", "totlAmnt"]].sort_values(dup_keys).to_csv(
        os.path.join(OUT_DIR, "item_duplicate_key_rows.csv"),
        index=False,
        encoding="utf-8-sig",
    )

    print("\n[품목명세서 금액 검증]")
    print(summary.to_string(index=False))
    print(f"사업키·조달요구번호·납품순위 중복 행: {len(dup):,}건")


def item_business_sum(item):
    """사업별 품목 총액. totlAmnt 결측 행은 합산에서 빼고 결측 행 수를 남긴다."""
    return item.groupby("businessKey").agg(
        item_rows=("totlAmnt", "size"),
        item_amount_missing_rows=("totlAmnt_num", lambda s: int(s.isna().sum())),
        item_sum=("totlAmnt_num", lambda s: s.sum(min_count=1)),
    )


def check_coverage(filtered):
    """기초금액 결측·1,000원 미만 공고 중 품목 총액으로 보완 가능한 건수를 센다."""
    rows = []
    reviews = []
    review_cols = [
        "noticeKey", "businessKey", "year", "bsicExpt", "amount", "fail",
        "amount_bin", "item_sum", "item_rows", "item_amount_missing_rows",
    ]

    for period, (start, end) in PERIODS.items():
        data = filtered[filtered.year.between(start, end)]
        missing = data.amount.isna()
        small = data.amount.between(0, 1000, inclusive="neither")
        has_item = data.item_sum > 0

        rows.append({
            "period": period,
            "target_count": len(data),
            "amount_valid": int((~missing).sum()),
            "amount_missing": int(missing.sum()),
            "missing_with_item_sum": int((missing & has_item).sum()),
            "small_under_1000": int(small.sum()),
            "small_with_item_sum": int((small & has_item).sum()),
        })

        review = data.loc[missing | small, review_cols].copy()
        review["review_reason"] = np.where(
            missing[review.index], "기초금액 결측", "1,000원 미만 양수"
        )
        review["item_sum_bin"] = to_amount_bin(review.item_sum)
        reviews.append(review)

    table = pd.DataFrame(rows)
    table.to_csv(
        os.path.join(OUT_DIR, "amount_item_coverage.csv"),
        index=False,
        encoding="utf-8-sig",
    )
    pd.concat(reviews).sort_values(["review_reason", "year", "noticeKey"]).to_csv(
        os.path.join(OUT_DIR, "amount_item_review_candidates.csv"),
        index=False,
        encoding="utf-8-sig",
    )
    print("\n[기초금액 결측·1,000원 미만 공고의 품목 총액 보완 가능 건수]")
    print(table.to_string(index=False))


def ratio_summary(scope, data):
    """품목총액 ÷ 기초금액 분포와 금액 구간 일치율 (기초금액·품목총액 모두 양수인 공고)."""
    both = data[(data.amount > 0) & (data.item_sum > 0)]
    ratio = both.item_sum / both.amount
    q = ratio.quantile([0.1, 0.25, 0.5, 0.75, 0.9])
    return {
        "scope": scope,
        "notice_count": len(data),
        "both_positive": len(both),
        "ratio_p10": round(q[0.1], 3),
        "ratio_q1": round(q[0.25], 3),
        "ratio_median": round(q[0.5], 3),
        "ratio_q3": round(q[0.75], 3),
        "ratio_p90": round(q[0.9], 3),
        "within_10pct": round(
            ratio.between(0.9, 1.1, inclusive="neither").mean() * 100, 1
        ),
        "within_50pct": round(
            ratio.between(0.5, 1.5, inclusive="neither").mean() * 100, 1
        ),
        "same_bin_pct": round(
            (to_amount_bin(both.amount) == to_amount_bin(both.item_sum)).mean() * 100, 1
        ),
    }


def compare_item_ratio(notice, filtered, biz_sum):
    """전체 입찰공고와 기간별 금액 분석 대상에서 품목 총액의 대리변수 적합성을 본다."""
    all_notice = notice[["businessKey", "bsicExpt"]].copy()
    all_notice["amount"] = pd.to_numeric(
        all_notice.bsicExpt.str.replace(",", "", regex=False),
        errors="coerce",
    )
    all_notice["item_sum"] = all_notice.businessKey.map(biz_sum.item_sum)

    rows = [ratio_summary("전체 입찰공고", all_notice)]
    for period, (start, end) in PERIODS.items():
        rows.append(ratio_summary(
            f"금액 분석 대상 {period}",
            filtered[filtered.year.between(start, end)],
        ))

    table = pd.DataFrame(rows)
    table.to_csv(
        os.path.join(OUT_DIR, "amount_item_ratio_summary.csv"),
        index=False,
        encoding="utf-8-sig",
    )
    print("\n[품목총액 ÷ 기초금액]")
    print(table.to_string(index=False))


def analyze_item_amount(notice, filtered):
    """품목명세서 CLEAN이 있을 때만 실행한다. 기존 금액 분석 결과는 바꾸지 않는다."""
    if not os.path.exists(ITEM_FILE):
        print(f"\n품목명세서 CLEAN이 없어 품목 금액 검토를 건너뜁니다:\n{ITEM_FILE}")
        return

    item = load_item()
    check_item_amount(item)

    biz_sum = item_business_sum(item)
    filtered = filtered.join(biz_sum, on="businessKey")
    check_coverage(filtered)
    compare_item_ratio(notice, filtered, biz_sum)


# ============================================================
# 8. 메인 실행 함수
# ============================================================
def main():
    configure_font()
    notice, result, filtered = prepare()
    print(f"입찰공고 {len(notice):,}행 / 결과 {len(result):,}행")
    print(f"분석대상 중 0원 기초금액: {(filtered.amount == 0).sum():,}건")

    evaluate(filtered.loc[filtered.year.between(2021, 2023)], "2021_2023")
    evaluate(filtered.loc[filtered.year.eq(2024)], "2024")
    draw_period_comparison()
    draw_yearly_line_chart(filtered)
    compare_periods(filtered)
    analyze_item_amount(notice, filtered)
    print("\nCSV 및 PNG 저장 위치:", OUT_DIR)


if __name__ == "__main__":
    main()
