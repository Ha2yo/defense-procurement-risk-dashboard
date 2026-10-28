"""전체 유찰 현황: 유찰 규모 → 집중 영역."""
import importlib
import os

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from scripts.dashboard import components as ui
from scripts.dashboard.data_loader import load_notice, load_result
from scripts.dashboard.pages.failure_factors_03 import BREAK_GAP, wave_gap_path  # 화면 3과 같은 물결 생략 표시

# 파일명이 숫자로 시작해 일반 import 문을 쓸 수 없으므로 importlib로 불러온다
prepare_bid_result = importlib.import_module(
    "scripts.analysis.05_rebid_followup_analysis"
).prepare

PRIVATE_KEYWORD = "수의"  # 계약방법에 이 글자가 있으면 수의계약 (화면 2·3과 같은 기준)
MIN_CATEGORY_SIZE = 30  # 계약방법: 결과확정 공고가 이보다 적으면 순위 비교에서 분리 (화면 3과 같은 기준)
ETC = "기타"
AXIS_PAD = 1.3  # x축 끝 = 최댓값 × 1.3 (막대 끝 '값  (n=표본 수)' 라벨 자리)


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
    return data


def apply_filters(df, years, org, business, method):
    filtered = df.loc[df["notice_year"].isin(years)].copy()
    for col, selected in [("기관그룹", org), ("사업유형", business), ("계약방법", method)]:
        if selected != "전체":
            filtered = filtered.loc[filtered[col].eq(selected).fillna(False)]
    return filtered


def metric_rate(numerator, denominator):
    return f"{numerator / denominator * 100:.2f}%" if denominator else "—"


def group_summary(df, column):
    confirmed = df.loc[df["confirmed"]]
    if confirmed.empty:
        return pd.DataFrame(columns=[column, "결과확정 공고 수", "유찰 공고 수", "유찰률(%)"])
    out = confirmed.groupby(column, dropna=False).agg(
        **{"결과확정 공고 수": ("noticeKey", "count"),
           "유찰 공고 수": ("failed", "sum")}
    ).reset_index()
    out["유찰률(%)"] = out["유찰 공고 수"] / out["결과확정 공고 수"] * 100
    return out


def split_small(summary, column):
    """결과확정 공고 30건 미만 범주를 한 행으로 묶어 따로 돌려줍니다 (화면 3 계약·경쟁방식과 같은 방식).

    하나뿐이면 그 범주 이름을, 둘 이상이면 '기타'를 이름으로 쓴다.
    반환: (비교 대상 요약표, 소표본 행 또는 빈 표, {묶인 범주: 공고 수})
    """
    is_small = summary["결과확정 공고 수"] < MIN_CATEGORY_SIZE
    small = summary.loc[is_small]
    if small.empty or is_small.all():  # 모두 소표본이면 비교 대상이 없으므로 나누지 않는다
        return summary, summary.iloc[0:0], {}
    members = dict(zip(small[column], small["결과확정 공고 수"]))
    label = small[column].iloc[0] if len(small) == 1 else ETC
    total, fails = small["결과확정 공고 수"].sum(), small["유찰 공고 수"].sum()
    merged = pd.DataFrame({column: [label], "결과확정 공고 수": [total],
                           "유찰 공고 수": [fails], "유찰률(%)": [fails / total * 100]})
    return summary.loc[~is_small], merged, members


def axis_limit(top_value, small_value, unit):
    """x축 끝(비교 대상 최댓값의 AXIS_PAD배)과, 소표본 유찰률이 그보다 훨씬 클 때 오른쪽 축 구간 (없으면 None).

    오른쪽 축은 화면 3과 같이 소표본 값 바로 아래 5 단위 눈금에서 한 칸 더 내려 시작하고 15만큼 보여 준다.
    그 시작점이 왼쪽 축 끝보다 작으면 축을 나누지 않고 값까지 늘린다.
    """
    axis_max = top_value * AXIS_PAD
    if small_value is None or small_value <= axis_max:
        return axis_max, None
    lo = np.floor(small_value / 5) * 5 - 5
    if unit != "%" or lo <= axis_max:
        return small_value * AXIS_PAD, None
    return axis_max, (lo, lo + 15)


# ============================================================
# 2. Streamlit 화면
# ============================================================
def render():
    st.title("전체 유찰 현황")
    ui.source_note()

    try:
        full = prepare_overview_data()
    except (FileNotFoundError, KeyError, ValueError, ImportError) as exc:
        st.error(f"공고 데이터를 준비하지 못했습니다: {exc}")
        st.stop()

    with st.container(border=True):
        st.subheader("조회 조건")
        c1, c2 = st.columns(2)
        with c1:
            # 연도 버튼을 눌러 켜고 끔. 연속되지 않은 연도 조합도 고를 수 있고 순서가 항상 고정된다
            years = st.pills("공고 연도", [2021, 2022, 2023, 2024, 2025], selection_mode="multi",
                             default=[2021, 2022, 2023, 2024, 2025], key="overview_years")
            org = st.selectbox("기관그룹", ["전체"] + sorted(full["기관그룹"].dropna().unique().tolist()))
        with c2:
            business = st.selectbox("사업유형", ["전체"] + sorted(full["사업유형"].dropna().unique().tolist()))
            methods = full.loc[~full["is_private"], "계약방법"].dropna().unique().tolist()
            method = st.selectbox("계약방법", ["전체"] + sorted(methods))

    if not years:
        st.warning("공고 연도를 하나 이상 선택해 주세요")
        st.stop()

    data = apply_filters(full, years, org, business, method)
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

    # --------------------------------------------------------
    # 02. 유찰 집중 영역
    # --------------------------------------------------------
    st.markdown("<div style='height: 21px'></div>", unsafe_allow_html=True)
    st.subheader("02. 유찰 집중 영역 — 우선 분석 대상")
    st.caption("유찰 건수와 유찰률을 함께 비교해 먼저 검토할 영역을 탐색합니다")
    order = st.radio("정렬 기준", ["유찰 건수", "유찰률"], horizontal=True)
    tabs = st.tabs(["기관그룹", "계약방법", "사업유형"])
    
    for tab, col in zip(tabs, ["기관그룹", "계약방법", "사업유형"]):
        with tab:
            summary = group_summary(data, col)
            if summary.empty:
                st.info("결과확정 공고가 없습니다")
                continue
            sort_col = "유찰 공고 수" if order == "유찰 건수" else "유찰률(%)"

            # 계약방법은 공고가 아주 적은 방식의 유찰률이 크게 흔들리므로 순위에서 빼고 맨 아래에 둔다
            small, members = summary.iloc[0:0], {}
            if col == "계약방법":
                summary, small, members = split_small(summary, col)
            summary = summary.sort_values([sort_col, "결과확정 공고 수"], ascending=False)
            top_value = summary[sort_col].max()
            summary = pd.concat([summary, small], ignore_index=True)
            unit = "건" if sort_col == "유찰 공고 수" else "%"
            is_small = summary.index >= len(summary) - len(small)
            # 화면 3처럼 막대 끝에 표본 수(결과확정 공고 수)를 함께 적는다
            summary["label"] = [(f"{v:,.0f}건" if unit == "건" else f"{v:.2f}%") + f"  (n={n:,})"
                                for v, n in zip(summary[sort_col], summary["결과확정 공고 수"])]
            # 소표본 막대가 훨씬 길면 화면 3처럼 x축을 둘로 나누고 중간을 물결로 생략한다
            small_value = summary.loc[is_small, sort_col].iloc[0] if is_small.any() else None
            axis_max, broken = axis_limit(top_value, small_value, unit)
            summary["bar"] = summary[sort_col]
            if broken:
                summary.loc[is_small, "bar"] = axis_max  # 왼쪽 축에서는 축 끝까지만
                summary.loc[is_small, "label"] = ""      # 숫자는 오른쪽 축 막대에 붙인다
            fig = px.bar(summary, x="bar", y=col, orientation="h", text="label",
                         custom_data=["유찰 공고 수", "결과확정 공고 수", "유찰률(%)"])
            # 화면 3과 같이 1위 막대만 진한 색으로 강조, 소표본 묶음은 회색
            is_top = summary[sort_col].eq(top_value) & ~is_small
            hover = ("%{y}<br>유찰 %{customdata[0]:,}건 / 결과확정 %{customdata[1]:,}건"
                     "<br>유찰률 %{customdata[2]:.2f}%<extra></extra>")
            fig.update_traces(
                marker_color=[ui.COLOR_MUTED if s else ui.COLOR_HIGHLIGHT if t else ui.COLOR_BASE
                              for s, t in zip(is_small, is_top)],
                textposition="outside", hovertemplate=hover,
            )
            # 가로 막대는 아래에서 위로 그려지므로 정렬 순서를 뒤집어 첫 항목이 맨 위에 오게 한다
            fig.update_yaxes(title=None, categoryorder="array", categoryarray=list(summary[col])[::-1])
            fig.update_xaxes(title=None, range=[0, axis_max])
            if broken:
                lo, hi = broken
                row = summary.loc[is_small].iloc[0]
                fig.add_bar(
                    y=[row[col]], x=[row[sort_col] - lo], base=[lo], orientation="h", xaxis="x2",
                    marker_color=ui.COLOR_MUTED, textposition="outside",
                    text=[f"{row[sort_col]:.2f}%  (n={row['결과확정 공고 수']:,})"],
                    customdata=[[row["유찰 공고 수"], row["결과확정 공고 수"], row["유찰률(%)"]]],
                    hovertemplate=hover, showlegend=False,
                )
                # 두 축의 1%당 길이가 같도록 차트 폭을 각 축의 구간 길이에 비례해 나눈다
                left_end = (1 - BREAK_GAP) * axis_max / (axis_max + hi - lo)
                fig.update_layout(
                    xaxis=dict(domain=[0, left_end], dtick=5),
                    xaxis2=dict(domain=[left_end + BREAK_GAP, 1], range=[lo, hi], anchor="y", dtick=5),
                )
                # 소표본 막대는 맨 아래(y=0)에 있으므로 그 위치에 물결 틈을 그린다
                fig.add_shape(type="path", xref="paper", yref="y", fillcolor="white", line=dict(width=0),
                              layer="above", path=wave_gap_path(left_end - 0.004, 0, BREAK_GAP + 0.008, 0.003))
            fig = ui.style_figure(fig, ui.hbar_height(len(summary)) + 30)
            fig.update_layout(title=f"{col}별 {sort_col.replace('(%)', '')} (단위: {unit})",
                              margin=dict(t=55))  # 차트 제목 자리
            st.plotly_chart(fig, use_container_width=True)
            st.caption("n = 결과확정 공고 수 (유찰률의 분모)")
            if members:
                label = summary[col].iloc[-1]
                reason = "공고가 적어 유찰률이 크게 흔들리므로 순위 비교·강조에서 제외하고 회색으로 맨 아래에 표시합니다"
                if label == ETC:
                    listed = " · ".join(f"{k} {v:,}건" for k, v in members.items())
                    st.caption(f"※ 기타 = 결과확정 공고 {MIN_CATEGORY_SIZE}건 미만 계약방법 묶음 ({listed}). {reason}")
                else:
                    st.caption(f"※ {label}: 결과확정 공고 {members[label]:,}건 ({MIN_CATEGORY_SIZE}건 미만). {reason}")
            if broken:
                st.caption(f"※ {summary[col].iloc[-1]} 유찰률({small_value:.1f}%)이 다른 범주보다 훨씬 커서 "
                           f"x축을 나누고 중간을 생략(물결 표시)했습니다. 오른쪽 축은 {broken[0]:.0f}~{broken[1]:.0f}% 구간")
            count_leader = summary.loc[~is_small].sort_values("유찰 공고 수", ascending=False).iloc[0]
            total_failed = int(confirmed["failed"].sum())
            leader_failed = int(count_leader["유찰 공고 수"])
            share = leader_failed / total_failed * 100 if total_failed else 0
            
            st.info(
                f"{col}별 유찰 건수가 가장 많은 그룹은 '{count_leader[col]}'입니다. "
                f"유찰 {leader_failed:,}건으로 선택한 범위의 전체 유찰 "
                f"{total_failed:,}건 중 {share:.1f}%를 차지합니다"
            )
            
            st.dataframe(summary.drop(columns=["label", "bar"]), hide_index=True, use_container_width=True,
                         column_config={"유찰률(%)": st.column_config.NumberColumn(format="%.2f%%")})
