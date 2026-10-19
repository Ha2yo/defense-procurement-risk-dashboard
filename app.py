import streamlit as st

from scripts.dashboard.pages import overview, risk_prediction, failure_factors, delay_analysis


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
    "전체 유찰 현황": overview.render,
    "신규 공고 위험도 및 유사사례": risk_prediction.render,
    "유찰 요인 분석": failure_factors.render,
    "유찰 후 지연 영향": delay_analysis.render,
}


# ============================================================
# 2. 사이드바 / 화면 이동
# ============================================================
st.sidebar.title("군수품 조달 분석")
st.sidebar.caption("2021~2025년 · 화면 설계 시안")

page = st.sidebar.radio("화면 선택", list(PAGES.keys()))
st.sidebar.divider()
st.sidebar.info("현재는 화면 구성 단계입니다. CLEAN/분석 데이터는 연결하지 않았습니다.")


# ============================================================
# 3. 선택 화면 출력
# ============================================================
PAGES[page]()
