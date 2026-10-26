"""전체 유찰 현황: 유찰 규모 → 집중 영역."""
import importlib
import os

import pandas as pd
import plotly.express as px
import streamlit as st

from scripts.dashboard.components import source_note
from scripts.dashboard.data_loader import load_notice, load_result

# 파일명이 숫자로 시작해 일반 import 문을 쓸 수 없으므로 importlib로 불러온다
prepare_bid_result = importlib.import_module(
    "scripts.analysis.05_rebid_followup_analysis"
).prepare


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


# ============================================================
# 2. Streamlit 화면
# ============================================================
def render():
    st.title("전체 유찰 현황")
    st.caption("2021~2025년 · 공고(noticeKey) 단위 · 유찰 규모와 우선 분석 대상")
    source_note()

    try:
        full = prepare_overview_data()
    except (FileNotFoundError, KeyError, ValueError, ImportError) as exc:
        st.error(f"공고 데이터를 준비하지 못했습니다: {exc}")
        st.stop()

    with st.container(border=True):
        st.subheader("조회 조건")
        c1, c2 = st.columns(2)
        with c1:
            years = st.multiselect("공고 연도", [2021, 2022, 2023, 2024, 2025], default=[2021, 2022, 2023, 2024, 2025])
            org = st.selectbox("기관그룹", ["전체"] + sorted(full["기관그룹"].dropna().unique().tolist()))
        with c2:
            business = st.selectbox("사업유형", ["전체"] + sorted(full["사업유형"].dropna().unique().tolist()))
            method = st.selectbox("계약방법", ["전체"] + sorted(full["계약방법"].dropna().unique().tolist()))

    data = apply_filters(full, years, org, business, method)
    confirmed = data.loc[data["confirmed"]]
    failed_count = int(confirmed["failed"].sum())

    # --------------------------------------------------------
    # 01. 전체 유찰 규모
    # --------------------------------------------------------
    st.subheader("01. 전체 유찰 규모")
    st.caption("국방 조달에서 유찰이 얼마나 발생하고 있는가?")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("전체 공고 수", f"{len(data):,}건")
    c2.metric("결과확정 공고 수", f"{len(confirmed):,}건")
    c3.metric("유찰 공고 수", f"{failed_count:,}건")
    c4.metric("유찰률", metric_rate(failed_count, len(confirmed)))
    st.caption("유찰률의 분모는 결과확정 공고(유찰·낙찰·순위확정)이며, 미확인·취소·판단보류는 포함하지 않습니다.")

    # --------------------------------------------------------
    # 02. 유찰 집중 영역
    # --------------------------------------------------------
    st.subheader("02. 유찰 집중 영역 — 우선 분석 대상")
    st.caption("유찰 건수와 유찰률을 함께 비교해 먼저 검토할 영역을 탐색합니다.")
    order = st.radio("정렬 기준", ["유찰 건수 많은 순", "유찰률 높은 순"], horizontal=True)
    tabs = st.tabs(["기관그룹", "계약방법", "사업유형"])
    
    for tab, col in zip(tabs, ["기관그룹", "계약방법", "사업유형"]):
        with tab:
            summary = group_summary(data, col)
            if summary.empty:
                st.info("결과확정 공고가 없습니다.")
                continue
            sort_col = "유찰 공고 수" if order == "유찰 건수 많은 순" else "유찰률(%)"
            
            summary = summary.sort_values([sort_col, "결과확정 공고 수"], ascending=False)
            fig = px.bar(summary, x=col, y=sort_col, hover_data=["유찰 공고 수", "결과확정 공고 수", "유찰률(%)"], color_discrete_sequence=["#4C78A8"])
            unit = "건" if sort_col == "유찰 공고 수" else "%"
            
            fig.update_layout(
                title=f"{col}별 {sort_col.replace('(%)', '')} (단위: {unit})",
                xaxis_title=None,
                yaxis_title=None,
            )
            
            st.plotly_chart(fig, use_container_width=True)
            count_leader = summary.sort_values("유찰 공고 수", ascending=False).iloc[0]
            total_failed = int(confirmed["failed"].sum())
            leader_failed = int(count_leader["유찰 공고 수"])
            share = leader_failed / total_failed * 100 if total_failed else 0
            
            st.info(
                f"{col}별 유찰 건수가 가장 많은 그룹은 '{count_leader[col]}'입니다. "
                f"유찰 {leader_failed:,}건으로 선택한 범위의 전체 유찰 "
                f"{total_failed:,}건 중 {share:.1f}%를 차지합니다."
            )
            
            st.dataframe(summary, hide_index=True, use_container_width=True,
                         column_config={"유찰률(%)": st.column_config.NumberColumn(format="%.2f%%")})
