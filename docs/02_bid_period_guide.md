# 02. 공고기간별 유찰률 및 연도 비교 — 실행 가이드

## 1. 개요

공고일(`pblancDate`)부터 입찰참가등록마감일(`bidPartcptRegistClosDt`)까지의 **달력 날짜 차이**를 공고기간으로 계산하고, 3개 기간구간별 유찰률과 카이제곱 독립성 검정을 **2021~2023년과 2024년 각각** 실시한다. 2021~2024년 연도별 추세와 두 기간의 차이도 함께 비교한다.

공식 코드: [전처리](../scripts/analysis/02_bid_period_preprocess.py) → [기간별 검정](../scripts/analysis/02_bid_period_analysis.py) → [연도 비교](../scripts/analysis/02_bid_period_year_compare.py).

결과 폴더: [`data/analysis/bid_period/`](../data/analysis/bid_period/) 및 [`year_comparison/`](../data/analysis/bid_period/year_comparison/).

## 2. 입력 데이터

- 입력: `data/clean/bid_notice_clean_20210101_20251231.csv`, `bid_result_clean_20210101_20251231.csv`.
- `noticeKey` 기준으로 공고 1건에 결과 상태 1개를 연결한다. 결과 행에 복수 상태가 있으면 `판단보류`로 보존하며, 결과 없는 공고도 삭제하지 않는다.
- **공고기간(일)** = `참가등록마감`의 날짜 부분 − `공고일`. 시간 차이나 개찰일까지의 간격이 아니다. 날짜를 정규화하므로 **0일**도 유효하다.
- 구간: **5일 이하(0~5일)**, **5일 초과~10일(6~10일)**, **10일 초과(11일 이상)**.
- 분석대상은 취소공고 제외, `cntrctMth == "수의계약"` 제외, 결과 상태가 유찰·낙찰·순위확정, 공고기간이 0 이상인 공고이다.
- 유찰=1, 낙찰·순위확정=0. 결과 미확인·판단보류·기간 음수·결측 등은 유찰률 분모에서 제외한다.
- **주의할 구현상 차이:** 공고기간 분석 전처리는 수의계약 제외에 `cntrctMth.eq("수의계약")`를 사용한다. 다른 분석의 `str.contains("수의")`와 완전히 같은 조건은 아니다. 실제 데이터에 '수의'가 포함된 다른 표기가 있는지 점검해야 전체 모집단의 정합성을 확신할 수 있다.
- 공고 CLEAN의 `noticeKey` 결측·중복, 결과 CLEAN의 `noticeKey` 결측은 오류로 중단. 제외사유별 건수는 **중복될 수 있어 합산 금지**.

## 3. 처리 흐름과 산식

공고기간은 공고일과 입찰참가등록마감일의 달력 날짜 차이로 계산하며, 유효한 기간을 세 구간으로 나눈다.

## 4. 실행 방법

```powershell
cd C:\workspace\defense-procurement-risk-dashboard
python -m pip install -r requirements.txt

# 1. 전처리 (CLEAN은 읽기만 함)
python scripts/analysis/02_bid_period_preprocess.py

# 2. 본 분석: 반드시 연도 명시
python scripts/analysis/02_bid_period_analysis.py --start-year 2021 --end-year 2023

# 3. 2024년 비교 분석
python scripts/analysis/02_bid_period_analysis.py --start-year 2024 --end-year 2024

# 4. 두 기간 비교 및 시각화
python scripts/analysis/02_bid_period_year_compare.py
```

**주의:** `02_bid_period_analysis.py`의 현재 기본값은 `--start-year 2024 --end-year 2024`다. 인자를 생략하면 **2021~2023년 본 분석이 실행되지 않는다**.

검정 코드는 기존 파일을 덮어쓰지 않고 동일 연도 출력이 있을 경우 `_v2`, `_v3` 접미사를 붙인다. 반면 비교 코드는 이미 결과가 존재하면 중단하며, 기존 파일을 의도적으로 갱신할 때만 `--overwrite`를 사용한다.

```powershell
# 기존 비교 결과를 명시적으로 교체해야 할 경우에만
python scripts/analysis/02_bid_period_year_compare.py --overwrite
```

## 5. 출력 파일

```text
data/analysis/bid_period/
  bid_period_preprocessed.csv
  period_distribution_2021_2023.csv
  period_failure_comparison_2021_2023.csv
  period_expected_counts_2021_2023.csv
  period_chi_square_2021_2023.csv
  period_distribution_2024_2024.csv
  period_failure_comparison_2024_2024.csv
  period_expected_counts_2024_2024.csv
  period_chi_square_2024_2024.csv
  year_comparison/
    period_distribution_2021_2023_vs_2024.csv
    period_group_failure_2021_2023_vs_2024.csv
    yearly_period_group_failure_2021_2024.csv
    period_chi_square_comparison_2021_2023_vs_2024.csv # 현재 main에 없음
    period_failure_rate_comparison.png
    yearly_failure_rate_trend.png
```

## 6. 결과 및 해석

| 항목 | 2021~2023년 | 2024년 |
|---|---:|---:|

| **기간 분석 대상** | **21,085** | **6,785** |
| 평균 공고기간 | 7.18일 | 6.79일 |
| 중앙값 | 7일 | 6일 |
| 표준편차 | 4.14일 | 3.15일 |
| Q1 / Q3 | 5일 / 7일 | 5일 / 7일 |
| 최소 / 최대 | 0일 / 62일 | 0일 / 52일 |

자료: `period_distribution_2021_2023.csv`, `period_distribution_2024_2024.csv`.

| 구간 | 2021~2023 공고 | 비유찰 | 유찰 | 유찰률 | 2024 공고 | 비유찰 | 유찰 | 유찰률 | 차이(%p) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 5일 이하 | 6,248 | 5,249 | 999 | 15.99% | 2,363 | 1,799 | 564 | 23.87% | +7.88 |
| 5일 초과~10일 | 13,414 | 11,225 | 2,189 | 16.32% | 4,027 | 3,202 | 825 | 20.49% | +4.17 |
| 10일 초과 | 1,423 | 1,185 | 238 | 16.73% | 395 | 278 | 117 | 29.62% | +12.89 |
| **합계** | **21,085** | **17,659** | **3,426** | **16.25%** | **6,785** | **5,279** | **1,506** | **22.20%** | **+5.95** |

자료: `period_failure_comparison_2021_2023.csv`, `period_failure_comparison_2024_2024.csv`, `year_comparison/period_group_failure_2021_2023_vs_2024.csv`.

**구분:** 기관·계약방법·사업구분 분석의 2021~2023 분석은 21,092건·유찰 3,426건(16.24%)이지만 공고기간 분석는 공고기간이 유효한 **21,085건**을 분모로 하므로 **16.25%**다. 두 값은 서로 다른 모집단에 대한 수치이며 오류가 아니다.

| 지표 | 2021~2023년 | 2024년 |
|---|---:|---:|
| 분석건수 | 21,085 | 6,785 |
| χ² | 0.595228 | 23.245417 |
| 자유도 | 2 | 2 |
| p값 | 0.74258791 | 0.0000089603 |
| 최소 기대빈도 | 231.2164 | 87.6743 |
| Cramér's V | 0.005313 | 0.058532 |
| 5% 기준 판정 | **유의하지 않음** | **유의함** |

출처: 저장된 `period_chi_square_2021_2023.csv`, `period_chi_square_2024_2024.csv`, 각 `period_expected_counts_*.csv`.

- 귀무가설: 공고기간 구간과 유찰 여부는 독립이다. 대립가설: 독립이 아니다.
- 코드에서 `chi2_contingency(..., correction=False)`를 사용한다. 3×2 표라 Yates 연속성 보정의 영향은 없다.
- 두 분석 모두 최소 기대빈도가 5보다 커 카이제곱 근사 기대빈도 조건을 충족한다.
- **2021~2023년:** p=0.742588, V≈0.0053 → 유의한 연관성을 확인하지 못함.
- **2024년:** p≈8.96×10⁻⁶, V≈0.0585 → 통계적으로 유의하나 연관성 강도는 매우 작음.
- 두 기간의 차이를 직접 검정한 상호작용 분석은 아니다. 집단 내 독립성 검정의 유의 여부가 다르다고 해서 두 기간의 효과 차이가 유의하다는 결론을 내릴 수는 없다.
- 본 공고기간 분석 두 검정은 **기관·계약방법·사업구분 분석의 주 검정 3개 Holm 묶음에 포함되지 않음**. 결과를 함께 보고할 때 보정 범위를 명시해야 한다.
- 동일 사업의 반복 공고가 있을 수 있으므로 공고 간 완전 독립성 가정은 별도 점검이 필요하다.

각 구간의 `2021~2023년` 대 `2024년` 유찰률 차이는 각각 **+7.88%p**, **+4.17%p**, **+12.89%p**다. 이 수치는 두 시기의 관측 유찰률 차이이며, 공고기간 변화의 인과적 효과를 뜻하지 않는다.

연도별 상세 기록: [`yearly_period_group_failure_2021_2024.csv`](../data/analysis/bid_period/year_comparison/yearly_period_group_failure_2021_2024.csv). 시각화: `period_failure_rate_comparison.png`, `yearly_failure_rate_trend.png`.

`02_bid_period_year_compare.py`는 전처리 데이터로 기간별 검정을 다시 계산해 연도별 카이제곱 결과와 일치하는지 확인한다. 기존 비교 출력물이 있을 경우 `--overwrite` 지정 여부에 따라 덮어쓰기 동작을 제어한다.

## 7. 실행 결과 확인

입력 데이터의 공고키 유일성과 결과 상태 확정 기준을 적용한다. 기간별 검정 결과는 `period_chi_square_2021_2023.csv`와 `period_chi_square_2024_2024.csv`에 기록된다.

