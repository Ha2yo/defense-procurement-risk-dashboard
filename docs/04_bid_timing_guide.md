# 04. 공고 시기별 유찰률 분석 — 실행 및 검증 가이드

## 1. 분석 목적과 근거 수준

공고일 `pblancDate` 기준으로 월·분기·연도별 공고 수 및 유찰률을 비교하고, **2021~2023년 결과확정 경쟁성 공고**에서 분기와 유찰 여부의 독립성을 카이제곱 검정한다. 2024년은 비교, 2025년은 보조 추세로 다룬다.

- **실행 코드:** [`scripts/analysis/04_bid_timing_analysis.py`](../scripts/analysis/04_bid_timing_analysis.py)
- **예상 출력:** `data/analysis/bid_timing/`
- **입력:** `data/clean/bid_notice_clean_20210101_20251231.csv`와 `bid_result_clean_20210101_20251231.csv`
- **검증 상태:** 현재 GitHub `main`에는 `data/analysis/bid_timing/` 결과 CSV·PNG가 **커밋되어 있지 않음**. 아래 수치는 사용자가 제공한 **`분석 계획표_v2(4).pdf` 4절의 문서 기재값**이며, 코드 구현과 지표 정의를 대조한 것이다. A4 출력 CSV와 독립적으로 수치 검증한 결과가 아니다.

## 2. 실행 환경 및 명령어

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

## 3. 모집단·변수·제외 조건

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

**중요:** A4의 수의계약 필터는 `'수의계약'` **정확 일치** 방식이다. A3·A5·KPI 일부 코드에서 쓰는 `contains('수의')` 방식과 완전히 동일하지 않다. 계약방법 표기가 더 다양하다면 모집단이 달라질 수 있으므로 재실행 전에 확인한다.

월·분기 집계 CSV에는 `all_notices`(전체 공고), `analysis_n`(해당 분석 모집단), `fail_n`(유찰) 등이 함께 있다. **유찰률 분모는 `analysis_n`이며 `all_notices`가 아니다**. 결과 연결률과 확정률은 별도 지표다.

## 4. 분석 계획표에 기록된 공식 주 분석 결과

**아래 값은 PDF 기재값으로서 A4 CSV 재현 확인 전까지 잠정 검증 상태이다.**

| 분기 | 공고 수 | 유찰 건수 | 유찰률 |
|---|---:|---:|---:|
| 1분기 | 6,709 | 1,135 | 16.92% |
| 2분기 | 6,490 | 962 | 14.82% |
| 3분기 | 4,046 | 717 | 17.72% |
| 4분기 | 3,847 | 612 | 15.91% |
| **전체** | **21,092** | **3,426** | **16.24%** |

- 가장 높은 분기: 3분기 **17.72%**; 가장 낮은 분기: 2분기 **14.82%**; 차이 **2.90%p**.
- 위 표와 기간별 모집단은 A1 공고 단위 학습 분석 21,092건과 일치하도록 문서에 기록돼 있다. 단 이는 별도 A4 실행 산출물의 확인을 대체하지 않는다.

## 5. 카이제곱 독립성 검정

**귀무가설:** 분기와 유찰 여부는 독립. **대립가설:** 독립이 아니다.

| 통계 | PDF 기재값 |
|---|---:|
| χ² | 18.6805 |
| 자유도 | 3 |
| p값 | 0.000318 |
| Cramér's V | 0.0298 |
| 최소 기대빈도 | 624.87 |
| 결론 | 유의(귀무가설 기각), 연관 강도 매우 작음 |

코드는 4×2 교차표에 `chi2_contingency(...,correction=False)`를 적용한다. 유의수준 0.05에서 연관성은 있지만 Cramér's V가 매우 작고, 이 결과만으로 분기가 유찰을 **유발**한다고 볼 수 없다.

`a4_06_chi_square_results.csv`에는 주 분석 합계와 **2021, 2022, 2023, 2024 각 연도별** 결과가 저장된다. 별도 `a4_09_sensitivity.csv`는 결과확정 전체(수의계약 포함)와 경쟁성 표본을 비교한다.

## 6. 연도별 분기 유찰률

PDF 4-4절 기재값:

| 연도 | 1분기 | 2분기 | 3분기 | 4분기 |
|---|---:|---:|---:|---:|
| 2021 | 20.97% | 17.06% | 18.58% | 11.67% |
| 2022 | 16.84% | 13.18% | 15.84% | 15.01% |
| 2023 | 11.93% | 14.31% | 18.72% | 20.75% |
| 2024 | 20.54% | 24.24% | 24.22% | 18.24% |

2021·2022년은 1분기, 2023년은 4분기, 2024년은 2분기가 최고 유찰률 분기였다. **연도 간 동일하게 반복되는 최고 위험분기는 확인되지 않았다.** 2025년 최고 분기에 대한 PDF 서술은 있지만 연도별 정확한 표 수치는 이번 검수에서 근거 CSV를 확인하지 못했으므로 추정해 기재하지 않는다.

## 7. 로지스틱 회귀 및 가정·한계

`a4_08_logistic_results.csv`는 `logit(유찰) ~ 연도 범주 + 분기 범주`의 보조 모델이다.

- 2021년과 1분기가 기준범주다.
- 동일 `businessKey` 반복 공고에 대해 **사업 단위 군집강건 샌드위치 표준오차**를 계산한다.
- 분기 전체의 `overall_quarter_lrt_p`는 연도만 포함한 축소모형과 연도+분기 모형의 **우도비 검정**이다. 개별 계수의 p값과 검정 방법이 다르다.
- 회귀 최적화 성공 여부 `converged`와 `optimizer_message`를 확인해야 한다.
- 문서의 A4 주 검정 χ²·p값과 보조 로지스틱 회귀 p값을 혼동하지 않는다.
- 보조 회귀는 인과성 입증이나 위험도 KPI 사전 식별 성능 검증을 대신하지 않는다.

## 8. A1 모집단 비교의 주의점

코드는 `a4_11_a1_a3_cohort_reconciliation.csv`를 만들어 **A1 참조용 개찰일 기준 입찰결과 품목 행**과 **A4 주 분석 공고일 기준 고유 공고**를 따로 보여준다.

- 코드 주석의 A1 참조 집계 **21,130건**과 A4의 **21,092건**은 **기간 기준·분석 단위가 다르다**.
- 서로 합치거나 강제로 같게 수정하면 안 된다.
- README·문서·CSV에 동일한 명칭의 A1이 쓰였는지 확인하고, 참고 비교라는 점을 명시한다.

## 9. 출력 파일 안내

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
| `a4_11_a1_a3_cohort_reconciliation.csv` | A1 참조 품목행과 A4 공고행 비교 |
| `a4_invalid_dates.csv` | 공고일 파싱 오류 공고 |
| `a4_run_metadata.json` | 입력 파일 해시·실행 조건·생성시각 |
| `a4_annual_rate.png` 등 6개 PNG | 연간·월간·분기·히트맵·신뢰구간·결과확정 비율 시각화 |

`--skip-plots` 실행 시 PNG는 생성되지 않는다.

## 10. GitHub 수치 검증 절차

현재 GitHub `main`에는 **A4의 CSV/PNG가 커밋돼 있지 않다**. 원천 CLEAN을 준비한 로컬 환경에서:

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
5. `a4_07_yearly_pattern.csv` 및 히트맵의 최고 분기가 PDF 설명과 일치하는지 확인.
6. A1 참조 21,130건과 A4 21,092건의 기간·행단위 차이를 유지.
7. 차이가 발생하면 PDF를 즉시 수정하지 말고 CLEAN 버전, 수의계약 필터, 결과 상태 분류를 먼저 검토.

**권장 관리:** 팀 정책상 공개 가능하다면 `data/analysis/bid_timing/`의 검증된 산출물을 GitHub에 추가하여 다른 A2·A3·A5와 동일한 재현성 기준으로 관리한다.

## 11. PDF 수정 전 확인사항

- **분석 계획표 4-1~4-4:** PDF 수치를 A4 결과 CSV와 1:1 대조할 것. 현재는 저장 결과가 없어 대조 미완료.
- **데이터 항목 정의서:** 공고일의 연도·월·분기 생성 기준과 `noticeKey` 단위 집계 일치 확인.
- **위험도 입력:** 공고 시기는 기술통계·검정상 분석했더라도 KPI 모델의 공식 입력 4변수 중 하나는 아니다. A4에서 유의하다는 이유만으로 자동으로 위험도에 추가하지 않는다.
- **수의계약 표현:** A4의 정확 일치 필터와 A3 등 '수의' 포함 필터 차이를 검증 전까지 명시적으로 보존한다.
