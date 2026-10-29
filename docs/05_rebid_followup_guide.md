# 05. 유찰 이후 후속공고 및 추가 소요일 분석 — 실행 가이드

## 1. 개요

**유찰이 확정된 경쟁성 원공고**가 이후 어떤 공고로 연결되는지, 관측된 후속 재공고 개찰일까지 며칠이 걸렸는지를 분석한다. 이미 유찰된 공고의 **사후 경과 분석**으로, 신규 공고의 사전 유찰 예측 모델이나 지연 절감 효과 측정이 아니다.

- **공식 코드:** [`scripts/analysis/05_rebid_followup_analysis.py`](../scripts/analysis/05_rebid_followup_analysis.py)
- **공식 결과 폴더:** [`data/analysis/rebid_followup/`](../data/analysis/rebid_followup/)
- **코드 기본 출력:** `OUTPUT_DIR = PROJECT_ROOT / "data" / "analysis" / "rebid_followup"`
- **분석 기간·관측 종료일:** 2021-01-01~2025-12-31, 원공고일 `pblancDate` 기준. 후속공고 게시일과 결과·개찰일의 관측 종료 조건을 별도로 확인한다.
- **현행 결과 구조:** CSV **14개**(요약 6, 상세 1, 감사 7), PNG **7개**. `--debug` 사용 시 사업공고 이력 CSV가 추가될 수 있다.
- 이전 `analysis/statistical/05_rebid_followup_analysis.py`와 `data/analysis/a5_output_csv_png/`는 **과거 작업 경로**이며, 현재 공식 GitHub 경로는 위 두 경로다.

## 2. 입력 데이터

`data/clean/`의 다음 접두사에 해당하는 CSV를 각각 하나씩 읽는다.

| 입력 | 필수 항목 및 역할 |
|---|---|
| `bid_notice_clean_*.csv` | `noticeKey`, `businessKey`, `pblancDate`, `opengDt`, `pblancSe`, `pblancOdr`, `cntrctMth`; 후속 후보 연결·시점 판단 |
| `bid_result_clean_*.csv` | `noticeKey`, `businessKey`, `bidResult`; 공고키별 결과확정·판단보류 판정 |

- **원공고 분석 단위:** `noticeKey` 기준 1건. 결과가 품목별 여러 행이면 먼저 공고키별 상태로 집계한다.
- 유찰과 다른 결과가 공존하면 `판단보류`; 유찰만 있으면 `유찰`, 낙찰·순위확정은 결과확정 비유찰이다.
- **공식 원공고:** 공고일이 2021~2025년이고 결과가 `유찰`이며 **계약방법 문자열에 `수의`가 포함되지 않는** 공고.
- **후속 후보:** 동일 `businessKey`의 유효 공고를 탐색하되, 유찰 이후 **수의계약으로 전환된 후보도 허용**한다. 취소공고는 후보에서 제외한다.
- 조달계획·품목명세서·계약정보 CSV는 이 후속공고 분석 코드의 기본 입력이 아니다.

## 3. 처리 흐름과 산식

1. 동일 사업키의 공고를 개찰시각·게시일·공고차수·공고키 순으로 정렬한다.
2. 원공고 개찰시각보다 뒤에 있는 후속 후보를 검색한다. 단순 `shift(-1)`만으로 공식 연결을 결정하지 않는다.
3. 동일 개찰시각, 동일 게시일, 시간 역전 등 모호한 후보는 **별도 감사 자료**로 보존한다. 공식 방식은 개찰일과 같은 날짜에 게시된 후보를 허용할 수 있어 시간순서 불확실성이 존재한다.
4. 관측 종료일 2025-12-31 이후에 게시된 공고는 후속 연결에서 제외한다. 개찰일이 관측 종료일을 넘는 경우의 결과·소요일 처리도 별도로 관리한다.
5. 후속공고의 `pblancSe == "재공고"`를 이용해 재공고 여부를 판정하고, 결과가 `유찰`·`낙찰`·`순위확정` 중 하나인지 결과확정 여부를 구분한다.
6. **공식 추가 소요일**은 '결과확정 후속 재공고'의 개찰일과 원공고 개찰일의 **달력 일수 차이**로 계산한다. 후속공고 전체를 기준으로 한 소요일과 표본을 구분한다.
7. **보수적 연결:** 원공고 개찰일 **다음 날 이후** 게시된 후보만 인정하며, 동일 게시일 후보가 연결됐던 경우 유효한 다음 후보를 **다시 탐색**한다. 공식 결과를 덮어쓰지 않고 민감도로 보관한다.

**주의:** `후속공고 미확인`은 관측 종료일까지 코드 기준으로 연결되지 않았다는 뜻이지 실제 재공고가 없었다는 확정 판정이 아니다.

### 관측기간별 연결·소요일

| 구간 | 원공고 | 후속공고 확인 | 연결률 | 공식 소요일 표본 | 평균 |
|---|---:|---:|---:|---:|---:|
| 2021~2025 전체 | 6,269 | 2,577 | 41.11% | 2,090 | 15.39일 |
| 2025년 4분기 제외 | 6,020 | 2,498 | 41.50% | 2,024 | 15.49일 |
| 90일 완전 관측 가능 | 5,996 | 2,488 | 41.49% | 2,017 | 15.50일 |

### 실제 90일 이내 연결

출처: [`a5_summary_03_90일관측.csv`](../data/analysis/rebid_followup/a5_summary_03_90일관측.csv).

| 지표 | 분모 | 확인 | 연결률 |
|---|---:|---:|---:|
| 90일 관측 가능 집단의 전체기간 연결률 | 5,996 | 2,488 | 41.49% |
| 동일 집단의 **실제 90일 이내** 후속공고 연결률 | 5,996 | 2,483 | 41.41% |

90일을 온전히 관측할 수 있는 집단과 그중 실제 90일 안에 연결된 집단을 혼동하지 않는다.

### 연결 기준 민감도

출처: [`a5_summary_02_보수적민감도.csv`](../data/analysis/rebid_followup/a5_summary_02_보수적민감도.csv).

| 기준 | 원공고 | 연결 | 연결률 | 결과확정 재공고 소요일 표본 | 평균 소요일 |
|---|---:|---:|---:|---:|---:|
| 공식: 동일 날짜 게시 후보 허용 | 6,269 | 2,577 | 41.11% | 2,090 | 15.39일 |
| 보수적: 개찰일 다음 날 이후 게시 후보 | 6,269 | 1,807 | 28.82% | 1,472 | 19.11일 |

두 방법의 소요일 분석 **표본이 다르므로**, 평균 차이를 기간 절감 효과 또는 인과적 영향으로 해석하지 않는다.

## 4. 실행 방법

프로젝트 루트에서:

```powershell
cd C:\workspace\defense-procurement-risk-dashboard
python -m pip install -r requirements.txt
python scripts/analysis/05_rebid_followup_analysis.py
```

입력·출력 폴더가 다른 경우:

```powershell
python scripts/analysis/05_rebid_followup_analysis.py --data-dir "C:\경로\clean" --output-dir "C:\경로\a5_test"
```

추가 사업별 이력 저장이 필요할 때만 `--debug`를 사용한다. **동일한 출력 폴더에서 재실행하면 기존 CSV·PNG가 갱신될 수 있으므로**, 변경사항을 비교하려면 별도 `--output-dir`에 실행한다.

## 5. 출력 파일

### 요약 CSV 6개

| 파일 | 사용 목적 |
|---|---|
| [`a5_summary_01_핵심요약.csv`](../data/analysis/rebid_followup/a5_summary_01_핵심요약.csv) | 공식 연결·결과확정·전체/공식 소요일 및 관측 구간 비교 |
| [`a5_summary_02_보수적민감도.csv`](../data/analysis/rebid_followup/a5_summary_02_보수적민감도.csv) | 동일 날짜 허용 vs 다음 날 게시 기준 |
| [`a5_summary_03_90일관측.csv`](../data/analysis/rebid_followup/a5_summary_03_90일관측.csv) | 관측 가능 집단과 실제 90일 연결 |
| [`a5_summary_04_사업구분.csv`](../data/analysis/rebid_followup/a5_summary_04_사업구분.csv) | 물품·용역별 연결 및 소요일 |
| [`a5_summary_05_계약방법.csv`](../data/analysis/rebid_followup/a5_summary_05_계약방법.csv) | 원공고 계약방법별 집계 |
| [`a5_summary_06_기관별.csv`](../data/analysis/rebid_followup/a5_summary_06_기관별.csv) | 기관코드별 집계 |

### 상세 CSV 1개

| 파일 | 사용 목적 |
|---|---|
| [`a5_followup_detail.csv`](../data/analysis/rebid_followup/a5_followup_detail.csv) | 공식 경쟁성 유찰 원공고 **6,269행**, 후속 후보·연결 상태·기간·보수적 후보의 공고 단위 검증 |

### 감사 CSV 7개

`a5_audit_01_공식검증.csv` ~ `a5_audit_07_검증건수요약.csv` (각 파일의 실제 이름 및 용도는 위 8절 참조).

### PNG 7개

| 파일 | 시각화 |
|---|---|
| `01_followup_stages.png` | 원공고 → 후속공고 → 후속 재공고 → 결과확정 재공고 |
| `02_link_rate_sensitivity.png` | 공식·보수적 연결률 |
| `03_duration_sensitivity.png` | 공식·보수적 평균 소요일 |
| `04_duration_histogram.png` | 공식 소요일 히스토그램 |
| `05_duration_ecdf.png` | 공식 소요일 누적분포(ECDF) |
| `06_by_business_type.png` | 사업구분별 연결률 |
| `07_observation_90day.png` | 관측 가능 집단과 실제 90일 연결률 |

## 6. 결과 및 해석

출처: [`a5_audit_02_계약방식모집단.csv`](../data/analysis/rebid_followup/a5_audit_02_계약방식모집단.csv), [`a5_audit_07_검증건수요약.csv`](../data/analysis/rebid_followup/a5_audit_07_검증건수요약.csv), [`a5_summary_01_핵심요약.csv`](../data/analysis/rebid_followup/a5_summary_01_핵심요약.csv).

| 구분 | 공고 수 |
|---|---:|
| CLEAN 전체 유찰 공고 | 9,845 |
| 분석기간(2021~2025년) 밖 유찰 | 9 |
| 분석기간 내 유찰(수의계약 포함) | 9,836 |
| 분석기간 내 수의계약 유찰 제외 | 3,567 |
| **공식 경쟁성 유찰 원공고** | **6,269** |

`9,845 − 9 = 9,836`, `9,836 − 3,567 = 6,269`. 구 기준의 '유찰 9,836건'은 **수의계약 포함한 기간 내 유찰**이고, 최종 후속공고 분석 공식 연결률의 분모가 아니다.

출처: [`a5_summary_01_핵심요약.csv`](../data/analysis/rebid_followup/a5_summary_01_핵심요약.csv).

| 지표 | 2021~2025년 전체 |
|---|---:|
| 추적 대상 경쟁성 유찰 원공고 | 6,269건 |
| 후속공고 확인 | 2,577건 |
| 후속공고 미확인 | 3,692건 |
| **공식 후속공고 연결률** | **41.11%** |
| 후속 재공고 | 2,560건 |
| 후속공고 결과확정 | 2,103건 |
| 후속공고 결과 미확정·기타 | 474건 |
| **결과확정 후속 재공고** | **2,090건** |
| 전체 연결 후속공고 개찰일 간격 | 평균 15.01일 / 중앙값 12일 / Q1 7일 / Q3 19일 (n=2,577) |
| **공식 결과확정 후속 재공고 개찰일 간격** | **평균 15.39일 / 중앙값 12일 / Q1 7일 / Q3 20일 (n=2,090)** |

수치 관계: `2,577 + 3,692 = 6,269`, `2,103 + 474 = 2,577`. 원공고 6,269건 중 후속공고가 없거나 후속 결과가 확정되지 않은 건은 `3,692 + 474 = 4,166건`이다. 하지만 **미확인·미확정의 원인은 단정하지 않는다**.

## 7. 실행 결과 확인

- [`a5_audit_01_공식검증.csv`](../data/analysis/rebid_followup/a5_audit_01_공식검증.csv): 경쟁성 원공고 키 유일성, 후속 확인/미확인 합계, 재공고 부분집합 관계, 소요일 유효성 등 공식 검증 **9항목**. 현재 산출물의 검증 결과 모두 참인지 확인한다.
- [`a5_audit_02_계약방식모집단.csv`](../data/analysis/rebid_followup/a5_audit_02_계약방식모집단.csv): 공식 모집단과 수의계약 제외 과정.
- [`a5_audit_03_시간순서후보.csv`](../data/analysis/rebid_followup/a5_audit_03_시간순서후보.csv): 게시·개찰 시간순서 모호 후보의 원시 감사행.
- [`a5_audit_04_동일개찰시각후보.csv`](../data/analysis/rebid_followup/a5_audit_04_동일개찰시각후보.csv): 동일 개찰시각 후보 감사행.
- [`a5_audit_05_관측종료후보.csv`](../data/analysis/rebid_followup/a5_audit_05_관측종료후보.csv): 관측 종료 조건 감사행.
- [`a5_audit_06_기간외유찰.csv`](../data/analysis/rebid_followup/a5_audit_06_기간외유찰.csv): 분석기간 외 유찰 공고.
- [`a5_audit_07_검증건수요약.csv`](../data/analysis/rebid_followup/a5_audit_07_검증건수요약.csv): 공식원공고 6,269건, 공식연결 2,577건, 미연결 3,692건, 공식소요일 2,090건, 동일게시일검토 **812건**, 동일개찰시각원공고 **140건**, 시간역전원공고 **176건**, 보수적연결 **1,807건**.

**주의:** 감사 파일의 **후보 행 수**와 `a5_audit_07`의 **원공고별 검토 건수**는 서로 다른 집계 단위일 수 있다. 감사 대상 수를 연결률 분모·분자에서 추가로 가감하지 않는다.

```powershell
cd C:\workspace\defense-procurement-risk-dashboard

# 커밋된 결과물을 덮지 않기 위해 별도 폴더에 실행
python scripts/analysis/05_rebid_followup_analysis.py --output-dir data/analysis/a5_verify_run

# 공식 요약
Import-Csv -Encoding UTF8 data/analysis/a5_verify_run/a5_summary_01_핵심요약.csv | Format-Table -AutoSize

# 보수적 연결
Import-Csv -Encoding UTF8 data/analysis/a5_verify_run/a5_summary_02_보수적민감도.csv | Format-Table -AutoSize

# 감사 검증
Import-Csv -Encoding UTF8 data/analysis/a5_verify_run/a5_audit_01_공식검증.csv | Format-Table -AutoSize

# 기존 GitHub 결과와 비교
git status
```

실행 결과의 핵심 기준:
1. 분석기간 내 수의계약 제외 결과 원공고 **6,269건**, 공고키 중복 0건.
2. 후속공고 **2,577건**, 미확인 **3,692건**, 결과확정 재공고 **2,090건**.
3. 공식 소요일 **15.39일(n=2,090)**, 전체 연결 후속 소요일 **15.01일(n=2,577)** 구분.
4. 보수적 **1,807건/28.82%**, 공식 **2,577건/41.11%** 비교.
5. 90일 완전 관측 집단 **5,996건**, 실제 90일 이내 연결 **2,483건**.
6. 공식 검증 9항목 모두 `True`인 경우에도 원천 데이터의 모든 연결 오류가 없다는 의미는 아님.

