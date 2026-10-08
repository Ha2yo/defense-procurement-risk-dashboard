# %%
"""2021~2023년 유찰 위험도 룩업 테이블 생성 (수의계약 제외, 소표본 흡수)."""
import pandas as pd

from kpi_common import (
    DATA_KPI_DIR, LOOKUP_PATH, KEYS, MIN_SAMPLE, load_master, get_train,
)

DATA_KPI_DIR.mkdir(parents=True, exist_ok=True)

print('📂 마스터 파일 로드 중 (수의계약 제외)...')
df_train = get_train(load_master())
print(f'🔍 2021~2023년 학습 데이터: {len(df_train):,}건')

# 1. 조합별 집계
df = (
    df_train.groupby(KEYS)
    .agg(own_count=('fail_yn', 'count'), fail_count=('fail_yn', 'sum'))
    .reset_index()
)

# 2. 10건 이상(기준 조합) vs 10건 미만(소표본) 분리
large_df = df[df['own_count'] >= MIN_SAMPLE].copy().reset_index(drop=True)
small_df = df[df['own_count'] < MIN_SAMPLE].copy().reset_index(drop=True)
large_df['sample_count'] = large_df['own_count']

print(f'✨ 기준 조합 (10건 이상): {len(large_df):,}개')
print(f'➕ 소표본 조합 (10건 미만): {len(small_df):,}개\n')


def find_target(row):
    """소표본 조합을 흡수할 기준 조합의 인덱스를 찾는다. 없으면 None."""
    same_org = large_df['org_group'] == row['org_group']
    candidates = [
        large_df[same_org & (large_df['mthd_group'] == row['mthd_group'])],  # 1순위: 기관+계약방식
        large_df[same_org & (large_df['busiDivs'] == row['busiDivs'])],      # 2순위: 기관+사업유형
        large_df[same_org],                                                  # 3순위: 같은 기관
    ]
    for cand in candidates:
        if not cand.empty:
            return cand['sample_count'].idxmax()
    return None


# 3. 소표본을 기준 조합에 흡수 (건수 합산 + 어디로 흡수됐는지 기록)
print('🔄 소표본 흡수 작업 중...')
targets = []
for _, row in small_df.iterrows():
    t = find_target(row)
    targets.append(t)
    if t is not None:
        large_df.loc[t, 'sample_count'] += row['own_count']
        large_df.loc[t, 'fail_count'] += row['fail_count']
small_df['target_idx'] = targets

# 4. 흡수 후 유찰률 계산
large_df['fail_rate'] = large_df['fail_count'] / large_df['sample_count']
large_df['absorbed_into'] = ''

# 5. 소표본 조합도 자기 키로 룩업에 남긴다 (흡수 대상의 유찰률·표본 수 사용)
#    -> 2024년에 같은 조합이 나오면 평균이 아니라 흡수된 위험도가 붙는다.
absorbed = small_df[small_df['target_idx'].notna()].copy()
dropped = small_df[small_df['target_idx'].isna()]

if not absorbed.empty:
    tgt = large_df.loc[absorbed['target_idx'].astype(int)].reset_index(drop=True)
    absorbed = absorbed.reset_index(drop=True)
    absorbed['fail_rate'] = tgt['fail_rate']
    absorbed['sample_count'] = tgt['sample_count']
    absorbed['absorbed_into'] = tgt['mthd_group'].astype(str) + ' / ' + tgt['busiDivs'].astype(str)

out_cols = KEYS + ['sample_count', 'own_count', 'fail_rate', 'absorbed_into']
lookup = pd.concat([large_df[out_cols], absorbed[out_cols]], ignore_index=True)
lookup = lookup.sort_values(KEYS).reset_index(drop=True)
lookup['fail_rate_pct'] = (lookup['fail_rate'] * 100).round(2).astype(str) + '%'

# 6. 저장
#    sample_count : 유찰률 계산에 쓰인 표본 수 (흡수된 건수 포함)
#    own_count    : 그 조합 자체의 원래 건수
#    absorbed_into: 소표본일 때 흡수된 기준 조합 (기준 조합은 빈칸)
lookup.to_csv(LOOKUP_PATH, index=False, encoding='utf-8-sig')

print(f'  - 흡수되어 룩업에 남은 소표본 조합: {len(absorbed):,}개')
print(f'  - 흡수할 곳이 없어 제외된 조합: {len(dropped):,}개 ({int(dropped["own_count"].sum()):,}건)')
print(f'\n🚀 룩업 테이블 생성 완료! 총 {len(lookup):,}개 조합')
print(f'📁 저장 경로: {LOOKUP_PATH}')