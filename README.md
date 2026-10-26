# 군수품 조달 유찰 위험 예측 대시보드 구축을 통한 조달 효율성 향상

방위사업청 군수품 조달 공공데이터(조달계획·입찰공고·입찰결과·품목명세서, 2021~2025)를 이용해
신규 입찰공고의 유찰 위험을 사전에 식별하고, 과거 유사 공고를 빠르게 검토할 수 있는 Streamlit 대시보드를 만든다.

기준 문서: 프로젝트 정의서 · 데이터 항목정의서 · KPI 정의서 · 분석 계획표 · 잠재원인 우선순위표 · 연도별 결과 미확인 분포표

---

## 시작 방법

```bash
# 1) 패키지 설치
pip install -r requirements.txt

# 2) 대시보드 실행 (프로젝트 루트에서)
streamlit run app.py
```

데이터를 새로 수집할 때만 프로젝트 루트에 `.env` 파일이 필요하다

```
API_KEY_DATA_PORTAL=...
```

모든 스크립트는 **프로젝트 루트에서** `python scripts/...` 형태로 실행한다

---

## 폴더 구조

```
├─ app.py                    대시보드 진입점
├─ requirements.txt
├─ scripts/                  실행 순서 = 폴더 순서
│  ├─ collection/            ① 데이터 수집 (OPEN API)
│  ├─ preprocessing/         ② 정제·병합·계약 요구연도 복원
│  ├─ ornt/                  ③ 기관그룹 분류
│  ├─ analysis/              ④ 분석 계획표 1~5번 (파일명 앞 번호 = 분석 번호)
│  ├─ kpi/                   ⑤ 분석 계획표 6번: 공식 4변수 위험도·KPI 검증
│  └─ dashboard/             ⑥ 대시보드 화면
├─ data/
│  ├─ raw/  clean/           원본·정제 데이터 (커밋하지 않음)
│  ├─ dashboard/             대시보드 입력 parquet
│  ├─ analysis/              분석 산출물 (기관 분류표, 공고기간·금액·후속공고 결과 등)
│  └─ kpi/                   KPI 산출물
├─ docs/                     분석별 실행 가이드
└─ archive/                  이전 기준 코드 (보관용, 현재 기준으로 실행하지 않음)
```

---

## 실행 순서

프로젝트 루트에서 `python scripts/<폴더>/<파일>` 형태로, 표의 위에서부터 순서대로 실행한다. 결과 경로는 `data/` 아래 기준이다.

### ① 데이터 수집 — `scripts/collection/`
| 파일 | 내용 → 결과 |
|---|---|
| `collect_data_plan.py` | 조달계획 → `raw/` |
| `collect_data_list.py` | 입찰공고·입찰결과 → `raw/` |
| `collect_data_contract.py` | 계약정보(별도 계약 연결 검증 시에만 수집) → `raw/` |
| `collect_data_item.py` | 품목명세서 → `raw/` |

### ② 정제·병합 — `scripts/preprocessing/`
| 파일 | 내용 → 결과 |
|---|---|
| `raw_to_clean.py` | 계획·공고·결과·품목명세 RAW → CLEAN. 계약정보는 기본 제외 (`INCLUDE_CONTRACT=1`에서만 별도 정제) → `clean/` |
| `merge_bid_data.py` | 입찰공고·입찰결과를 공고키로 병합, 공고 단위 집계, 판단보류 제외 → `kpi/bid_master_2021_2025.csv` |
| `restore_contract_demand_year.py` | **후속 검증 전용**: 계약정보 요구연도 복원 및 계약 후보 연결 → `analysis/contract_year_independent/` (KPI·화면에서는 사용하지 않음) |
| `check_item_amount.py` | 품목명세서 단가×수량과 총금액 일치 검증 (데이터 항목정의서 6.3) → `analysis/item_amount_check/` |

### ③ 기관그룹 분류 — `scripts/ornt/`
| 파일 | 내용 → 결과 |
|---|---|
| `orntCode_unique.py` | 기관코드 기준 기관 목록(226개) → `analysis/ornt_code/` |
| `compare_ornt_codeVld4.py` | **기관그룹 분류 기준표** `orntCode_codeVld4_all.csv` (품목명세서 소요군 분포를 근거로 집계, `data/raw/` 품목명세서 필요) → `analysis/ornt_code/` |
| `collect_ornt_item_sample.py` | (참고) 기관별 공고 1건의 품목명세서 표본 수집 → `analysis/ornt_code/` |

### ④ 분석 — `scripts/analysis/` (파일명 앞 번호 = 분석 계획표 번호)
| 분석 계획표 | 파일 (실행 순서) → 결과 |
|---|---|
| 사전 점검: 연도별 결과 미확인 | `00_unconfirmed_by_year.py` → `analysis/unconfirmed_by_year.csv` |
| 1. 기관·계약방법·사업구분 | `01_bid_conditions_prepare.py` → `01_bid_conditions_analysis.py` → `analysis/results/` |
| 2. 공고기간 | `02_bid_period_preprocess.py` → `02_bid_period_analysis.py` → `02_bid_period_year_compare.py` → `analysis/bid_period/` |
| 3. 금액 구간 (사업규모금액 5분위) | `03_bid_amount_analysis.py` → `analysis/bid_amount/` |
| 4. 공고 시기 | `04_bid_timing_analysis.py` → `analysis/bid_timing/` |
| 5. 유찰 후 재공고 ([가이드](docs/05_rebid_followup_guide.md)) | `05_rebid_followup_analysis.py` → `analysis/a5_output_project/` (요약: `analysis/rebid_followup/`) |

### ⑤ 위험도·KPI (분석 계획표 6번) — `scripts/kpi/`
공식 위험도 = 2021~2023년 결과확정 경쟁성 공고의 **기관그룹 × 계약방법 × 사업구분 × 사업규모금액 5분위** 조합별 유찰률
(10건 미만이거나 금액 미확인이면 상위 조합으로 대체). 2024년 예비 검증, 2025년 최종 평가.

| 파일 | 내용 → 결과 |
|---|---|
| `kpi_2024_validation.py` | 공식 4변수 위험도 산정, 2024년 KPI 1(상위 20% 사전 식별률)·KPI 2(10분위 스피어만) → `kpi/kpi_validation/` |
| `kpi_rolling_pattern_2022_2025.py` | 반기·연간 안정성, 동점 민감도 → `kpi/rolling_validation/` |
| `kpi_statistical_validation.py` | 사업키 군집 부트스트랩 95% 신뢰구간 (위 결과 필요, 실행 옵션 `--reps 2000 --seed 2026`) → `kpi/statistical_validation/` |

### ⑥ 대시보드 — `scripts/dashboard/`
`streamlit run app.py` · 화면: 전체 유찰 현황 / 공고별 위험도·유사사례 / 유찰 요인 분석 / 유찰 후 지연 영향
입력은 `data/dashboard/*.parquet`와 `data/analysis/ornt_code/orntCode_codeVld4_all.csv`.

**계약정보 분리:** KPI 및 화면 1~4는 `contract_info`를 로드하지 않습니다. `data/dashboard/contract_info.parquet`는 현재 화면에서 사용하지 않는 기존 파일로, Git 추적 여부를 확인한 후 별도 커밋에서 제거할 수 있습니다. 기존 계약정보 원본·정제파일과 `restore_contract_demand_year.py`는 후속 분석 재현을 위해 보관합니다.

**계약 연계 연구를 재현하려면** PowerShell에서 `$env:INCLUDE_CONTRACT="1"; python scripts/preprocessing/raw_to_clean.py`를 실행하고, 종료 후 `$env:INCLUDE_CONTRACT="0"`으로 돌립니다.

---

## 분석 기준 요약
- **분석 단위**: 공고키(`demandYear + orntCode + dcsNo + pblancNo + pblancOdr`) 기준 공고 1건
- **결과 변수**: 유찰 = 1, 낙찰·순위확정 = 0. 결과 미확인·판단보류·수의계약은 제외
- **기간 구분**: 공고일(`pblancDate`) 기준 — 학습 2021~2023 / 예비 검증 2024 / 최종 평가 2025
- **기초금액(`bsicExpt`)은 위험도에 사용하지 않음** (단가제 공고에 단가 입력 혼입) → 조달계획 예산·품목 총액으로 산정한 사업규모금액 사용
- 상세 정의는 데이터 항목정의서·KPI 정의서를 따른다.

---

## archive/ (보관용)
| 경로 | 내용 |
|---|---|
| `archive/kpi_3var/kpi_12_2024.py` | 이전 3변수(기관그룹×계약방법×사업구분) KPI. 4변수 비교 기준선 확인용 |
| `archive/kpi1_statistical/` | 이전 KPI 1 동점 처리 통계 분석 ([가이드](docs/kpi1_statistical_guide.md)) |
| `archive/notebooks/` | 초기 위험도 분석 설명 노트북 |
| `archive/amount_validation/` | 03번과 같은 수치를 내는 이전 금액 검증 코드. 03번에 없는 입찰방법 교차검증표(06) 확인용 |

---

## 협업 규칙
- git 사용 설명서: https://app.notion.com/p/3f1830b2d9f78025875de5f35e33ee2d
- main에는 PR로만 합친다. 커밋 전 `git status`로 의도하지 않은 파일이 없는지 확인한다.
