import streamlit as st

from scripts.dashboard.components import content_placeholder, metric_placeholder


def render():
    st.title("신규 공고 위험도 및 유사사례")
    st.caption("2025년 공고를 선택해 공고 당시 이용 가능한 정보에 기반한 위험도와 근거를 확인하는 화면입니다.")
    st.warning("현재 화면 시안입니다. 실제 2025년 공고 목록과 위험도 기준표는 추후 연결합니다.")

    with st.form("notice_lookup_form"):
        st.subheader("공고 선택")
        notice_key = st.text_input("공고키(noticeKey)", placeholder="예: 2025_ABC_01234_ABC0001_1")
        c1, c2 = st.columns(2)
        with c1:
            st.selectbox("2025년 공고 목록", ["데이터 연결 후 선택 가능"], disabled=True)
        with c2:
            st.checkbox("실제 결과도 함께 보기", value=True)
        submitted = st.form_submit_button("공고 조회", type="primary")

    if submitted:
        if notice_key.strip():
            st.info("화면 입력은 확인됐습니다. 아직 조회 데이터가 연결되지 않아 위험도를 계산하지 않습니다.")
        else:
            st.warning("조회할 공고키를 입력해 주세요.")

    c1, c2, c3 = st.columns(3)
    with c1:
        metric_placeholder("예측 유찰 위험도")
    with c2:
        metric_placeholder("근거 공고 수")
    with c3:
        metric_placeholder("실제 유찰 결과")

    content_placeholder("위험도 산정 근거", "실제 적용 조합, 10건 미만 상위 조합 대체 여부, 과거 유찰률")
    content_placeholder("유사 공고 목록", "선택 공고의 조건에 해당하는 과거 공고와 당시 낙찰·유찰 결과")
    st.caption("주의: 2025년 실제 결과는 위험도 계산에 사용하지 않고, 계산이 끝난 뒤 비교 목적으로만 표시합니다.")
