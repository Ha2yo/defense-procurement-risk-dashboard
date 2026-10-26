# %%
"""[분석계획 2단계] 분석용 데이터 준비

분석 단위: 입찰공고 1건 = 1행 (공고 단위)
  - 입력은 KPI와 같은 병합 마스터(data/kpi/bid_master_2021_2025.csv)를 쓴다.
    공고 단위 집계, 연도는 공고일(pblancDate) 기준, 판단보류 제외가 이미 반영되어 있다.
  - 기관그룹은 기관그룹 분류 기준표(data/analysis/ornt_code/orntCode_codeVld4_all.csv)의
    기관그룹 열을 기관코드(orntCode)로 붙인다.
  - 2021~2023년, 수의계약 제외
  - 결과는 data/analysis/ 폴더에 저장한다 (data/ 폴더는 .gitignore로 업로드 제외).

실행 순서
  1) python scripts/preprocessing/merge_bid_data.py
  2) python scripts/ornt/compare_ornt_codeVld4.py
  3) python scripts/analysis/01_bid_conditions_prepare.py   → 이 파일
  4) python scripts/analysis/01_bid_conditions_analysis.py
"""
from pathlib import Path

import pandas as pd

try:
    BASE_DIR = Path(__file__).resolve().parents[2]
except NameError:
    BASE_DIR = Path.cwd()

MASTER_PATH = BASE_DIR / 'data' / 'kpi' / 'bid_master_2021_2025.csv'
ORG_GROUP_PATH = BASE_DIR / 'data' / 'analysis' / 'ornt_code' / 'orntCode_codeVld4_all.csv'
OUT_DIR = BASE_DIR / 'data' / 'analysis'
OUTPUT_PATH = OUT_DIR / 'analysis_master_2021_2023.csv'

KEY = ['demandYear', 'orntCode', 'dcsNo', 'pblancNo', 'pblancOdr']
YEARS = (2021, 2023)

for path, script in [(MASTER_PATH, 'scripts/preprocessing/merge_bid_data.py'),
                     (ORG_GROUP_PATH, 'scripts/ornt/compare_ornt_codeVld4.py')]:
    if not path.exists():
        raise SystemExit(f'❌ {path} 가 없습니다. {script} 를 먼저 실행하세요.')  

# 1. 마스터 + 기관그룹
df = pd.read_csv(MASTER_PATH, encoding='utf-8-sig', dtype={'orntCode': str})
org = pd.read_csv(ORG_GROUP_PATH, encoding='utf-8-sig', dtype={'orntCode': str})
org = org[['orntCode', '기관그룹']].rename(columns={'기관그룹': 'org_group'})
df = df.merge(org, on='orntCode', how='left')
print(f'📂 마스터 {len(df):,}건 (공고 단위)')

missing = df.loc[df['org_group'].isna(), 'orntCode'].unique()
if len(missing):
    print(f'⚠️ 기관그룹 기준표에 없는 기관코드 {len(missing)}개: {list(missing)[:10]} → "미분류"')
    df['org_group'] = df['org_group'].fillna('미분류')

# 2. 분석 대상 필터
before = len(df)
df = df[df['year'].between(*YEARS)]
print(f'🧹 필터: {YEARS[0]}~{YEARS[1]}년 공고 → {len(df):,}건 (제외 {before - len(df):,})')

before = len(df)
df = df[~df['cntrctMth'].astype(str).str.contains('수의', na=False)]
print(f'🧹 필터: 수의계약 제외 → {len(df):,}건 (제외 {before - len(df):,})')

# 3. 저장
print('\n🏷️ 기관그룹별 건수')
print(df['org_group'].value_counts().to_string())

OUT_DIR.mkdir(parents=True, exist_ok=True)
cols = KEY + ['year', 'ornt', 'org_group', 'cntrctMth', 'busiDivs', 'bidResult', 'fail_yn']
df = df[cols].sort_values(KEY).reset_index(drop=True)
df.to_csv(OUTPUT_PATH, index=False, encoding='utf-8-sig')

print(f'\n✨ 분석 대상 {len(df):,}건, 유찰 {df["fail_yn"].sum():,}건 (유찰률 {df["fail_yn"].mean():.2%})')
print(f'🚀 저장: {OUTPUT_PATH}')
