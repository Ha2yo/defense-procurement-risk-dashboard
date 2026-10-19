import streamlit as st

from scripts.dashboard.components import content_placeholder, metric_placeholder, source_note

from scripts.dashboard.data_loader import load_notic
def render():
    st.title("전체 유찰 현황")
    st.caption("전체 유찰률 및 연도·기관·사업유형·계약방법별 현황을 탐색합니다.")
    source_note()

    with st.container(border=True):
        st.subheader("조회 조건")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.multiselect("공고 연도", [2021, 2022, 2023, 2024, 2025], default=[2021, 2022, 2023, 2024, 2025])
        with c2:
            st.selectbox("기관그룹", ["전체", "육군", "해군", "공군", "국방부 직할·공통", "기타"])
        with c3:
            st.selectbox("사업유형", ["전체", "물품", "용역"])
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        metric_placeholder("결과확정 공고")
    with c2:
        metric_placeholder("유찰 공고")
    with c3:
        metric_placeholder("유찰률")
    with c4:
        metric_placeholder("재공고 비중")

    tab1, tab2 = st.tabs(["유찰 추이", "기관·사업유형 비교"])
    with tab1:
        content_placeholder("연도·월별 유찰률", "공고일(pblancDate) 기준 추이 그래프")
    with tab2:
        content_placeholder("그룹별 유찰률 비교", "기관그룹·사업유형·계약방법별 막대그래프")

    with st.expander("집계 기준 확인"):
        st.write("유찰률 = 유찰 공고 수 / 결과확정 공고 수 × 100")
        st.write("재공고 비중은 유찰률과 분모가 다르며, 취소공고를 제외한 실질공고 기준으로 산출합니다.")
