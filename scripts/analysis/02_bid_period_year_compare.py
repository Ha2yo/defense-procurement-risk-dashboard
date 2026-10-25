"""2021~2023년과 2024년 공고기간·유찰률 비교 및 선 그래프 생성.

설치 위치: <프로젝트 루트>/scripts/analysis/02_bid_period_year_compare.py
기존 02_bid_period_preprocess.py로 생성한 CSV를 읽기만 합니다.
기존 데이터 정의 유지: '순위확정'을 '비유찰(0)'로 간주합니다.

실행(프로젝트 루트에서):
  python scripts/analysis/02_bid_period_year_compare.py
  python scripts/analysis/02_bid_period_year_compare.py --show-plots
  python scripts/analysis/02_bid_period_year_compare.py --overwrite

기본적으로 CSV 4개와 선 그래프 PNG 2개를 저장합니다.
카이제곱 통계표는 연도 접미사(_2021_2023, _2024_2024) 및 _v2/_v3를 자동 인식합니다.
--show-plots: 파일 저장 후 그래프 창도 열기(화면 표시 가능한 환경에서)
--overwrite: 동일 이름의 기존 출력물 덮어쓰기 허용
--chi-dir: 연도별 카이제곱 검정 CSV 저장 폴더(기본 자동 탐색)
상대 입력·출력 경로는 현재 터미널 폴더가 아닌 프로젝트 루트 기준입니다.
"""

from pathlib import Path
import argparse
import math
import re
import sys
import pandas as pd
from scipy.stats import chi2_contingency

# 일부 Windows 콘솔에서 특수 아이콘을 표시할 수 없어도 중단하지 않습니다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(errors="replace")

# 비교할 두 연도 범위, 공고기간 구간 순서, 필수 데이터 열을 정의합니다.
PERIODS = [("2021~2023년", 2021, 2023), ("2024년", 2024, 2024)]
ORDER = ["5일 이하", "5일 초과~10일", "10일 초과"]
REQUIRED = {"noticeKey", "공고연도", "공고기간_일", "기간구간", "is_failed", "분석대상"}



def section(icon: str, title: str) -> None:
    # 터미널에서 분석 단계 제목을 구분선으로 감싸 표시합니다.
    print("\n" + "━" * 58)
    print(f"{icon} {title}")
    print("━" * 58)



def project_root() -> Path:
    """scripts/analysis/02_bid_period_year_compare.py 기준, 두 단계 위가 프로젝트 루트."""
    return Path(__file__).resolve().parents[2]



def from_project_root(root: Path, path: Path) -> Path:
    """사용자 경로가 상대경로이면 프로젝트 루트 기준으로 변환."""
    return path if path.is_absolute() else root / path



def find_input(root: Path, given: Path | None) -> Path:
    # 사용자가 파일을 지정하면 우선하고, 없으면 지정된 기본 위치를 순서대로 찾습니다.
    if given is not None:
        path = from_project_root(root, given)
        if not path.is_file():
            raise FileNotFoundError(f"지정한 입력 파일이 없습니다: {path}")
        return path

    candidates = [
        root / "data" / "analysis" / "bid_period_preprocessed.csv",
        root / "data" / "analysis" / "bid_period" / "bid_period_preprocessed.csv",
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(
        "전처리 CSV를 찾지 못했습니다. --input-file로 실제 경로를 지정하세요.\n"
        + "\n".join(f"확인한 경로: {p}" for p in candidates)
    )



def find_chi_result(root: Path, chi_dir: Path | None, input_path: Path,
                    start_year: int, end_year: int) -> Path | None:
    """02_bid_period_analysis.py 저장 규칙: 연도별 기본 파일 / _v2, _v3 ...

    한 디렉터리에 동일 연도 파일 여러 개가 있다면 가장 큰 버전 선택.
    지정된 --chi-dir에서는 그 폴더만 검색합니다.
    """
    if chi_dir is not None:
        directories = [from_project_root(root, chi_dir)]
        if not directories[0].is_dir():
            raise FileNotFoundError(f"카이제곱 CSV 폴더가 없습니다: {directories[0]}")
    else:
        directories = [
            root / "data" / "analysis" / "bid_period",
            root / "data" / "analysis",
            input_path.parent,
        ]

    # 지정 연도의 카이제곱 결과 파일명만 찾고 _v2·_v3 같은 버전을 인식합니다.
    pattern = re.compile(
        rf"^period_chi_square_{start_year}_{end_year}(?:_v([1-9][0-9]*))?\.csv$"
    )
    # 데이터가 여러 폴더에 중복 저장되어 있다면 첫 번째 폴더를 우선합니다.
    for folder in dict.fromkeys(directories):
        if not folder.is_dir():
            continue
        candidates = []
        for path in folder.glob(f"period_chi_square_{start_year}_{end_year}*.csv"):
            match = pattern.fullmatch(path.name)
            if match:
                version = int(match.group(1)) if match.group(1) else 1
                if match.group(1) and version < 2:
                    continue  # 실행 스크립트의 버전은 _v2부터 시작
                candidates.append((version, path))
        if candidates:
            return max(candidates, key=lambda pair: pair[0])[1]
    return None



def chi_square_comparison(root: Path, chi_dir: Path | None, input_path: Path,
                          samples: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """기간별 카이제곱 파일을 비교하되, 분석대상 데이터와 일치하는지 재계산 검증."""
    # 두 비교 기간에 대해 동일한 기준으로 독립성 검정을 다시 계산합니다.
    rows = []
    for label, start, end in PERIODS:
        sample = samples[label]
        # 행=공고기간 범주, 열=비유찰(0)·유찰(1)의 교차표입니다.
        ct = pd.crosstab(sample["기간구간"], sample["is_failed"]).reindex(
            index=ORDER, columns=[0, 1], fill_value=0
        )
        if (ct.sum(axis=1) == 0).any():
            raise ValueError(f"{label}: 빈 공고기간 구간이 있어 카이제곱 검정을 할 수 없습니다.")
        # χ² 통계량, p값, 자유도, 기대빈도를 계산합니다.
        chi2, p_value, dof, expected = chi2_contingency(ct, correction=False)
        # Cramér의 V로 두 변수의 연관성 크기를 함께 산출합니다.
        v = (chi2 / (len(sample) * min(ct.shape[0] - 1, ct.shape[1] - 1))) ** .5
        calculated = {
            "시작연도": start, "종료연도": end, "분석건수": len(sample),
            "카이제곱통계량": chi2, "자유도": dof, "p_value": p_value,
            "최소기대빈도": expected.min(), "Cramers_V": v,
            "유의수준_0.05": "유의" if p_value < 0.05 else "유의하지 않음",
        }
        # 이미 저장된 연도별 검정 파일이 있으면 재계산 값과 일치하는지 검증합니다.
        chi_path = find_chi_result(root, chi_dir, input_path, start, end)
        if chi_path is None:
            source = "전처리 CSV에서 재계산(외부 결과 파일 없음)"
            print(f"⚠️ {label}: 연도별 카이제곱 CSV 없음 → 전처리 데이터에서 직접 계산")
        else:
            # 기존 파일의 열 구성과 각 검정 수치가 동일한지 하나씩 검사합니다.
            saved = pd.read_csv(chi_path, encoding="utf-8-sig")
            if len(saved) != 1:
                raise ValueError(f"카이제곱 검정 결과 CSV는 한 행이어야 합니다: {chi_path}")
            missing = set(calculated) - set(saved.columns)
            if missing:
                raise ValueError(f"카이제곱 결과 컬럼 누락 {sorted(missing)}: {chi_path}")
            values = saved.iloc[0]
            for col, calculated_value in calculated.items():
                actual = values[col]
                if col == "유의수준_0.05":
                    good = str(actual) == calculated_value
                else:
                    try:
                        # 저장 과정에서 생길 수 있는 아주 작은 부동소수점 오차는 허용합니다.
                        good = math.isclose(float(actual), float(calculated_value),
                                            rel_tol=1e-7, abs_tol=1e-9)
                    except (TypeError, ValueError):
                        good = False
                if not good:
                    raise ValueError(
                        f"❌ {label} 카이제곱 CSV와 전처리 데이터의 계산 결과 불일치: "
                        f"{col} (저장값={actual}, 재계산값={calculated_value}). "
                        f"사용한 파일: {chi_path}. 두 분석의 데이터·제외 규칙을 확인하세요."
                    )
            source = "연도별 CSV와 재계산 결과 일치"
            print(f"✅ {label} 카이제곱 CSV 확인: {chi_path.name}")

        rows.append({"분석기간": label, **calculated,
                     "검증상태": source,
                     "카이제곱_원본파일명": chi_path.name if chi_path else ""})
    return pd.DataFrame(rows)



def prepare_data(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    # 입력 CSV에 필요한 열이 모두 존재하는지 먼저 확인합니다.
    missing = REQUIRED - set(df.columns)
    if missing:
        raise ValueError(f"필수 컬럼 누락: {sorted(missing)}")

    # 공고키가 비어 있거나 중복되면 분석을 중단합니다.
    if df["noticeKey"].isna().any() or df["noticeKey"].duplicated().any():
        raise ValueError("noticeKey 결측/중복 발견: 기존 코드와 동일하게 분석을 중단합니다.")

    # read_csv로 저장된 True/False를 읽을 때 pandas 버전에 따른 타입 차이를 방어.
    # 분석대상=True인 행만 사용하며 문자열/불리언 표기 차이를 흡수합니다.
    eligible = df["분석대상"].astype("string").str.strip().str.lower().eq("true")
    eligible = eligible.fillna(False)
    work = df.loc[eligible].copy()
    work["공고연도"] = pd.to_numeric(work["공고연도"], errors="coerce")
    work["공고기간_일"] = pd.to_numeric(work["공고기간_일"], errors="coerce")
    work["is_failed"] = pd.to_numeric(work["is_failed"], errors="coerce")

    # 결측값, 유찰 이진값, 음수 기간 및 구간명 오류를 검사합니다.
    if work[["공고연도", "공고기간_일", "is_failed", "기간구간"]].isna().any().any():
        raise ValueError("분석대상에 연도/기간/기간구간/유찰여부 결측이 있습니다.")
    if not work["is_failed"].isin([0, 1]).all():
        raise ValueError("분석대상에 0/1이 아닌 is_failed 값이 있습니다.")
    if (work["공고기간_일"] < 0).any():
        raise ValueError("분석대상에서 음수 공고기간을 발견했습니다.")
    if not work["기간구간"].isin(ORDER).all():
        raise ValueError("기존 구간 정의와 다른 기간구간 값이 있습니다.")

    # 기존 전처리의 구간 경계가 그대로 유지되었는지 확인.
    expected = pd.cut(
        work["공고기간_일"], bins=[-1, 5, 10, float("inf")], labels=ORDER
    ).astype("string")
    if not expected.eq(work["기간구간"].astype("string")).all():
        raise ValueError("공고기간_일과 기간구간이 불일치합니다.")

    # 2021~2023년과 2024년을 비교할 표본으로 각각 분리합니다.
    samples = {}
    for label, first, last in PERIODS:
        samples[label] = work.loc[work["공고연도"].between(first, last)].copy()
        if samples[label].empty:
            raise ValueError(f"{label} 분석 대상이 0건입니다.")
    return samples



def describe_periods(samples: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """기존 period_distribution.csv의 8개 지표를 두 기간에 나란히 표시."""
    # describe()가 반환하는 통계 항목명을 팀 보고서용 한글 지표명에 연결합니다.
    mapping = [
        ("건수", "count"),
        ("평균(일)", "mean"),
        ("중앙값(일)", "50%"),
        ("표준편차(일)", "std"),
        ("최소(일)", "min"),
        ("1사분위수 Q1(일)", "25%"),
        ("3사분위수 Q3(일)", "75%"),
        ("최대(일)", "max"),
    ]
    # 두 기간의 기술통계를 동일한 행에 병렬로 배치합니다.
    out = pd.DataFrame({"지표": [name for name, _ in mapping]})
    for label, sample in samples.items():
        stats = sample["공고기간_일"].describe(percentiles=[.25, .5, .75])
        out[label] = [float(stats[key]) for _, key in mapping]
        out[label] = out[label].round(2)
    return out



def summarize_groups(sample: pd.DataFrame) -> pd.DataFrame:
    """한 기간의 기간구간별 건수, 구성비, 비유찰/유찰, 유찰률."""
    # 기간구간별 유찰·비유찰 건수를 교차표로 센 뒤 구성비를 계산합니다.
    ct = pd.crosstab(sample["기간구간"], sample["is_failed"])
    ct = ct.reindex(index=ORDER, columns=[0, 1], fill_value=0)
    ct.columns = ["비유찰건수", "유찰건수"]
    ct["공고건수"] = ct["비유찰건수"] + ct["유찰건수"]
    ct["구간비중(%)"] = ct["공고건수"] / len(sample) * 100
    # 유찰률 = 구간 내 유찰건수 / 구간 내 전체 공고건수 × 100.
    ct["유찰률(%)"] = ct["유찰건수"] / ct["공고건수"].replace(0, float("nan")) * 100
    return ct[["공고건수", "구간비중(%)", "비유찰건수", "유찰건수", "유찰률(%)"]]



def compare_groups(samples: dict[str, pd.DataFrame]) -> pd.DataFrame:
    # 각각의 기간 구간별 집계를 같은 행에 정렬해 변화량을 비교합니다.
    p1, p2 = [summarize_groups(samples[label]) for label, _, _ in PERIODS]
    out = pd.DataFrame({"기간구간": ORDER})
    # 인덱스 자동 정렬에 따른 오배치를 막기 위해 .to_numpy() 사용.
    for label, table in [(PERIODS[0][0], p1), (PERIODS[1][0], p2)]:
        for col in table.columns:
            out[f"{label}_{col}"] = table[col].to_numpy()
    # 표에 표시되는 소수 둘째 자리 유찰률끼리 비교해 수치가 일관되게 보이도록 계산.
    # %p는 비율의 차이를 의미하며, 2024년 값에서 과거 기간 값을 뺍니다.
    out["유찰률차이(%p,2024-과거)"] = (
        p2["유찰률(%)"].round(2).to_numpy() - p1["유찰률(%)"].round(2).to_numpy()
    )
    out["구간비중차이(%p,2024-과거)"] = (
        p2["구간비중(%)"].round(2).to_numpy() - p1["구간비중(%)"].round(2).to_numpy()
    )
    float_cols = [c for c in out.columns if "(%)" in c or "(%p" in c]
    out[float_cols] = out[float_cols].round(2)
    return out



def yearly_group_summary(samples: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """2021, 2022, 2023, 2024년 각각의 구간별 집계를 계산(추세 그래프 근거)."""
    # 두 기간의 데이터를 합쳐 연도별(2021~2024) 유찰률을 따로 계산합니다.
    work = pd.concat(list(samples.values()), ignore_index=True)
    records = []
    for year in (2021, 2022, 2023, 2024):
        year_df = work.loc[work["공고연도"].eq(year)]
        if year_df.empty:
            raise ValueError(f"{year}년 분석 가능한 공고가 없어 추세 그래프를 작성할 수 없습니다.")
        table = summarize_groups(year_df)
        for group, r in table.iterrows():
            records.append({
                "공고연도": year,
                "기간구간": group,
                "공고건수": int(r["공고건수"]),
                "비유찰건수": int(r["비유찰건수"]),
                "유찰건수": int(r["유찰건수"]),
                "유찰률(%)": round(float(r["유찰률(%)"]), 2),
            })
    return pd.DataFrame(records)



def set_korean_font() -> None:
    """Windows의 맑은 고딕 우선; 한글 그래프 글자가 깨지지 않게 설정."""
    # 설치된 한글 글꼴 중 첫 번째로 사용 가능한 글꼴을 선택합니다.
    from matplotlib import font_manager, rcParams
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for font in ("Malgun Gothic", "NanumGothic", "NanumSquare", "AppleGothic", "Noto Sans CJK KR"):
        if font in installed:
            rcParams["font.family"] = font
            rcParams["axes.unicode_minus"] = False
            print(f"🔤 그래프 한글 글꼴: {font}")
            return
    print("⚠️ 한글 글꼴을 찾지 못했습니다. 그래프 한글이 깨지면 '맑은 고딕' 설치를 확인하세요.")



def draw_comparison_line(groups: pd.DataFrame):
    """가로축=공고기간 범주, 세로축=유찰률; 2개 기간의 선 비교."""
    import matplotlib.pyplot as plt
    # 두 기간의 구간별 유찰률을 한 그래프에 표시합니다.
    fig, ax = plt.subplots(figsize=(10, 5.8))
    # 세 공고기간 구간을 x축 위치 0, 1, 2로 배치합니다.
    x = list(range(len(ORDER)))
    for label, _, _ in PERIODS:
        y = groups[f"{label}_유찰률(%)"].tolist()
        ax.plot(x, y, marker="o", markersize=8, linewidth=2.2, label=label)
        # 각 구간의 실제 유찰률을 점 옆에 숫자로 표시합니다.
        for xi, rate in zip(x, y):
            if pd.notna(rate):
                offset = -16 if label == PERIODS[0][0] else 9
                ax.annotate(f"{rate:.2f}%", (xi, rate), xytext=(0, offset),
                            textcoords="offset points", ha="center", fontsize=10)
    ax.set_xticks(x, ORDER)
    ax.set_xlabel("공고기간 구간")
    ax.set_ylabel("유찰률 (%)")
    ax.set_title("공고기간 구간별 유찰률 비교 (2021~2023 vs 2024)", pad=16)
    ax.grid(axis="y", alpha=0.3)
    ax.set_ylim(bottom=0)
    ax.legend(title="비교 기간")
    fig.text(0.01, 0.01, "※ 구간별 집계값을 연결한 선이며, 공고기간에 대한 연속적 추정선은 아닙니다.", fontsize=9)
    fig.tight_layout(rect=(0, 0.045, 1, 1))
    return fig



def draw_yearly_trend(annual: pd.DataFrame):
    """가로축=연도(2021~2024), 각 공고기간 구간별 유찰률 추세."""
    import matplotlib.pyplot as plt
    # 2021~2024년 연도별 구간 유찰률을 선 그래프로 나타냅니다.
    fig, ax = plt.subplots(figsize=(10, 5.8))
    # 매 연도의 같은 공고기간 구간을 선으로 연결해 추세를 비교합니다.
    for group in ORDER:
        part = annual.loc[annual["기간구간"].eq(group)].sort_values("공고연도")
        ax.plot(part["공고연도"], part["유찰률(%)"],
                marker="o", markersize=7, linewidth=2.2, label=group)
    ax.set_xticks([2021, 2022, 2023, 2024])
    ax.set_xlabel("공고연도")
    ax.set_ylabel("유찰률 (%)")
    ax.set_title("연도별 공고기간 구간 유찰률 추이 (2021~2024)", pad=16)
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", alpha=0.3)
    ax.legend(title="공고기간 구간")
    fig.text(0.01, 0.01, "※ 연도별 집계값을 연결한 추세입니다. 연도 사이 값은 관측되지 않았습니다.", fontsize=9)
    fig.tight_layout(rect=(0, 0.045, 1, 1))
    return fig



def main() -> None:
    # 실행 옵션으로 입력 데이터, 출력 폴더, 그래프 표시·덮어쓰기 여부를 정합니다.
    parser = argparse.ArgumentParser(description="공고기간·유찰률 비교표 및 선 그래프 2종 생성")
    parser.add_argument("--input-file", type=Path, help="bid_period_preprocessed.csv 경로")
    parser.add_argument("--output-dir", type=Path, help="CSV·PNG 저장 폴더")
    parser.add_argument("--show-plots", action="store_true", help="PNG 저장 후 그래프 창도 열기")
    parser.add_argument("--chi-dir", type=Path, help="카이제곱 통계 CSV 폴더 (기본 자동 탐색)")
    parser.add_argument("--overwrite", action="store_true", help="기존 결과 CSV·PNG 덮어쓰기 허용")
    args = parser.parse_args()

    # GUI가 없는 환경에서도 그림을 파일로 저장할 수 있도록 기본 백엔드 설정.
    # 그래프 창이 없어도 PNG 파일을 저장할 수 있도록 비대화형 모드를 사용합니다.
    if not args.show_plots:
        import matplotlib
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # 프로젝트 기준 경로를 결정하고 입력 파일과 결과 저장 폴더를 지정합니다.
    root = project_root()
    path = find_input(root, args.input_file)
    output_dir = (
        from_project_root(root, args.output_dir)
        if args.output_dir is not None
        else root / "data" / "analysis" / "bid_period" / "year_comparison"
    )

    section("🛡️", "국방조달 공고기간 비교 | 2021~2023년 vs 2024년")
    print(f"📥 입력 파일 : {path}")
    print(f"📂 출력 폴더 : {output_dir}")
    print("🔒 원본 CLEAN 데이터·전처리 파일은 읽기만 합니다.")
    print("⚙️ 기존 규칙 유지: 순위확정 = 비유찰(0)")

    # 이 스크립트가 생성할 CSV 4개와 PNG 2개의 이름을 미리 지정합니다.
    files = {
        "📋 공고기간 분포표": output_dir / "period_distribution_2021_2023_vs_2024.csv",
        "📊 구간별 유찰률 비교표": output_dir / "period_group_failure_2021_2023_vs_2024.csv",
        "📅 연도별 구간 집계표": output_dir / "yearly_period_group_failure_2021_2024.csv",
        "🧪 카이제곱 비교표": output_dir / "period_chi_square_comparison_2021_2023_vs_2024.csv",
        "📈 구간별 유찰률 비교 선 그래프": output_dir / "period_failure_rate_comparison.png",
        "📈 연도별 유찰률 추이 선 그래프": output_dir / "yearly_failure_rate_trend.png",
    }
    # 여러 결과 파일 중 하나만 기존에 있어도 부분 덮어쓰기를 막음.
    existing = [p for p in files.values() if p.exists()]
    if existing and not args.overwrite:
        raise FileExistsError(
            "❌ 기존 결과 파일이 있어 저장을 중단합니다. 다른 --output-dir을 "
            "지정하거나, 의도한 경우에만 --overwrite를 사용하세요:\n"
            + "\n".join(str(p) for p in existing)
        )

    section("📥", "1/4 전처리 데이터 불러오기 및 점검")
    # 공고키 문자열을 보존하여 전처리 CSV를 불러오고 분석기간별로 나눕니다.
    df = pd.read_csv(path, dtype={"noticeKey": "string"}, low_memory=False)
    print(f"✅ 전처리 데이터 로드 완료: {len(df):,}건")
    samples = prepare_data(df)
    for label, sample in samples.items():
        print(f"  🔎 {label}: 분석 대상 {len(sample):,}건")

    section("📊", "2/4 공고기간 분포표 및 구간별 비교표")
    # 기술통계·구간별 비교·연도별 추세·카이제곱 검정을 차례로 계산합니다.
    dist = describe_periods(samples)
    groups = compare_groups(samples)
    annual = yearly_group_summary(samples)
    chi = chi_square_comparison(root, args.chi_dir, path, samples)
    print("📋 공고기간 분포표 (전체 공고기간, 일 단위)")
    print(dist.to_string(index=False))
    print("\n📊 구간별 유찰률 비교표")
    print(groups.to_string(index=False))
    print("\n📅 연도별 공고기간 구간 집계표")
    print(annual.to_string(index=False))
    print("\n🧪 카이제곱 검정 결과 비교표")
    print(chi.to_string(index=False))

    section("⚠️", "자료 해석 시 주의사항")
    # 해석상 유의점: 순위확정은 이 분석에서 비유찰에 포함됩니다.
    if "결과상태" in df.columns:
        print("⚠️ 기존 전처리 기준에 따라 '순위확정'을 비유찰(0)로 포함합니다.")
        for label, sample in samples.items():
            n = int(sample["결과상태"].eq("순위확정").sum())
            print(f"  • {label}: 순위확정 {n:,}건 포함")
    else:
        print("⚠️ 결과상태 컬럼이 없어 순위확정 포함 건수를 확인할 수 없습니다.")
    print("ℹ️ 선 그래프는 집계값의 비교·추세 표시이며, 인과관계를 증명하지 않습니다.")

    section("📈", "3/4 선 그래프 작성")
    # 앞서 계산한 집계값을 선 그래프 두 종류로 표현합니다.
    set_korean_font()
    comparison_fig = draw_comparison_line(groups)
    yearly_fig = draw_yearly_trend(annual)

    section("💾", "4/4 결과 CSV 및 PNG 저장")
    # 결과는 출력 폴더에만 저장하며 입력 CLEAN/전처리 CSV는 수정하지 않습니다.
    output_dir.mkdir(parents=True, exist_ok=True)
    dist.to_csv(files["📋 공고기간 분포표"], index=False, encoding="utf-8-sig")
    groups.to_csv(files["📊 구간별 유찰률 비교표"], index=False, encoding="utf-8-sig")
    annual.to_csv(files["📅 연도별 구간 집계표"], index=False, encoding="utf-8-sig")
    chi.to_csv(files["🧪 카이제곱 비교표"], index=False, encoding="utf-8-sig")
    comparison_fig.savefig(files["📈 구간별 유찰률 비교 선 그래프"], dpi=170, bbox_inches="tight")
    yearly_fig.savefig(files["📈 연도별 유찰률 추이 선 그래프"], dpi=170, bbox_inches="tight")
    for description, filepath in files.items():
        print(f"✅ {description} → {filepath}")
    print("🔒 기존 CLEAN·전처리 데이터 및 다른 통계 CSV는 수정하지 않았습니다.")
    print(f"🎉 완료! CSV 4개 · PNG 2개를 {output_dir}에 저장했습니다.")
    # --show-plots 옵션이 있는 경우에만 그래프 창을 띄웁니다.
    if args.show_plots:
        print("🖥️ 그래프 창을 표시합니다. 창을 닫으면 프로그램이 종료됩니다.")
        plt.show()
    plt.close(comparison_fig)
    plt.close(yearly_fig)


# 스크립트를 직접 실행했을 때만 main()을 호출하고 오류를 안내합니다.
if __name__ == "__main__":
    try:
        main()
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        print(f"\n❌ 실행 중단: {exc}", file=sys.stderr)
        sys.exit(1)
