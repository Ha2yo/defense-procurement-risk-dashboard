# %%
from pathlib import Path
import pandas as pd
from scipy.stats import chi2_contingency

# 1. 기준 경로 설정
try:
    BASE_DIR = Path(__file__).resolve().parent
except NameError:
    BASE_DIR = Path.cwd()

DATA_KPI_DIR = BASE_DIR / 'data-kpi'
lookup_path = DATA_KPI_DIR / 'risk_lookup_table_absorbed_2021_2023.csv'

print('📂 룩업 테이블 로드 중...')
df_lookup = pd.read_csv(lookup_path, encoding='utf-8-sig')

# 2. 유찰 건수(fail_count) 및 낙찰/성공 건수(success_count) 복원
df_lookup['fail_count'] = (df_lookup['sample_count'] * df_lookup['fail_rate']).round().astype(int)
df_lookup['success_count'] = df_lookup['sample_count'] - df_lookup['fail_count']


# ==============================================================================
# 카이제곱 독립성 검정 수행 함수
# ==============================================================================
def run_chi2_test(df, group_col, col_name_kr):
    # 교차표(Contingency Table) 생성: [유찰 건수, 낙찰 건수]
    contingency_table = df.groupby(group_col)[['fail_count', 'success_count']].sum()
    
    # 카이제곱 검정 실행
    chi2, p_val, dof, expected = chi2_contingency(contingency_table)
    
    print('=' * 60)
    print(f'📊 [{col_name_kr} ({group_col}) 카이제곱 검정 결과]')
    print('=' * 60)
    print(f'  • 카이제곱 통계량 (χ²): {chi2:,.4f}')
    print(f'  • 자유도 (d.o.f): {dof}')
    print(f'  • p-value: {p_val:.4e}')
    
    if p_val < 0.05:
        print(f'  ✅ 결론: p-value가 0.05 미만이므로, {col_name_kr}에 따른 유찰률 차이가 통계적으로 유의미합니다.')
        print(f'          (-> 룩업 테이블의 위험도 지표로서 타당함)')
    else:
        print(f'  ❌ 결론: p-value가 0.05 이상이므로, {col_name_kr}에 따른 유찰률 차이가 통계적으로 유의미하지 않습니다.')
    print()


# 3. 주요 변수별 카이제곱 검정 실행
run_chi2_test(df_lookup, 'org_group', '발주기관 그룹')
run_chi2_test(df_lookup, 'mthd_group', '계약방식')
run_chi2_test(df_lookup, 'busiDivs', '사업유형')