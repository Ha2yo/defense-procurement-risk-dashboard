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


## KPI (scripts/kpi)

###  데이터 병합

python scripts/preprocessing/merge_bid_data.py
- 입찰공고·입찰결과 CLEAN 데이터 병합 (요구년도+기관코드+판단번호+공고번호+차수 기준), 유찰 여부 생성
- 결과: data/kpi/bid_master_2021_2025.csv

### 1. 공통 기준
python scripts/kpi/kpi_common.py
- 발주기관 분류 규칙, 기관 분류 기준표(org_group_map.csv) 생성
- 위험도 흡수 규칙, 고위험 정렬 등 KPI·분석 공통 함수

### 2. 위험도 룩업 테이블
python scripts/kpi/lookup_table_2021_2023.py
- 2021~2023년 기관그룹×계약방법×사업구분 조합별 유찰률 (10건 미만 조합 흡수)

### 3. KPI 1
python scripts/kpi/kpi1_2024.py
- 고위험 공고(상위 20%) 사전 식별률 (2024년 검증)

### 4. KPI 2
python scripts/kpi/kpi2_2024.py
- 위험도 10분위별 실제 유찰률 단조성 (스피어만 상관계수)

## 분석 (analysis)

### 1. 분석 데이터 준비
python analysis/01_prepare_data.py
- 2021~2023년 경쟁성 입찰, 기관 그룹·유찰 여부 부여

### 2. 유찰 조건 분석
python analysis/02_analysis.py
- 기관·계약방법·사업구분별 유찰률표, 카이제곱 검정·Cramér's V, 막대그래프

## 기관 분류 확인 (scripts/ornt)

### 1. 기관 목록
python scripts/ornt/orntCode_unique.py
- 공고 CLEAN 데이터에서 기관코드(orntCode) 기준 기관 목록(226개) 생성

### 2. 기관별 품목명세서 표본 수집
python scripts/ornt/collect_ornt_item_sample.py
- 기관별 공고 1건의 품목명세서 API 호출, 군 구분(miltDivs·codeVld4) 수집

### 3. 기관별 소요군 분포
python scripts/ornt/compare_ornt_codeVld4.py
- 품목명세서 전체에서 기관별 codeVld4(소요군) 분포·비율·군종류수 집계
---------------------------------------------------------
## A2.공고기간 분석
분석 순서: 데이터 전처리 → 유찰률 통계 분석 → 연도별 비교
### 1.공고기간 데이터 전처리

preprocess_bid_period.py

=> 입찰공고와 입찰결과 데이터를 공고키(noticeKey)로 연결
공고일과 참가등록마감일을 활용한 공고기간 계산
공고기간 구간 분류(5일 이하 / 5일 초과~10일 / 10일 초과) 및 분석대상 추출
### 2. 공고기간별 유찰률 통계 분석

analyze_bid_period.py

=>공고기간 기초통계 및 구간별 유찰률 산출
카이제곱 독립성 검정을 통한 공고기간과 유찰 여부의 통계적 연관성 분석

### 3.연도별 공고기간 및 유찰률 비교

compare_bid_period_years.py

=>2021~2023년과 2024년의 공고기간 및 유찰률 비교
연도별 유찰률 추세 분석 및 선 그래프 생성
기간별 카이제곱 검정 결과 비교 및 검증
--------------------------------------------

## 폴더 구조

data/raw
- API 원본 데이터

data/clean
- 정제 데이터

## 환경설정

.env 파일에 다음 항목 필요

API_KEY_DATA_PORTAL=...