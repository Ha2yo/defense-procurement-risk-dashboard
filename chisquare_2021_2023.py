# %%
"""변수별 유찰률 차이 카이제곱 독립성 검정 (2021~2023년 학습 데이터 원본 기준)."""
import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency

from kpi_common import load_master, get_train

print('📂 학습 데이터 로드 중 (수의계약 제외, 2021~2023년)...')
df_train = get_train(load_master())
print(f'  - 검정 대상: {len(df_train):,}건\n')


def run_chi2_test(df, group_col, col_name_kr):
    # 교차표: 행 = 범주, 열 = [낙찰(0), 유찰(1)]
    table = pd.crosstab(df[group_col], df['fail_yn'])
    chi2, p_val, dof, expected = chi2_contingency(table)

    # 효과크기 Cramér's V (0.1 작음 / 0.3 중간 / 0.5 큼)
    n = table.values.sum()
    cramers_v = np.sqrt(chi2 / (n * (min(table.shape) - 1)))
    low_expected = int((expected < 5).sum())

    print('=' * 60)
    print(f'📊 [{col_name_kr} ({group_col}) 카이제곱 검정 결과]')
    print('=' * 60)
    print(f'  • 카이제곱 통계량 (χ²): {chi2:,.4f}')
    print(f'  • 자유도 (d.o.f): {dof}')
    print(f'  • p-value: {p_val:.4e}')
    print(f"  • 효과크기 (Cramér's V): {cramers_v:.4f}")
    if low_expected:
        print(f'  ⚠️ 기대빈도 5 미만 칸이 {low_expected}개 있어 검정 결과가 부정확할 수 있습니다.')

    if p_val < 0.05:
        print(f'  ✅ 결론: {col_name_kr}에 따른 유찰률 차이가 통계적으로 유의미합니다.')
    else:
        print(f'  ❌ 결론: {col_name_kr}에 따른 유찰률 차이가 통계적으로 유의미하지 않습니다.')
    print()


run_chi2_test(df_train, 'org_group', '발주기관 그룹')
run_chi2_test(df_train, 'mthd_group', '계약방식')
run_chi2_test(df_train, 'busiDivs', '사업유형')