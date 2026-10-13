# %%
"""[KPI 1] 2024년 고위험 공고(상위 20%) 사전 식별률."""
import pandas as pd

from kpi_common import (
    LOOKUP_PATH, load_master, get_train, get_eval, attach_risk, sort_by_risk,
)

print('📂 마스터 데이터 및 룩업 테이블 로드 중...')
df_master = load_master()
df_lookup = pd.read_csv(LOOKUP_PATH, encoding='utf-8-sig')

# 룩업에 없는 조합에 줄 기본 위험도 = 2021~2023년 전체 유찰률
fallback_rate = get_train(df_master)['fail_yn'].mean()

df_2024 = get_eval(df_master, 2024)
print(f'✨ 2024년 검증 대상 공고: {len(df_2024):,}건 (실제 유찰 {int(df_2024["fail_yn"].sum()):,}건)')

df_eval = sort_by_risk(attach_risk(df_2024, df_lookup, fallback_rate))

# 상위 20% 고위험군
total_count = len(df_eval)
top_20_count = int(round(total_count * 0.20))
df_high_risk = df_eval.iloc[:top_20_count]

total_actual_fails = df_eval['fail_yn'].sum()
high_risk_actual_fails = df_high_risk['fail_yn'].sum()
kpi1_recall = high_risk_actual_fails / total_actual_fails * 100

# 20% 경계에 걸린 동점 규모 (크면 경계가 공고키 순서로 갈린 것)
cut_rate = df_high_risk['fail_rate'].iloc[-1]
tie_total = int((df_eval['fail_rate'] == cut_rate).sum())
tie_inside = int((df_high_risk['fail_rate'] == cut_rate).sum())

print('=' * 65)
print('📊 [KPI 1] 고위험 공고 사전 식별률 (Recall) 평가 결과')
print('=' * 65)
print(f'  • 2024년 전체 검증 공고 수: {total_count:,}건')
print(f'  • 전체 실제 유찰 공고 수: {int(total_actual_fails):,}건')
print(f'  • 고위험군 선정 규모 (상위 20%): {top_20_count:,}건')
print(f'  • 고위험군 내 실제 유찰 공고 수: {int(high_risk_actual_fails):,}건')
print(f'  • 기본 위험도({fallback_rate:.4f})로 채운 공고: {int(df_eval["is_fallback"].sum()):,}건')
print(f'  • 경계 위험도({cut_rate:.4f}) 동점 {tie_total:,}건 중 {tie_inside:,}건만 고위험군 포함')
print('-' * 65)
print(f'  🎯 [KPI 1 산출 식별률]: {kpi1_recall:.2f}%')
print(f'  🎯 [목표 수준 (35% 이상)] 달성 여부: {"✅ 성공" if kpi1_recall >= 35 else "❌ 미달"}')
print('=' * 65)