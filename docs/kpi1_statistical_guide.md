# KPI 1 통계 기반 위험도 및 동점 처리 — 실행 안내

> **보관용(archive)**: 현재 공식 모델(4변수, `scripts/kpi/`) 이전의 분석이다. 코드는 `archive/kpi1_statistical/`에 있다.

## 실행할 내용
기존 연구의 **2023년 기준 선정, 2024·2025년 검증** 결과를 재현하는 코드입니다.
머신러닝(CatBoost)은 사용하지 않습니다. Python, pandas, numpy, scipy만 필요합니다.

- 1차: 기관그룹×계약방법×사업구분의 과거 유찰률
- 2차: 기관코드 → 기관코드×공고구분 → 기관코드×공고구분×금액구간 순으로 집단별 유찰률 보정
- 3차: 위험도가 같을 때만 과거 **제출기간 구간 유찰률 → 세부금액구간 유찰률 → 공고키** 순으로 정렬
- 학습 기준: 2023년 검증으로 모델·동점 규칙 선정; 2024·2025년에는 그대로 적용
- 순위확정·결과 미확인·상충 결과·수의계약은 KPI 평가에서 제외

## 1. 필요한 입력 파일

아래 **2개 CLEAN CSV** 파일이 필요합니다. 입찰원본이나 계약정보 CSV는 필요하지 않습니다.

```
bid_notice_clean_20210101_20251231.csv
bid_result_clean_20210101_20251231.csv
```

배치 예시(Windows):

```
C:\code\defense-procurement-risk-dashboard\
├── data\
│   └── clean\
│       ├── bid_notice_clean_20210101_20251231.csv
│       └── bid_result_clean_20210101_20251231.csv
├── archive\
│   └── kpi1_statistical\
│       ├── run_kpi1_revised.py
│       ├── kpi1_statistics_stepwise.py
│       ├── kpi1_statistics_hierarchy.py
│       ├── kpi1_tiebreak_statistics.py
│       └── kpi_C_60day_comparison.py
└── docs\
    └── kpi1_statistical_guide.md   (이 문서)
```

## 2. 설치

프로젝트 루트에서 설치합니다.

```powershell
python -m pip install -r requirements.txt
```

## 3. 실행 (PowerShell, 프로젝트 루트에서)

```powershell
python archive\kpi1_statistical\run_kpi1_revised.py --input-dir "C:\code\defense-procurement-risk-dashboard\data\clean"
```

- `--input-dir`: CLEAN CSV가 있는 폴더(직접 지정 권장)
- `--outdir`: 결과를 저장할 폴더(생략 시 `run_kpi1_revised.py` 옆의 `results` 폴더)

결과 폴더를 따로 지정하고 싶다면:

```powershell
python archive\kpi1_statistical\run_kpi1_revised.py --input-dir "C:\code\defense-procurement-risk-dashboard\data\clean" --outdir "C:\code\defense-procurement-risk-dashboard\data\kpi\statistics_tiebreak"
```

**Windows 참고:** `python`이 다른 환경을 가리킨다면 `conda activate <가상환경명>` 후 실행하고, 설치 역시 동일한 환경에서 해야 합니다.

## 4. 기대 출력 (제공된 CLEAN 데이터와 동일한 경우)

| 평가연도 | 공고 | 유찰 | 상위 20% | 동점 처리 전 | 동점 처리 후 |
|---|---:|---:|---:|---:|---:|
| 2024 | 6,489건 | 1,506건 | 1,298건 | 586건 / 38.91% | **586건 / 38.91%** |
| 2025 | 5,946건 | 1,337건 | 1,189건 | 541건 / 40.46% | **548건 / 40.99%** |

위 값은 제공된 버전의 데이터에서 **기준표 없이 기관명·기관코드 기반 임시 기관그룹**으로 계산한 결과입니다. 프로젝트 기준표가 달라지면 수치가 달라질 수 있습니다.

## 5. 생성되는 파일

- `KPI_summary.csv`: 2023~2025년 핵심 KPI/경계 동점 비교
- `all_tiebreak_methods.csv`: 후보 동점 처리 5종의 KPI 비교
- `2023_ranked_scores.csv`, `2024_ranked_scores.csv`, `2025_ranked_scores.csv`: 공고별 원 위험도, 세부 유찰률, 순위, 상위 20% 여부
- `2023_boundary_ties.csv`, `2024_boundary_ties.csv`, `2025_boundary_ties.csv`: 경계 동점 공고 상세
- `model_settings.json`: 재현용 모델 설정
- `실행결과_보고서.md`: 결과 해석과 한계

## 6. 꼭 알아둘 한계

1. 이 분석은 **결과가 확정된 공고**에서 상위 20%의 실제 유찰 포착률을 구합니다. 전체 공고에서의 성능과 같지 않을 수 있습니다.
2. 결과 확정일이 CLEAN에 없어 `opengDate`를 대용으로 사용합니다. 정보누출 가능성이 완전히 제거된 것은 아닙니다.
3. 2023년 경계 동점 2건 모두 낙찰이어서 동점 처리 규칙 선택은 주로 KPI2 기준에 의존합니다.
4. 2024년 상위 20% 경계 동점 4건은 **모두 포함**되므로, 동점 처리로 그해 KPI1은 증가할 수 없습니다.
5. 2025년 개선은 평가 자료에서의 결과이며 다음 연도의 성능을 보장하지 않습니다.
6. 이 코드는 기존 프로젝트 파일이나 CLEAN 데이터를 **수정하지 않습니다.** 새 결과 파일만 지정된 outdir에 생성합니다. 같은 outdir에 실행하면 이전 결과 파일은 갱신됩니다.
