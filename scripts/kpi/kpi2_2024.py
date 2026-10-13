# %%
"""[KPI 2] 2024년 위험도 10분위별 실제 유찰률의 단조성 (스피어만 상관계수)."""
from pathlib import Path
import pandas as pd
from scipy.stats import spearmanr

from kpi_common import (
    LOOKUP_PATH, load_master, get_eval, attach_risk, split_unscored, sort_by_risk,
)

# 1. 기준 경로 설정
try:
    BASE_DIR = Path(__file__).resolve().parent
except NameError:
    BASE_DIR = Path.cwd()

DATA_KPI_DIR = BASE_DIR / '../..' / 'data/kpi'
master_path = DATA_KPI_DIR / 'bid_master_2021_2025.csv'
lookup_path = DATA_KPI_DIR / 'risk_lookup_table_absorbed_2021_2023.csv'

print('📂 마스터 데이터 및 룩업 테이블 로드 중...')
df_master = load_master()
df_lookup = pd.read_csv(LOOKUP_PATH, encoding='utf-8-sig')

# KPI1과 동일하게 위험도 미산정 공고(흡수할 기준 조합 없음)는 제외
df_all = attach_risk(get_eval(df_master, 2024), df_lookup)
df_eval, df_unscored = split_unscored(df_all)

# KPI1과 같은 정렬 규칙으로 줄 세운 뒤 뒤집어서 (저위험 -> 고위험) 10등분
# 1등급 = 가장 낮은 위험, 10등급 = 가장 높은 위험
df_eval = sort_by_risk(df_eval).iloc[::-1].reset_index(drop=True)
df_eval['decile'] = pd.qcut(df_eval.index, 10, labels=range(1, 11))

decile_summary = df_eval.groupby('decile', observed=True).agg(
    total_count=('fail_yn', 'count'),
    actual_fail_count=('fail_yn', 'sum'),
    risk_min=('fail_rate', 'min'),
    risk_max=('fail_rate', 'max'),
).reset_index()
decile_summary['actual_fail_rate(%)'] = (
    decile_summary['actual_fail_count'] / decile_summary['total_count'] * 100
).round(2)

spearman_rho, p_value = spearmanr(
    decile_summary['decile'].astype(int), decile_summary['actual_fail_rate(%)']
)

n_unique = df_eval['fail_rate'].nunique()

print('=' * 75)
print('📊 [KPI 2] 위험도 10분위 구간별 단조성 평가 리포트')
print('=' * 75)
print(decile_summary.to_string(index=False))
print('-' * 75)
print(f'  • 서로 다른 위험도 값: {n_unique}개' + ('  ⚠️ 10개 미만이라 10분위가 동점을 억지로 나눈 것입니다.' if n_unique < 10 else ''))
if len(df_unscored):
    print(f'  • 위험도 미산정으로 제외한 공고: {len(df_unscored):,}건')
print(f'  🎯 [스피어만 순위 상관계수 (Rho)]: {spearman_rho:.4f}  (목표: 0.85 이상)')
print(f'  🎯 [통계적 유의성 p-value]: {p_value:.4e}')
print(f'  🎯 [목표 달성 여부]: {"✅ 성공" if spearman_rho >= 0.85 else "❌ 미달"}')
print('=' * 75)