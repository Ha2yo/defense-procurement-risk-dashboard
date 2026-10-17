# %%
"""[KPI 1] 2024년 고위험 공고(상위 20%) 사전 식별률.

위험도 산정 단계(1~4단계)별 분포를 함께 출력하고,
표본 부족으로 완화된 위험도를 받은 공고(2~4단계) 목록을 data/kpi/risk_stage_2024.csv로 저장한다.
위험도 미산정 공고는 KPI에서 빼고 따로 센다 (4단계가 있어 항상 0건).
"""
import pandas as pd

from kpi_common import (
    DATA_KPI_DIR, LOOKUP_PATH, KEYS, STAGE_LABELS,
    load_master, get_eval, attach_risk, split_unscored, sort_by_risk,
)

print('📂 마스터 데이터 및 룩업 테이블 로드 중...')
df_master = load_master()
df_lookup = pd.read_csv(LOOKUP_PATH, encoding='utf-8-sig')

df_2024 = get_eval(df_master, 2024)
print(f'✨ 2024년 검증 대상 공고: {len(df_2024):,}건 (실제 유찰 {int(df_2024["fail_yn"].sum()):,}건)')

df_all = attach_risk(df_2024, df_lookup)
df_scored, df_unscored = split_unscored(df_all)
df_eval = sort_by_risk(df_scored)

# 위험도 산정 단계별 분포
stage_summary = (
    df_all.assign(단계=df_all['risk_stage'].map(STAGE_LABELS).fillna('미산정'))
    .groupby('단계')
    .agg(건수=('fail_yn', 'size'), 유찰=('fail_yn', 'sum'), 학습기간_0건_조합=('own_count', lambda s: int((s == 0).sum())))
    .reindex(list(STAGE_LABELS.values()) + ['미산정'], fill_value=0)
)
stage_summary['비율(%)'] = (stage_summary['건수'] / len(df_all) * 100).round(2)

# 완화된 위험도를 받은 공고 목록 저장 (2~4단계 + 미산정)
stage_cols = (['notice_key', 'opengDate', 'ornt'] + KEYS
              + ['own_count', 'risk_stage', 'applied_combo', 'sample_count', 'fail_rate', 'bidNm', 'bidResult', 'fail_yn'])
stage_path = DATA_KPI_DIR / 'risk_stage_2024.csv'
df_all[(df_all['risk_stage'] != 1) | df_all['risk_stage'].isna()][stage_cols] \
    .sort_values(['risk_stage', 'notice_key']).to_csv(stage_path, index=False, encoding='utf-8-sig')

# 상위 20% 고위험군 (위험도 산정 공고 기준)
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
print('[위험도 산정 단계별 분포]')
print(stage_summary.to_string())
print(f'  • 완화 공고 목록 저장: {stage_path}')
print('-' * 65)
print(f'  • 2024년 전체 검증 공고 수: {len(df_all):,}건')
if len(df_unscored):
    print(f'    - 위험도 미산정 : {len(df_unscored):,}건 → KPI 계산 제외')
print(f'  • 실제 유찰 공고 수 (KPI 대상): {int(total_actual_fails):,}건')
print(f'  • 고위험군 선정 규모 (상위 20%): {top_20_count:,}건')
print(f'  • 고위험군 내 실제 유찰 공고 수: {int(high_risk_actual_fails):,}건')
print(f'  • 경계 위험도({cut_rate:.4f}) 동점 {tie_total:,}건 중 {tie_inside:,}건만 고위험군 포함')
print('-' * 65)
print(f'  🎯 [KPI 1 산출 식별률]: {kpi1_recall:.2f}%')
print(f'  🎯 [목표 수준 (35% 이상)] 달성 여부: {"✅ 성공" if kpi1_recall >= 35 else "❌ 미달"}')
print('=' * 65)