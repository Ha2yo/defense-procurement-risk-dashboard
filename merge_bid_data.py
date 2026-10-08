# %%
from pathlib import Path
import pandas as pd

# 1. 현재 실행 환경에 따른 기준 경로 설정
try:
    BASE_DIR = Path(__file__).resolve().parent
except NameError:
    BASE_DIR = Path.cwd()

# 2. 디렉토리 구조 설정 및 자동 생성
# - 원본 데이터용: data/raw/
DATA_RAW_DIR = BASE_DIR / 'data' / 'raw'
DATA_RAW_DIR.mkdir(parents=True, exist_ok=True)

# - KPI 결과물용: data-kpi/
DATA_KPI_DIR = BASE_DIR / 'data-kpi'
DATA_KPI_DIR.mkdir(parents=True, exist_ok=True)

notice_path = DATA_RAW_DIR / 'bid_notice_20210101_20251231.csv'
result_path = DATA_RAW_DIR / 'bid_result_20210101_20251231.csv'
output_path = DATA_KPI_DIR / 'bid_master_2021_2025.csv'  # 👈 data-kpi 폴더 안으로 저장 경로 변경

print('📂 입찰공고 및 입찰결과 원본 파일 로드 중...')
df_notice = pd.read_csv(notice_path, encoding='utf-8-sig')
df_result = pd.read_csv(result_path, encoding='utf-8-sig')

# 컬럼명 앞뒤 공백 제거
df_notice.columns = df_notice.columns.str.strip()
df_result.columns = df_result.columns.str.strip()

print(f'  - 공고 원본 파일 건수: {len(df_notice):,}건')
print(f'  - 입찰 결과 파일 건수: {len(df_result):,}건')

# 3. 공통 키 설정
merge_keys = ['pblancNo', 'pblancOdr']

# 4. 공고 원본에서 필요한 컬럼만 추출 후 중복 제거
notice_subset = df_notice[['pblancNo', 'pblancOdr', 'busiDivs']].drop_duplicates(subset=merge_keys)

print('\n🔗 공고번호 기준으로 데이터 병합(Merge) 진행 중...')
df_master = pd.merge(df_result, notice_subset, on=merge_keys, how='inner')

# 5. 유찰 여부(fail_yn) 라벨 생성
df_master['fail_yn'] = df_master['bidResult'].astype(str).str.contains('유찰').astype(int)

# 6. 연도 컬럼 추출
if 'opengDate' in df_master.columns:
    df_master['year'] = df_master['opengDate'].astype(str).str[:4].astype(int)
elif 'demandYear' in df_master.columns:
    df_master['year'] = df_master['demandYear']

print(f'✨ 병합 및 정제 완료된 마스터 데이터 총 건수: {len(df_master):,}건')
print(f"  - 사업유형이 성공적으로 장착된 건수: {df_master['busiDivs'].notnull().sum():,}건")

# 7. 최종 통합 마스터 파일 저장 (data-kpi 폴더 내부)
df_master.to_csv(output_path, index=False, encoding='utf-8-sig')
print(f'🚀 통합 마스터 파일 저장 완료!\n📁 저장 경로: {output_path}')