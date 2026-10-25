# %%
"""[분석계획 4·5단계] 기술통계·시각화 + 추론통계

입력: data/analysis/analysis_master_2021_2023.csv  (01_bid_conditions_prepare.py 결과)
출력: data/analysis/results/
  - rate_<변수>.csv          조건별 건수·유찰·비유찰 건수·유찰률(95% 신뢰구간)·조정 표준화 잔차
  - chi2_summary.csv         검정 결과표 (분석건수, χ², 자유도, p값, Holm 보정 p값, Cramér's V,
                             유찰률 차이(%p), 기대빈도<5 비율)
                             Holm 보정은 기관그룹·계약방법·사업구분 3개 검정에 적용하고,
                             조합 검정은 위험도 참고용이라 보정에서 제외한다.
  - high_risk_categories.csv 잔차 기준 유찰이 유의하게 많은 범주/조합
  - fig_<변수>.png           유찰률 막대그래프 (전체 유찰률 기준선, 막대별 n)
  - analysis_results.xlsx    위 표들을 시트별로 묶은 파일
"""
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

try:
    BASE_DIR = Path(__file__).resolve().parents[2]
except NameError:
    BASE_DIR = Path.cwd()

INPUT_PATH = BASE_DIR / 'data' / 'analysis' / 'analysis_master_2021_2023.csv'
OUT_DIR = BASE_DIR / 'data' / 'analysis' / 'results'
OUT_DIR.mkdir(parents=True, exist_ok=True)

ALPHA = 0.05
RESID_CUT = 1.96      # 조정 표준화 잔차 기준 (|r| > 1.96 이면 유의수준 5%에서 기대와 다름)
MIN_EXPECTED = 5      # 기대 유찰 건수가 이보다 작은 범주는 병합

# ---------------------------------------------------------------------------
# 한글 글꼴 (Windows: 맑은 고딕, 그 외: Noto Sans CJK / AppleGothic)
# ---------------------------------------------------------------------------
_installed = {f.name for f in font_manager.fontManager.ttflist}
for _font in ['Malgun Gothic', 'Noto Sans CJK KR', 'NanumGothic', 'AppleGothic']:
    if _font in _installed:
        plt.rcParams['font.family'] = _font
        break
plt.rcParams['axes.unicode_minus'] = False

BAR_COLOR = '#2a78d6'
HIGH_COLOR = '#eb6834'
TEXT = '#0b0b0b'
MUTED = '#52514e'
GRID = '#e4e3df'


# ---------------------------------------------------------------------------
# 1. 데이터
# ---------------------------------------------------------------------------
df = pd.read_csv(INPUT_PATH, encoding='utf-8-sig', dtype=str)
df['fail_yn'] = df['fail_yn'].astype(int)
N = len(df)
P = df['fail_yn'].mean()
print(f'📂 분석 대상 {N:,}건, 유찰 {df["fail_yn"].sum():,}건, 전체 유찰률 {P:.2%}\n')


def merge_small_by_family(s, p, min_expected=MIN_EXPECTED):
    """기대 유찰 건수가 작은 범주를 같은 계열(괄호 앞 이름)로 묶는다.
    예: 2단계경쟁(분리) 9건 → 2단계경쟁(동시)와 합쳐 '2단계경쟁'."""
    counts = s.value_counts()
    small = counts[counts * p < min_expected].index
    family = s.str.split('(').str[0]
    small_families = set(family[s.isin(small)])
    return s.where(~family.isin(small_families), family), list(small)


df['mthd'], merged_mthd = merge_small_by_family(df['cntrctMth'], P)

# 조합 변수: 기대 유찰 건수가 5 미만인 조합은 '소표본 조합(병합)'으로 묶음
df['combo'] = df['org_group'] + ' | ' + df['mthd'] + ' | ' + df['busiDivs']
combo_n = df['combo'].value_counts()
small_combos = combo_n[combo_n * P < MIN_EXPECTED].index
df['combo_test'] = df['combo'].where(~df['combo'].isin(small_combos), '소표본 조합(병합)')

VARIABLES = [
    ('org_group', '발주기관 그룹'),
    ('mthd', '계약방법'),
    ('busiDivs', '사업구분'),
    ('combo_test', '기관×계약방법×사업구분 조합'),
]
HOLM_VARS = ['org_group', 'mthd', 'busiDivs']   # 주 검정 (Holm 보정 대상)
MERGED_LABEL = '소표본 조합(병합)'


# ---------------------------------------------------------------------------
# 2. 기술통계 + 카이제곱 + 조정 표준화 잔차
# ---------------------------------------------------------------------------
def wilson_ci(k, n, z=1.96):
    """유찰률의 95% Wilson 신뢰구간(%). 건수가 적은 범주일수록 구간이 넓다."""
    k = np.asarray(k, dtype=float)
    n = np.asarray(n, dtype=float)
    p = k / n
    denom = 1 + z ** 2 / n
    center = (p + z ** 2 / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / denom
    return ((center - half) * 100).round(2), ((center + half) * 100).round(2)


def holm(pvals):
    """Holm 보정 p값 (statsmodels의 multipletests(method='holm')과 같은 결과)."""
    pvals = np.asarray(pvals, dtype=float)
    m = len(pvals)
    adj = np.empty(m)
    running = 0.0
    for rank, i in enumerate(np.argsort(pvals)):
        running = max(running, (m - rank) * pvals[i])
        adj[i] = min(running, 1.0)
    return adj


def analyze(col):
    table = pd.crosstab(df[col], df['fail_yn']).reindex(columns=[0, 1], fill_value=0)
    chi2, p_val, dof, expected = chi2_contingency(table)
    n = table.values.sum()
    cramers_v = np.sqrt(chi2 / (n * (min(table.shape) - 1)))

    row_tot = table.sum(axis=1)
    col_tot = table.sum(axis=0)
    exp_fail = expected[:, 1]
    adj_resid = (table[1] - exp_fail) / np.sqrt(
        exp_fail * (1 - row_tot / n) * (1 - col_tot[1] / n)
    )

    ci_low, ci_high = wilson_ci(table[1].values, row_tot.values)
    rate = pd.DataFrame({
        '범주': table.index,
        '건수': row_tot.values,
        '유찰건수': table[1].values,
        '비유찰건수': table[0].values,
        '유찰률(%)': (table[1] / row_tot * 100).round(2).values,
        '95% CI 하한(%)': ci_low,
        '95% CI 상한(%)': ci_high,
        '기대유찰건수': exp_fail.round(1),
        '조정표준화잔차': adj_resid.round(2).values,
    })
    rate['판정'] = np.select(
        [rate['조정표준화잔차'] > RESID_CUT, rate['조정표준화잔차'] < -RESID_CUT],
        ['유찰 많음', '유찰 적음'], default='',
    )
    rate = rate.sort_values('유찰률(%)', ascending=False).reset_index(drop=True)

    # 유찰률 차이(%p): 가장 높은 범주 - 가장 낮은 범주 (병합된 소표본 조합은 제외)
    real = rate[rate['범주'] != MERGED_LABEL]['유찰률(%)']
    gap = real.max() - real.min()

    summary = {
        '분석건수': int(n),
        'χ²': round(chi2, 2),
        '자유도': dof,
        'p값': p_val,
        "Cramér's V": round(cramers_v, 4),
        '유찰률 차이(%p)': round(gap, 2),
        '범주 수': table.shape[0],
        '기대빈도<5 칸 비율(%)': round((expected < 5).mean() * 100, 1),
        '관련 강도': ('무시할 수준(<0.1)' if cramers_v < 0.1 else
                  '약함(0.1~0.3)' if cramers_v < 0.3 else
                  '중간(0.3~0.5)' if cramers_v < 0.5 else '강함(≥0.5)'),
    }
    return rate, summary


results = {}
summaries = []
for col, label in VARIABLES:
    rate, summary = analyze(col)
    results[col] = rate
    summaries.append({'변수': label, '_col': col, **summary})
    rate.to_csv(OUT_DIR / f'rate_{col}.csv', index=False, encoding='utf-8-sig')

# Holm 보정: 주 검정 3개(기관그룹·계약방법·사업구분)에만 적용, 결론은 보정 p값 기준
main_idx = [i for i, s in enumerate(summaries) if s['_col'] in HOLM_VARS]
adj = holm([summaries[i]['p값'] for i in main_idx])
for i, a in zip(main_idx, adj):
    summaries[i]['보정 p값(Holm)'] = a
    summaries[i]['결론'] = 'H0 기각' if a < ALPHA else 'H0 채택'
for s in summaries:
    if s['_col'] not in HOLM_VARS:
        s['보정 p값(Holm)'] = None
        s['결론'] = ('H0 기각' if s['p값'] < ALPHA else 'H0 채택') + ' (참고, 보정 제외)'

for (col, label), s in zip(VARIABLES, summaries):
    print('=' * 72)
    print(f'📊 {label}')
    print('=' * 72)
    print(results[col].to_string(index=False))
    adj_txt = f"{s['보정 p값(Holm)']:.3e}" if s['보정 p값(Holm)'] is not None else '보정 제외'
    v = s["Cramér's V"]
    print(f"  n={s['분석건수']:,}  χ²={s['χ²']:,}  df={s['자유도']}  p={s['p값']:.3e}  "
          f"Holm p={adj_txt}  V={v}  차이={s['유찰률 차이(%p)']}%p  "
          f"→ {s['결론']}, {s['관련 강도']}")
    if s['기대빈도<5 칸 비율(%)'] > 20:
        print('  ⚠️ 기대빈도 5 미만 칸이 20%를 넘습니다. 범주 병합을 검토하세요.')
    print()

SUMMARY_COLS = ['변수', '분석건수', 'χ²', '자유도', 'p값', '보정 p값(Holm)', "Cramér's V",
                '유찰률 차이(%p)', '범주 수', '기대빈도<5 칸 비율(%)', '결론', '관련 강도']
chi2_summary = pd.DataFrame(summaries)[SUMMARY_COLS]
chi2_summary['p값'] = chi2_summary['p값'].map(lambda v: f'{v:.3e}')
chi2_summary['보정 p값(Holm)'] = chi2_summary['보정 p값(Holm)'].map(
    lambda v: f'{v:.3e}' if pd.notna(v) else '참고(보정 제외)')
chi2_summary.to_csv(OUT_DIR / 'chi2_summary.csv', index=False, encoding='utf-8-sig')

# 잔차 기준 고위험 범주/조합
high = []
for col, label in VARIABLES:
    r = results[col]
    r = r[(r['판정'] == '유찰 많음') & (r['범주'] != '소표본 조합(병합)')]
    for _, row in r.iterrows():
        high.append({'변수': label, **row.to_dict()})
high_risk = pd.DataFrame(high)
high_risk.to_csv(OUT_DIR / 'high_risk_categories.csv', index=False, encoding='utf-8-sig')

# 전체 결과를 CSV 하나로 정리 (변수별 범주 결과 + 해당 변수의 검정 결과)
summary_by_var = {row['변수']: row for row in summaries}
all_rows = []
for col, label in VARIABLES:
    s = summary_by_var[label]
    r = results[col].copy()
    r.insert(0, '변수', label)
    r['χ²'] = s['χ²']
    r['자유도'] = s['자유도']
    r['p값'] = f"{s['p값']:.3e}"
    r['보정 p값(Holm)'] = (f"{s['보정 p값(Holm)']:.3e}" if s['보정 p값(Holm)'] is not None
                         else '참고(보정 제외)')
    r["Cramér's V"] = s["Cramér's V"]
    r['유찰률 차이(%p)'] = s['유찰률 차이(%p)']
    r['결론'] = s['결론']
    r['관련 강도'] = s['관련 강도']
    all_rows.append(r)
pd.concat(all_rows, ignore_index=True).to_csv(
    OUT_DIR / 'analysis_summary.csv', index=False, encoding='utf-8-sig'
)

print('📌 병합 처리')
print(f'  - 계약방법: 기대 유찰 건수 {MIN_EXPECTED} 미만 범주 {merged_mthd} → 같은 계열로 병합')
print(f'  - 조합: {len(small_combos)}개 조합({combo_n[small_combos].sum():,}건) → "소표본 조합(병합)"')


# ---------------------------------------------------------------------------
# 3. 유찰률 막대그래프
# ---------------------------------------------------------------------------
def bar_chart(rate, title, path, top=None):
    r = rate[rate['범주'] != '소표본 조합(병합)']
    if top:
        r = r.head(top)
    r = r.iloc[::-1]  # 위에서부터 높은 순
    h = max(2.6, 0.42 * len(r) + 1.2)
    fig, ax = plt.subplots(figsize=(8.5, h), dpi=150)

    colors = [HIGH_COLOR if j == '유찰 많음' else BAR_COLOR for j in r['판정']]
    ax.barh(r['범주'], r['유찰률(%)'], color=colors, height=0.6, edgecolor='white', linewidth=2)
    ax.axvline(P * 100, color=MUTED, linestyle='--', linewidth=1.2)
    ax.text(P * 100, len(r) - 0.35, f' 전체 {P:.1%}', color=MUTED, fontsize=9, va='bottom')

    xmax = r['유찰률(%)'].max()
    for i, (v, n) in enumerate(zip(r['유찰률(%)'], r['건수'])):
        ax.text(v + xmax * 0.01, i, f'{v:.1f}%  (n={n:,})', va='center', fontsize=9, color=TEXT,
                bbox=dict(facecolor='white', edgecolor='none', pad=1.5), zorder=3)

    ax.set_xlim(0, xmax * 1.35)
    ax.set_xlabel('유찰률(%)', color=MUTED)
    ax.set_title(title, loc='left', fontsize=12, color=TEXT, pad=10)
    ax.tick_params(colors=MUTED, length=0)
    ax.grid(axis='x', color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ['top', 'right', 'left']:
        ax.spines[side].set_visible(False)
    ax.spines['bottom'].set_color(GRID)

    fig.text(0.01, 0.005, '주황 = 조정 표준화 잔차 > 1.96 (기대보다 유찰이 유의하게 많음)',
             fontsize=8, color=MUTED)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path)
    plt.close(fig)


for col, label in VARIABLES:
    top = 15 if col == 'combo_test' else None
    title = f'{label}별 유찰률' + (' (상위 15개)' if top else '')
    bar_chart(results[col], title, OUT_DIR / f'fig_{col}.png', top=top)

# ---------------------------------------------------------------------------
# 4. 엑셀로 묶어 저장
# ---------------------------------------------------------------------------
try:
    with pd.ExcelWriter(OUT_DIR / 'analysis_results.xlsx') as xw:
        chi2_summary.to_excel(xw, sheet_name='검정결과', index=False)
        high_risk.to_excel(xw, sheet_name='고위험범주', index=False)
        for col, label in VARIABLES:
            results[col].to_excel(xw, sheet_name=f'유찰률_{col}'[:31], index=False)
    print(f'\n🚀 결과 저장: {OUT_DIR}')
except ImportError:
    print(f'\n🚀 CSV/PNG 저장: {OUT_DIR} (엑셀 저장은 openpyxl 설치 필요)')

print('\n' + chi2_summary.to_string(index=False))