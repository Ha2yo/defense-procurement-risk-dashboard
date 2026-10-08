# %%
# %%
from pathlib import Path
import pandas as pd
import numpy as np
from scipy.stats import spearmanr


# 1. 기준 경로 설정
try:
    BASE_DIR = Path(__file__).resolve().parent
except NameError:
    BASE_DIR = Path.cwd()

DATA_KPI_DIR = BASE_DIR / 'data-kpi'
master_path = DATA_KPI_DIR / 'bid_master_2021_2025.csv'
lookup_path = DATA_KPI_DIR / 'risk_lookup_table_absorbed_2021_2023.csv'

print('📂 마스터 데이터 및 룩업 테이블 로드 중...')
df_master = pd.read_csv(master_path, encoding='utf-8-sig')
df_lookup = pd.read_csv(lookup_path, encoding='utf-8-sig')


# 2. 발주기관 그룹 매핑 함수
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

# 3. 2024년 검증 데이터 전처리 (수의계약 제외 및 결과 미확인 제외)
print("🧹 2024년 검증 데이터 전처리 중 (수의계약 제외)...")
df_2024 = df_master[
    (df_master['year'] == 2024)
    & (~df_master['cntrctMth'].astype(str).str.contains('수의', na=False))
    & (df_master['fail_yn'].notna())
].copy()

df_2024['notice_key'] = (
    df_2024['pblancNo'].astype(str) + '-' + df_2024['pblancOdr'].astype(str)
)


# 4. 룩업 테이블 병합 및 결측치 처리
global_mean_fail = df_lookup['fail_rate'].mean()

df_eval = pd.merge(
    df_2024,
    df_lookup[['org_group', 'mthd_group', 'busiDivs', 'fail_rate', 'sample_count']],
    on=['org_group', 'mthd_group', 'busiDivs'],
    how='left',
)
df_eval['fail_rate'] = df_eval['fail_rate'].fillna(global_mean_fail)
df_eval['sample_count'] = df_eval['sample_count'].fillna(0)


# 5. 위험도 기준 10분위(Decile, 1~10등급) 균등 분할
# 동일한 fail_rate 동점자가 있더라도 정확히 10개 등급으로 쪼개기 위해 rank(method='first') 활용
df_eval['risk_rank'] = df_eval['fail_rate'].rank(method='first')
df_eval['decile'] = pd.qcut(df_eval['risk_rank'], 10, labels=range(1, 11))


# 6. 등급별 실제 유찰률 집계
decile_summary = df_eval.groupby('decile').agg(
    total_count=('fail_yn', 'count'),
    actual_fail_count=('fail_yn', 'sum')
).reset_index()

decile_summary['actual_fail_rate(%)'] = (
    decile_summary['actual_fail_count'] / decile_summary['total_count']
) * 100
decile_summary['actual_fail_rate(%)'] = decile_summary['actual_fail_rate(%)'].round(2)


# 7. 스피어만 순위 상관계수(Spearman's Rho) 산출
decile_nums = decile_summary['decile'].astype(int)
fail_rates = decile_summary['actual_fail_rate(%)']
spearman_rho, p_value = spearmanr(decile_nums, fail_rates)


# 8. 결과 출력
print('=' * 65)
print("📊 [KPI 2] 위험도 10분위 구간별 단조성 평가 리포트")
print('=' * 65)
print(decile_summary.to_string(index=False))
print('-' * 65)
print(f'  🎯 [스피어만 순위 상관계수 (Rho)]: {spearman_rho:.4f}  (목표: 0.75 이상)')
print(f'  🎯 [통계적 유의성 p-value]: {p_value:.4e}')
print(f'  🎯 [목표 달성 여부]: {"✅ 성공" if spearman_rho >= 0.75 else "❌ 미달"}')
print('=' * 65)