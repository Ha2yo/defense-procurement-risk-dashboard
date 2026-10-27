"""2025년 공고 선택 / 직접 입력: 금액 5분위 4변수 위험도 및 3변수 비교.

공고 단위·공고일 기준 2021~2024년 결과확정 경쟁성 공고만 근거로 사용.
2025년 결과는 산정에 사용하지 않고 선택 공고의 사후 결과 표시용으로만 사용.
"""
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from scripts.dashboard import components as ui
from scripts.dashboard.data_loader import (
    load_notice,
    load_result,
    load_org_group,
    load_procure_plan,
    load_item,
)

BASE_KEYS = ["기관그룹", "busiDivs", "cntrctMth"]
STEPS = [BASE_KEYS, ["기관그룹", "busiDivs"], ["기관그룹"], []]
AMOUNT_KEYS = BASE_KEYS + ["사업규모구간"]
AMOUNT_STEPS = [AMOUNT_KEYS] + STEPS
MIN_SAMPLE = 10
TRAIN_START, TRAIN_END, TARGET_YEAR = 2021, 2024, 2025

# 화면 표시용 결과 상태 이름과 설명. 내부 판정값(결과상태)은 바꾸지 않는다.
RESULT_ORDER = ["낙찰", "유찰", "순위확정", "판단보류", "동일사업_다른공고결과",
                "결과미확인_후속있음", "결과미확인_후속없음", "결과미확인"]
RESULT_LABELS = {"동일사업_다른공고결과": "동일사업 다른 공고 결과 존재",
                 "결과미확인_후속있음": "결과미확인 · 후속공고 있음",
                 "결과미확인_후속없음": "결과미확인 · 후속공고 없음"}
RESULT_HELP = {
    "낙찰": "낙찰자가 정해진 공고 (낙찰·순위확정이 섞인 공고 포함)",
    "유찰": "입찰이 성립하지 않은 공고",
    "순위확정": "낙찰 예정자 순위만 정해진 공고. 분석에서는 비유찰로 본다",
    "판단보류": "한 공고 안에 유찰과 비유찰 품목 결과가 섞인 공고",
    "동일사업_다른공고결과": "이 공고의 결과는 없지만 같은 사업의 다른 공고(재공고 등)에는 결과가 있다",
    "결과미확인": "수집 시점까지 입찰결과가 확인되지 않은 공고",
    "결과미확인_후속있음": "입찰결과는 없지만 같은 사업에서 이후 유효 공고(재공고 등)가 확인된 공고. "
                       "유찰·취소 등으로 다시 공고했을 가능성이 있다 (후속공고 기준: 데이터 항목정의서 4.3)",
    "결과미확인_후속없음": "입찰결과도, 같은 사업의 이후 유효 공고도 확인되지 않은 공고",
}
# 표·체크박스용 짧은 이름과 결과 열 글자색
RESULT_SHORT = {"동일사업_다른공고결과": "다른 공고 결과",
                "결과미확인_후속있음": "미확인(후속 있음)",
                "결과미확인_후속없음": "미확인(후속 없음)"}
RESULT_COLORS = {"유찰": "#C0392B", "낙찰": "#1E8449", "순위확정": "#1E8449"}
FOLLOWUP_OBSERVATION_END = pd.Timestamp("2025-12-31")  # 5번 분석과 같은 관측 종료일


def result_status(values):
    """공고키 내 복수 입찰결과 판정. 원본 03_kpi의 판정 규칙."""
    vals = set(values.dropna().astype("string").str.strip().replace("", pd.NA).dropna().tolist())
    if "유찰" in vals and len(vals) > 1:
        return "판단보류"
    if vals == {"유찰"}:
        return "유찰"
    if vals and vals.issubset({"낙찰", "순위확정"}) and "낙찰" in vals:
        return "낙찰"
    if vals == {"순위확정"}:
        return "순위확정"
    return "판단보류" if vals else "결과미확인"


def prepare_tables(notice, result, mapping):
    """입찰공고 전체를 보존하고 결과 상태 및 공식 평가가능 여부를 생성."""
    n = notice.copy()
    for col in ("noticeKey", "businessKey", "orntCode"):
        n[col] = n[col].astype("string").str.strip()
    if n["noticeKey"].isna().any() or n["noticeKey"].duplicated().any():
        raise ValueError("입찰공고 noticeKey가 누락되거나 중복됩니다.")
    m = mapping[["orntCode", "기관그룹"]].copy()
    m["orntCode"] = m["orntCode"].astype("string").str.strip()
    if m["orntCode"].duplicated().any():
        raise ValueError("기관그룹 기준표 orntCode 중복")
    n = n.merge(m, on="orntCode", how="left", validate="many_to_one")
    n["기관그룹"] = n["기관그룹"].fillna("미분류")
    r = result[["noticeKey", "bidResult"]].copy()
    r["noticeKey"] = r["noticeKey"].astype("string").str.strip()
    by_key = r.groupby("noticeKey", dropna=False)["bidResult"].apply(result_status).rename("결과상태").reset_index()
    n = n.merge(by_key, on="noticeKey", how="left", validate="one_to_one")
    # 원본의 상호배타적인 상태 판정: 직접매칭 -> 취소 -> 동일사업 다른 공고 결과 -> 미확인
    known_business = set(n.loc[n["결과상태"].notna(), "businessKey"].dropna())
    missing = n["결과상태"].isna()
    n.loc[missing & n["pblancSe"].eq("취소공고"), "결과상태"] = "취소공고"
    missing = n["결과상태"].isna()
    n.loc[missing & n["businessKey"].isin(known_business), "결과상태"] = "동일사업_다른공고결과"
    n["결과상태"] = n["결과상태"].fillna("결과미확인")
    n["공고일"] = pd.to_datetime(n["pblancDate"].astype("string").str.replace(r"\D", "", regex=True).str[:8],
                                 format="%Y%m%d", errors="coerce")
    n["연도"] = n["공고일"].dt.year
    n["유찰여부"] = n["결과상태"].map({"유찰": 1, "낙찰": 0, "순위확정": 0})
    n["평가가능"] = n["공고일"].notna() & ~n["cntrctMth"].astype("string").str.contains("수의", na=False) & n["유찰여부"].notna()
    return n


def _positive_amount(series):
    value = pd.to_numeric(series.astype("string").str.replace(",", "", regex=False), errors="coerce")
    return value.where(value.gt(0))


# 금액 검증 스크립트와 동일한 규칙을 사용한다.
AMOUNT_DISAGREEMENT_RATE = 0.10
AMOUNT_QUINTILE_LABELS = ["Q1", "Q2", "Q3", "Q4", "Q5"]


def amount_bin(value, boundaries):
    """2021~2024년 학습 공고에서 구한 경계를 2025년 조회에 고정 적용."""
    if pd.isna(value) or float(value) <= 0:
        return pd.NA

    for index, boundary in enumerate(boundaries):
        if float(value) <= boundary:
            return AMOUNT_QUINTILE_LABELS[index]
    return AMOUNT_QUINTILE_LABELS[-1]


def calculate_boundaries(train):
    """결과확정 경쟁성 학습 공고의 유효 금액으로 5분위 경계를 정한다."""
    valid_amounts = train["사업규모금액"].dropna()
    if valid_amounts.empty:
        raise ValueError("학습 공고에 유효한 사업규모금액이 없습니다.")

    boundaries = valid_amounts.quantile([0.2, 0.4, 0.6, 0.8]).to_numpy()
    if len(np.unique(boundaries)) != 4:
        raise ValueError("금액 5분위 경계가 중복됩니다.")
    return boundaries


def summarize_result_methods(results):
    """동일 공고키에서 입찰방법이 하나로 확정되는 경우만 복원한다."""
    grouped = (
        results[["noticeKey", "bidMth"]]
        .assign(
            noticeKey=lambda frame: frame["noticeKey"].astype("string").str.strip(),
            bidMth=lambda frame: frame["bidMth"].astype("string").str.strip(),
        )
        .groupby("noticeKey")["bidMth"]
        .agg(lambda values: sorted(set(values.dropna()) - {""}))
    )
    return grouped.map(
        lambda methods: methods[0]
        if len(methods) == 1 and methods[0] in ("총액제", "단가제")
        else pd.NA
    )


def prepare_amount_table(notices, plans, items, results):
    """공고별 사업규모금액을 최신 검증 규칙에 따라 계산한다.

    조달계획의 입찰방법을 우선 사용하며, 과거 결과확정 공고에 한해
    입찰결과의 bidMth로 빠진 41건을 사후 복원한다.
    2025년의 결과 정보는 입찰방법 복원에 사용하지 않는다.
    """
    plan = plans[["businessKey", "budgetAmount", "bidMth"]].copy()
    plan["businessKey"] = plan["businessKey"].astype("string").str.strip()
    plan = plan.dropna(subset=["businessKey"])
    if plan["businessKey"].duplicated().any():
        raise ValueError("조달계획 businessKey가 중복됩니다.")

    plan["예산금액"] = _positive_amount(plan["budgetAmount"])
    plan["계획입찰방법"] = plan["bidMth"].astype("string").str.strip()

    item = items[["businessKey", "totlAmnt"]].copy()
    item["businessKey"] = item["businessKey"].astype("string").str.strip()
    item = item.dropna(subset=["businessKey"])
    item["품목금액"] = pd.to_numeric(
        item["totlAmnt"].astype("string").str.replace(",", "", regex=False),
        errors="coerce",
    )
    item["유효품목"] = item["품목금액"].notna() & item["품목금액"].ge(0)

    grouped = (
        item.groupby("businessKey", sort=False)
        .agg(
            품목수=("품목금액", "size"),
            유효품목수=("유효품목", "sum"),
            품목합계=("품목금액", "sum"),
        )
        .reset_index()
    )
    complete = (
        grouped["품목수"].eq(grouped["유효품목수"])
        & grouped["품목합계"].gt(0)
    )
    grouped["품목합계"] = grouped["품목합계"].where(complete)

    combined = (
        notices[["noticeKey", "businessKey", "연도", "평가가능"]]
        .merge(
            plan[["businessKey", "예산금액", "계획입찰방법"]],
            on="businessKey", how="left", validate="many_to_one",
        )
        .merge(
            grouped[["businessKey", "품목합계"]],
            on="businessKey", how="left", validate="many_to_one",
        )
    )

    combined["입찰방법"] = combined["계획입찰방법"]
    combined["입찰방법출처"] = "조달계획"
    missing_method = ~combined["입찰방법"].isin(["총액제", "단가제"])

    # 사후 복원은 과거 학습 데이터의 결과확정 공고에서만 허용한다.
    historical = combined["연도"].between(TRAIN_START, TRAIN_END)
    recoverable = missing_method & historical & combined["평가가능"]
    result_methods = summarize_result_methods(results)
    recovered = combined["noticeKey"].map(result_methods)
    can_recover = recoverable & recovered.isin(["총액제", "단가제"])
    combined.loc[can_recover, "입찰방법"] = recovered.loc[can_recover]
    combined.loc[can_recover, "입찰방법출처"] = "입찰결과_사후복원"
    combined.loc[missing_method & ~can_recover, "입찰방법출처"] = "미확인"

    combined["사업규모금액"] = np.nan
    combined["금액출처"] = "미확인"
    is_total = combined["입찰방법"].eq("총액제").fillna(False)
    is_unit = combined["입찰방법"].eq("단가제").fillna(False)

    use_budget = is_total & combined["예산금액"].notna()
    combined.loc[use_budget, "사업규모금액"] = combined.loc[use_budget, "예산금액"]
    combined.loc[use_budget, "금액출처"] = "계획예산"

    use_fallback = is_total & combined["예산금액"].isna() & combined["품목합계"].notna()
    combined.loc[use_fallback, "사업규모금액"] = combined.loc[use_fallback, "품목합계"]
    combined.loc[use_fallback, "금액출처"] = "총액제_품목대체"

    use_item = is_unit & combined["품목합계"].notna()
    combined.loc[use_item, "사업규모금액"] = combined.loc[use_item, "품목합계"]
    combined.loc[use_item, "금액출처"] = "단가제_품목합계"

    # 차이율은 조달계획 예산을 분모로 한다.
    can_compare = is_total & combined["예산금액"].notna() & combined["품목합계"].notna()
    combined["금액차이율"] = np.nan
    combined.loc[can_compare, "금액차이율"] = (
        (combined.loc[can_compare, "예산금액"] - combined.loc[can_compare, "품목합계"]).abs()
        / combined.loc[can_compare, "예산금액"]
    )
    disagreement = can_compare & combined["금액차이율"].gt(AMOUNT_DISAGREEMENT_RATE)
    combined.loc[disagreement, "사업규모금액"] = np.nan
    combined.loc[disagreement, "금액출처"] = "총액제_불일치10pct초과"

    return combined[[
        "noticeKey", "사업규모금액", "금액출처", "입찰방법", "입찰방법출처", "금액차이율",
    ]]


@st.cache_data(show_spinner="과거 공고·입찰결과를 연결하는 중입니다...")
def load_prepared():
    notice_columns = [
        "noticeKey", "businessKey", "pblancDate", "opengDt", "pblancNo", "pblancSe",
        "bidNm", "ornt", "orntCode", "busiDivs", "cntrctMth",
    ]
    result_columns = ["noticeKey", "bidResult", "bidMth"]
    results = load_result(columns=result_columns)
    notices = prepare_tables(
        load_notice(columns=notice_columns),
        results,
        load_org_group(),
    )
    amounts = prepare_amount_table(
        notices,
        load_procure_plan(columns=["businessKey", "budgetAmount", "bidMth"]),
        load_item(columns=["businessKey", "totlAmnt"]),
        results,
    )
    return notices.merge(amounts, on="noticeKey", how="left", validate="one_to_one")



def build_reference(train, steps=STEPS):
    if train.empty:
        raise ValueError("2021~2024년 결과확정 경쟁성 공고가 없습니다.")
    tables = []
    for cols in steps:
        if cols:
            grouped = (train.dropna(subset=cols).groupby(cols, observed=True)["유찰여부"]
                       .agg(근거공고수="size", 근거유찰건수="sum").reset_index())
        else:
            grouped = pd.DataFrame([{"근거공고수": len(train), "근거유찰건수": int(train["유찰여부"].sum())}])
        tables.append(grouped)
    return tables


def get_risk(condition, reference, steps=STEPS):
    """문서 기준 3→2→1→전체, 10건 이상 첫 조합 적용."""
    for stage, (cols, lookup) in enumerate(zip(steps, reference), 1):
        if cols:
            if any(pd.isna(condition.get(col)) or str(condition.get(col)).strip() == "" for col in cols):
                continue
            mask = pd.Series(True, index=lookup.index)
            for col in cols:
                mask &= lookup[col].astype("string").eq(str(condition[col]))
            found = lookup.loc[mask]
        else:
            found = lookup
        if len(found) != 1:
            continue
        row = found.iloc[0]
        n = int(row["근거공고수"])
        if n >= MIN_SAMPLE:
            f = int(row["근거유찰건수"])
            return {"위험도": f / n, "근거공고수": n, "근거유찰건수": f,
                    "적용단계": stage, "적용변수": cols, "적용조건": " × ".join(cols) if cols else "전체"}
    raise ValueError("위험도 산정용 과거 공고가 10건 미만입니다.")



def compare_risk_by_level(condition, amount_reference):
    """4·3·2·1변수 조합의 과거 유찰률을 독립적으로 조회한다.

    각 단계의 표본이 10건 미만이면 대체 단계의 값으로 채우지 않는다.
    실제 대표 위험도 산정에 사용하는 계층적 대체와는 별개인 비교용 수치다.
    """
    labels = ["4변수", "3변수", "2변수", "1변수"]
    rows = []

    for label, columns, lookup in zip(
        labels,
        AMOUNT_STEPS[:4],
        amount_reference[:4],
    ):
        if any(
            pd.isna(condition.get(column))
            or str(condition.get(column)).strip() == ""
            for column in columns
        ):
            rows.append({"모델": label, "유찰률": np.nan, "근거공고수": 0})
            continue

        matched = pd.Series(True, index=lookup.index)
        for column in columns:
            matched &= lookup[column].astype("string").eq(str(condition[column]))

        found = lookup.loc[matched]
        if len(found) != 1 or int(found.iloc[0]["근거공고수"]) < MIN_SAMPLE:
            rows.append({"모델": label, "유찰률": np.nan, "근거공고수": 0})
            continue

        row = found.iloc[0]
        count = int(row["근거공고수"])
        failures = int(row["근거유찰건수"])
        rows.append({"모델": label, "유찰률": failures / count * 100, "근거공고수": count})

    return pd.DataFrame(rows)


def risk_comparison_chart(comparison):
    """가장 높은 유찰률의 막대만 은은하게 강조한다."""
    chart_data = comparison.dropna(subset=["유찰률"]).copy()
    if chart_data.empty:
        return None

    highest = chart_data["유찰률"].max()
    chart_data["강조"] = np.where(chart_data["유찰률"].eq(highest), "최고", "일반")
    chart_data["라벨"] = chart_data["유찰률"].map(lambda value: f"{value:.2f}%")

    order = ["4변수", "3변수", "2변수", "1변수"]
    fig = px.bar(
        chart_data, x="유찰률", y="모델", orientation="h", color="강조", text="라벨",
        color_discrete_map={"일반": ui.COLOR_BASE, "최고": ui.COLOR_HIGHLIGHT},
        custom_data=["근거공고수"],
    )
    fig.update_traces(textposition="outside",
                      hovertemplate="%{y}<br>과거 유찰률 %{x:.2f}%<br>근거 공고 수 %{customdata[0]:,}건"
                                    "<extra></extra>")
    # 가로 막대는 아래에서 위로 그려지므로 순서를 뒤집어 4변수가 맨 위에 오게 한다
    fig.update_yaxes(title=None, categoryorder="array", categoryarray=order[::-1])
    fig.update_xaxes(title="과거 유찰률(%)", range=[0, chart_data["유찰률"].max() + 5])
    return ui.style_figure(fig, ui.hbar_height(len(chart_data)))


def comparable_notices(train, condition, cols):
    mask = pd.Series(True, index=train.index)
    for col in cols:
        mask &= train[col].astype("string").eq(str(condition[col]))
    return train.loc[mask].copy()


def candidates(df, year):
    """선택 가능한 공고만 노출. 취소공고·수의계약·개찰일 역전 공고는 검색에서 제외한다.

    결과미확인·판단보류는 제외하지 않는다. 사전 위험도 조회가 가능해야 하기 때문이다.
    개찰일 역전(개찰일시 < 공고일)은 연도별 결과 미확인 분포표 기준에 따라 제외한다.
    원본 데이터 및 과거 위험도 학습 대상은 여기서 변경하지 않는다.
    """
    is_target_year = df["연도"].eq(year)
    is_private = df["cntrctMth"].astype("string").str.contains("수의", na=False, regex=False)
    is_cancelled = df["pblancSe"].astype("string").str.strip().eq("취소공고").fillna(False)
    open_date = pd.to_datetime(df["opengDt"].astype("string").str.replace(r"\D", "", regex=True).str[:8],
                               format="%Y%m%d", errors="coerce")
    is_reversed = (open_date < df["공고일"]).fillna(False)
    data = df.loc[is_target_year & ~is_private & ~is_cancelled & ~is_reversed].copy()
    return data.sort_values(["공고일", "noticeKey"], ascending=[False, True])


def parse_datetime(series):
    """YYYYMMDD / YYYYMMDDHHMM / YYYYMMDDHHMMSS를 변환한다 (5번 분석과 같은 방식)."""
    values = series.astype("string").str.replace(r"\D", "", regex=True)
    result = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    for digits, fmt in [(14, "%Y%m%d%H%M%S"), (12, "%Y%m%d%H%M"), (8, "%Y%m%d")]:
        mask = values.str.len().eq(digits).fillna(False)
        if mask.any():
            result.loc[mask] = pd.to_datetime(values.loc[mask], format=fmt, errors="coerce")
    return result


def split_unconfirmed(notices, all_notices):
    """결과미확인 공고를 후속공고 유무로 나눈다. 화면 표시용이며 분석·KPI 기준은 바꾸지 않는다.

    후속공고는 5번 분석(데이터 항목정의서 4.3)과 같은 기준이다:
    같은 사업키의 다른 공고 중 취소공고가 아니고, 개찰시각이 원공고보다 엄격히 늦고,
    원공고 개찰일 이후 게시되었으며, 게시일이 관측 종료일 이내인 공고.
    """
    out = notices.copy()
    targets = out.loc[out["결과상태"].eq("결과미확인"), ["noticeKey", "businessKey", "opengDt"]].copy()
    targets["opening_at"] = parse_datetime(targets["opengDt"])
    pool = all_notices[["noticeKey", "businessKey", "opengDt", "pblancSe", "공고일"]].copy()
    pool["opening_at"] = parse_datetime(pool["opengDt"])
    pool = pool.loc[pool["businessKey"].notna() & pool["opening_at"].notna() & pool["공고일"].notna()
                    & pool["공고일"].le(FOLLOWUP_OBSERVATION_END)
                    & ~pool["pblancSe"].astype("string").str.strip().eq("취소공고").fillna(False)]
    pairs = targets.dropna(subset=["businessKey", "opening_at"]).merge(
        pool[["businessKey", "noticeKey", "opening_at", "공고일"]],
        on="businessKey", suffixes=("", "_next"))
    pairs = pairs.loc[pairs["noticeKey_next"].ne(pairs["noticeKey"])
                      & pairs["opening_at_next"].gt(pairs["opening_at"])
                      & pairs["공고일"].ge(pairs["opening_at"].dt.normalize())]
    found = set(pairs["noticeKey"])
    unconfirmed = out["결과상태"].eq("결과미확인")
    out.loc[unconfirmed, "결과상태"] = np.where(out.loc[unconfirmed, "noticeKey"].isin(found),
                                            "결과미확인_후속있음", "결과미확인_후속없음")
    return out


def rank_notices(notices, amount_reference):
    """2025년 공고 전체를 위험도 순으로 줄 세워 10등급을 매긴다 (KPI 2와 같은 방식).

    정렬: 위험도 → 근거 표본 수 → 공고키 (KPI 정의서 규칙). 실제 결과는 사용하지 않는다.
    낮은 쪽부터 같은 건수씩 1~10등급, 9·10등급이 상위 20% 고위험군이다.
    같은 조건 조합은 위험도가 같으므로 조합별로 한 번만 계산한다.
    """
    combos = notices[AMOUNT_KEYS].drop_duplicates()
    rows = []
    for combo in combos.to_dict("records"):
        risk = get_risk(combo, amount_reference, AMOUNT_STEPS)
        rows.append({**combo, "위험도": risk["위험도"], "근거공고수": risk["근거공고수"]})
    risks = pd.DataFrame(rows, columns=AMOUNT_KEYS + ["위험도", "근거공고수"])
    ranked = notices[["noticeKey"] + AMOUNT_KEYS].merge(risks, on=AMOUNT_KEYS, how="left", validate="many_to_one")
    ranked = ranked.sort_values(["위험도", "근거공고수", "noticeKey"],
                                ascending=[True, True, False]).reset_index(drop=True)
    total = len(ranked)
    ranked["위험등급"] = ranked.index * 10 // total + 1
    ranked["상위비율"] = (total - ranked.index) / total
    return ranked[["noticeKey", "위험도", "근거공고수", "위험등급", "상위비율"]]


def grade_of(risk, ranked, notice_key=None):
    """선택 공고는 순위표에서 그대로 찾고, 직접 입력은 같은 순위표에 끼워 넣어 등급을 구한다."""
    total = len(ranked)
    if notice_key is not None and ranked["noticeKey"].eq(notice_key).any():
        row = ranked.loc[ranked["noticeKey"].eq(notice_key)].iloc[0]
        return int(row["위험등급"]), float(row["상위비율"])
    lower = ((ranked["위험도"] < risk["위험도"]) |
             (ranked["위험도"].eq(risk["위험도"]) & (ranked["근거공고수"] < risk["근거공고수"]))).sum()
    position = min(int(lower), total - 1)
    return position * 10 // total + 1, (total - position) / total


def grade_summary(ranked, notices):
    """등급별 공고 수·위험도 범위와, 결과확정 공고의 실제 유찰률(사후)."""
    merged = ranked.merge(notices[["noticeKey", "결과상태"]], on="noticeKey", how="left")
    merged["확정"] = merged["결과상태"].isin(["낙찰", "유찰", "순위확정"])
    merged["유찰"] = merged["결과상태"].eq("유찰")
    table = merged.groupby("위험등급").agg(
        공고수=("noticeKey", "size"), 위험도최소=("위험도", "min"), 위험도최대=("위험도", "max"),
        결과확정=("확정", "sum"), 유찰=("유찰", "sum")).reset_index()
    table["위험 등급"] = table["위험등급"].map(lambda g: f"{g}등급" + (" · 고위험" if g >= 9 else ""))
    table["위험도 범위"] = [f"{a * 100:.1f} ~ {b * 100:.1f}%" for a, b in zip(table["위험도최소"], table["위험도최대"])]
    table["실제 유찰률(사후)"] = [f"{u / n * 100:.1f}% ({u:,}/{n:,})" if n else "-"
                             for u, n in zip(table["유찰"], table["결과확정"])]
    table = table.sort_values("위험등급", ascending=False)
    return table[["위험 등급", "공고수", "위험도 범위", "실제 유찰률(사후)"]].rename(columns={"공고수": "공고 수"})


def notice_table(notices):
    """필터된 2025년 공고 목록. 위험도 높은 순."""
    view = notices.sort_values(["위험도", "noticeKey"], ascending=[False, True])
    return pd.DataFrame({
        "위험 등급": view["위험등급"].astype("Int64"),
        "위험도": (view["위험도"] * 100).round(2).astype(str) + "%",
        "공고일": view["공고일"].dt.strftime("%Y-%m-%d"),
        "공고번호": view["pblancNo"],
        "공고명": view["bidNm"],
        "발주기관": view["ornt"],
        "기관그룹": view["기관그룹"],
        "사업구분": view["busiDivs"],
        "계약방법": view["cntrctMth"],
        "금액 구간": view["사업규모구간"],
        "실제 결과": view["결과상태"].map(lambda s: RESULT_SHORT.get(s, s)),
    })


def style_results(table):
    """실제 결과 열 글자색: 유찰 빨강, 낙찰·순위확정 초록, 나머지(결과 없음 계열) 회색."""
    colors = {RESULT_SHORT.get(k, k): v for k, v in RESULT_COLORS.items()}
    styler = table.style
    apply_cells = getattr(styler, "map", None) or styler.applymap  # pandas 2.0은 applymap만 있음
    return apply_cells(lambda v: f"color: {colors.get(v, '#7F8C8D')}; font-weight: 600",
                       subset=["실제 결과"])


def choices(frame, key):
    return sorted(frame[key].dropna().astype(str).str.strip().loc[lambda s: s.ne("")].unique().tolist())


def render():
    st.title("공고별 유찰 위험도 및 유사사례")
    st.caption("2025년 공고의 금액 5분위 4변수 위험도(금액 미확인 시 3변수 대체)를 2021~2024년 과거 사례와 비교합니다.")
    try:
        data = load_prepared()
        train = data.loc[data["연도"].between(TRAIN_START, TRAIN_END) & data["평가가능"]].copy()
        reference = build_reference(train)
        # 5분위 기준은 2021~2024년 학습 공고에서만 계산한다.
        boundaries = calculate_boundaries(train)
        data["사업규모구간"] = data["사업규모금액"].map(
            lambda value: amount_bin(value, boundaries)
        )
        train = data.loc[
            data["연도"].between(TRAIN_START, TRAIN_END) & data["평가가능"]
        ].copy()
        amount_reference = build_reference(train, AMOUNT_STEPS)
        notice_2025 = split_unconfirmed(candidates(data, TARGET_YEAR), data)
        # 등급은 결과·등급 필터를 걸기 전 2025년 공고 전체 기준으로 매긴다
        ranked_2025 = rank_notices(notice_2025, amount_reference)
        notice_2025 = notice_2025.merge(ranked_2025[["noticeKey", "위험도", "위험등급"]],
                                        on="noticeKey", how="left", validate="one_to_one")
        notice_all = notice_2025  # 필터 전 2025년 공고 전체
    except (FileNotFoundError, KeyError, ValueError) as exc:
        st.error(f"분석 데이터 준비 실패: {exc}")
        return

    # 사전 검토 화면이므로 2025년 실제 결과는 기본 숨김. 켜면 결과 필터·결과 열·사후 유찰률을 함께 보여 준다
    show_actual = st.toggle("2025년 실제 결과 보기 (사후 확인용)", value=False, key="risk_show_actual",
                            help="사전 검토 화면이므로 기본은 숨김입니다. 검증할 때만 켜세요.")

    tab_notice, tab_manual = st.tabs(["2025년 공고 선택", "직접 입력"])
    with tab_notice:
        st.caption("취소공고·수의계약·개찰일 역전 공고 제외 · 결과 미확인 공고는 조회 가능")
        if not notice_2025.empty:
            if show_actual:
                # 실제 결과별 체크박스 필터. 기본값은 전부 선택
                counts = notice_2025["결과상태"].value_counts()
                statuses = [s for s in RESULT_ORDER if s in counts.index]
                statuses += [s for s in counts.index if s not in statuses]
                st.write("**2025년 실제 결과로 거르기**")
                filter_cols = st.columns(len(statuses))
                picked = [
                    status for col, status in zip(filter_cols, statuses)
                    if col.checkbox(f"{RESULT_SHORT.get(status, status)} ({counts[status]:,})", value=True,
                                    key=f"risk_result_filter_{status}", help=RESULT_HELP.get(status))
                ]
                notice_2025 = notice_2025.loc[notice_2025["결과상태"].isin(picked)]

            # 위험 등급 범위 필터. 등급 자체는 필터 전 2025년 공고 전체 기준
            low, high = st.select_slider(
                "위험 등급 범위 (1 = 가장 낮음 · 9·10등급 = 상위 20% 고위험)",
                options=list(range(1, 11)), value=(1, 10), key="risk_grade_range")
            notice_2025 = notice_2025.loc[notice_2025["위험등급"].between(low, high)]

            with st.expander("위험 등급별 2025년 공고 현황", expanded=False):
                summary_tbl = grade_summary(ranked_2025, notice_all)
                if not show_actual:
                    summary_tbl = summary_tbl.drop(columns="실제 유찰률(사후)")
                st.dataframe(summary_tbl, hide_index=True, use_container_width=True)
                st.caption("등급은 2025년 공고 전체를 위험도 순으로 같은 건수씩 나눈 순위입니다."
                           + (" 실제 유찰률은 결과확정 공고(낙찰·유찰·순위확정)만으로 계산한 사후 결과입니다."
                              if show_actual else ""))
        if notice_2025.empty:
            st.warning("조건에 맞는 2025년 공고가 없습니다.")
        else:
            notice_2025 = notice_2025.copy()
            notice_2025["검색표시"] = (
                notice_2025["위험등급"].astype("Int64").astype("string") + "등급 | " +
                notice_2025["공고일"].dt.strftime("%Y-%m-%d").fillna("") + " | " +
                notice_2025["pblancNo"].astype("string").fillna("") + " | " +
                notice_2025["bidNm"].astype("string").fillna("") + " | " +
                notice_2025["ornt"].astype("string").fillna("")
            )
            search = st.text_input("공고명·공고번호·기관명 검색", key="risk_notice_search")
            matched = notice_2025.loc[notice_2025["검색표시"].str.contains(search, regex=False, case=False, na=False)]
            if matched.empty:
                st.info("검색 결과가 없습니다.")
            else:
                st.caption(f"조건에 맞는 2025년 공고 {len(matched):,}건 (위험도 높은 순)")
                table = notice_table(matched)
                if show_actual:
                    st.dataframe(style_results(table), hide_index=True, use_container_width=True, height=280)
                else:
                    st.dataframe(table.drop(columns="실제 결과"), hide_index=True, use_container_width=True,
                                 height=280)
                label_map = dict(zip(matched["noticeKey"].astype(str), matched["검색표시"].astype(str)))
                with st.form("risk_notice_form"):
                    key = st.selectbox("공고 선택", options=[""] + list(label_map),
                                       format_func=lambda x: label_map.get(x, "공고를 선택하세요"))
                    submit = st.form_submit_button("선택 공고 분석", type="primary")
                if submit:
                    if not key:
                        st.warning("공고를 선택해 주세요.")
                    else:
                        selected = matched.loc[matched["noticeKey"].astype(str).eq(key)].iloc[0]
                        st.session_state["risk_input"] = {
                            "mode": "공고 선택", "noticeKey": key, "기관그룹": str(selected["기관그룹"]),
                            "busiDivs": str(selected["busiDivs"]), "cntrctMth": str(selected["cntrctMth"]),
                            "공고명": str(selected["bidNm"]), "발주기관": str(selected["ornt"]),
                            "실제결과": str(selected["결과상태"]),
                            "사업규모금액": selected["사업규모금액"],
                            "사업규모구간": selected["사업규모구간"],
                            "금액출처": selected["금액출처"],
                            "입찰방법출처": selected["입찰방법출처"],
                        }
    with tab_manual:
        st.caption("공고번호 없이도 조건과 사업금액을 입력해 4변수 위험도를 조회할 수 있습니다.")
        with st.form("risk_manual_form"):
            c1, c2 = st.columns(2)
            with c1:
                org = st.selectbox("기관그룹", [""] + [v for v in choices(train, "기관그룹") if v != "미분류"])
                business = st.selectbox("사업구분", [""] + choices(train, "busiDivs"))
            with c2:
                methods = [m for m in choices(train, "cntrctMth") if "수의" not in m]
                method = st.selectbox("계약방법", [""] + methods)
            manual_amount = st.number_input("사업금액 (원) — 4변수 위험도용", min_value=0, value=0, step=1_000_000,
                                            help="0원은 금액 미입력을 뜻하며, 이 경우 3변수 위험도로 대체합니다.")
            submit_manual = st.form_submit_button("입력 조건 분석", type="primary")
        if submit_manual:
            if not all((org, business, method)):
                st.warning("세 조건을 모두 선택해 주세요.")
            else:
                st.session_state["risk_input"] = {"mode": "직접 입력", "noticeKey": None,
                    "기관그룹": org, "busiDivs": business, "cntrctMth": method,
                    "사업규모금액": manual_amount if manual_amount > 0 else None,
                    "사업규모구간": amount_bin(manual_amount, boundaries),
                    "금액출처": "직접 입력" if manual_amount > 0 else "금액 미입력"}

    inp = st.session_state.get("risk_input")
    if inp is not None:
        inp["사업규모구간"] = amount_bin(inp.get("사업규모금액"), boundaries)
    if not inp:
        st.info("상단의 두 탭 중 하나에서 분석 대상을 지정해 주세요.")
        return
    if inp.get("noticeKey") and inp["noticeKey"] not in set(notice_all["noticeKey"].astype(str)):
        st.warning("이전에 선택한 공고가 현재 목록에 없습니다. 다시 선택해 주세요.")
        return

    with st.container(border=True):
        st.subheader("분석 조건")
        st.caption("입력 방식: " + inp["mode"])
        if inp.get("noticeKey"):
            st.write(f'**공고:** {inp.get("공고명", "")}')
            st.caption(f'공고키: {inp["noticeKey"]}')
        c1, c2, c3 = st.columns(3)
        c1.metric("기관그룹", inp["기관그룹"])
        c2.metric("사업구분", inp["busiDivs"])
        c3.metric("계약방법", inp["cntrctMth"])
        amount = inp.get("사업규모금액")
        st.write(f'**사업금액:** {float(amount):,.0f}원' if pd.notna(amount) else '**사업금액:** 미확인')
        st.caption(f'금액 구간(학습 5분위): {inp.get("사업규모구간") if pd.notna(inp.get("사업규모구간")) else "미분류"} · '
                   f'출처: {inp.get("금액출처", "미확인")}')

    try:
        base_risk = get_risk(inp, reference)
        risk = get_risk(inp, amount_reference, AMOUNT_STEPS)
        history = comparable_notices(train, inp, risk["적용변수"])
        if len(history) != risk["근거공고수"]:
            raise ValueError("근거 공고 수와 유사공고 목록의 건수가 다릅니다.")
    except ValueError as exc:
        st.error(f"위험도 산정 실패: {exc}")
        return

    grade, top_share = grade_of(risk, ranked_2025, inp.get("noticeKey"))
    # 큰 지표는 사전 예측 정보만. 실제 결과는 스위치를 켰을 때 아래에 작게 사후 확인으로 표시
    a, g, b = st.columns(3)
    a.metric("과거 유사조건 유찰률 (4변수 우선)", f'{risk["위험도"] * 100:.2f}%')
    g.metric("위험 등급", f"{grade}등급 / 10" + (" · 고위험" if grade >= 9 else ""),
             delta=f"2025년 공고 중 상위 {np.ceil(top_share * 100):.0f}%", delta_color="off",
             help=f"2025년 공고 {len(ranked_2025):,}건을 위험도 순으로 10등급으로 나눈 순위 (1 = 가장 낮음). "
                  "9·10등급이 상위 20% 고위험군이며, 실제 결과는 순위에 사용하지 않는다.")
    b.metric("근거 공고 수", f'{risk["근거공고수"]:,}건')
    if show_actual and inp.get("noticeKey"):
        actual = inp.get("실제결과")
        st.caption(f"사후 확인 · 2025년 실제 결과: **{RESULT_LABELS.get(actual, actual)}** — "
                   f"{RESULT_HELP.get(actual, '')}")

    with st.container(border=True):
        st.subheader("위험도 산정 근거")
        st.write(f'**적용 단계:** {risk["적용단계"]}단계 · {risk["적용조건"]}')
        st.write(f'**과거 유찰:** {risk["근거유찰건수"]:,}건 / **비유찰:** {risk["근거공고수"] - risk["근거유찰건수"]:,}건')
        st.write(f'**산식:** {risk["근거유찰건수"]:,} ÷ {risk["근거공고수"]:,} × 100 = {risk["위험도"]*100:.2f}%')
        if risk["적용단계"] > 1:
            st.info(
                "금액 미확인 또는 4변수 표본 부족으로 3변수 이하 단계가 적용됐습니다."
            )
        st.caption("과거 동일 조건의 관측 유찰률이며, 해당 공고의 확정적인 유찰 확률은 아닙니다.")

    with st.expander("4·3·2·1변수 위험도 비교", expanded=False):
        comparison = compare_risk_by_level(inp, amount_reference)
        chart = risk_comparison_chart(comparison)

        if chart is not None:
            st.plotly_chart(chart, use_container_width=True)
        else:
            st.info("비교할 수 있는 과거 조합이 없습니다.")

        unavailable = comparison.loc[comparison["유찰률"].isna(), "모델"].tolist()
        if unavailable:
            st.caption(
                " · ".join(unavailable)
                + "는 입력 정보가 없거나 동일 조건의 과거 공고가 10건 미만이어서 표시하지 않았습니다."
            )

        st.caption(
            "막대는 각 변수 조합의 관측 유찰률을 독립적으로 비교한 값입니다. "
            "가장 높은 막대가 최종 선택 모델이라는 의미는 아닙니다. "
            "대표 위험도는 위의 4→3→2→1변수 단계적 대체 규칙을 따릅니다."
        )

        boundaries_text = " / ".join(f"{value:,.0f}" for value in boundaries)
        st.caption(
            f"5분위 경계(2021~2024년 학습): {boundaries_text}원. "
            "2025년 결과는 학습·금액 구간 산출에 사용하지 않습니다."
        )
        if inp.get("입찰방법출처") == "입찰결과_사후복원":
            st.warning("이 공고의 입찰방법은 사후 입찰결과에서 복원된 값입니다.")

    with st.container(border=True):
        st.subheader("과거 유사공고")
        st.caption(f'적용 조건과 일치하는 2021~2024년 결과확정 경쟁성 공고 {len(history):,}건 중 최근 100건을 표시합니다.')
        columns = ["공고일", "pblancNo", "bidNm", "ornt", "기관그룹", "busiDivs", "cntrctMth", "결과상태", "noticeKey"]
        view = history.sort_values(["공고일", "noticeKey"], ascending=[False, True]).head(100)[columns].copy()
        view["공고일"] = view["공고일"].dt.strftime("%Y-%m-%d")
        st.dataframe(view.rename(columns={"pblancNo":"공고번호", "bidNm":"공고명", "ornt":"발주기관",
                                          "busiDivs":"사업구분", "cntrctMth":"계약방법", "결과상태":"실제결과"}),
                     hide_index=True, use_container_width=True)
        download_cols = history[columns].copy()
        download_cols["공고일"] = download_cols["공고일"].dt.strftime("%Y-%m-%d")
        st.download_button("전체 유사공고 CSV", download_cols.to_csv(index=False).encode("utf-8-sig"),
                           file_name="historical_comparable_notices.csv", mime="text/csv")
    st.caption("2025년 입찰결과는 위험도 학습·금액 구간·유사공고 집계에 사용하지 않습니다.")
