import streamlit as st

# 네 화면 차트(plotly) 공통 색·크기. 강조색은 .streamlit/config.toml의 primaryColor와 같다
COLOR_BAR = "#4C78A8"        # 기본 막대
COLOR_BASE = "#B7C6D9"       # 강조 차트의 일반 막대
COLOR_HIGHLIGHT = "#2F5D8C"  # 강조 막대 (최댓값)
COLOR_MUTED = "#D3D3D3"      # 순위 비교에서 뺀 범주 (예: 기타)
COLOR_REFERENCE = "#C0392B"  # 전체 평균·중앙값 기준선
BAR_STEP = 46                # 가로 막대 한 칸 높이(px)


def style_figure(fig, height):
    """plotly 차트 공통 레이아웃: 범례 없음, 여백·글꼴 통일, 막대 옆 숫자가 잘리지 않게 함."""
    fig.update_layout(
        height=height,
        showlegend=False,
        barmode="overlay",
        margin=dict(l=10, r=30, t=30, b=40),
        font=dict(size=13),
        hoverlabel=dict(font_size=13),
    )
    fig.update_traces(cliponaxis=False, selector=dict(type="bar"))
    return fig


def hbar_height(n_rows):
    """가로 막대 차트 높이: 막대 수와 관계없이 한 칸 높이를 BAR_STEP으로 맞춘다."""
    return BAR_STEP * max(n_rows, 1) + 80


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
        "유찰은 공고(noticeKey) 단위 · 결과 미확인/판단보류/수의계약은 유찰률에서 제외"
    )
