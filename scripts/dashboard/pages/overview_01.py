"""전체 유찰 현황: 유찰 규모 → 연·월별 추이 → 공고·유찰 분포(연도별 구성 포함)."""
import importlib
import os

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from scripts.dashboard import components as ui
from scripts.dashboard.data_loader import load_item, load_notice, load_procure_plan, load_result
# 화면 3과 같은 사업규모금액 산정·5분위 규칙을 그대로 쓴다
from scripts.dashboard.pages.failure_factors_03 import (
    HOLDOUT_YEAR, amount_quintiles, attach_amount, item_business_sum, md_safe, result_bid_method,
)

# 파일명이 숫자로 시작해 일반 import 문을 쓸 수 없으므로 importlib로 불러온다
prepare_bid_result = importlib.import_module(
    "scripts.analysis.05_rebid_followup_analysis"
).prepare

PRIVATE_KEYWORD = "수의"  # 계약방법에 이 글자가 있으면 수의계약 (화면 2·3과 같은 기준)
AMOUNT_COL = "사업규모금액 구간"
AMOUNT_UNKNOWN = "금액 미확인"
AMOUNT_BASIS = (2021, 2023)  # 5분위 경계 계산 기간: 분석계획표 3·KPI 정의서와 같은 학습기간


# ============================================================
# 1. 공통 분석 데이터
# ============================================================
@st.cache_data(show_spinner="공고별 결과를 판정하는 중입니다...")
def prepare_overview_data():
    notice = load_notice()
    result = load_result()

    # 공고별 유찰·낙찰·순위확정·판단보류 판정
    data, _ = prepare_bid_result(notice, result)
    data["notice_year"] = data["notice_date"].dt.year
    
    # 기관그룹은 검증된 기관코드 분류표를 기준으로 결합한다
    base_dir = os.path.dirname(os.path.abspath(__file__))
    ornt_mapping_path = os.path.join(
        base_dir, "../../..", "data", "analysis", "ornt_code",
        "orntCode_codeVld4_all.csv",
    )
    org_map = pd.read_csv(
        ornt_mapping_path, encoding="utf-8-sig", dtype="string",
        usecols=["orntCode", "기관그룹"],
    )
    
    org_map["orntCode"] = org_map["orntCode"].str.strip()
    org_map["기관그룹"] = org_map["기관그룹"].str.strip()
    org_map = org_map.dropna(subset=["orntCode"])
    org_map = org_map[org_map["orntCode"].ne("")]

    data["orntCode"] = data["orntCode"].astype("string").str.strip()
    data = data.merge(org_map, on="orntCode", how="left", validate="many_to_one")
    data["기관그룹"] = data["기관그룹"].fillna("미분류")
    data["계약방법"] = data["cntrctMth"].astype("string").str.strip().replace("", pd.NA).fillna("미분류")
    data["사업유형"] = data["busiDivs"].astype("string").str.strip().replace("", pd.NA).fillna("미분류")
    # 팀 기준(분석계획표 공통 기준): 수의계약은 경쟁입찰과 발생 구조가 달라 모든 분석에서 제외
    data["is_private"] = data["cntrctMth"].astype("string").str.contains(PRIVATE_KEYWORD, na=False, regex=False)

    # 결과확정은 3종만, 취소공고는 집계에서 제외
    data["confirmed"] = data["result_status"].isin(["유찰", "낙찰", "순위확정"])
    data["failed"] = data["result_status"].eq("유찰")

    # 사업규모금액: 결과 미확인·취소 공고도 금액 필터에 걸리도록 전체 공고에 화면 3과 같은 규칙으로 산정한다.
    # 입찰결과 bidMth 사후 복원은 화면 3과 같이 결과확정 공고(2025년 제외)에만 허용
    data = data.merge(result_bid_method(result), left_on="noticeKey", right_index=True,
                      how="left", validate="many_to_one")
    data["연도"] = data["notice_year"].astype("Int64")
    restore = data["confirmed"] & data["연도"].ne(HOLDOUT_YEAR).fillna(False)
    data = attach_amount(data, load_procure_plan(columns=["businessKey", "bidMth", "budgetAmount"]),
                         item_business_sum(load_item(columns=["businessKey", "totlAmnt"])), restore)
    # 5분위 경계는 2021~2023년 결과확정 경쟁성 공고 기준 (화면 3·분석계획표 3과 같은 함수)
    basis = data.loc[data["confirmed"]].assign(year=data["notice_year"])
    bins, names, _, _, n_basis = amount_quintiles(data, basis, AMOUNT_BASIS)
    data[AMOUNT_COL] = bins.fillna(AMOUNT_UNKNOWN)
    return data, names, n_basis


def apply_filters(df, years, org, business, method, amount="전체"):
    filtered = df.loc[df["notice_year"].isin(years)].copy()
    for col, selected in [("기관그룹", org), ("사업유형", business), ("계약방법", method), (AMOUNT_COL, amount)]:
        if selected != "전체":
            filtered = filtered.loc[filtered[col].eq(selected).fillna(False)]
    return filtered


def metric_rate(numerator, denominator):
    return f"{numerator / denominator * 100:.2f}%" if denominator else "—"


def share_table(confirmed, column, order=None):
    """그룹별 결과확정 공고 수·유찰 수와, 각각이 전체에서 차지하는 비중.

    order가 있으면(금액 구간처럼 순서가 있는 변수) 그 순서로, 없으면 유찰 비중이 큰 순서로 둔다.
    """
    g = confirmed.groupby(column).agg(**{"결과확정 공고 수": ("noticeKey", "count"),
                                         "유찰 공고 수": ("failed", "sum")})
    g["유찰 공고 수"] = g["유찰 공고 수"].astype(int)
    total_n, total_f = g["결과확정 공고 수"].sum(), g["유찰 공고 수"].sum()
    g["공고 비중(%)"] = g["결과확정 공고 수"] / total_n * 100
    g["유찰 비중(%)"] = g["유찰 공고 수"] / total_f * 100 if total_f else 0.0
    g["차이(%p)"] = g["유찰 비중(%)"] - g["공고 비중(%)"]
    if order:
        g = g.reindex([o for o in order if o in g.index])
    else:
        g = g.sort_values(["유찰 비중(%)", "결과확정 공고 수"], ascending=False)
    return g.rename_axis(column).reset_index()


def share_chart(table, column):
    """그룹마다 '결과확정 공고 중 비중'과 '유찰 중 비중' 막대를 나란히 둔다."""
    cats = table[column].tolist()
    fig = go.Figure()
    # 가로 묶음 막대는 먼저 넣은 막대가 아래에 그려지므로, 공고 비중이 위에 오도록 유찰을 먼저 넣는다
    for name, val, cnt, color in [("유찰 중 비중", "유찰 비중(%)", "유찰 공고 수", ui.COLOR_REFERENCE),
                                  ("결과확정 공고 중 비중", "공고 비중(%)", "결과확정 공고 수", ui.COLOR_BASE)]:
        fig.add_bar(y=cats, x=table[val], name=name, orientation="h", marker_color=color,
                    text=[f"{v:.1f}%" for v in table[val]], textposition="outside",
                    customdata=table[cnt], hovertemplate=f"%{{y}}<br>{name} %{{x:.1f}}% (%{{customdata:,}}건)<extra></extra>")
    # 가로 막대는 아래에서 위로 그려지므로 순서를 뒤집어 첫 그룹이 맨 위에 오게 한다
    fig.update_yaxes(title=None, categoryorder="array", categoryarray=cats[::-1])
    top = float(table[["공고 비중(%)", "유찰 비중(%)"]].max().max())
    fig.update_xaxes(title=None, range=[0, top * 1.2], ticksuffix="%")
    fig = ui.style_figure(fig, int(ui.hbar_height(len(cats)) * 1.5) + 30)
    # 두 막대를 겹치지 않고 나란히, 범례는 차트 위에 가로로
    fig.update_layout(barmode="group", bargap=0.25, showlegend=True, title=f"{column}별 공고·유찰 비중 (단위: %)",
                      legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0, traceorder="reversed"),
                      margin=dict(t=80))
    return fig


def composition_chart(confirmed, column, years, order):
    """연도별 결과확정 공고 구성 (100% 누적 가로 막대). 금액 구간은 작은 구간일수록 옅은 파랑."""
    tab = pd.crosstab(confirmed["notice_year"], confirmed[column]).reindex(columns=order, fill_value=0)
    tab = tab.reindex(sorted(y for y in years if y in tab.index))
    pct = tab.div(tab.sum(axis=1), axis=0) * 100
    if column == AMOUNT_COL:
        colors = px.colors.sequential.Blues[2:2 + len(order) - 1] + [ui.COLOR_MUTED]
    else:
        colors = px.colors.qualitative.Set2
    fig = go.Figure()
    rows = [f"{y}년" for y in pct.index]
    for i, cat in enumerate(order):
        fig.add_bar(y=rows, x=pct[cat], name=str(cat), orientation="h", marker_color=colors[i % len(colors)],
                    text=[f"{v:.1f}%" if v >= 6 else "" for v in pct[cat]], textposition="inside",
                    insidetextanchor="middle", customdata=tab[cat],
                    hovertemplate=f"%{{y}} · {cat}<br>%{{x:.1f}}% (%{{customdata:,}}건)<extra></extra>")
    fig.update_yaxes(title=None, categoryorder="array", categoryarray=rows[::-1])  # 이른 연도가 위
    fig.update_xaxes(title=None, range=[0, 100], ticksuffix="%")
    fig = ui.style_figure(fig, ui.hbar_height(len(rows)) + 110)
    # 범례가 두 줄로 넘어가도 제목과 겹치지 않도록 차트 아래에 둔다
    fig.update_layout(barmode="stack", showlegend=True, title=f"연도별 결과확정 공고 구성 — {column} (단위: %)",
                      legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="left", x=0, traceorder="normal"),
                      margin=dict(t=55, b=90))
    fig.update_traces(textfont=dict(color="#1F2937", shadow="none"), selector=dict(type="bar"))
    return fig


def _period_rates(confirmed, freq, start, end):
    """공고일 기준 기간(월 'M'·분기 'Q')별 결과확정 수·유찰 수·유찰률. 데이터 없는 기간은 NaN으로 남긴다."""
    period = confirmed["notice_date"].dt.to_period(freq)
    g = confirmed.groupby(period)["failed"].agg(n="size", f="sum")
    g = g.reindex(pd.period_range(start, end, freq=freq))
    g["rate"] = g["f"] / g["n"] * 100
    return g


def trend_chart(confirmed, years):
    """연·월별 유찰률 추이: 월별 값(흐린 선) + 분기 유찰률(진한 선) + 선택 기간 전체(빨간 점선).

    가로축은 실제 시간 순서. 선택하지 않은 연도는 회색 '미선택' 띠로 두고 선을 끊는다
    (빈 연도를 빼고 이어 붙이면 1년이 사라진 걸 모르고 연속 추세로 읽게 되므로).
    """
    first, last = min(years), max(years)
    monthly = _period_rates(confirmed, "M", f"{first}-01", f"{last}-12")
    quarterly = _period_rates(confirmed, "Q", f"{first}Q1", f"{last}Q4")
    hover = "%{customdata[0]}<br>유찰률 %{y:.1f}%<br>유찰 %{customdata[1]:,.0f}건 / 결과확정 %{customdata[2]:,.0f}건<extra></extra>"

    fig = go.Figure()
    fig.add_scatter(
        x=monthly.index.to_timestamp(how="start"), y=monthly["rate"],
        mode="lines+markers", line=dict(color=ui.COLOR_BASE, width=1.5), marker=dict(size=4),
        connectgaps=False, hovertemplate=hover,
        customdata=np.column_stack([monthly.index.strftime("%Y년 %m월"), monthly["f"], monthly["n"]]),
    )
    fig.add_scatter(  # 분기 값은 분기 첫 달(1·4·7·10월) 위치에 찍어 월별 점·눈금과 맞춘다
        x=quarterly.index.to_timestamp(how="start"), y=quarterly["rate"],
        mode="lines+markers", line=dict(color=ui.COLOR_HIGHLIGHT, width=3), marker=dict(size=7),
        connectgaps=False, hovertemplate=hover,
        customdata=np.column_stack([[f"{p.year}년 {p.quarter}분기" for p in quarterly.index],
                                    quarterly["f"], quarterly["n"]]),
    )
    overall = confirmed["failed"].mean() * 100 if len(confirmed) else np.nan
    if pd.notna(overall):
        fig.add_hline(y=overall, line_dash="dash", line_color=ui.COLOR_REFERENCE, line_width=2,
                      annotation_text=f"선택 기간 전체 {overall:.2f}%", annotation_position="bottom right",
                      annotation_font=dict(color=ui.COLOR_REFERENCE, size=12),
                      annotation_bgcolor="rgba(255,255,255,0.85)")  # 월별 선과 겹쳐도 읽히게
    for y in range(first, last + 1):
        if y not in years:
            # 날짜축에서는 add_vrect의 글자 위치 계산이 되지 않아 띠와 '미선택' 글자를 따로 그린다
            fig.add_vrect(x0=f"{y}-01-01", x1=f"{y + 1}-01-01", fillcolor="#E5E7EB", opacity=0.6,
                          layer="below", line_width=0)
            fig.add_annotation(x=f"{y}-07-01", y=1, yref="paper", yanchor="top", showarrow=False,
                               text="미선택", font=dict(color="#6B7280", size=12))
    top = np.nanmax([monthly["rate"].max(), quarterly["rate"].max(), overall if pd.notna(overall) else 0])
    fig.update_yaxes(title=None, range=[0, (top if pd.notna(top) else 10) * 1.15], ticksuffix="%")
    # 큰 눈금은 6개월(1·7월), 보조 격자는 분기 시작(1·4·7·10월)마다 그어 분기 점이 격자 위에 오게 한다.
    # 점이 달의 1일에 찍히므로 양 끝은 보름씩 여유를 둔다
    fig.update_xaxes(title=None, dtick="M6", tickformat="%Y-%m",
                     minor=dict(dtick="M3", showgrid=True, gridcolor="#F3F4F6"),
                     range=[pd.Timestamp(first, 1, 1) - pd.Timedelta(days=15),
                            pd.Timestamp(last, 12, 1) + pd.Timedelta(days=15)])
    fig = ui.style_figure(fig, 360)
    # 세로축 이름은 글자를 돌리지 않고 차트 제목에 단위와 함께 쓴다
    fig.update_layout(title="연·월별 유찰률 (단위: %)", margin=dict(t=55))
    return fig


# ============================================================
# 2. Streamlit 화면
# ============================================================
def render():
    st.title("전체 유찰 현황")
    ui.source_note()

    try:
        full, amount_names, amount_basis_n = prepare_overview_data()
    except (FileNotFoundError, KeyError, ValueError, ImportError) as exc:
        st.error(f"공고 데이터를 준비하지 못했습니다: {exc}")
        st.stop()

    with st.container(border=True):
        st.subheader("조회 조건")
        # 연도 버튼을 눌러 켜고 끔. 연속되지 않은 연도 조합도 고를 수 있고 순서가 항상 고정된다
        years = st.pills("공고 연도", [2021, 2022, 2023, 2024, 2025], selection_mode="multi",
                         default=[2021, 2022, 2023, 2024, 2025], key="overview_years")
        # 나머지 조건은 한 줄에 두 개씩 놓아 행·열을 맞춘다
        c1, c2 = st.columns(2)
        org = c1.selectbox("기관그룹", ["전체"] + sorted(full["기관그룹"].dropna().unique().tolist()))
        business = c2.selectbox("사업유형", ["전체"] + sorted(full["사업유형"].dropna().unique().tolist()))
        c3, c4 = st.columns(2)
        methods = full.loc[~full["is_private"], "계약방법"].dropna().unique().tolist()
        method = c3.selectbox("계약방법", ["전체"] + sorted(methods))
        # 금액 구간은 크기 순서(Q1→Q5)로 두고 금액 미확인은 맨 뒤
        amount = c4.selectbox("사업규모금액", ["전체"] + amount_names + [AMOUNT_UNKNOWN],
                              help=md_safe(f"{AMOUNT_BASIS[0]}~{AMOUNT_BASIS[1]}년 결과확정 경쟁성 공고 기준 5분위 "
                                           "(분석계획표 3·KPI 정의서와 같은 경계). 연도 비교를 위해 공고 연도를 바꿔도 "
                                           "경계는 고정합니다"))

    if not years:
        st.warning("공고 연도를 하나 이상 선택해 주세요")
        st.stop()

    data = apply_filters(full, years, org, business, method, amount)
    private_excluded = int(data["is_private"].sum())
    data = data.loc[~data["is_private"]]
    confirmed = data.loc[data["confirmed"]]
    failed_count = int(confirmed["failed"].sum())

    # --------------------------------------------------------
    # 01. 전체 유찰 규모
    # --------------------------------------------------------
    st.markdown("<div style='height: 32px'></div>", unsafe_allow_html=True)
    st.subheader("01. 전체 유찰 규모")
    st.caption("국방 조달에서 유찰이 얼마나 발생하고 있는가?")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("전체 공고 수", f"{len(data):,}건")
    c2.metric("결과확정 공고 수", f"{len(confirmed):,}건")
    c3.metric("유찰 공고 수", f"{failed_count:,}건")
    c4.metric("유찰률", metric_rate(failed_count, len(confirmed)))
    st.caption("유찰률의 분모는 결과확정 경쟁성 공고(유찰·낙찰·순위확정)이며, 미확인·취소·판단보류는 포함하지 않습니다  \n"
               f"수의계약 {private_excluded:,}건은 경쟁입찰과 발생 구조가 달라 모든 지표에서 제외")

    # 연·월별 추이 (프로젝트 정의서 6 화면 설계의 '월별 유찰률')
    if confirmed.empty:
        st.info("결과확정 공고가 없어 연·월별 추이를 그릴 수 없습니다")
    else:
        st.plotly_chart(trend_chart(confirmed, years), use_container_width=True)
        st.caption("흐린 선 = 월별 유찰률, 진한 선 = 분기 유찰률, 빨간 점선 = 선택 기간 전체 유찰률 · 공고일 기준  \n"
                   "추세 확인용입니다")
        
    # --------------------------------------------------------
    # 02. 공고·유찰 분포 (유찰률 비교·검정은 화면 3이 맡는다)
    # --------------------------------------------------------
    st.markdown("<div style='height: 21px'></div>", unsafe_allow_html=True)
    st.subheader("02. 공고·유찰 분포 — 유찰이 어디에 몰렸나")
    st.caption("결과확정 공고와 유찰이 각 그룹에 얼마나 나뉘어 있는지 비교합니다. "
               "유찰 비중이 공고 비중보다 크면 그 그룹에 유찰이 몰린 것입니다")
    tabs = st.tabs(["기관그룹", "계약방법", "사업유형", "사업규모금액"])

    for tab, col in zip(tabs, ["기관그룹", "계약방법", "사업유형", AMOUNT_COL]):
        with tab:
            if confirmed.empty:
                st.info("결과확정 공고가 없습니다")
                continue
            order = amount_names + [AMOUNT_UNKNOWN] if col == AMOUNT_COL else None
            table = share_table(confirmed, col, order)
            st.plotly_chart(share_chart(table, col), use_container_width=True)

            # 공고 몫보다 유찰이 가장 많이 몰린 그룹 (유찰이 있을 때만)
            if table["유찰 공고 수"].sum():
                top = table.sort_values("차이(%p)", ascending=False).iloc[0]
                st.info(f"'{top[col]}'은(는) 결과확정 공고의 {top['공고 비중(%)']:.1f}%인데 "
                        f"유찰의 {top['유찰 비중(%)']:.1f}%를 차지합니다 (차이 {top['차이(%p)']:+.1f}%p)")
            if col == AMOUNT_COL:
                # '~' 두 개 사이가 마크다운 취소선이 되지 않도록 md_safe로 감싼다 (화면 3과 같은 방식)
                st.caption(md_safe(
                    f"※ 구간 경계: {AMOUNT_BASIS[0]}~{AMOUNT_BASIS[1]}년 결과확정 경쟁성 공고 "
                    f"{amount_basis_n:,}건의 20·40·60·80% 분위수. "
                    f"{AMOUNT_UNKNOWN} = 계획예산·품목합계로 금액을 산정하지 못한 공고  \n"
                    "※ 경계는 공고 연도를 바꿔도 고정합니다. 연도마다 다시 나누면 같은 'Q1'이라도 금액대가 달라져 "
                    "연도 간 비교가 안 되기 때문입니다. 화면 2 위험도의 금액 구간은 2025년 최종 평가 학습 범위"
                    "(2021~2024년) 기준이라 경계가 조금 다릅니다"))
            with st.expander("그룹별 건수 표"):
                st.dataframe(table, hide_index=True, use_container_width=True, column_config={
                    c: st.column_config.NumberColumn(format="%.1f") for c in table.columns if "(%" in c})

            # 연도별 구성: 선택한 연도마다 결과확정 공고가 그룹에 어떻게 나뉘었나 (합 100%)
            st.plotly_chart(composition_chart(confirmed, col, years, table[col].tolist()),
                            use_container_width=True)
            st.caption("막대 하나 = 그해 결과확정 공고 전체(100%) · 6% 미만 구간은 숫자 생략")
