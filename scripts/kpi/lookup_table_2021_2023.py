# %%
"""2021~2023년 유찰 위험도 룩업 테이블 생성 (수의계약 제외, 표본 부족 시 계층적 완화).

단계별 유찰률을 한 표에 저장한다 (stage 열).
  1단계: 기관그룹 × 계약방법 × 사업구분
  2단계: 기관그룹 × 사업구분   (계약방법 제외)
  3단계: 기관그룹
  4단계: 학습 데이터 전체
2024년 위험도는 kpi_common.attach_risk가 1단계부터 차례로 보고
표본이 10건 이상인 첫 단계의 유찰률을 붙인다.
"""
from kpi_common import (
    DATA_KPI_DIR, LOOKUP_PATH, MIN_SAMPLE, STAGE_LABELS, load_master, get_train, build_lookup,
)

DATA_KPI_DIR.mkdir(parents=True, exist_ok=True)

print('📂 마스터 파일 로드 중 (수의계약 제외)...')
df_train = get_train(load_master())
print(f'🔍 2021~2023년 학습 데이터: {len(df_train):,}건')

lookup = build_lookup(df_train)
lookup['fail_rate_pct'] = (lookup['fail_rate'] * 100).round(2).astype(str) + '%'
lookup.to_csv(LOOKUP_PATH, index=False, encoding='utf-8-sig')

print('\n[단계별 행 수 (10건 이상 / 전체)]')
for stage, label in STAGE_LABELS.items():
    part = lookup[lookup['stage'] == stage]
    print(f'  {label}: {int((part["sample_count"] >= MIN_SAMPLE).sum())} / {len(part)}')
print(f'\n🚀 룩업 테이블 생성 완료! 총 {len(lookup):,}행')
print(f'📁 저장 경로: {LOOKUP_PATH}')