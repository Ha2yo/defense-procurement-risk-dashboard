# %%
"""[KPI 2] 2024년 위험도 10분위별 실제 유찰률의 단조성 (스피어만 상관계수)."""
import pandas as pd
from scipy.stats import spearmanr

from kpi_common import (
    LOOKUP_PATH, load_master, get_train, get_eval, attach_risk, sort_by_risk,
)

print('📂 마스터 데이터 및 룩업 테이블 로드 중...')
df_master = load_master()
df_lookup = pd.read_csv(LOOKUP_PATH, encoding='utf-8-sig')

# KPI1과 동일한 기본 위험도
fallback_rate = get_train(df_master)['fail_yn'].mean()

df_eval = attach_risk(get_eval(df_master, 2024), df_lookup, fallback_rate)

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
print(f'  • 기본 위험도({fallback_rate:.4f})로 채운 공고: {int(df_eval["is_fallback"].sum()):,}건')
print(f'  🎯 [스피어만 순위 상관계수 (Rho)]: {spearman_rho:.4f}  (목표: 0.75 이상)')
print(f'  🎯 [통계적 유의성 p-value]: {p_value:.4e}')
print(f'  🎯 [목표 달성 여부]: {"✅ 성공" if spearman_rho >= 0.75 else "❌ 미달"}')
print('=' * 75)