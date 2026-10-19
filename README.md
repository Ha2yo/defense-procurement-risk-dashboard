# 군수품 조달 유찰 위험 예측 대시보드 구축을 통한 조달 효율성 향상

## git 사용 설명서
https://app.notion.com/p/3f1830b2d9f78025875de5f35e33ee2d

## 데이터 수집

### 1. 조달계획
python collect_data_plan.py

### 2. 입찰공고 / 입찰결과
python collect_data_list.py

### 3. 계약정보
python collect_data_contract.py

## 데이터 정제

python raw_to_clean.py


## 데이터 병합

python scripts/preprocessing/merge_bid_data.py
- 입찰공고·입찰결과 CLEAN 데이터 병합 (요구년도+기관코드+판단번호+공고번호+차수 기준)
- 공고 단위로 집계, 연도는 공고일(pblancDate) 기준, 판단보류(유찰·낙찰 혼재) 공고 제외
- 결과: data/kpi/bid_master_2021_2025.csv

## 기관 분류 (scripts/ornt)

### 1. 기관 목록
python scripts/ornt/orntCode_unique.py
- 공고 CLEAN 데이터에서 기관코드(orntCode) 기준 기관 목록(226개) 생성

### 2. 기관그룹 분류 기준표
python scripts/ornt/compare_ornt_codeVld4.py
- 프로젝트 기관그룹 분류 기준 (기관명에 소속이 명시된 경우 기관명, 그 외 기관코드 + 개별 지정)
- 품목명세서 codeVld4(소요군) 분포를 분류 근거로 함께 집계
- 필요: data/raw/bid_item_20210101_20251231.csv (품목명세서)
- 결과: data/analysis/ornt_code/orntCode_codeVld4_all.csv (기관그룹 열)

### (참고) 기관별 품목명세서 표본 수집
python scripts/ornt/collect_ornt_item_sample.py
- 기관별 공고 1건의 품목명세서 API 호출, 군 구분(miltDivs·codeVld4) 수집

## KPI (scripts/kpi)

python scripts/kpi/kpi_12_2024.py
- 2021~2023년 기관그룹×계약방법×사업구분 조합별 유찰률 룩업 (10건 미만은 상위 조합 대체)
- KPI 1: 고위험 공고(상위 20%) 사전 식별률 (2024년 검증, 동점 상한·하한·기대값 포함)
- KPI 2: 위험도 10분위별 실제 유찰률 단조성 (스피어만 상관계수)
- 결과: data/kpi/risk_lookup_2021_2023.csv, data/kpi/risk_stage_2024.csv

## 분석 (analysis)

### 1. 분석 데이터 준비
python analysis/01_prepare_data.py
- 2021~2023년 경쟁성 입찰, 기관 그룹·유찰 여부 부여

### 2. 유찰 조건 분석
python analysis/02_analysis.py
- 기관·계약방법·사업구분별 유찰률표, 카이제곱 검정·Cramér's V, 막대그래프

## 폴더 구조

data/raw
- API 원본 데이터

data/clean
- 정제 데이터

## 환경설정

.env 파일에 다음 항목 필요

API_KEY_DATA_PORTAL=...