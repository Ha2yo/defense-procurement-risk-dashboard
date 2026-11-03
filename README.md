# 🛡️ 군수품 조달 유찰 위험 예측 대시보드

<!-- 대표 이미지: docs/images/ 에 캡처를 넣은 뒤 아래 주석을 해제
<p align="center"><img src="docs/images/risk_prediction.png" width="90%"></p>
-->

## 📝 프로젝트 소개

- 방위사업청 군수품 조달 공공데이터로 **신규 입찰공고의 유찰 위험을 공고 전에 확인**하는 Streamlit 대시보드입니다.
- 위험도는 **기관그룹 × 계약방법 × 사업유형 × 사업규모금액 5분위**가 같은 과거 공고의 유찰률입니다. 같은 조건의 공고가 10건 미만이면 조건을 하나씩 줄여 계산합니다.
- 위험도와 함께 산정 근거와 과거 유사공고를 보여 줘, 담당자가 결과를 직접 확인할 수 있습니다.

<p align="center"><a href="https://defense-procurement-risk.streamlit.app/"><img src="https://img.shields.io/badge/🚀_대시보드_바로가기-FF4B4B?style=for-the-badge"></a>
<a href="https://drive.google.com/file/d/1QegxA6lLpeckYm1Xr1H8lE8EkgLv5mJu/view?usp=sharing"><img src="https://img.shields.io/badge/🎬_시연_영상-555555?style=for-the-badge"></a></p>

---

## 📊 주요 결과

| 지표 | 결과 |
|---|---|
| 전체 유찰률 | 18.39% (6,269 / 34,097건) |
| KPI 1 · 위험도 상위 20% 사전 식별률 | 2024년 34.99% · 2025년 35.53% |
| KPI 3 · 공고 1건 과거 이력 확인 시간 | 3분 6초 → 37.66초 |
| 유찰 후 재공고까지 추가 소요일 | 평균 15.39일 (중앙값 12일, 2,090건) |

---

## ⏳ 데이터

- **출처:** 방위사업청 군수품 조달 공공데이터 (OPEN API)
- **범위:** 조달계획 · 입찰공고 · 입찰결과 · 품목명세서, 2021~2025년
- `data/raw/`, `data/clean/`은 커밋하지 않습니다. 대시보드는 `data/dashboard/`의 parquet만으로 실행됩니다.
- 데이터를 새로 수집할 때만 프로젝트 루트에 `.env` 파일이 필요합니다.
  ```
  API_KEY_DATA_PORTAL=...
  ```
---

## 🗂️ 프로젝트 구조

- [`app.py`](app.py) — 대시보드 진입점
- [`scripts/collection/`](scripts/collection) — ① 데이터 수집 (OPEN API)
- [`scripts/preprocessing/`](scripts/preprocessing) — ② 정제 및 공고-결과 병합
- [`scripts/ornt/`](scripts/ornt) — ③ 기관그룹 분류
- [`scripts/analysis/`](scripts/analysis) — ④ 분석 계획표 1~5번 (파일명 앞 번호 = 분석 번호)
- [`scripts/kpi/`](scripts/kpi) — ⑤ 공식 4변수 위험도 · KPI 검증
- [`scripts/dashboard/`](scripts/dashboard) — ⑥ 대시보드 화면
- [`data/dashboard/`](data/dashboard) — 대시보드 입력 parquet
- [`data/analysis/`](data/analysis) · [`data/kpi/`](data/kpi) — 분석 · KPI 산출물
- [`docs/`](docs) — 분석별 실행 가이드
- [`archive/`](archive) — 이전 기준 코드 (보관용, 기본 실행 제외)

<details>
<summary>폴더 트리 펼치기</summary>

```
├─ app.py                    대시보드 진입점
├─ requirements.txt
├─ scripts/                  실행 순서 = 폴더 순서
│  ├─ collection/            ① 데이터 수집 (OPEN API)
│  ├─ preprocessing/         ② 조달계획·공고·결과·품목명세 정제 및 공고-결과 병합
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
└─ archive/                  이전 기준 코드 (보관용, 기본 실행 제외)
   └─ contract/              계약정보 수집·요구연도 복원 코드 (현행 분석 범위 밖)
```

</details>

---

## 🛠️ 설치 및 실행

1. 레포지토리 복제
   ```bash
   git clone https://github.com/Ha2yo/defense-procurement-risk-dashboard.git
   cd defense-procurement-risk-dashboard
   ```
2. 패키지 설치
   ```bash
   pip install -r requirements.txt
   ```
3. 대시보드 실행 (프로젝트 루트에서)
   ```bash
   streamlit run app.py
   ```

분석 스크립트는 **프로젝트 루트에서** `python scripts/...` 형태로 실행합니다. 전체 순서는 아래 [📚 상세 문서](#-상세-문서)를 참고하세요.

---

## 👨🏻‍💻 대시보드 사용법

| 페이지 | 내용 |
|---|---|
| 1. 전체 유찰 현황 | 핵심 지표, 연도별 유찰률 추이, 기관그룹·계약방법·사업유형·금액별 공고·유찰 분포 |
| 2. 공고별 위험도·유사사례 | 2025년 공고 선택 또는 조건 직접 입력 → 위험 등급(1~10), 산정 근거, 과거 유사공고 |
| 3. 유찰 요인 분석 | 6개 변수 검정 요약, 변수별 범주 유찰률과 교차표 |
| 4. 유찰 후 지연 영향 | 후속공고 현황, 재공고까지 추가 소요일 분포 |

<!-- 페이지별 캡처: docs/images/ 에 넣은 뒤 주석 해제
<img src="docs/images/overview.png" width="49%"> <img src="docs/images/risk_prediction.png" width="49%">
<img src="docs/images/failure_factors.png" width="49%"> <img src="docs/images/delay_effect.png" width="49%">
-->

---

## 🌐 배포

- 대시보드: [https://defense-procurement-risk.streamlit.app/](https://defense-procurement-risk.streamlit.app/) (Streamlit Community Cloud)
- 시연 영상: [https://drive.google.com/file/d/1QegxA6lLpeckYm1Xr1H8lE8EkgLv5mJu/view?usp=sharing](https://drive.google.com/file/d/1QegxA6lLpeckYm1Xr1H8lE8EkgLv5mJu/view?usp=sharing)

---

## 📚 상세 문서

<details>
<summary><b>실행 순서 ①~⑥ 펼치기</b></summary>

프로젝트 루트에서 `python scripts/<폴더>/<파일>` 형태로 현재 사용 중인 파일을 표의 위에서부터 순서대로 실행한다. 결과 경로는 `data/` 아래 기준이며, **계약정보 수집·정제·복원 단계는 이 실행 순서에 포함되지 않는다.**

### ① 데이터 수집 — `scripts/collection/`
| 파일 | 내용 → 결과 |
|---|---|
| `collect_data_plan.py` | 조달계획 → `raw/` |
| `collect_data_list.py` | 입찰공고·입찰결과 → `raw/` |
| `collect_data_item.py` | 품목명세서 → `raw/` |

### ② 정제·병합 — `scripts/preprocessing/`
| 파일 | 내용 → 결과 |
|---|---|
| `raw_to_clean.py` | 조달계획·입찰공고·입찰결과·품목명세 RAW → CLEAN (`clean/`). 현행 프로젝트에서는 계약정보 정제 옵션을 사용하지 않음 |
| `merge_bid_data.py` | 입찰공고·입찰결과를 공고키로 병합, 공고 단위 집계, 판단보류 제외 → `kpi/bid_master_2021_2025.csv` |
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
| 사전 점검: 연도별 결과 미확인 ([가이드](docs/00_unconfirmed_by_year_guide.md)) | `00_unconfirmed_by_year.py` → `analysis/unconfirmed_by_year.csv` |
| 1. 기관·계약방법·사업구분 ([가이드](docs/01_bid_conditions_guide.md)) | `01_bid_conditions_prepare.py` → `01_bid_conditions_analysis.py` → `analysis/results/` |
| 2. 공고기간 ([가이드](docs/02_bid_period_guide.md)) | `02_bid_period_preprocess.py` → `02_bid_period_analysis.py` → `02_bid_period_year_compare.py` → `analysis/bid_period/` |
| 3. 금액 구간 (사업규모금액 5분위) ([가이드](docs/03_bid_amount_guide.md)) | `03_bid_amount_analysis.py` → `analysis/bid_amount/` |
| 4. 공고 시기 ([가이드](docs/04_bid_timing_guide.md)) | `04_bid_timing_analysis.py` → `analysis/bid_timing/` |
| 5. 유찰 후 재공고 ([가이드](docs/05_rebid_followup_guide.md)) | `05_rebid_followup_analysis.py` → `analysis/rebid_followup/` (CSV 14개·PNG 7개) |

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
입력은 `data/dashboard/`의 현행 대시보드용 parquet(**계약정보 parquet 제외**)와 `data/analysis/ornt_code/orntCode_codeVld4_all.csv`이다.

**계약정보 사용 범위:** 현재 KPI와 대시보드 화면 1~4에서는 `contract_info` 데이터셋을 사용하지 않는다. `data/dashboard/contract_info.parquet`가 남아 있더라도 **현행 입력 데이터로 취급하지 않는다.** 기존 계약정보 RAW·CLEAN 및 `data/analysis/contract_year_independent/`의 과거 산출물은 이번 README 정리만으로 삭제하거나 변경하지 않는다. 불필요한 파일 제거 여부는 참조 코드·Git 추적 상태를 검증한 뒤 별도 작업으로 결정한다.

</details>

<details>
<summary><b>분석 기준 요약 펼치기</b></summary>

- **분석 단위**: 공고키(`demandYear + orntCode + dcsNo + pblancNo + pblancOdr`) 기준 공고 1건
- **결과 변수**: 유찰 = 1, 낙찰·순위확정 = 0. 결과 미확인·판단보류·수의계약은 제외
- **데이터 범위**: 계약정보(`contract_info`) 미사용. 입찰공고의 계약방법(`cntrctMth`)은 분석 변수로 유지
- **기간 구분**: 공고일(`pblancDate`) 기준 — 학습 2021~2023 / 예비 검증 2024 / 최종 평가 2025
- **기초금액(`bsicExpt`)은 위험도에 사용하지 않음** (단가제 공고에 단가 입력 혼입) → 조달계획 예산·품목 총액으로 산정한 사업규모금액 사용
- 상세 정의는 데이터 항목정의서·KPI 정의서를 따른다.

</details>

<details>
<summary><b>archive/ (보관용) 펼치기</b></summary>

아래 파일·폴더는 **현재 수집→전처리→분석→KPI→대시보드 실행 경로에 포함되지 않는다.** 별도 과거 연구를 재현할 때는 코드와 입력 경로를 다시 확인한다.

| 경로 | 내용 |
|---|---|
| `archive/contract/collect_data_contract.py` | 이전 계약정보 API 수집 코드. 현행 데이터 수집 대상에서 제외 |
| `archive/contract/restore_contract_demand_year.py` | 이전 계약정보 요구연도 복원·계약 후보 연결 코드. 현행 전처리·KPI·대시보드에서는 미사용 |
| `archive/kpi_3var/kpi_12_2024.py` | 이전 3변수(기관그룹×계약방법×사업구분) KPI. 4변수 비교 기준선 확인용 |
| `archive/kpi1_statistical/` | 이전 KPI 1 동점 처리 통계 분석 ([가이드](docs/kpi1_statistical_guide.md)) |
| `archive/notebooks/` | 초기 위험도 분석 설명 노트북 |
| `archive/amount_validation/` | 03번과 같은 수치를 내는 이전 금액 검증 코드. 03번에 없는 입찰방법 교차검증표(06) 확인용 |

</details>

---

## 🤝 협업 규칙

- git 사용 설명서: https://app.notion.com/p/3f1830b2d9f78025875de5f35e33ee2d
- main에는 PR로만 합친다. 커밋 전 `git status`로 의도하지 않은 파일이 없는지 확인한다.
