from pathlib import Path
import pandas as pd

# 1. 파일 경로 설정 (지정하신 data-kpi 폴더 기준)
base_path = Path(r'C:\repository\defense-procurement-risk-dashboard\data-kpi')
base_path.mkdir(parents=True, exist_ok=True)

master_path = base_path / 'bid_master_2021_2025.csv'
lookup_output_path = base_path / 'risk_lookup_table_absorbed_2021_2023.csv'

print('📂 마스터 파일 로드 중...')
df_master = pd.read_csv(master_path, encoding='utf-8-sig')

# 2. 발주기관 그룹 매핑 함수 (공군 계열 분리 포함)
def map_org_group(val):
  val_str = str(val)
  if (
      '육군' in val_str
      or '3군단' in val_str
      or '군단' in val_str
      or '사령부' in val_str
  ):
    return '1. 육군계열'
  elif '공군' in val_str:
    return '2. 공군계열'
  elif '해군' in val_str or '해병대' in val_str:
    return '3. 해군/해병대계열'
  elif (
      '국방부' in val_str
      or '합동' in val_str
      or '근무지원단' in val_str
  ):
    return '4. 국방부직할/합동'
  else:
    return '기타계열'


df_master['org_group'] = df_master['ornt'].apply(map_org_group)
df_master['mthd_group'] = df_master['cntrctMth']

# 3. '수의' 관련 계약 방식 완벽 제외 전처리
print("🧹 '수의계약' 관련 데이터를 분석 대상에서 완벽히 제외하는 중...")
df_filtered = df_master[
    ~df_master['cntrctMth'].astype(str).str.contains('수의', na=False)
].copy()

# 4. 2021~2023년 학습 데이터 추출 및 초기 조합 집계
print('🔍 2021~2023년 학습 데이터 추출 및 조합 집계 중...')
df_train = df_filtered[
    (df_filtered['year'] >= 2021) & (df_filtered['year'] <= 2023)
].copy()

df = (
    df_train.groupby(['org_group', 'mthd_group', 'busiDivs'])
    .agg(sample_count=('fail_yn', 'count'), fail_rate=('fail_yn', 'mean'))
    .reset_index()
)

# 정밀 가중평균 계산을 위한 유찰 건수 복원
df['fail_count'] = df['sample_count'] * df['fail_rate']

# 5. 데이터 분리: 10개 이상(기준 테이블) vs 10개 미만(소표본)
large_df = df[df['sample_count'] >= 10].copy().reset_index(drop=True)
small_df = df[df['sample_count'] < 10].copy().reset_index(drop=True)

print(f'✨ 기준 유지 데이터 (10건 이상): {len(large_df):,}개 행')
print(f'➕ 흡수시킬 소표본 데이터 (10건 미만): {len(small_df):,}개 행\n')

# 6. 소표본 행들을 우선순위에 따라 10개 이상 행들에 흡수시키기
print('🔄 소표본 데이터 상위 그룹 흡수(Absorption) 작업 중...')
for idx, row in small_df.iterrows():
  org = row['org_group']
  mthd = row['mthd_group']
  busi = row['busiDivs']
  s_cnt = row['sample_count']
  f_cnt = row['fail_count']

  matched = False

  # [1순위] 기관 + 계약방식이 일치하는 10개 이상 행 찾기
  cand1 = large_df[
      (large_df['org_group'] == org) & (large_df['mthd_group'] == mthd)
  ]
  if not cand1.empty:
    target_idx = cand1['sample_count'].idxmax()
    large_df.loc[target_idx, 'sample_count'] += s_cnt
    large_df.loc[target_idx, 'fail_count'] += f_cnt
    matched = True
  else:
    # [2순위] 기관 + 사업유형이 일치하는 10개 이상 행 찾기
    cand2 = large_df[
        (large_df['org_group'] == org) & (large_df['busiDivs'] == busi)
    ]
    if not cand2.empty:
      target_idx = cand2['sample_count'].idxmax()
      large_df.loc[target_idx, 'sample_count'] += s_cnt
      large_df.loc[target_idx, 'fail_count'] += f_cnt
      matched = True

  # [안전장치] 1, 2순위 모두 못 찾았다면 해당 기관의 가장 큰 그룹에 흡수
  if not matched:
    cand3 = large_df[large_df['org_group'] == org]
    if not cand3.empty:
      target_idx = cand3['sample_count'].idxmax()
      large_df.loc[target_idx, 'sample_count'] += s_cnt
      large_df.loc[target_idx, 'fail_count'] += f_cnt

# 7. 흡수 완료 후 유찰률(fail_rate) 재계산 및 포맷팅
large_df['fail_rate'] = large_df['fail_count'] / large_df['sample_count']
large_df['fail_rate_pct'] = (large_df['fail_rate'] * 100).round(2).astype(
    str
) + '%'

# 8. 최종 룩업 테이블 저장
large_df[[
    'org_group',
    'mthd_group',
    'busiDivs',
    'sample_count',
    'fail_rate',
    'fail_rate_pct',
]].to_csv(lookup_output_path, index=False, encoding='utf-8-sig')

print(f'\n🚀 [2021~2023] 수의계약 제외 및 소표본 흡수 룩업 테이블 생성 완료!')
print(f'📁 저장 경로: {lookup_output_path}')
