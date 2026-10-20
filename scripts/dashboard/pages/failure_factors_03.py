import streamlit as st

from scripts.dashboard.components import content_placeholder, metric_placeholder, source_note


FACTORS = ["기관그룹", "사업유형", "계약·경쟁방식", "공고기간", "기초금액", "공고 시기"]


def render():
    st.title("유찰 요인 분석")
    st.caption("공고 조건에 따라 유찰률이 어떻게 달라지는지 탐색합니다. 통계적 연관성과 인과관계는 구분합니다.")
    source_note()

    c1, c2 = st.columns([2, 1])
    with c1:
        factor = st.selectbox("분석 변수", FACTORS)
    with c2:
        period = st.selectbox("분석 기간", ["2021~2023년", "2024년", "2025년", "2021~2025년"])

    c1, c2, c3 = st.columns(3)
    with c1:
        metric_placeholder("분석 대상 공고 수")
    with c2:
        metric_placeholder("카이제곱 p-value")
    with c3:
        metric_placeholder("Cramér's V")

    content_placeholder(f"{factor}별 유찰률", f"기간: {period} · 구간별 공고 수와 유찰률")
    tab1, tab2 = st.tabs(["교차표", "통계 검정 해석"])
    with tab1:
        content_placeholder("변수 × 유찰 여부 교차표", "데이터 연결 후 각 범주의 유찰·비유찰 건수")
    with tab2:
        st.info("p-value는 유의성 검정, Cramér's V는 연관성 크기 확인에 사용합니다. 이 값만으로 유찰의 원인이라고 단정하지 않습니다.")
    if factor == "기초금액":
        st.warning("bsicExpt의 단가성·총액성 해석은 품목명세서와 교차 검증 후 확정할 예정입니다.")
