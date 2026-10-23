# 공식 4변수 분석: 실제 데이터 입력 점검

## 제공 자료 확인

| 자료 | 파일경로 | 행수 | 컬럼수 | 필수컬럼누락 | 상태 |
| --- | --- | --- | --- | --- | --- |
| notice | C:\workspace\defense-procurement-risk-dashboard\data\clean\bid_notice_clean_20210101_20251231.csv | 61142 | 26 | 없음 | 확인 |
| result | C:\workspace\defense-procurement-risk-dashboard\data\clean\bid_result_clean_20210101_20251231.csv | 38114 | 18 | 없음 | 확인 |
| plan | C:\workspace\defense-procurement-risk-dashboard\data\clean\procure_plan_clean_20210101_20251231.csv | 225599 | 15 | 없음 | 확인 |
| contract | C:\workspace\defense-procurement-risk-dashboard\data\clean\contract_info_clean_20210101_20251231.csv | 181276 | 14 | 없음 | 확인 |
| items | C:\workspace\defense-procurement-risk-dashboard\data\clean\bid_item_clean_20210101_20251231.csv | 415963 | 30 | 없음 | 확인 |
| mapping | C:\workspace\defense-procurement-risk-dashboard\data\clean\orntCode_codeVld4_all.csv | 226 | 10 | 없음 | 확인 |

## 공식 KPI 실행 가능 여부

**가능**

- 공식 4변수는 조달계획 예산과 품목명세 `totlAmnt`를 규정에 따라 연결해야 합니다.
- 기관그룹은 공식 `orntCode` 매핑표의 `기관그룹`만 사용할 수 있습니다.
- 계약정보는 진단용이며 공식 사업규모금액에 임의로 넣지 않습니다.
- 품목합계 결측은 값이 0이라는 뜻이 아니므로 가짜 품목 데이터로 보완하지 않습니다.
- `bsicExpt`는 기존 5-4 탐색 비교에만 쓰고 공식 금액 5분위에 쓰지 않습니다.
- 필수 입력자료 5종의 스키마를 확인했고, 2024년 공식 4변수와 비교 모델 KPI 산출을 완료했습니다.
- 결과: `03_model_comparison.csv`, `06_report.md`, `13_kpi2_tie_sensitivity.csv`.

## CLEAN 원자료 연결 진단

| 검사항목 | 값 |
| --- | --- |
| 입찰공고 전체 행수 | 61142 |
| 공고키 중복 건수 | 0 |
| 공고 고유 사업키 | 44079 |
| 입찰결과 전체 행수 | 38114 |
| 결과 고유 공고키 | 38025 |
| 조달계획 전체 행수 | 225599 |
| 계획 사업키 중복 건수 | 0 |
| 계획 예산 양수 행수 | 207411 |
| 총액제 계획 행수 | 220308 |
| 단가제 계획 행수 | 5291 |
| 조달계획 사업키 연결 가능 공고 수 | 60874 |
| 입찰결과 공고키 연결 가능 공고 수 | 38025 |

※ 연결 가능 수는 키 존재 여부만 확인한 수이며, 결과확정 KPI 모집단 수가 아닙니다.
