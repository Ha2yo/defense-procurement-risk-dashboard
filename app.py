import streamlit as st

from scripts.dashboard.pages import overview_01, risk_prediction_02, failure_factors_03, delay_effect_04


# ============================================================
# 1. 기본 설정
# ============================================================
st.set_page_config(
    page_title="군수품 조달 유찰 위험 대시보드",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

PAGES = {
    "전체 유찰 현황": overview_01.render,
    "신규 공고 위험도 및 유사사례": risk_prediction_02.render,
    "유찰 요인 분석": failure_factors_03.render,
    "유찰 후 지연 영향": delay_effect_04.render,
}


# ============================================================
# 2. 사이드바 / 화면 이동
# ============================================================
st.sidebar.title("군수품 조달 분석")
st.sidebar.caption("데이터 기간: 2021.01.01 ~ 2025.12.31")

st.sidebar.divider()
page = st.sidebar.radio("화면 선택", list(PAGES.keys()))
st.sidebar.divider()

# ============================================================
# 3. 선택 화면 출력
# ============================================================
PAGES[page]()
