import os

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from scripts.dashboard.data_loader import DATA_DIR, TABLE_FILES, load_notice, load_result

# ============================================================
# 1. 공통 정제 함수
# ============================================================

def clean_str(series):
    """문자열 정리: 공백 제거, 빈 값은 NA."""
    s = series.astype("string").str.strip()
    return s.mask(s.isin(["", "nan", "None", "NaN", "<NA>"]))


def to_datetime(series, with_time=False):
    """YYYYMMDD / YYYYMMDDHHMM(문자·숫자 모두)을 datetime으로 변환."""
    digits = series.astype("string").str.replace(r"\.0$", "", regex=True)
    digits = digits.str.replace(r"\D", "", regex=True)
    date_only = pd.to_datetime(digits.str[:8], format="%Y%m%d", errors="coerce")
    if not with_time:
        return date_only
    full = pd.to_datetime(digits.str[:12], format="%Y%m%d%H%M", errors="coerce")
    return full.fillna(date_only)


# ============================================================
# 2. 후속공고 추적 테이블 — scripts/analysis/05_rebid_followup_analysis.py 기준
#    - 유찰 공고: 입찰결과의 모든 행이 '유찰'인 공고키 (유찰·비유찰 혼재는 판단보류)
#    - 분석 대상: 원공고 공고일 2021-01-01 ~ 2025-12-31
#    - 후속공고 후보: 동일 사업키, 취소공고 제외, 공고일 ≤ 관측 종료일
#    - 후속공고: 후보 중 개찰시각이 원공고보다 엄격히 늦고,
#               공고일 ≥ 원공고 개찰일(날짜)인 공고 중 가장 빠른 1건
#               (정렬: 개찰시각 → 공고일 → 공고차수 → 공고키)
#    - 재공고 판정: 후속공고의 공고구분(pblancSe) == '재공고'
#    - 결과확정: 후속공고 결과가 유찰·낙찰·순위확정이고 개찰일 ≤ 관측 종료일
#    - 추가 소요일: 후속공고 개찰일 − 원공고 개찰일 (개찰일 ≤ 관측 종료일만)
# ============================================================

DELAY_NOTICE_COLS = ["businessKey", "noticeKey", "pblancSe", "pblancDate", "opengDt", "pblancOdr"]
DELAY_RESULT_COLS = ["noticeKey", "bidResult"]

ANALYSIS_START = pd.Timestamp("2021-01-01")  # 분석 시작일 (원공고 공고일 기준)
ANALYSIS_END = pd.Timestamp("2025-12-31")    # 분석 종료일 (원공고 공고일 기준)
OBS_END = pd.Timestamp("2025-12-31")         # 관측 종료일
CONFIRMED_RESULTS = {"유찰", "낙찰", "순위확정"}
Q4_START = pd.Timestamp("2025-10-01")        # 마지막 분기 시작일

STATUS_CONFIRMED = "후속공고·결과 확인"
STATUS_NO_RESULT = "후속공고 있음·결과 미확인"
STATUS_NO_FOLLOWUP = "후속공고 자체 미확인"
FOLLOWUP_STATUS_ORDER = [STATUS_CONFIRMED, STATUS_NO_RESULT, STATUS_NO_FOLLOWUP]

# 재공고 후 결과
RESULT_WON = "낙찰·순위확정"
RESULT_FAILED_AGAIN = "다시 유찰"
RESULT_PARTIAL = "일부 유찰(판단보류)"
RESULT_UNKNOWN = "결과 미확인"
NEXT_RESULT_ORDER = [RESULT_WON, RESULT_FAILED_AGAIN, RESULT_PARTIAL, RESULT_UNKNOWN]


def table_mtime(name):
    """원본 Parquet 수정 시간 (파일이 바뀌면 후속공고 계산 캐시 갱신)."""
    path = os.path.join(DATA_DIR, TABLE_FILES[name])
    return os.path.getmtime(path) if os.path.isfile(path) else 0


def build_followup_table():
    """유찰 공고 1건 = 1행인 후속공고 추적 테이블 (파일이 바뀌면 자동 재계산)."""
    return _build_followup_table(table_mtime("bid_notice"), table_mtime("bid_result"))


@st.cache_data(show_spinner="유찰 공고의 후속공고를 연결하는 중입니다...")
def _build_followup_table(notice_mtime, result_mtime):
    # 1) 입찰공고 (공고키 단위 1건)
    notice = load_notice(DELAY_NOTICE_COLS).copy()
    for col in ["businessKey", "noticeKey", "pblancSe"]:
        notice[col] = clean_str(notice[col])
    notice["pblanc_date"] = to_datetime(notice["pblancDate"])
    notice["openg_dt"] = to_datetime(notice["opengDt"], with_time=True)
    notice["notice_round"] = pd.to_numeric(notice["pblancOdr"], errors="coerce")
    notice = notice.dropna(subset=["businessKey", "noticeKey"]).drop_duplicates("noticeKey")

    # 2) 입찰결과: 품목 복수 행 → 공고키 단위 집계
    result = load_result(DELAY_RESULT_COLS).copy()
    result["noticeKey"] = clean_str(result["noticeKey"])
    result["bidResult"] = clean_str(result["bidResult"])
    def classify(values):
        """한 공고의 여러 품목 결과를 하나의 공고 결과로 변환 (FINAL과 동일)."""
        v = set(values.dropna().tolist())
        if "유찰" in v and len(v) > 1:
            return "판단보류"
        if v == {"유찰"}:
            return "유찰"
        if v and v.issubset({"낙찰", "순위확정"}) and "낙찰" in v:
            return "낙찰"
        if v == {"순위확정"}:
            return "순위확정"
        return "판단보류" if v else "결과 미확인"

    result_status = (
        result.dropna(subset=["noticeKey"]).groupby("noticeKey")["bidResult"].agg(classify)
    )
    failed_keys = result_status[result_status.eq("유찰")].index

    # 3) 유찰 원공고
    origin = notice[notice["noticeKey"].isin(failed_keys)]
    origin = origin[
        origin["pblanc_date"].between(ANALYSIS_START, ANALYSIS_END)
        & origin["openg_dt"].notna()
    ]
    origin = origin[["businessKey", "noticeKey", "pblancSe", "pblanc_date", "openg_dt"]].rename(
        columns={"pblancSe": "origin_se", "pblanc_date": "origin_pblanc_date",
                 "openg_dt": "origin_openg"}
    )

    # 4) 후속공고 후보: 같은 사업키, 취소공고 제외, 공고일·개찰일시 존재, 공고일 <= 관측 종료일
    cand = notice.loc[
        notice["pblancSe"].ne("취소공고").fillna(True)
        & notice["pblanc_date"].notna()
        & notice["openg_dt"].notna()
        & notice["pblanc_date"].le(OBS_END),
        ["businessKey", "noticeKey", "pblancSe", "pblanc_date", "openg_dt", "notice_round"],
    ]
    cand = cand.rename(columns={"noticeKey": "next_key", "pblancSe": "next_se",
                                "pblanc_date": "next_pblanc_date", "openg_dt": "next_openg",
                                "notice_round": "next_round"})
    pairs = origin[["businessKey", "noticeKey", "origin_openg"]].merge(cand, on="businessKey")
    pairs = pairs[
        (pairs["next_key"] != pairs["noticeKey"])
        & (pairs["next_openg"] > pairs["origin_openg"])                         # 개찰시각 엄격히 늦음
        & (pairs["next_pblanc_date"] >= pairs["origin_openg"].dt.normalize())  # 유찰 이후 게시
    ]
    first = (
        pairs.sort_values(["noticeKey", "next_openg", "next_pblanc_date", "next_round", "next_key"],
                          na_position="last", kind="stable")
        .drop_duplicates("noticeKey")
        [["noticeKey", "next_key", "next_se", "next_pblanc_date", "next_openg"]]
    )

    # 5) 후속 상태 / 재공고 여부 / 추가 소요일
    df = origin.merge(first, on="noticeKey", how="left")
    has_followup = df["next_key"].notna()
    # 후속 개찰이 관측 종료일 이내인지 (넘으면 결과·소요일 제외)
    observable_opening = df["next_openg"].notna() & df["next_openg"].dt.normalize().le(OBS_END)
    next_has_result = (
        has_followup
        & observable_opening
        & df["next_key"].map(result_status).isin(CONFIRMED_RESULTS)
    )
    df["next_opening_after_cutoff"] = has_followup & ~observable_opening

    df["has_followup"] = has_followup
    df["next_has_result"] = next_has_result
    df["status"] = np.select(
        [~has_followup, ~next_has_result],
        [STATUS_NO_FOLLOWUP, STATUS_NO_RESULT],
        default=STATUS_CONFIRMED,
    )
    df["is_rebid"] = df["next_se"].eq("재공고").fillna(False).astype(bool)
    df["next_type"] = np.where(has_followup, df["next_se"].fillna("구분 미상"), "후속공고 없음")
    # 후속공고의 결과 (관측 종료일 이후 개찰이면 결과 미확인)
    # string 타입은 비교 결과에 <NA>가 섞여 np.select가 거부하므로 일반 문자열로 바꾸고 빈칸은 ""로 채움
    next_raw = (df["next_key"].map(result_status).where(observable_opening)
                .astype(object).fillna("").astype(str))
    df["next_result"] = np.select(
        [(~has_followup).to_numpy(dtype=bool),
         next_raw.isin(["낙찰", "순위확정"]).to_numpy(dtype=bool),
         next_raw.eq("유찰").to_numpy(dtype=bool),
         next_raw.eq("판단보류").to_numpy(dtype=bool)],
        ["후속공고 없음", RESULT_WON, RESULT_FAILED_AGAIN, RESULT_PARTIAL],
        default=RESULT_UNKNOWN,
    )
    df["delay_days"] = (
        df["next_openg"].dt.normalize() - df["origin_openg"].dt.normalize()
    ).dt.days
    df["delay_days"] = df["delay_days"].where(observable_opening & df["delay_days"].ge(0))
    df["is_zero_delay"] = df["delay_days"].eq(0)
    # 공식 지표 대상: 결과까지 확인된 후속 재공고
    df["is_delay_target"] = (
        df["status"].eq(STATUS_CONFIRMED) & df["is_rebid"] & df["delay_days"].notna()
    )

    # 6) 기간·관측 조건 플래그 (원공고 공고일 기준)
    df["notice_year"] = df["origin_pblanc_date"].dt.year.astype("Int64")
    df["is_2025q4"] = df["origin_pblanc_date"].between(Q4_START, OBS_END)
    df["observable_90"] = (
        df["origin_openg"].dt.normalize() + pd.Timedelta(days=90)
    ) <= OBS_END

    return df.sort_values(["businessKey", "origin_openg"]).reset_index(drop=True)


# ============================================================
# 3. 집계 함수
# ============================================================

def delay_stats(series):
    """추가 소요일 기술통계 (n, 평균, 중앙값, Q1, Q3, 최소, 최대)."""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return {k: (0 if k == "n" else np.nan)
                for k in ["n", "mean", "median", "q1", "q3", "min", "max"]}
    return {
        "n": int(len(s)),
        "mean": s.mean(),
        "median": s.median(),
        "q1": s.quantile(0.25),
        "q3": s.quantile(0.75),
        "min": s.min(),
        "max": s.max(),
    }


def followup_summary(df):
    """화면 상단 지표·요약표용 집계. 소요일 통계는 0일 사례를 제외합니다."""
    target = df[df["is_delay_target"]]
    return {
        "failed": int(len(df)),
        "followup_found": int(df["has_followup"].sum()),
        "rebid_found": int((df["has_followup"] & df["is_rebid"]).sum()),
        "confirmed": int(df["status"].eq(STATUS_CONFIRMED).sum()),
        "no_result": int(df["status"].eq(STATUS_NO_RESULT).sum()),
        "no_followup": int(df["status"].eq(STATUS_NO_FOLLOWUP).sum()),
        "delay_target": int(len(target)),
        "zero_delay": int(target["is_zero_delay"].sum()),
        "delay": delay_stats(target.loc[~target["is_zero_delay"], "delay_days"]),
    }


def sensitivity_table(df):
    """관측 조건별 결과 비교 (전체 / 2025년 4분기 제외 / 90일 완전 관측 가능)."""
    groups = {
        "전체": df,
        "2025년 4분기 제외": df[~df["is_2025q4"]],
        "90일 완전 관측 가능": df[df["observable_90"]],
    }
    rows = []
    for name, g in groups.items():
        s = followup_summary(g)
        rate = s["followup_found"] / s["failed"] * 100 if s["failed"] else np.nan
        rows.append({
            "구분": name,
            "추적 대상": s["failed"],
            "후속공고 확인": s["followup_found"],
            "확인 비율(%)": rate,
            "결과확인 후속 재공고": s["delay_target"],
            "평균(일)": s["delay"]["mean"],
            "중앙값(일)": s["delay"]["median"],
        })
    return pd.DataFrame(rows)


def within_90_table(df):
    """90일 관측 가능 집단의 전체기간 연결률 vs 실제 90일 이내 연결률."""
    obs = df[df["observable_90"]]
    total = len(obs)
    full = int(obs["has_followup"].sum())
    within = int((obs["has_followup"] & obs["delay_days"].le(90)).sum())
    rate = lambda n: n / total * 100 if total else np.nan
    return pd.DataFrame([
        {"지표": "90일 관측 가능 집단의 전체기간 연결률", "분모": total,
         "후속공고 확인": full, "연결률(%)": rate(full)},
        {"지표": "실제 90일 이내 후속공고 연결률", "분모": total,
         "후속공고 확인": within, "연결률(%)": rate(within)},
    ])


# ============================================================
# 4. 화면
# ============================================================

STATUS_FILTERS = {
    "전체": None,
    "후속공고 확인": lambda d: d["has_followup"],
    "후속 재공고 결과확정": lambda d: d["is_delay_target"],
    "후속입찰 미확인": lambda d: ~d["has_followup"],
}

DETAIL_COLUMNS = {
    "noticeKey": "유찰 공고키",
    "businessKey": "사업키",
    "origin_pblanc_date": "원공고 공고일",
    "origin_openg": "원공고 개찰일",
    "status": "후속 상태",
    "next_key": "후속 공고키",
    "next_type": "후속공고 유형",
    "next_result": "후속 결과",
    "next_openg": "후속 개찰일",
    "delay_days": "추가 소요일",
}

# ---- 차트 공통 스타일 (색·글자) ----
INK = "#1F2937"      # 진한 글자
MUTED = "#6B7280"    # 보조 글자·축 숫자
GRID = "#E5E7EB"     # 연한 격자선
TRACK = "#F1F3F5"    # 막대 뒤 배경 트랙
STATUS_COLORS = {    # 후속 상태별 색 (확인=초록, 결과 미확인=주황, 자체 미확인=회색)
    STATUS_CONFIRMED: "#2F9E77",
    STATUS_NO_RESULT: "#F2A541",
    STATUS_NO_FOLLOWUP: "#9AA5B4",
}
NEXT_RESULT_COLORS = {  # 재공고 후 결과 (낙찰=초록, 다시 유찰=빨강, 일부 유찰=주황, 미확인=회색)
    RESULT_WON: "#2F9E77",
    RESULT_FAILED_AGAIN: "#E8590C",
    RESULT_PARTIAL: "#F2A541",
    RESULT_UNKNOWN: "#9AA5B4",
}


def _days(v, digits=2):
    return "-" if pd.isna(v) else f"{v:.{digits}f}일"


def _rate(num, den):
    return num / den * 100 if den else 0.0


def _count_label(n):
    """막대 끝 라벨: '3,400건'."""
    return f"{int(n):,}건"


def _nice_step(raw):
    """눈금 간격을 1·2·5·10 단위의 깔끔한 숫자로 맞춤 (예: 8.7 → 10, 340 → 500)."""
    if raw <= 0:
        return 1
    mag = 10 ** np.floor(np.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * mag:
            return max(m * mag, 1)
    return 10 * mag


def _bar(data, cat, val, order=None, color="#4C78A8", colors=None):
    """가로 막대 + 끝 숫자 라벨. 회색 배경 트랙 위에 둥근 막대를 그림.
    colors={항목: 색}을 주면 항목별로 다른 색을 씀."""
    data = data.copy()
    data["라벨"] = [_count_label(n) for n in data[val]]
    max_v = float(data[val].max()) if len(data) else 0.0
    step = _nice_step(max(max_v, 1) * 1.3 / 4)
    domain_max = float(np.ceil(max(max_v, 1) * 1.3 / step) * step)  # 눈금이 딱 떨어지게
    data["_track"] = domain_max

    y = alt.Y(f"{cat}:N", sort=order or alt.EncodingSortField(val, order="descending"), title=None,
              axis=alt.Axis(labelFontSize=15, labelFontWeight=600, labelColor=INK, labelLimit=400,
                            labelPadding=12, domain=False, ticks=False))
    x_scale = alt.Scale(domain=[0, domain_max], nice=False)
    tooltip = [alt.Tooltip(f"{cat}:N", title="구분"), alt.Tooltip(f"{val}:Q", title="건수", format=",")]

    track = alt.Chart(data).mark_bar(color=TRACK, size=26, cornerRadius=6).encode(
        x=alt.X("_track:Q", scale=x_scale, title=None,
                axis=alt.Axis(format=",d", values=list(np.arange(0, domain_max + step / 2, step)),
                              labelColor=MUTED, labelFontSize=12, grid=False,
                              domain=False, ticks=False, labelPadding=6)),
        y=y,
    )
    if colors:
        bar_color = alt.Color(f"{cat}:N", legend=None,
                              scale=alt.Scale(domain=list(colors), range=list(colors.values())))
    else:
        bar_color = alt.value(color)
    bars = alt.Chart(data).mark_bar(size=26, cornerRadius=6).encode(
        x=alt.X(f"{val}:Q", scale=x_scale), y=y, color=bar_color, tooltip=tooltip)
    labels = alt.Chart(data).mark_text(align="left", dx=8, fontSize=15, fontWeight="bold",
                                       color=INK).encode(
        x=alt.X(f"{val}:Q", scale=x_scale), y=y, text="라벨:N")
    return (alt.layer(track, bars, labels)
            .properties(height=58 * max(len(data), 2))
            .configure_view(strokeWidth=0))


def render():
    st.title("유찰 후 지연 영향")
    st.caption("유찰 후 후속공고·후속결과 확인 여부와 추가 소요일을 살펴봅니다.")

    try:
        df = build_followup_table()
    except FileNotFoundError as e:
        st.error(str(e))
        return

    # ---------------- 필터 ----------------
    years = sorted(int(y) for y in df["notice_year"].dropna().unique())
    with st.container(border=True):
        c1, c2, c3 = st.columns([1, 2, 1])
        with c1:
            if len(years) > 1:
                year_from, year_to = st.slider(
                    "공고 연도", min_value=years[0], max_value=years[-1],
                    value=(years[0], years[-1]), step=1,
                )
            else:
                year_from = year_to = years[0] if years else 0
                st.caption(f"공고 연도: {year_from}")
        with c2:
            status = st.radio("후속 데이터 상태", list(STATUS_FILTERS), horizontal=True)
        with c3:
            exclude_q4 = st.checkbox(
                "2025년 4분기 공고 제외",
                help="관측 종료일(2025-12-31)에 가까워 후속공고가 아직 나오지 않았을 수 있는 공고를 제외합니다.",
            )

    year_df = df[df["notice_year"].between(year_from, year_to)]
    base = year_df[~year_df["is_2025q4"]] if exclude_q4 else year_df
    cond = STATUS_FILTERS[status]
    view = base[cond(base)] if cond else base
    s = followup_summary(base)

    # ---------------- 핵심 지표 ----------------
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("유찰 공고 수", f"{s['failed']:,}건")
    c2.metric(
        "후속공고 확인", f"{s['followup_found']:,}건",
        delta=f"확인율 {_rate(s['followup_found'], s['failed']):.2f}%", delta_color="off",
    )
    c3.metric(
        "결과확인 후속 재공고", f"{s['delay_target']:,}건",
        delta=f"후속 재공고 {s['rebid_found']:,}건 중", delta_color="off",
    )
    c4.metric(
        "평균 추가 소요일", _days(s["delay"]["mean"]),
        delta=f"중앙값 {_days(s['delay']['median'], 0)}", delta_color="off",
    )
    if status != "전체":
        st.caption("※ 상단 지표와 상태 분포는 연도·분기 필터 기준, 아래 유형·분포·상세는 후속 데이터 상태 필터까지 반영됩니다.")

    tab1, tab2, tab3 = st.tabs(["후속공고 현황", "추가지연 분포", "관측 조건 민감도"])

    # ---------------- 탭1: 후속공고 현황 ----------------
    with tab1:
        left, right = st.columns([3, 2])
        with left:
            st.subheader("후속 상태 분포")
            status_counts = (
                base["status"].value_counts()
                .reindex(FOLLOWUP_STATUS_ORDER, fill_value=0)
                .rename_axis("상태").reset_index(name="건수")
            )
            st.altair_chart(
                _bar(status_counts, "상태", "건수", FOLLOWUP_STATUS_ORDER, colors=STATUS_COLORS),
                use_container_width=True,
            )
        with right:
            st.subheader("요약")
            rebid_share = _rate(s["rebid_found"], s["followup_found"])
            # 천 단위 콤마 표기 (Streamlit 버전과 무관하게 동일하게 보이도록 문자열로 변환)
            summary = pd.DataFrame([
                ("전체 유찰공고", f"{s['failed']:,}건"),
                ("후속공고·확정 결과 확인", f"{s['confirmed']:,}건"),
                ("후속 확정 결과 미확인 합계", f"{s['no_result'] + s['no_followup']:,}건"),
                ("ㄴ 후속공고 있음·결과 미확인", f"{s['no_result']:,}건"),
                ("ㄴ 후속공고 자체 미확인", f"{s['no_followup']:,}건"),
                ("후속공고 중 재공고", f"{s['rebid_found']:,}건 ({rebid_share:.1f}%)"),
                ("결과까지 확인된 후속 재공고", f"{s['delay_target']:,}건"),
            ], columns=["구분", "건수"])
            st.dataframe(
                summary.style.set_properties(subset=["건수"], **{"text-align": "right"}),
                hide_index=True, use_container_width=True,
            )

        # 재공고 후 결과: 후속 재공고가 결국 낙찰됐는지, 또 유찰됐는지 (테두리로 별도 영역)
        with st.container(border=True):
            st.subheader("재공고 후 결과")
            rebids = view[view["has_followup"] & view["is_rebid"]]
            if rebids.empty:
                st.info("선택한 조건에 후속 재공고가 없습니다.")
            else:
                res = (rebids["next_result"].value_counts()
                       .reindex(NEXT_RESULT_ORDER, fill_value=0)
                       .rename_axis("결과").reset_index(name="건수"))
                st.altair_chart(
                    _bar(res, "결과", "건수", NEXT_RESULT_ORDER, colors=NEXT_RESULT_COLORS),
                    use_container_width=True,
                )
                total_rb = len(rebids)
                won = int(res.loc[res["결과"].eq(RESULT_WON), "건수"].sum())
                again = int(res.loc[res["결과"].eq(RESULT_FAILED_AGAIN), "건수"].sum())
                st.caption(
                    f"후속 재공고 {total_rb:,}건 중 낙찰·순위확정 {won:,}건({_rate(won, total_rb):.1f}%), "
                    f"다시 유찰 {again:,}건({_rate(again, total_rb):.1f}%) · "
                    "결과 미확인 = 개찰일이 관측 종료일(2025-12-31) 이후이거나 결과 데이터 없음"
                )
            # 재공고가 아닌 후속공고는 그래프에서 빠지므로 아래에 종류와 건수만 언급
            # '후속 데이터 상태' 필터와 상관없이 항상 보이도록 연도·분기 필터만 반영한 base 기준
            others = base.loc[base["has_followup"] & ~base["is_rebid"], "next_type"].value_counts()
            if not others.empty:
                st.caption(
                    "※ 재공고 외 후속공고도 " + " · ".join(f"{k} {v:,}건" for k, v in others.items())
                    + f" (총 {int(others.sum()):,}건) 있으며, 이 그래프에서는 제외했습니다."
                )

        st.subheader("연도별 추이")
        trend_src = base  # 연도 범위·4분기 제외 필터 반영
        g = trend_src.groupby("notice_year")
        target = trend_src[trend_src["is_delay_target"] & ~trend_src["is_zero_delay"]]
        trend = pd.DataFrame({
            "유찰 공고": g.size(),
            "후속공고 확인": g["has_followup"].sum(),
            "결과확인 후속 재공고": g["is_delay_target"].sum(),
            "평균 추가 소요일": target.groupby("notice_year")["delay_days"].mean(),
        })
        trend["후속공고 확인율(%)"] = trend["후속공고 확인"] / trend["유찰 공고"] * 100
        trend = trend.reset_index().rename(columns={"notice_year": "공고 연도"})
        st.dataframe(
            trend, hide_index=True, use_container_width=True,
            column_config={
                "공고 연도": st.column_config.NumberColumn(format="%d"),
                "평균 추가 소요일": st.column_config.NumberColumn(format="%.2f일"),
                "후속공고 확인율(%)": st.column_config.NumberColumn(format="%.2f%%"),
            },
        )

        with st.expander(f"상세 목록 ({len(view):,}건)"):
            st.dataframe(view[list(DETAIL_COLUMNS)].rename(columns=DETAIL_COLUMNS),
                         hide_index=True, use_container_width=True)

    # ---------------- 탭2: 추가지연 분포 ----------------
    with tab2:
        basis = st.radio(
            "소요일 산정 대상", ["결과확인 후속 재공고 (공식)", "후속공고 전체"],
            horizontal=True, key="delay_basis",
        )
        d = view[view["is_delay_target"]] if basis.startswith("결과") else view[view["has_followup"]]
        zero_cnt = int(d["is_zero_delay"].sum())
        d = d[~d["is_zero_delay"] & d["delay_days"].notna()]

        if d.empty:
            st.info("선택한 조건에 추가 소요일을 계산할 수 있는 사례가 없습니다.")
        else:
            st_ = delay_stats(d["delay_days"])
            stats_df = pd.DataFrame([{
                "계산 가능 건수": st_["n"], "0일 제외 건수": zero_cnt,
                "평균(일)": st_["mean"], "중앙값(일)": st_["median"],
                "Q1(일)": st_["q1"], "Q3(일)": st_["q3"],
                "최소(일)": st_["min"], "최대(일)": st_["max"],
            }])
            st.dataframe(stats_df, hide_index=True, use_container_width=True,
                         column_config={c: st.column_config.NumberColumn(format="%.2f")
                                        for c in stats_df.columns if "(일)" in c})

            max_day = int(d["delay_days"].max())
            limit = st.slider("표시 상한(일)", 10, max(max_day, 10), min(max_day, 100), step=5)
            shown = d[d["delay_days"] <= limit]
            hist = alt.Chart(shown).mark_bar(color="#4C78A8").encode(
                x=alt.X("delay_days:Q", bin=alt.Bin(step=5), title="추가 소요일(일)",
                        axis=alt.Axis(format=",d")),
                y=alt.Y("count()", title="건수", axis=alt.Axis(format=",d")),
                tooltip=[alt.Tooltip("count()", title="건수")],
            )
            median_rule = alt.Chart(pd.DataFrame({"v": [st_["median"]]})).mark_rule(
                color="#E45756", strokeDash=[4, 4]).encode(x="v:Q")
            st.altair_chart((hist + median_rule).properties(height=320), use_container_width=True)
            st.caption(
                f"빨간 점선 = 중앙값 {st_['median']:.0f}일 · "
                f"상한 초과 {len(d) - len(shown):,}건은 그래프에서 생략 · "
                f"평균이 중앙값보다 크면 장기 소요 사례의 영향이 있습니다."
            )

    # ---------------- 탭3: 관측 조건 민감도 ----------------
    with tab3:
        st.caption("연도 필터만 반영합니다 (분기 제외 체크와 무관하게 조건별로 비교).")
        sens = sensitivity_table(year_df)
        st.dataframe(sens, hide_index=True, use_container_width=True, column_config={
            "확인 비율(%)": st.column_config.NumberColumn(format="%.2f%%"),
            "평균(일)": st.column_config.NumberColumn(format="%.2f"),
            "중앙값(일)": st.column_config.NumberColumn(format="%.0f"),
        })

        m = year_df["is_delay_target"] & ~year_df["is_zero_delay"]
        box_src = pd.concat([
            year_df.loc[m, ["delay_days"]].assign(구분="전체"),
            year_df.loc[m & ~year_df["is_2025q4"], ["delay_days"]].assign(구분="2025년 4분기 제외"),
        ])
        if not box_src.empty:
            box = alt.Chart(box_src).mark_boxplot(extent=1.5, size=60).encode(
                x=alt.X("구분:N", sort=["전체", "2025년 4분기 제외"], title=None),
                y=alt.Y("delay_days:Q", title="추가 소요일(일)", axis=alt.Axis(format=",d")),
            ).properties(height=320)
            st.altair_chart(box, use_container_width=True)

        st.subheader("90일 이내 연결률")
        st.dataframe(within_90_table(year_df), hide_index=True, use_container_width=True,
                     column_config={"연결률(%)": st.column_config.NumberColumn(format="%.2f%%")})
        st.caption("조건 간 차이가 작더라도, 후속공고 미확인 건의 재공고 미발생을 확정할 수는 없습니다.")

    with st.expander("추가 소요일 산정 기준"):
        st.write("- 기준: 동일 사업키(demandYear+orntCode+dcsNo) 내 개찰일 순서")
        st.write("- 후속공고: 유찰 공고 다음에 오는 첫 유효 공고(취소 공고 제외)")
        st.write("- 재공고 판정: 후속공고의 공고구분(pblancSe)에 '재공고' 포함 여부")
        st.write("- 추가 소요일: 후속 재공고의 개찰일 − 직전 유찰 공고의 개찰일")
        st.write("- 개찰일 차이 0일 사례는 평균·분포에서 제외하고 건수만 별도 표시")
        st.write("- 후속입찰 미확인 사례는 '재공고 없음'으로 단정하지 않고 별도 상태로 보존")
