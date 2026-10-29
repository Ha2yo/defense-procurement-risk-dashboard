# 01. 기관·계약방법·사업구분별 유찰 분석 — 실행 가이드

## 1. 분석 목적과 적용 범위

**2021~2023년 공고일 기준 결과확정 경쟁성 입찰공고**에서 기관그룹·계약방법·사업구분별 유찰률을 비교하고 카이제곱 독립성 검정, Holm 보정, Cramér’s V, 범주별 조정 표준화 잔차를 보고한다.

- 코드: [`01_bid_conditions_prepare.py`](../scripts/analysis/01_bid_conditions_prepare.py) → [`01_bid_conditions_analysis.py`](../scripts/analysis/01_bid_conditions_analysis.py)
- 입력 마스터: `data/kpi/bid_master_2021_2025.csv`; 기관그룹 기준표: [`data/analysis/ornt_code/orntCode_codeVld4_all.csv`](../data/analysis/ornt_code/orntCode_codeVld4_all.csv)
- 준비 출력: `data/analysis/analysis_master_2021_2023.csv`
- 검정·시각화 출력: `data/analysis/results/`

## 2. 실행 전제와 분석 모집단

1. `merge_bid_data.py`가 생성한 공고 단위 마스터 사용. 원천 입찰결과의 복수 품목행을 공고 건수로 직접 세지 않는다.
2. `orntCode` 기준으로 기관그룹 매핑. 매핑되지 않는 코드는 `미분류`로 보존한다.
3. `year`가 2021~2023년인 공고만 선택한다. `year`는 선행 병합 코드의 **공고일(`pblancDate`) 기준**.
4. `cntrctMth`에 '수의'가 포함된 원공고는 제외한다.
5. 마스터에서 결과확정·판단보류 처리된 `fail_yn`을 활용한다. 분석 시 유찰 `1`, 비유찰(낙찰·순위확정) `0`을 사용한다.

> 검증 주의: `prepare`는 기관그룹 매핑 후 행 수 증가 여부/공고키 중복 여부를 별도 assert 하지 않는다. 기관코드 매핑표의 `orntCode` 유일성을 점검해야 한다. `미분류`를 남기지만 별도 제외 필터는 없으므로 '미분류 제외'라고 무조건 기술하지 않는다.

## 3. 실행 명령어 (PowerShell)

프로젝트 루트 `C:\workspace\defense-procurement-risk-dashboard`에서:

```powershell
cd C:\workspace\defense-procurement-risk-dashboard
python -m pip install -r requirements.txt

# 선행 산출물이 없다면 순서대로 실행
python scripts/preprocessing/merge_bid_data.py
python scripts/ornt/compare_ornt_codeVld4.py

# 1번 분석
python scripts/analysis/01_bid_conditions_prepare.py
python scripts/analysis/01_bid_conditions_analysis.py
```

선행 스크립트가 `data/raw/`, `data/clean/` 파일을 필요로 할 수 있으므로 로컬 데이터가 준비돼 있어야 한다.

## 4. 변수 정리·소표본 처리

| 변수 | 코드상 컬럼 | 처리 방식 |
|---|---|---|
| 기관그룹 | `org_group` | 기관코드 매핑 결과. 주 검정 |
| 계약방법 | `mthd` | 원본 `cntrctMth`에서 기대 유찰 건수 5 미만인 소범주를 같은 계약 계열(괄호 앞 명칭)로 병합. 주 검정 |
| 사업구분 | `busiDivs` | 물품·용역 범주. 주 검정 |
| 3변수 조합 | `combo_test` | 기관그룹 × 병합 계약방법 × 사업구분; 기대 유찰 건수 5 미만 조합을 `소표본 조합(병합)`으로 합쳐 참고 검정 |

- 결합/소표본 판단의 기대 유찰 건수는 해당 범주 건수 × **전체 분석 유찰률**로 계산한다.

- 조합 분석은 **공식 KPI 위험도 4변수 조합과 다름**. 여기에는 금액 5분위가 포함돼 있지 않다.

## 5. 통계검정과 산출 지표

- **카이제곱 독립성 검정:** 각 범주 × `fail_yn` (0/1) 교차표의 독립성 검정. 귀무가설: 해당 범주와 유찰 여부가 독립.
- **Holm 다중검정 보정:** 주 검정 `org_group`, `mthd`, `busiDivs` **세 검정의 p값만** 보정. `combo_test`는 참고 검정으로 보정 제외. 서로 다른 공고기간 분석~공고 시기 분석 검정까지 묶은 6개 보정과 혼용하지 않는다.
- **유의수준:** 0.05. 코드의 'H0 채택' 표기는 엄밀히는 'H0 기각하지 못함'으로 해석.
- **Cramér’s V:** 카이제곱 통계량을 표본 수와 교차표 크기로 표준화한 관련 강도. 코드 구간: 0.1 미만 무시할 수준, 0.1~0.3 약함, 0.3~0.5 중간, 0.5 이상 강함.
- **95% 유찰률 구간:** 범주별 **Wilson 신뢰구간**.
- **조정 표준화 잔차:** 범주별 기대 유찰 수와 관측 유찰 수를 비교하며 `>1.96` 유찰 많음, `<-1.96` 유찰 적음. **범주별 사후잔차에는 별도의 다중검정 보정이 구현돼 있지 않다.** 개별 범주 판단은 탐색적 결과로 제시한다.
- **기대빈도 진단:** 코드에서 기대빈도 5 미만 칸 비율을 계산하며 20% 초과 시 경고. '모든 칸의 기대빈도가 반드시 5 이상'으로 자동 강제하는 것은 아니다.

## 6. 결과 요약

| 검정 | n | 범주 수 | χ² | 자유도 | p값 | Holm 보정 p값 | Cramér’s V | 유찰률 차이(%p) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 기관그룹 | 21,092 | 5 | 273.53 | 4 | 5.54 × 10⁻⁵⁸ | 1.66 × 10⁻⁵⁷ | 0.114 | 10.17 |
| 계약방법 | 21,092 | 6 | 16.87 | 5 | 0.0047 | 0.0047 | 0.028 | 17.80 |
| 사업구분 | 21,092 | 2 | 22.68 | 1 | 1.91 × 10⁻⁶ | 3.82 × 10⁻⁶ | 0.033 | 3.02 |
| 기관×계약방법×사업구분 | 21,092 | 31 | 380.68 | 30 | 2.20 × 10⁻⁶² | 참고(보정 제외) | 0.134 | 29.47 |

| 기관그룹 | 공고 수 | 유찰 수 | 유찰률 |
|---|---:|---:|---:|
| 공군 | 1,352 | 290 | 21.45% |
| 육군 | 7,406 | 1,567 | 21.16% |
| 국방부직할/합동 | 5,562 | 723 | 13.00% |
| 해군/해병대 | 5,301 | 680 | 12.83% |
| 방위사업청 | 1,471 | 166 | 11.28% |
| **계** | **21,092** | **3,426** | **16.24%** |

## 7. 출력 파일 구조

`01_bid_conditions_prepare.py`:
- `data/analysis/analysis_master_2021_2023.csv` — 공고별 기관그룹·계약방법·사업구분·유찰 여부.

`01_bid_conditions_analysis.py`:
- `data/analysis/results/rate_org_group.csv`, `rate_mthd.csv`, `rate_busiDivs.csv`, `rate_combo_test.csv` — 변수별 건수·유찰·비유찰·유찰률·Wilson 95% CI·기대 유찰 건수·조정 표준화 잔차·판정.
- `chi2_summary.csv` — 각 검정의 χ²·자유도·p값·Holm 보정 p값·Cramér’s V·유찰률 차이·기대빈도 진단.
- `high_risk_categories.csv` — 잔차 기준 유찰이 많은 범주/조합(탐색적).
- `analysis_summary.csv` — 범주별 수치와 검정값 통합.
- `fig_org_group.png`, `fig_mthd.png`, `fig_busiDivs.png`, `fig_combo_test.png` — 유찰률 시각화.
- `analysis_results.xlsx` — 시트별 결과 통합 (엑셀 저장 모듈이 없으면 CSV/PNG는 별도 생성).

## 8. 실행 결과 확인

```powershell
# 출력 파일 존재 확인
Test-Path data/analysis/analysis_master_2021_2023.csv
Test-Path data/analysis/results/chi2_summary.csv
Get-ChildItem data/analysis/results/

# 출력한 검정 결과 확인
Import-Csv -Encoding UTF8 data/analysis/results/chi2_summary.csv | Format-Table -AutoSize
```

검수 순서:
1. 준비 CSV의 행 수 21,092건, `fail_yn` 합 3,426건, 중복 공고키 여부 확인.
2. `rate_org_group.csv`, `rate_mthd.csv`, `rate_busiDivs.csv`의 합이 모두 같은 모집단을 반영하는지 확인.
3. `chi2_summary.csv`에서 주 검정 3개 Holm 보정값, 조합 참고 검정 제외 여부 확인.
4. 기대빈도 5 미만 칸 비율과 소범주 병합 후 범주 수 확인.

6. 각 출력 표의 건수와 검정값이 동일한 분석 집단을 기준으로 계산됐는지 확인한다.
