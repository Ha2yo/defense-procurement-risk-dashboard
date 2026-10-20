import streamlit as st

from scripts.dashboard.components import content_placeholder, metric_placeholder


def render():
    st.title("유찰 후 지연 영향")
    st.caption("유찰 후 후속공고·후속결과 확인 여부와 추가 소요일을 살펴봅니다.")

    with st.container(border=True):
        c1, c2 = st.columns(2)
        with c1:
            st.selectbox("공고 연도", ["전체", "2021", "2022", "2023", "2024", "2025"])
        with c2:
            st.radio("후속 데이터 상태", ["전체", "후속공고 확인", "후속 재공고 결과확정", "후속입찰 미확인"], horizontal=False)

    c1, c2, c3 = st.columns(3)
    with c1:
        metric_placeholder("유찰 공고 수")
    with c2:
        metric_placeholder("후속 재공고 확인")
    with c3:
        metric_placeholder("평균 추가 소요일")

    tab1, tab2 = st.tabs(["후속공고 현황", "추가지연 분포"])
    with tab1:
        content_placeholder("후속 상태 분포", "후속공고 발견 여부 · 재공고 여부 · 결과 확인 상태")
    with tab2:
        content_placeholder("추가 소요일 히스토그램", "결과까지 확인된 후속 재공고의 개찰일 간격")

    with st.expander("추가 소요일 산정 기준"):
        st.write("후속 재공고의 개찰일 − 직전 유찰 공고의 개찰일")
        st.write("후속입찰 미확인 사례는 추가 소요일 평균에서 제외하되 별도 상태로 보존합니다.")
        st.write("공고일이 2025년 4분기인 유찰 사례를 제외한 민감도 결과도 후속 구현 시 표시합니다.")
