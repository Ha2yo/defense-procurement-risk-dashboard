import math

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from scripts.dashboard.components import source_note
from scripts.dashboard.data_loader import load_notice, load_org_group, load_result


# ============================================================
# 1. 화면 설정
# ============================================================

FACTORS = ["기관그룹", "사업유형", "계약·경쟁방식", "공고기간", "기초금액", "공고 시기"]

YEAR_MIN, YEAR_MAX = 2021, 2025
DEFAULT_YEARS = (2021, 2023)  # 위험도 기준 생성기간

# 변수 → 분석용 컬럼
FACTOR_COLUMNS = {
    "기관그룹": "org_group",
    "사업유형": "busiDivs",
    "계약·경쟁방식": "cntrctMth",
    "공고기간": "period_bin",
    "기초금액": "amount_bin",
    "공고 시기": "quarter",
}

# 변수별 결측/제외 사유
EXCLUDE_REASON = {
    "기관그룹": "기관그룹 매핑 없음",
    "사업유형": "사업유형 결측",
    "계약·경쟁방식": "계약방법 결측",
    "공고기간": "공고기간 계산 불가 또는 0일 이하",
    "기초금액": "기초금액 결측",
    "공고 시기": "공고일 결측",
}

PERIOD_BINS = [0, 5, 7, 10, np.inf]
PERIOD_LABELS = ["5일 이하", "6~7일", "8~10일", "11일 이상"]
QUARTER_LABELS = ["1분기(1~3월)", "2분기(4~6월)", "3분기(7~9월)", "4분기(10~12월)"]

MIN_CATEGORY_SIZE = 30  # 계약·경쟁방식: 이보다 작은 범주는 '기타'
ETC = "기타"
ALPHA = 0.05


# ============================================================
# 2. 데이터 준비
# ============================================================

def _to_date(series):
    """YYYYMMDD / YYYYMMDDHHMM 문자열·숫자·날짜형을 날짜로 변환합니다."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return series.dt.normalize()
    digits = series.astype("string").str.replace(r"\D", "", regex=True).str[:8]
    return pd.to_datetime(digits, format="%Y%m%d", errors="coerce")


def build_notice_fail(result):
    """품목 단위 입찰결과 → 공고 단위 fail (유찰=1, 낙찰·순위확정=0, 혼합=판단보류 NA)."""
    is_fail = result["bidResult"].astype("string").str.strip().eq("유찰")
    g = is_fail.groupby(result["noticeKey"])
    any_fail, all_fail = g.any(), g.all()
    fail = pd.Series(np.where(all_fail, 1.0, 0.0), index=all_fail.index)
    fail[any_fail & ~all_fail] = np.nan  # 유찰·비유찰 혼합 → 판단보류
    return fail.rename("fail")


def _format_won(value):
    if value >= 1e8:
        return f"{value / 1e8:,.1f}억"
    return f"{value / 1e4:,.0f}만"


def amount_bins(amount):
    """기초금액 4분위 구간 (전체 기간 기준 고정 경계)."""
    valid = amount.dropna()
    q = valid.quantile([0.25, 0.5, 0.75]).tolist()
    edges = [-np.inf, *q, np.inf]
    labels = [
        f"Q1 ({_format_won(q[0])}원 이하)",
        f"Q2 ({_format_won(q[0])}~{_format_won(q[1])}원)",
        f"Q3 ({_format_won(q[1])}~{_format_won(q[2])}원)",
        f"Q4 ({_format_won(q[2])}원 초과)",
    ]
    return pd.cut(amount, bins=edges, labels=labels), labels


def prepare_base(notice, result, org_map):
    """결과확정 공고(판단보류·결과 미확인·수의계약 제외)에 분석 변수를 붙입니다."""
    fail = build_notice_fail(result)
    df = notice.merge(fail, left_on="noticeKey", right_index=True, how="inner")
    df = df[df["fail"].notna()].copy()
    df["fail"] = df["fail"].astype(int)

    pblanc = _to_date(df["pblancDate"])
    close = _to_date(df["bidPartcptRegistClosDt"])
    df["year"] = pblanc.dt.year

    # 공고 시기
    df["quarter"] = pd.Categorical.from_codes(
        (pblanc.dt.quarter.fillna(0).astype(int) - 1).clip(lower=-1), QUARTER_LABELS
    )

    # 공고기간 = 참가등록마감일 - 공고일 (0일 이하는 이상값으로 제외)
    df["period_days"] = (close - pblanc).dt.days
    df["period_bin"] = pd.cut(df["period_days"], bins=PERIOD_BINS, labels=PERIOD_LABELS)

    # 기초금액
    df["amount"] = pd.to_numeric(df["bsicExpt"], errors="coerce")
    df["amount_bin"], amount_labels = amount_bins(df["amount"])

    # 기관그룹
    mapping = org_map.drop_duplicates("orntCode").set_index("orntCode")["기관그룹"]
    df["org_group"] = df["orntCode"].astype("string").map(mapping)

    for col in ["busiDivs", "cntrctMth"]:
        df[col] = df[col].astype("string").str.strip().replace("", pd.NA)

    order = {
        "공고기간": PERIOD_LABELS,
        "공고 시기": QUARTER_LABELS,
        "기초금액": amount_labels,
    }
    return df, order


@st.cache_data(show_spinner=False)  # 화면의 st.spinner 하나로 표시
def load_base():
    notice = load_notice(columns=[
        "noticeKey", "orntCode", "busiDivs", "cntrctMth",
        "pblancDate", "bidPartcptRegistClosDt", "bsicExpt",
    ])
    result = load_result(columns=["noticeKey", "bidResult"])
    return prepare_base(notice, result, load_org_group())


def period_label(years):
    start, end = years
    return f"{start}년" if start == end else f"{start}~{end}년"


def filter_period(df, years):
    start, end = years
    return df[df["year"].between(start, end)]


PRIVATE_CONTRACT = "수의계약"


def exclude_private_contracts(df):
    """팀 기준: 수의계약 공고를 분석 대상에서 제외합니다. (제외 후 데이터, 제외 건수)"""
    mask = df["cntrctMth"].eq(PRIVATE_CONTRACT).fillna(False)
    return df[~mask], int(mask.sum())


def factor_frame(df, factor):
    """선택 변수의 범주값과 fail만 남기고, 제외 건수를 함께 돌려줍니다."""
    col = FACTOR_COLUMNS[factor]
    data = df[[col, "fail"]].rename(columns={col: "category"})
    excluded = int(data["category"].isna().sum())
    data = data.dropna(subset=["category"]).copy()
    data["category"] = data["category"].astype(str)

    if factor == "계약·경쟁방식":
        counts = data["category"].value_counts()
        small = counts[counts < MIN_CATEGORY_SIZE].index
        data.loc[data["category"].isin(small), "category"] = ETC
    return data, excluded


# ============================================================
# 3. 통계
# ============================================================

def _gammaincc(a, x):
    """정규화된 상위 불완전 감마함수 Q(a, x) (Numerical Recipes gser/gcf)."""
    if x <= 0:
        return 1.0
    gln = math.lgamma(a)
    if x < a + 1:
        ap, total, delta = a, 1.0 / a, 1.0 / a
        for _ in range(1000):
            ap += 1
            delta *= x / ap
            total += delta
            if abs(delta) < abs(total) * 1e-15:
                break
        return max(0.0, 1.0 - total * math.exp(-x + a * math.log(x) - gln))
    tiny = 1e-300
    b = x + 1 - a
    c, d = 1 / tiny, 1 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    return math.exp(-x + a * math.log(x) - gln) * h


def chi_square_test(table):
    """범주 × 유찰여부 교차표의 카이제곱 독립성 검정과 Cramér's V."""
    observed = table.to_numpy(dtype=float)
    n = observed.sum()
    expected = observed.sum(axis=1, keepdims=True) * observed.sum(axis=0, keepdims=True) / n
    chi2 = float(((observed - expected) ** 2 / expected).sum())
    r, c = observed.shape
    dof = (r - 1) * (c - 1)
    p_value = _gammaincc(dof / 2, chi2 / 2)
    cramers_v = math.sqrt(chi2 / (n * (min(r, c) - 1)))
    return {
        "chi2": chi2,
        "dof": dof,
        "p_value": p_value,
        "cramers_v": cramers_v,
        "low_expected_ratio": float((expected < 5).mean()),
        "min_expected": float(expected.min()),
    }


def summarize(data, order=None):
    """범주별 공고 수·유찰 수·유찰률 요약표와 교차표."""
    table = pd.crosstab(data["category"], data["fail"]).reindex(columns=[0, 1], fill_value=0)
    if order:
        table = table.reindex([c for c in order if c in table.index])
    else:
        table = table.loc[table.sum(axis=1).sort_values(ascending=False).index]
        if ETC in table.index:
            table = pd.concat([table.drop(ETC), table.loc[[ETC]]])
    summary = pd.DataFrame({
        "범주": table.index.astype(str),
        "비유찰": table[0].to_numpy(),
        "유찰": table[1].to_numpy(),
    })
    summary["공고 수"] = summary["비유찰"] + summary["유찰"]
    summary["유찰률(%)"] = (summary["유찰"] / summary["공고 수"] * 100).round(2)
    return table, summary


def strength_label(v):
    if v < 0.1:
        return "매우 약함"
    if v < 0.3:
        return "약함"
    if v < 0.5:
        return "중간"
    return "강함"


def format_p(p):
    return "< 0.001" if p < 0.001 else f"{p:.3f}"


# ============================================================
# 4. 화면 요소
# ============================================================

def rate_chart(summary, overall_rate):
    height = max(220, 46 * len(summary))
    chart_df = pd.DataFrame({
        "category": summary["범주"],
        "n": summary["공고 수"],
        "fails": summary["유찰"],
        "rate": summary["유찰률(%)"],
    })
    chart_df["label"] = [f"{r:.1f}%  (n={n:,})" for r, n in zip(chart_df["rate"], chart_df["n"])]

    base = alt.Chart(chart_df).encode(
        y=alt.Y("category:N", sort=list(chart_df["category"]), title=None, axis=alt.Axis(labelLimit=220)),
    )
    bars = base.mark_bar(color="#4C78A8", cornerRadiusEnd=3).encode(
        x=alt.X("rate:Q", title="유찰률(%)", scale=alt.Scale(domain=[0, 100])),
        tooltip=[
            alt.Tooltip("category:N", title="범주"),
            alt.Tooltip("n:Q", title="공고 수", format=","),
            alt.Tooltip("fails:Q", title="유찰 수", format=","),
            alt.Tooltip("rate:Q", title="유찰률(%)", format=".2f"),
        ],
    )
    labels = base.mark_text(align="left", dx=4, fontSize=12).encode(x="rate:Q", text="label:N")
    overall = alt.Chart(pd.DataFrame({"x": [overall_rate]})).mark_rule(
        color="#E45756", strokeDash=[4, 4]
    ).encode(x="x:Q", tooltip=[alt.Tooltip("x:Q", title="전체 유찰률(%)", format=".2f")])
    return (bars + labels + overall).properties(height=height)


def crosstab_view(summary):
    total = pd.DataFrame([{
        "범주": "합계",
        "비유찰": summary["비유찰"].sum(),
        "유찰": summary["유찰"].sum(),
        "공고 수": summary["공고 수"].sum(),
    }])
    total["유찰률(%)"] = round(total["유찰"] / total["공고 수"] * 100, 2)
    view = pd.concat([summary, total], ignore_index=True)
    return view.rename(columns={"비유찰": "비유찰(낙찰·순위확정)"})[
        ["범주", "공고 수", "비유찰(낙찰·순위확정)", "유찰", "유찰률(%)"]
    ]


def interpretation(factor, period, summary, stats, overall_rate):
    lines = []
    if stats["p_value"] < ALPHA:
        lines.append(
            f"- **유의성**: p-value {format_p(stats['p_value'])} (< {ALPHA}) → "
            f"{period} 공고에서 **{factor}에 따라 유찰률이 통계적으로 다릅니다.**"
        )
    else:
        lines.append(
            f"- **유의성**: p-value {format_p(stats['p_value'])} (≥ {ALPHA}) → "
            f"{factor}에 따른 유찰률 차이가 통계적으로 확인되지 않습니다."
        )
    lines.append(
        f"- **연관성 크기**: Cramér's V {stats['cramers_v']:.3f} → **{strength_label(stats['cramers_v'])}** "
        "(기준: 0.1 미만 매우 약함 · 0.1~0.3 약함 · 0.3~0.5 중간 · 0.5 이상 강함)"
    )
    hi = summary.loc[summary["유찰률(%)"].idxmax()]
    lo = summary.loc[summary["유찰률(%)"].idxmin()]
    lines.append(
        f"- **범주 비교**: 유찰률이 가장 높은 범주는 **{hi['범주']}** {hi['유찰률(%)']:.1f}% "
        f"(n={hi['공고 수']:,}), 가장 낮은 범주는 **{lo['범주']}** {lo['유찰률(%)']:.1f}% "
        f"(n={lo['공고 수']:,})입니다. 전체 유찰률은 {overall_rate:.2f}%입니다."
    )
    if stats["p_value"] < ALPHA and stats["cramers_v"] < 0.1:
        lines.append(
            "- 표본이 커서 작은 차이도 유의하게 나올 수 있습니다. "
            "p-value보다 Cramér's V와 범주별 유찰률 차이를 함께 보고 판단하세요."
        )
    return "\n".join(lines)


# ============================================================
# 5. 렌더링
# ============================================================

def render():
    st.title("유찰 요인 분석")
    st.caption("공고 조건에 따라 유찰률이 어떻게 달라지는지 탐색합니다. 통계적 연관성과 인과관계는 구분합니다.")
    source_note()

    c1, c2 = st.columns([2, 1])
    with c1:
        factor = st.selectbox("분석 변수", FACTORS)
    with c2:
        years = st.slider(
            "분석 기간 (공고일 기준)",
            min_value=YEAR_MIN,
            max_value=YEAR_MAX,
            value=DEFAULT_YEARS,
            step=1,
            format="%d년",
        )
    period = period_label(years)


    with st.spinner("유찰 요인 분석 결과를 불러오는 중입니다..."):
        try:
            base, order = load_base()
        except FileNotFoundError as e:
            st.error(str(e))
            return

        period_df = filter_period(base, years)
        # 팀 기준: 수의계약은 모든 분석에서 제외
        period_df, private_excluded = exclude_private_contracts(period_df)
        data, excluded = factor_frame(period_df, factor)
        if data.empty:
            st.warning("선택한 조건에 해당하는 공고가 없습니다.")
            return

        table, summary = summarize(data, order.get(factor))
        overall_rate = data["fail"].mean() * 100
        stats = chi_square_test(table) if len(table) >= 2 else None

    c1, c2, c3 = st.columns(3)
    c1.metric("분석 대상 공고 수", f"{len(data):,}건", help=f"전체 유찰률 {overall_rate:.2f}%")
    c2.metric("카이제곱 p-value", format_p(stats["p_value"]) if stats else "—")
    c3.metric(
        "Cramér's V",
        f"{stats['cramers_v']:.3f}" if stats else "—",
        help=strength_label(stats["cramers_v"]) if stats else None,
    )

    notes = [f"유찰률 = 유찰 공고 수 ÷ 결과확정 공고 수 (판단보류·결과 미확인·수의계약 제외) · 전체 {overall_rate:.2f}%"]
    if private_excluded:
        notes.append(f"수의계약 {private_excluded:,}건 제외")
    if excluded:
        notes.append(f"{EXCLUDE_REASON[factor]} {excluded:,}건 제외")
    if years[0] == YEAR_MIN:
        notes.append("2020년 공고일(pblancDate) 공고는 기간에서 제외")
    if years[1] == YEAR_MAX:
        notes.append("2025년 4분기 공고는 결과 미확인 비중이 높을 수 있음")

    with st.container(border=True):
        st.subheader(f"{factor}별 유찰률")
        st.caption(" · ".join([f"기간: {period}", "막대 = 범주별 유찰률, 빨간 점선 = 전체 유찰률", *notes[1:]]))
        st.altair_chart(rate_chart(summary, overall_rate), use_container_width=True)

    tab1, tab2 = st.tabs(["교차표", "통계 검정 해석"])
    with tab1:
        with st.container(border=True):
            st.subheader("변수 × 유찰 여부 교차표")
            st.caption(notes[0])
            st.dataframe(
                crosstab_view(summary),
                hide_index=True,
                use_container_width=True,
                column_config={
                    "공고 수": st.column_config.NumberColumn(format="%d"),
                    "비유찰(낙찰·순위확정)": st.column_config.NumberColumn(format="%d"),
                    "유찰": st.column_config.NumberColumn(format="%d"),
                    "유찰률(%)": st.column_config.NumberColumn(format="%.2f"),
                },
            )
    with tab2:
        if stats is None:
            st.warning("범주가 1개뿐이라 카이제곱 검정을 할 수 없습니다.")
        else:
            st.markdown(interpretation(factor, period, summary, stats, overall_rate))
            st.caption(f"χ² = {stats['chi2']:,.2f}, 자유도 = {stats['dof']}, 최소 기대빈도 = {stats['min_expected']:.1f}")
            if stats["low_expected_ratio"] > 0:
                st.warning(
                    f"기대빈도 5 미만인 칸이 {stats['low_expected_ratio']:.0%} 있어 "
                    "카이제곱 결과가 불안정할 수 있습니다."
                )
        st.info("p-value는 유의성 검정, Cramér's V는 연관성 크기 확인에 사용합니다. 이 값만으로 유찰의 원인이라고 단정하지 않습니다.")

    if factor == "기초금액":
        st.warning("bsicExpt의 단가성·총액성 해석은 품목명세서와 교차 검증 후 확정할 예정입니다.")