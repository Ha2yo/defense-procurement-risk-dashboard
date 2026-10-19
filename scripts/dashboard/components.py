import streamlit as st


def metric_placeholder(label, help_text=None):
    """실제 데이터 연동 전 지표의 자리를 표시합니다."""
    st.metric(label, "—", help=help_text)


def content_placeholder(title, description, height=190):
    """데이터 차트·테이블이 배치될 영역을 표시합니다."""
    with st.container(border=True):
        st.subheader(title)
        st.caption(description)
        st.info("데이터 연결 후 이 영역에 결과를 표시합니다.")


def source_note():
    st.caption(
        "자료 범위: 2021~2025년 군수품 조달자료 · "
        "유찰은 공고(noticeKey) 단위 · 결과 미확인/판단보류는 유찰률에서 제외"
    )
