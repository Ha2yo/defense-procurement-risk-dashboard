# %%
# %%
from pathlib import Path
import pandas as pd

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


# 2. 발주기관 그룹 매핑 함수 (학습 데이터와 동일한 기준 적용)
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

# 3. 2024년 검증 데이터 전처리 (수의계약 제외, 결과확정 경쟁성 공고 대상)
print("🧹 2024년 검증 데이터 전처리 중 (수의계약 제외)...")
df_2024 = df_master[
    (df_master['year'] == 2024)
    & (~df_master['cntrctMth'].astype(str).str.contains('수의', na=False))
].copy()

# 공고 고유 키 설정 (pblancNo + pblancOdr)
df_2024['notice_key'] = (
    df_2024['pblancNo'].astype(str) + '-' + df_2024['pblancOdr'].astype(str)
)

print(f'✨ 2024년 검증 대상 공고 총 건수: {len(df_2024):,}건')
print(f'   - 실제 유찰 공고 건수: {df_2024["fail_yn"].sum():,}건\n')

# 4. 2021~2023 룩업 테이블과 2024년 데이터 병합 (위험도 부여)
print('🔗 룩업 테이블의 위험도(fail_rate) 및 표본 수 매핑 중...')
df_eval = pd.merge(
    df_2024,
    df_lookup[['org_group', 'mthd_group', 'busiDivs', 'fail_rate', 'sample_count']],
    on=['org_group', 'mthd_group', 'busiDivs'],
    how='left',
)

# 룩업에 없는 조합의 경우 기본 위험도(전체 평균 등) 처리 혹은 0 처리
df_eval['fail_rate'] = df_eval['fail_rate'].fillna(df_eval['fail_rate'].mean())
df_eval['sample_count'] = df_eval['sample_count'].fillna(0)


# 5. 고위험군 정렬 규칙 적용
# 1순위: 위험도(fail_rate) 내림차순
# 2순위: 표본 수(sample_count) 내림차순
# 3순위: 공고키(notice_key) 오름차순
print('🔄 제시된 3원 정렬 규칙에 따른 고위험군 산정 중...')
df_eval = df_eval.sort_values(
    by=['fail_rate', 'sample_count', 'notice_key'],
    ascending=[False, False, True]
).reset_index(drop=True)


# 6. 상위 정확히 20% 고위험군 추출
total_count = len(df_eval)
top_20_count = int(round(total_count * 0.20))

df_high_risk = df_eval.iloc[:top_20_count].copy()


# 7. KPI 1 (고위험 공고 사전 식별률 / Recall) 산식 계산
total_actual_fails = df_eval['fail_yn'].sum()
high_risk_actual_fails = df_high_risk['fail_yn'].sum()

kpi1_recall = (high_risk_actual_fails / total_actual_fails) * 100


# 8. 결과 출력
print('=' * 65)
print('📊 [KPI 1] 고위험 공고 사전 식별률 (Recall) 평가 결과')
print('=' * 65)
print(f'  • 2024년 전체 검증 공고 수: {total_count:,}건')
print(f'  • 전체 실제 유찰 공고 수: {total_actual_fails:,}건')
print(f'  • 고위험군 선정 규모 (상위 20%): {top_20_count:,}건')
print(f'  • 고위험군 내 실제 유찰 공고 수: {high_risk_actual_fails:,}건')
print('-' * 65)
print(f'  🎯 [KPI 1 산출 식별률]: {kpi1_recall:.2f}%')
print(f'  🎯 [목표 수준 (60% 이상)] 달성 여부: {"✅ 성공" if kpi1_recall >= 60 else "❌ 미달"}')
print('=' * 65)