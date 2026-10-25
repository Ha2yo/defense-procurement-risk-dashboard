from pathlib import Path
import pandas as pd


# 스크립트 위치 기준으로 프로젝트 폴더 찾기
# 프로젝트/scripts/analysis/00_unconfirmed_by_year.py
PROJECT_DIR = Path(__file__).resolve().parents[2]

INPUT_DIR = PROJECT_DIR / "data" / "clean"
OUTPUT_PATH = PROJECT_DIR / "data" / "analysis" / "unconfirmed_by_year.csv"

NOTICE_PATH = INPUT_DIR / "bid_notice_clean_20210101_20251231.csv"
RESULT_PATH = INPUT_DIR / "bid_result_clean_20210101_20251231.csv"

DIRECT_KEY = [
    "demandYear",  # 요구년도
    "orntCode",    # 발주기관코드
    "dcsNo",       # 판단번호
    "pblancNo",    # 공고번호
    "pblancOdr",   # 공고차수
]

BUSINESS_KEY = ["demandYear", "orntCode", "dcsNo"]


def read_data(path, required_columns):
    """파일을 읽고 필수 열을 확인한다."""
    if not path.is_file():
        raise FileNotFoundError(f"입력 파일이 없습니다:\n{path}")

    # 식별자의 앞자리 0을 유지하도록 문자열로 읽기
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")

    missing = sorted(set(required_columns) - set(df.columns))
    if missing:
        raise ValueError(f"{path.name}에 필수 열이 없습니다: {missing}")

    return df


def has_result(notice, result, keys):
    """지정한 식별자가 모두 일치하는 결과의 존재 여부."""
    result_keys = result[keys].dropna().drop_duplicates()

    matched = pd.MultiIndex.from_frame(notice[keys]).isin(
        pd.MultiIndex.from_frame(result_keys)
    )

    return (
        pd.Series(matched, index=notice.index)
        & notice[keys].notna().all(axis=1)
    )


def main():
    # 1. 데이터 읽기
    notice = read_data(
        NOTICE_PATH,
        DIRECT_KEY + ["pblancSe", "pblancDate"],
    )
    result = read_data(RESULT_PATH, DIRECT_KEY)

    original_count = len(notice)

    # 모든 열의 값이 동일한 완전 중복 행만 제거
    notice = notice.drop_duplicates().reset_index(drop=True)

    if notice.empty:
        raise ValueError("집계할 공고 데이터가 없습니다.")

    # 2. 식별자 정리
    for df in (notice, result):
        for col in DIRECT_KEY:
            df[col] = df[col].str.strip().replace("", pd.NA)

    # 3. 순서대로 제외하여 결과 미확인 공고 추출
    direct_match = has_result(notice, result, DIRECT_KEY)

    cancelled = (
        ~direct_match
        & notice["pblancSe"].str.strip().eq("취소공고").fillna(False)
    )

    other_notice_result = (
        ~direct_match
        & ~cancelled
        & has_result(notice, result, BUSINESS_KEY)
    )

    unconfirmed = ~(direct_match | cancelled | other_notice_result)

    # 4. 공고일자 기준 연도 추출
    dates = pd.to_datetime(
        notice["pblancDate"].str.strip(),
        format="%Y%m%d",
        errors="coerce",
    )

    # 날짜 오류 때문에 일부 공고가 집계에서 빠지는 것을 방지
    if dates.isna().any():
        invalid_count = int(dates.isna().sum())
        raise ValueError(
            f"공고일자 누락 또는 형식 오류가 {invalid_count:,}건 있습니다. "
            "pblancDate를 확인하세요."
        )

    notice["공고 연도"] = dates.dt.year.astype(int)

    # 5. 연도별 전체 공고와 결과 미확인 공고 집계
    total_by_year = notice.groupby("공고 연도").size()

    unknown_by_year = (
        notice.loc[unconfirmed]
        .groupby("공고 연도")
        .size()
    )

    summary = pd.DataFrame({
        "전체 공고 수": total_by_year,
        "결과 미확인 수": unknown_by_year.reindex(
            total_by_year.index, fill_value=0
        ),
    }).sort_index()

    total_count = len(notice)
    unknown_count = int(unconfirmed.sum())

    # 집계 누락 여부 확인
    if int(summary["전체 공고 수"].sum()) != total_count:
        raise RuntimeError("연도별 전체 공고 합계가 일치하지 않습니다.")

    if int(summary["결과 미확인 수"].sum()) != unknown_count:
        raise RuntimeError("연도별 결과 미확인 합계가 일치하지 않습니다.")

    # 합계 행 추가
    summary.loc["합계"] = [total_count, unknown_count]

    # 해당 연도의 전체 공고를 분모로 계산
    summary["해당 연도 공고 중 미확인 비율(%)"] = (
        summary["결과 미확인 수"]
        / summary["전체 공고 수"]
        * 100
    ).round(2)

    # 전체 결과 미확인 공고를 분모로 계산
    summary["전체 미확인 중 비중(%)"] = (
        (summary["결과 미확인 수"] / unknown_count * 100).round(2)
        if unknown_count > 0 else 0.0
    )

    summary = summary.rename_axis("공고 연도").reset_index()

    # 6. CSV 저장: 건수와 비율은 숫자형으로 유지
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(
        OUTPUT_PATH,
        index=False,
        encoding="utf-8-sig",
        float_format="%.2f",
    )

    # 7. 처리 내역과 표 출력
    print(f"\n입력 공고: {NOTICE_PATH}")
    print(f"입력 결과: {RESULT_PATH}")
    print(f"\n원본 공고: {original_count:,}건")
    print(f"완전 중복 제거: {original_count - total_count:,}건")
    print(f"전체 공고: {total_count:,}건")
    print(f"결과 직접매칭: {int(direct_match.sum()):,}건")
    print(f"취소 공고: {int(cancelled.sum()):,}건")
    print(
        "동일사업 다른 공고 결과 존재: "
        f"{int(other_notice_result.sum()):,}건"
    )
    print(f"최종 결과 미확인: {unknown_count:,}건")

    if unknown_count != 13769:
        print(
            "\n[확인 필요] 이전 집계 13,769건과 차이가 있습니다. "
            "현재 입력 파일로 계산한 값을 저장했습니다."
        )

    print("\n[결과 미확인 공고의 연도별 분포]")
    print(
        "| 공고 연도 | 전체 공고 수 | 결과 미확인 수 | "
        "해당 연도 공고 중 미확인 비율 | 전체 미확인 중 비중 |"
    )
    print("| --- | ---: | ---: | ---: | ---: |")

    for year, total, unknown, ratio, share in summary.itertuples(
        index=False, name=None
    ):
        print(
            f"| {year} | {total:,} | {unknown:,} | "
            f"{ratio:.2f}% | {share:.2f}% |"
        )

    print(f"\n결과 저장 완료: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()