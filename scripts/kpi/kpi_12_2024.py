# %%
"""2024년 KPI 예비 검증 — 금액 5분위 4변수 모델 (유찰 위험도 공식 기준)

실행 순서
  1) python scripts/preprocessing/merge_bid_data.py   → data/kpi/bid_master_2021_2025.csv
  2) python scripts/ornt/compare_ornt_codeVld4.py     → data/analysis/ornt_code/orntCode_codeVld4_all.csv (기관그룹)
  3) python scripts/preprocessing/business_amount.py  → data/clean/business_amount_20210101_20251231.csv (사업규모금액)
  4) python scripts/kpi/kpi_12_2024.py                → 이 파일

기준
  - 대상: 공고 단위, 공고일 기준 연도, 판단보류 제외(merge_bid_data.py), 수의계약 제외(이 파일)
  - 기관그룹: 기관그룹 기준표를 기관코드(orntCode)로 연결
  - 사업규모금액: business_amount.py 결과 (기초금액 bsicExpt는 쓰지 않음)
  - 금액 5분위: 학습기간 결과확정 경쟁성 공고 중 금액이 있는 공고의 20/40/60/80 분위수,
    경계값 이하면 그 구간(오른쪽 경계 포함). 검증 연도에도 학습기간 경계를 그대로 적용
  - 위험도(4변수): 아래 단계 중 과거 공고가 10건 이상인 첫 단계의 과거 유찰률
      1 기관그룹 × 계약방법 × 사업구분 × 금액 5분위 (금액이 있을 때만)
      2 기관그룹 × 계약방법 × 사업구분
      3 기관그룹 × 사업구분
      4 기관그룹
      5 학습 전체
  - 3변수 모델(기관그룹 × 계약방법 × 사업구분)은 비교 기준선으로 함께 계산
  - 고위험군: 위험도 내림차순 → 근거공고수 내림차순 → 공고키 오름차순으로 정렬한 상위 20%
  - KPI 1: 고위험군 내 실제 유찰 ÷ 전체 실제 유찰 (경계 동점 상한·하한·기대값 함께 보고)
  - KPI 2: 순위 기준 10분위 등급과 등급별 실제 유찰률의 스피어만 상관계수
  - 위험도는 과거 관측 유찰률이며 그 공고의 확정 유찰 확률이 아니다

출력 (data/kpi/)
  - risk_lookup_2021_2023.csv     : 4변수 단계별 과거 유찰률 기준표
  - amount_quintile_2021_2023.csv : 금액 5분위 경계
  - risk_scored_2024.csv          : 2024년 공고별 위험도·근거공고수·근거유찰건수·적용단계·적용조건·대체사유
  - kpi2_decile_2024.csv / .png   : 10분위 실제 유찰률 vs 평균 예측 위험도
  - kpi2_decile_combo_2024.csv    : 분위 × 적용 조건별 건수·예측·실제 (꺾임 진단)
"""
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager


def find_project_dir():
    """data 폴더가 있는 상위 폴더를 프로젝트 폴더로 본다 (실행 위치와 무관)."""
    try:
        start = Path(__file__).resolve().parent
    except NameError:
        start = Path.cwd()
    for p in [start, *start.parents]:
        if (p / 'data').is_dir():
            return p
    return Path.cwd()


PROJECT_DIR = find_project_dir()
DATA_KPI_DIR = PROJECT_DIR / 'data' / 'kpi'
MASTER_PATH = DATA_KPI_DIR / 'bid_master_2021_2025.csv'
ORG_GROUP_PATH = PROJECT_DIR / 'data' / 'analysis' / 'ornt_code' / 'orntCode_codeVld4_all.csv'
AMOUNT_PATH = PROJECT_DIR / 'data' / 'clean' / 'business_amount_20210101_20251231.csv'

LOOKUP_PATH = DATA_KPI_DIR / 'risk_lookup_2021_2023.csv'
QUINTILE_PATH = DATA_KPI_DIR / 'amount_quintile_2021_2023.csv'
SCORED_PATH = DATA_KPI_DIR / 'risk_scored_2024.csv'
DECILE_PATH = DATA_KPI_DIR / 'kpi2_decile_2024.csv'
DECILE_FIG_PATH = DATA_KPI_DIR / 'kpi2_decile_2024.png'
DECILE_COMBO_PATH = DATA_KPI_DIR / 'kpi2_decile_combo_2024.csv'

TRAIN_YEARS = (2021, 2023)
EVAL_YEAR = 2024
MIN_SAMPLE = 10
TOP_RATIO = 0.20
KPI2_TARGET = 0.85

KEY = ['demandYear', 'orntCode', 'dcsNo', 'pblancNo', 'pblancOdr']
STAGES_4 = {
    1: ['org_group', 'mthd_group', 'busiDivs', 'amount_q'],
    2: ['org_group', 'mthd_group', 'busiDivs'],
    3: ['org_group', 'busiDivs'],
    4: ['org_group'],
    5: [],
}
STAGES_3 = {
    1: ['org_group', 'mthd_group', 'busiDivs'],
    2: ['org_group', 'busiDivs'],
    3: ['org_group'],
    4: [],
}
STAGE_LABELS_4 = {
    1: '1단계: 기관그룹×계약방법×사업구분×금액5분위',
    2: '2단계: 기관그룹×계약방법×사업구분',
    3: '3단계: 기관그룹×사업구분',
    4: '4단계: 기관그룹',
    5: '5단계: 학습 전체',
}


def norm_key(df):
    for c in KEY:
        df[c] = df[c].astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
    return df


# ---------------------------------------------------------------------------
# 1. 데이터 준비
# ---------------------------------------------------------------------------
for path, script in [(MASTER_PATH, 'scripts/preprocessing/merge_bid_data.py'),
                     (ORG_GROUP_PATH, 'scripts/ornt/compare_ornt_codeVld4.py'),
                     (AMOUNT_PATH, 'scripts/preprocessing/business_amount.py')]:
    if not path.exists():
        raise SystemExit(f'❌ {path} 가 없습니다. {script} 를 먼저 실행하세요.')

df = norm_key(pd.read_csv(MASTER_PATH, encoding='utf-8-sig', dtype={c: str for c in KEY}))
org = pd.read_csv(ORG_GROUP_PATH, encoding='utf-8-sig', dtype={'orntCode': str})
org = org[['orntCode', '기관그룹']].rename(columns={'기관그룹': 'org_group'})
df = df.merge(org, on='orntCode', how='left')

missing = df.loc[df['org_group'].isna(), 'orntCode'].unique()
if len(missing):
    print(f'⚠️ 기관그룹 기준표에 없는 기관코드 {len(missing)}개: {list(missing)[:10]} → "미분류"')
    df['org_group'] = df['org_group'].fillna('미분류')

amt = norm_key(pd.read_csv(AMOUNT_PATH, encoding='utf-8-sig', dtype=str))
amt = amt[KEY + ['사업규모금액', '금액출처', '입찰방법출처']]
amt['사업규모금액'] = pd.to_numeric(amt['사업규모금액'], errors='coerce')
n_before = len(df)
df = df.merge(amt, on=KEY, how='left', validate='one_to_one')
if len(df) != n_before:
    raise SystemExit('❌ 사업규모금액 연결 후 공고 수가 달라졌습니다. 공고키를 확인하세요.')

df['mthd_group'] = df['cntrctMth']
df = df[~df['cntrctMth'].astype(str).str.contains('수의', na=False)]
df['year'] = df['year'].astype(int)

train = df[df['year'].between(*TRAIN_YEARS)].copy()
test = df[(df['year'] == EVAL_YEAR) & df['fail_yn'].notna()].copy()
test['notice_key'] = test['pblancNo'].astype(str) + '-' + test['pblancOdr'].astype(str)

print(f'📂 학습 {TRAIN_YEARS[0]}~{TRAIN_YEARS[1]}년: {len(train):,}건 (유찰 {int(train["fail_yn"].sum()):,}), '
      f'사업규모금액 있음 {train["사업규모금액"].notna().sum():,}건')
print(f'📂 검증 {EVAL_YEAR}년: {len(test):,}건 (유찰 {int(test["fail_yn"].sum()):,}), '
      f'사업규모금액 있음 {test["사업규모금액"].notna().sum():,}건')

# ---------------------------------------------------------------------------
# 2. 금액 5분위 경계 (학습기간 금액만 사용)
# ---------------------------------------------------------------------------
q_edges = train['사업규모금액'].dropna().quantile([0.2, 0.4, 0.6, 0.8]).values
bins = [-np.inf, *q_edges, np.inf]
labels = ['Q1', 'Q2', 'Q3', 'Q4', 'Q5']
for d in (train, test):
    d['amount_q'] = pd.cut(d['사업규모금액'], bins=bins, labels=labels, right=True).astype(object)
    d.loc[d['사업규모금액'].isna(), 'amount_q'] = np.nan

pd.DataFrame({
    '구간': labels,
    '하한(초과)': [None, *q_edges],
    '상한(이하)': [*q_edges, None],
    '학습 공고 수': train['amount_q'].value_counts().reindex(labels).values,
}).to_csv(QUINTILE_PATH, index=False, encoding='utf-8-sig')
print('\n[금액 5분위 경계 (원)]  ' + ' / '.join(f'{v:,.0f}' for v in q_edges))


# ---------------------------------------------------------------------------
# 3. 위험도 산정 함수
# ---------------------------------------------------------------------------
def build_lookup(train, stages):
    rows = []
    for stage, cols in stages.items():
        t = train.dropna(subset=cols) if cols else train
        if cols:
            g = t.groupby(cols)['fail_yn'].agg(sample_count='count', fail_count='sum').reset_index()
        else:
            g = pd.DataFrame({'sample_count': [len(t)], 'fail_count': [t['fail_yn'].sum()]})
        g['stage'] = stage
        rows.append(g)
    lk = pd.concat(rows, ignore_index=True)
    lk['fail_rate'] = lk['fail_count'] / lk['sample_count']
    return lk


def score(test, lookup, stages):
    t = test.copy()
    t['fail_rate'] = np.nan
    t['sample_count'] = 0
    t['fail_count'] = 0
    t['risk_stage'] = 0
    t['applied_combo'] = ''
    for stage, cols in stages.items():
        todo = t['fail_rate'].isna()
        if not todo.any():
            break
        ref = lookup[(lookup['stage'] == stage) & (lookup['sample_count'] >= MIN_SAMPLE)]
        if cols:
            sub = t.loc[todo, cols].dropna()
            m = sub.reset_index().merge(ref[cols + ['fail_rate', 'sample_count', 'fail_count']],
                                        on=cols, how='inner').set_index('index')
            combo = m[cols].astype(str).agg(' / '.join, axis=1)
        else:
            m = pd.DataFrame(index=t.index[todo])
            for c in ['fail_rate', 'sample_count', 'fail_count']:
                m[c] = ref[c].iloc[0]
            combo = pd.Series('전체', index=m.index)
        t.loc[m.index, 'fail_rate'] = m['fail_rate']
        t.loc[m.index, 'sample_count'] = m['sample_count']
        t.loc[m.index, 'fail_count'] = m['fail_count']
        t.loc[m.index, 'risk_stage'] = stage
        t.loc[m.index, 'applied_combo'] = combo
    t['sample_count'] = t['sample_count'].astype(int)
    t['fail_count'] = t['fail_count'].astype(int)
    return t


def kpi1(t):
    ranked = t.sort_values(['fail_rate', 'sample_count', 'notice_key'],
                           ascending=[False, False, True]).reset_index(drop=True)
    n_top = int(round(len(ranked) * TOP_RATIO))
    total_fail = int(ranked['fail_yn'].sum())
    top = ranked.iloc[:n_top]
    cut = top['fail_rate'].iloc[-1]
    above = ranked[ranked['fail_rate'] > cut]
    tie = ranked[ranked['fail_rate'] == cut]
    tie_in = n_top - len(above)
    a_f, t_f = int(above['fail_yn'].sum()), int(tie['fail_yn'].sum())
    return ranked, {
        '검증 공고': len(ranked), '실제 유찰': total_fail, '고위험군': n_top,
        '포착 유찰': int(top['fail_yn'].sum()),
        'KPI 1': top['fail_yn'].sum() / total_fail * 100,
        '상한': (a_f + t_f) / total_fail * 100,
        '기대값': (a_f + t_f * tie_in / len(tie)) / total_fail * 100,
        '하한': a_f / total_fail * 100,
        '경계 위험도': cut, '경계 동점': len(tie), '동점 선정': tie_in,
        '위험도 값 수': ranked['fail_rate'].nunique(),
        '최대 동점(%)': ranked['fail_rate'].value_counts().iloc[0] / len(ranked) * 100,
    }


def kpi2(ranked):
    asc = ranked.iloc[::-1].reset_index(drop=True)          # 1등급 = 가장 낮은 위험
    asc['decile'] = pd.qcut(asc.index, 10, labels=range(1, 11))
    dec = asc.groupby('decile', observed=True).agg(
        total_count=('fail_yn', 'count'),
        actual_fail_count=('fail_yn', 'sum'),
        risk_min=('fail_rate', 'min'),
        risk_max=('fail_rate', 'max'),
        risk_mean=('fail_rate', 'mean'),
    ).reset_index()
    dec['actual_fail_rate(%)'] = (dec['actual_fail_count'] / dec['total_count'] * 100).round(2)
    dec['pred_risk_mean(%)'] = (dec['risk_mean'] * 100).round(2)
    rho, p = spearmanr(dec['decile'].astype(int), dec['actual_fail_rate(%)'])
    return asc, dec, rho, p


# ---------------------------------------------------------------------------
# 4. 4변수(공식) / 3변수(기준선) 위험도
# ---------------------------------------------------------------------------
lookup4 = build_lookup(train, STAGES_4)
lookup4.to_csv(LOOKUP_PATH, index=False, encoding='utf-8-sig')
s4 = score(test, lookup4, STAGES_4)
s3 = score(test, build_lookup(train, STAGES_3), STAGES_3)

has_amount = s4['amount_q'].notna()
s4['대체사유'] = ''
s4.loc[(s4['risk_stage'] > 1) & ~has_amount, '대체사유'] = '금액 미확인'
s4.loc[(s4['risk_stage'] > 2) & ~has_amount, '대체사유'] = '금액 미확인 + 표본 부족'
s4.loc[(s4['risk_stage'] > 1) & has_amount, '대체사유'] = '표본 부족'

ranked4, k4 = kpi1(s4)
ranked3, k3 = kpi1(s3)
asc4, dec4, rho4, p4 = kpi2(ranked4)
_, dec3, rho3, p3 = kpi2(ranked3)

print('\n' + '=' * 72)
print('📊 KPI 비교: 4변수(공식) vs 3변수(기준선)')
print('=' * 72)
rows = ['검증 공고', '실제 유찰', '고위험군', '포착 유찰', 'KPI 1', '상한', '기대값', '하한',
        '경계 위험도', '경계 동점', '동점 선정', '위험도 값 수', '최대 동점(%)']
cmp = pd.DataFrame({'4변수(공식)': [k4[r] for r in rows], '3변수(기준선)': [k3[r] for r in rows]}, index=rows)
cmp.loc['KPI 2 (Spearman)'] = [rho4, rho3]
cmp.loc['KPI 2 p값'] = [p4, p3]
print(cmp.to_string(float_format=lambda v: f'{v:,.4f}' if abs(v) < 1 else f'{v:,.2f}'))
print(f'  🎯 KPI 2 목표 {KPI2_TARGET} 이상: {"✅" if rho4 >= KPI2_TARGET else "❌"} (4변수)')

print('\n[4변수 위험도 산정 단계별 분포]')
st = (s4.assign(단계=s4['risk_stage'].map(STAGE_LABELS_4))
      .groupby(['단계', '대체사유']).size().rename('건수').reset_index())
st['비율(%)'] = (st['건수'] / len(s4) * 100).round(2)
print(st.to_string(index=False))

s4_out = s4.rename(columns={'fail_rate': '위험도', 'sample_count': '근거공고수', 'fail_count': '근거유찰건수',
                            'risk_stage': '적용단계', 'applied_combo': '적용조건'})
s4_out['3변수_위험도'] = s3['fail_rate']
out_cols = KEY + ['notice_key', 'pblancDate', 'ornt', 'org_group', 'mthd_group', 'busiDivs',
                  '사업규모금액', '금액출처', 'amount_q', '위험도', '근거공고수', '근거유찰건수',
                  '적용단계', '적용조건', '대체사유', '3변수_위험도', 'bidNm', 'bidResult', 'fail_yn']
s4_out[[c for c in out_cols if c in s4_out.columns]].to_csv(SCORED_PATH, index=False, encoding='utf-8-sig')

# ---------------------------------------------------------------------------
# 5. KPI 2 진단 (4변수): 분위 표·꺾임 구간 조합·동점 분포·선그래프
# ---------------------------------------------------------------------------
dec4.drop(columns='risk_mean').to_csv(DECILE_PATH, index=False, encoding='utf-8-sig')
print('\n' + '=' * 72)
print('📊 [KPI 2] 4변수 위험도 10분위')
print('=' * 72)
print(dec4.drop(columns='risk_mean').to_string(index=False))

rates = dec4['actual_fail_rate(%)'].tolist()
dips = [i + 1 for i in range(1, 10) if rates[i] < rates[i - 1]]

combo_tbl = (asc4.groupby(['decile', 'applied_combo'], observed=True)
             .agg(건수=('fail_yn', 'size'), 실제유찰=('fail_yn', 'sum'), 예측위험도=('fail_rate', 'mean'))
             .reset_index())
combo_tbl['예측위험도(%)'] = (combo_tbl.pop('예측위험도') * 100).round(2)
combo_tbl['실제유찰률(%)'] = (combo_tbl['실제유찰'] / combo_tbl['건수'] * 100).round(2)
combo_tbl['차이(%p)'] = (combo_tbl['실제유찰률(%)'] - combo_tbl['예측위험도(%)']).round(2)
combo_tbl = combo_tbl.sort_values(['decile', '건수'], ascending=[True, False])
combo_tbl.to_csv(DECILE_COMBO_PATH, index=False, encoding='utf-8-sig')

print('\n🔎 실제 유찰률이 앞 등급보다 낮아지는 구간')
for d in dips:
    print(f'  ▶ {d - 1}등급 {rates[d - 2]:.2f}% → {d}등급 {rates[d - 1]:.2f}%')
    for dd in (d - 1, d):
        for _, r in combo_tbl[combo_tbl['decile'] == dd].head(3).iterrows():
            print(f'     {dd}등급 | {r["applied_combo"]} | {r["건수"]:,}건 | '
                  f'예측 {r["예측위험도(%)"]:.1f}% → 실제 {r["실제유찰률(%)"]:.1f}%')

span = asc4.groupby('fail_rate')['decile'].nunique()
print('\n🔎 여러 등급에 걸친 같은 위험도')
for v in span[span > 1].index:
    sub = asc4[asc4['fail_rate'] == v].groupby('decile', observed=True)['fail_yn'].agg(['size', 'mean'])
    parts = ', '.join(f'{int(d)}등급 {int(r["size"]):,}건({r["mean"] * 100:.1f}%)' for d, r in sub.iterrows())
    print(f'  • 위험도 {v:.4f}: {parts}')

_installed = {f.name for f in font_manager.fontManager.ttflist}
for _font in ['Malgun Gothic', 'Noto Sans CJK KR', 'NanumGothic', 'AppleGothic']:
    if _font in _installed:
        plt.rcParams['font.family'] = _font
        break
plt.rcParams['axes.unicode_minus'] = False

x = dec4['decile'].astype(int)
fig, ax = plt.subplots(figsize=(8.5, 4.5), dpi=150)
ax.plot(x, dec4['actual_fail_rate(%)'], marker='o', color='#2a78d6', linewidth=2, label='실제 유찰률')
ax.plot(x, dec4['pred_risk_mean(%)'], marker='s', color='#52514e', linewidth=1.5,
        linestyle='--', label='평균 예측 위험도')
for d in dips:
    ax.scatter(d, rates[d - 1], s=140, facecolors='none', edgecolors='#eb6834', linewidths=2, zorder=3)
for xi, yi in zip(x, dec4['actual_fail_rate(%)']):
    ax.annotate(f'{yi:.1f}', (xi, yi), textcoords='offset points', xytext=(0, 8),
                ha='center', fontsize=8, color='#0b0b0b')
ax.set_xticks(range(1, 11))
ax.set_xlabel('위험도 등급 (1 = 가장 낮음)')
ax.set_ylabel('%')
ax.set_title(f'{EVAL_YEAR}년 위험도 10분위별 실제 유찰률 — 4변수 (Spearman ρ = {rho4:.4f})',
             loc='left', fontsize=12)
ax.grid(axis='y', color='#e4e3df', linewidth=0.8)
for side in ['top', 'right']:
    ax.spines[side].set_visible(False)
ax.legend(frameon=False, loc='upper left')
fig.text(0.01, 0.01, '주황 원 = 실제 유찰률이 앞 등급보다 낮아진 등급', fontsize=8, color='#52514e')
fig.tight_layout(rect=(0, 0.03, 1, 1))
fig.savefig(DECILE_FIG_PATH)
plt.close(fig)

print('\n' + '=' * 72)
for p in [LOOKUP_PATH, QUINTILE_PATH, SCORED_PATH, DECILE_PATH, DECILE_FIG_PATH, DECILE_COMBO_PATH]:
    print(f'📁 {p}')