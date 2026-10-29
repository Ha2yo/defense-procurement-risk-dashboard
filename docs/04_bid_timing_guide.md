# 04. 공고 시기별 유찰률 분석 — 실행 가이드

## 1. 개요

공고일 `pblancDate` 기준으로 월·분기·연도별 공고 수 및 유찰률을 비교하고, **2021~2023년 결과확정 경쟁성 공고**에서 분기와 유찰 여부의 독립성을 카이제곱 검정한다. 2024년은 비교, 2025년은 보조 추세로 다룬다.

- **실행 코드:** [`scripts/analysis/04_bid_timing_analysis.py`](../scripts/analysis/04_bid_timing_analysis.py)
- **예상 출력:** `data/analysis/bid_timing/`
- **입력:** `data/clean/bid_notice_clean_20210101_20251231.csv`와 `bid_result_clean_20210101_20251231.csv`

## 2. 입력 데이터

| 항목 | 코드 기준 |
|---|---|
| 분석 단위 | 공고키 `noticeKey`별 고유 공고 1건 |
| 연도·월·분기 | **공고일 `pblancDate`** 기준 |
| 결과 데이터 | 공고키별 여러 품목 결과를 하나의 결과 상태로 집계 |
| 결과확정 | 유찰, 낙찰, 순위확정 |
| 결과 제외 | 결과 미확인, 판단보류, 기타 검토 필요 |
| 비유찰 처리 | 낙찰·순위확정=0, 유찰=1 |
| 수의계약 제외 | 코드상 `cntrctMth.ne("수의계약")` |
| 주 분석 | 2021~2023년 결과확정 경쟁성 공고 |
| 보조 분석 | 2024년 및 2025년, 별도 민감도 분석 |

**중요:** 공고 시기 분석의 수의계약 필터는 `'수의계약'` **정확 일치** 방식이다. 사업규모금액 분석·후속공고 분석·KPI 일부 코드에서 쓰는 `contains('수의')` 방식과 완전히 동일하지 않다. 계약방법 표기가 더 다양하다면 모집단이 달라질 수 있으므로 재실행 전에 확인한다.

월·분기 집계 CSV에는 `all_notices`(전체 공고), `analysis_n`(해당 분석 모집단), `fail_n`(유찰) 등이 함께 있다. **유찰률 분모는 `analysis_n`이며 `all_notices`가 아니다**. 결과 연결률과 확정률은 별도 지표다.

## 3. 처리 흐름과 산식

`a4_08_logistic_results.csv`는 `logit(유찰) ~ 연도 범주 + 분기 범주`의 보조 모델이다.

- 2021년과 1분기가 기준범주다.
- 동일 `businessKey` 반복 공고에 대해 **사업 단위 군집강건 샌드위치 표준오차**를 계산한다.
- 분기 전체의 `overall_quarter_lrt_p`는 연도만 포함한 축소모형과 연도+분기 모형의 **우도비 검정**이다. 개별 계수의 p값과 검정 방법이 다르다.
- 회귀 최적화 성공 여부 `converged`와 `optimizer_message`를 확인해야 한다.

- 보조 회귀는 인과성 입증이나 위험도 KPI 사전 식별 성능 검증을 대신하지 않는다.

코드는 `a4_11_a1_a3_cohort_reconciliation.csv`를 만들어 **기관·계약방법·사업구분 분석 참조용 개찰일 기준 입찰결과 품목 행**과 **공고 시기 분석 주 분석 공고일 기준 고유 공고**를 따로 보여준다.

- 코드 주석의 기관·계약방법·사업구분 분석 참조 집계 **21,130건**과 공고 시기 분석의 **21,092건**은 **기간 기준·분석 단위가 다르다**.

- 표본 비교는 공고일·개찰일과 공고·품목행이라는 단위 차이를 설명하기 위한 참고 자료다.

## 4. 실행 방법

프로젝트 루트 PowerShell:

```powershell
cd C:\workspace\defense-procurement-risk-dashboard
python -m pip install -r requirements.txt
python scripts/analysis/04_bid_timing_analysis.py
```

그래프를 생략하고 CSV만 생성하려면:

```powershell
python scripts/analysis/04_bid_timing_analysis.py --skip-plots
```

다른 CLEAN 파일 경로를 사용할 경우 `--notice`, `--result`, `--output` 옵션을 사용할 수 있다.

코드는 지정된 출력 경로에 같은 이름의 CSV·PNG를 **덮어쓴다**. 재실행 전에 로컬 결과물과 Git 변경사항을 점검한다. 결과를 계산한 뒤 입력 CSV SHA-256·실행 시각 등을 `a4_run_metadata.json`에 저장한다.

## 5. 출력 파일

기본 출력 위치: `data/analysis/bid_timing/`.

| 파일 | 내용 |
|---|---|
| `a4_01_data_quality.csv` | 공고연도별 전체 공고·직접매칭·확정 건수 |
| `a4_02_population_audit.csv` | 수의계약 제외 전후 분석 모집단 |
| `a4_03_yearly_summary.csv` | 연도별 공고 수·유찰 건수·유찰률 |
| `a4_04_monthly_summary.csv` | 월별 유찰률 및 연결 상태 |
| `a4_05_quarterly_summary.csv` | 분기별 유찰률 및 연결 상태 |
| `a4_06_chi_square_results.csv` | 주 분석 및 연도별 카이제곱 결과 |
| `a4_07_yearly_pattern.csv` | 연도 내 최고 유찰률 분기 순위 |
| `a4_08_logistic_results.csv` | 연도 통제 로지스틱 회귀·군집강건 표준오차 |
| `a4_09_sensitivity.csv` | 수의계약 포함 여부·기간별 민감도 |
| `a4_10_validation.csv` | 원천 건수와 결과 상태의 감사 기준 대조 |
| `a4_11_a1_a3_cohort_reconciliation.csv` | 기관·계약방법·사업구분 분석 참조 품목행과 공고 시기 분석 공고행 비교 |
| `a4_invalid_dates.csv` | 공고일 파싱 오류 공고 |
| `a4_run_metadata.json` | 입력 파일 해시·실행 조건·생성시각 |
| `a4_annual_rate.png` 등 6개 PNG | 연간·월간·분기·히트맵·신뢰구간·결과확정 비율 시각화 |

`--skip-plots` 실행 시 PNG는 생성되지 않는다.

## 6. 결과 및 해석

| 분기 | 공고 수 | 유찰 건수 | 유찰률 |
|---|---:|---:|---:|
| 1분기 | 6,709 | 1,135 | 16.92% |
| 2분기 | 6,490 | 962 | 14.82% |
| 3분기 | 4,046 | 717 | 17.72% |
| 4분기 | 3,847 | 612 | 15.91% |
| **전체** | **21,092** | **3,426** | **16.24%** |

- 가장 높은 분기: 3분기 **17.72%**; 가장 낮은 분기: 2분기 **14.82%**; 차이 **2.90%p**.

**귀무가설:** 분기와 유찰 여부는 독립. **대립가설:** 독립이 아니다.

|---|---:|
| χ² | 18.6805 |
| 자유도 | 3 |
| p값 | 0.000318 |
| Cramér's V | 0.0298 |
| 최소 기대빈도 | 624.87 |
| 결론 | 유의(귀무가설 기각), 연관 강도 매우 작음 |

코드는 4×2 교차표에 `chi2_contingency(...,correction=False)`를 적용한다. 유의수준 0.05에서 연관성은 있지만 Cramér's V가 매우 작고, 이 결과만으로 분기가 유찰을 **유발**한다고 볼 수 없다.

`a4_06_chi_square_results.csv`에는 주 분석 합계와 **2021, 2022, 2023, 2024 각 연도별** 결과가 저장된다. 별도 `a4_09_sensitivity.csv`는 결과확정 전체(수의계약 포함)와 경쟁성 표본을 비교한다.

| 연도 | 1분기 | 2분기 | 3분기 | 4분기 |
|---|---:|---:|---:|---:|
| 2021 | 20.97% | 17.06% | 18.58% | 11.67% |
| 2022 | 16.84% | 13.18% | 15.84% | 15.01% |
| 2023 | 11.93% | 14.31% | 18.72% | 20.75% |
| 2024 | 20.54% | 24.24% | 24.22% | 18.24% |

## 7. 실행 결과 확인

기본 출력 경로는 `data/analysis/bid_timing/`이며, 실행 시 분석 결과가 이 위치에 저장된다.

```powershell
cd C:\workspace\defense-procurement-risk-dashboard
python scripts/analysis/04_bid_timing_analysis.py
Get-ChildItem data/analysis/bid_timing/
Import-Csv -Encoding UTF8 data/analysis/bid_timing/a4_06_chi_square_results.csv | Format-Table -AutoSize
Import-Csv -Encoding UTF8 data/analysis/bid_timing/a4_05_quarterly_summary.csv | Format-Table -AutoSize
Import-Csv -Encoding UTF8 data/analysis/bid_timing/a4_10_validation.csv | Format-Table -AutoSize
```

결과 검수:
1. `a4_10_validation.csv`에서 원천·상태 건수 기대값 및 불일치 여부 점검.
2. `a4_02_population_audit.csv`와 `a4_05_quarterly_summary.csv`에서 2021~2023년 21,092건/유찰 3,426건 재현.
3. `a4_06_chi_square_results.csv`의 주 분석 χ²=18.6805, p=0.000318, V=0.0298 비교.
4. `a4_08_logistic_results.csv`의 `converged` 확인.

6. 기관·계약방법·사업구분 분석 참조 21,130건과 공고 시기 분석 21,092건의 기간·행단위 차이를 유지.

