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

## 폴더 구조

data/raw
- API 원본 데이터

data/clean
- 정제 데이터

## 환경설정

.env 파일에 다음 항목 필요

API_KEY_DATA_PORTAL=...