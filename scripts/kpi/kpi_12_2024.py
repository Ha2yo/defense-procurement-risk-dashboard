# %%
"""2024년 KPI 예비 검증 (위험도 룩업 + KPI 1 + KPI 2를 한 번에).

실행 순서
  1) python scripts/preprocessing/merge_big_data.py   → data/kpi/bid_master_2021_2025.csv
  2) python scripts/ornt/compare_ornt_codeVld4.py     → data/analysis/ornt_code/orntCode_codeVld4_all.csv (기관그룹)
  3) python scripts/kpi/kpi_12_2024.py                → 이 파일

기준
  - 대상: 수의계약 제외, 공고 단위, 연도는 공고일 기준 (merge_bid_data.py에서 처리)
  - 기관그룹: orntCode_codeVld4_all.csv의 기관그룹 열을 기관코드(orntCode)로 붙임
  - 위험도: 2021~2023년 기관그룹 × 계약방법 × 사업구분 조합의 과거 유찰률
  - 표본 10건 미만: 상위 조합 대체 (기관그룹×사업구분 → 기관그룹 → 학습 전체)
  - 고위험군: 위험도 내림차순 → 표본 수 내림차순 → 공고키 오름차순으로 정렬한 상위 20%
  - KPI 1: 고위험군 내 실제 유찰 ÷ 전체 실제 유찰
  - KPI 2: 위험도 10분위와 분위별 실제 유찰률의 스피어만 상관계수

출력 (data/kpi/)
  - risk_lookup_2021_2023.csv : 단계별 과거 유찰률 표
  - risk_stage_2024.csv       : 상위 조합으로 대체된(2~4단계) 2024년 공고 목록
  - kpi2_decile_2024.csv      : 10분위별 건수·실제 유찰률·위험도 범위·평균 예측 위험도
  - kpi2_decile_2024.png      : 10분위 실제 유찰률 vs 평균 예측 위험도 선그래프
  - kpi2_decile_combo_2024.csv: 분위 × 적용 조합별 건수·예측 위험도·실제 유찰률 (꺾임 진단용)
"""
# 팀원용 안내: 현재 전처리 파일명은 merge_big_data.py입니다.
# 이 파일은 마스터를 읽는 기존 3변수 모델 버전이며, 2024년만 평가합니다.
# 순위확정 결과 판정은 전처리가 담당하고 여기서는 확정된 fail_yn(0/1)을 사용합니다.
# 모듈을 import해도 아래 평가 코드가 실행되므로 재사용 API처럼 import하지 않습니다.
from pathlib import Path

import pandas as pd
from scipy.stats import spearmanr
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

# scripts/kpi에 배치한 파일 위치를 기준으로 프로젝트 루트를 계산합니다.
# __file__이 없는 대화형 환경에서는 현재 폴더를 쓰므로 프로젝트 루트에서 실행해야 합니다.
try:
    PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
except NameError:
    PROJECT_DIR = Path.cwd()

DATA_KPI_DIR = PROJECT_DIR / 'data' / 'kpi'
MASTER_PATH = DATA_KPI_DIR / 'bid_master_2021_2025.csv'
ORG_GROUP_PATH = PROJECT_DIR / 'data' / 'analysis' / 'ornt_code' / 'orntCode_codeVld4_all.csv'
LOOKUP_PATH = DATA_KPI_DIR / 'risk_lookup_2021_2023.csv'
STAGE_PATH = DATA_KPI_DIR / 'risk_stage_2024.csv'
DECILE_PATH = DATA_KPI_DIR / 'kpi2_decile_2024.csv'
DECILE_FIG_PATH = DATA_KPI_DIR / 'kpi2_decile_2024.png'
DECILE_COMBO_PATH = DATA_KPI_DIR / 'kpi2_decile_combo_2024.csv'

# 설정은 이전 통합판과 같습니다. 이번 편집에서는 주석만 보강했습니다.
# KPI1_TARGET은 기존 문서의 참고 목표이며 현재 표본에서 새로 추정한 값이 아닙니다.
TRAIN_YEARS = (2021, 2023)
EVAL_YEAR = 2024
MIN_SAMPLE = 10
TOP_RATIO = 0.20
KPI1_TARGET = 34.73   # % (KPI 정의서 목표, 허용범위 1%p)
KPI2_TARGET = 0.85

# 여기의 KEYS는 공고 식별키가 아니라 위험도 집계용 3변수 조합입니다.
# mthd_group은 계약방법(cntrctMth)을 그대로 복사한 열입니다.
KEYS = ['org_group', 'mthd_group', 'busiDivs']
STAGE_KEYS = {
    1: ['org_group', 'mthd_group', 'busiDivs'],   # 자기 조합
    2: ['org_group', 'busiDivs'],                 # 계약방법 제외
    3: ['org_group'],                             # 기관그룹만
    4: [],                                        # 학습 데이터 전체
}
STAGE_LABELS = {
    1: '1단계: 자기 조합 (10건 이상)',
    2: '2단계: 기관그룹×사업구분 전체 유찰률',
    3: '3단계: 기관그룹 전체 유찰률',
    4: '4단계: 학습 데이터 전체 유찰률',
}


# ---------------------------------------------------------------------------
# 1. 데이터 준비
# ---------------------------------------------------------------------------
for path, script in [(MASTER_PATH, 'scripts/preprocessing/merge_big_data.py'),
                     (ORG_GROUP_PATH, 'scripts/ornt/compare_ornt_codeVld4.py')]:
    if not path.exists():
        raise SystemExit(f'❌ {path} 가 없습니다. {script} 를 먼저 실행하세요.')

# 공고 마스터와 기관코드별 그룹 기준표를 붙입니다.
# 제공된 기준표는 기관코드별 1행이며, 누락 코드는 아래에서 미분류로 보존합니다.
df = pd.read_csv(MASTER_PATH, encoding='utf-8-sig')
org = pd.read_csv(ORG_GROUP_PATH, encoding='utf-8-sig', dtype={'orntCode': str})
org = org[['orntCode', '기관그룹']].rename(columns={'기관그룹': 'org_group'})
df = df.merge(org, on='orntCode', how='left')

missing = df.loc[df['org_group'].isna(), 'orntCode'].unique()
if len(missing):
    print(f'⚠️ 기관그룹 기준표에 없는 기관코드 {len(missing)}개: {list(missing)[:10]} → "미분류". '
          'compare_ornt_codeVld4.py를 다시 실행하세요.')
    df['org_group'] = df['org_group'].fillna('미분류')

# 수의계약은 마스터에는 남겨 두고 이 분석 단계에서 제외합니다.
df['mthd_group'] = df['cntrctMth']
df = df[~df['cntrctMth'].astype(str).str.contains('수의', na=False)]

# 학습 시점 제한: 공고연도뿐 아니라 해당 공고의 최대 개찰일도 2023년 말 이하여야 합니다.
# result_last_date는 전처리에서 집계한 개찰일 대용값이며 실제 공개·확정 시점은 아닙니다.
# 날짜 결측/해석 실패는 NaT가 되어 이 학습 조건을 충족하지 못합니다.
result_available_date = pd.to_datetime(df['result_last_date'].astype(str), format='%Y%m%d', errors='coerce')
train = df[df['year'].between(*TRAIN_YEARS) & result_available_date.le(pd.Timestamp('2023-12-31'))].copy()
test = df[(df['year'] == EVAL_YEAR) & df['fail_yn'].notna()].copy()
# 기존 모델 정렬용 키는 공고번호-차수 문자열입니다. SC-1의 5개 항목 전체키와 다릅니다.
test['notice_key'] = test['pblancNo'].astype(str) + '-' + test['pblancOdr'].astype(str)

print(f'📂 학습 {TRAIN_YEARS[0]}~{TRAIN_YEARS[1]}년: {len(train):,}건 (유찰 {int(train["fail_yn"].sum()):,})')
print(f'📂 검증 {EVAL_YEAR}년: {len(test):,}건 (유찰 {int(test["fail_yn"].sum()):,})')


# ---------------------------------------------------------------------------
# 2. 위험도 룩업 표 (단계별 과거 유찰률)
# ---------------------------------------------------------------------------
# 학습 자료만으로 각 대체 단계의 표본수와 유찰 수를 집계합니다.
# 룩업 표에는 10건 미만도 기록하되, 실제 위험도 적용 시에는 최소 표본 조건을 검사합니다.
rows = []
for stage, cols in STAGE_KEYS.items():
    if cols:
        g = train.groupby(cols)['fail_yn'].agg(sample_count='count', fail_count='sum').reset_index()
    else:
        g = pd.DataFrame({'sample_count': [len(train)], 'fail_count': [train['fail_yn'].sum()]})
    g['stage'] = stage
    rows.append(g)
lookup = pd.concat(rows, ignore_index=True)[['stage'] + KEYS + ['sample_count', 'fail_count']]
lookup['fail_rate'] = lookup['fail_count'] / lookup['sample_count']
DATA_KPI_DIR.mkdir(parents=True, exist_ok=True)
lookup.to_csv(LOOKUP_PATH, index=False, encoding='utf-8-sig')


# ---------------------------------------------------------------------------
# 3. 2024년 공고에 위험도 부여 (10건 이상인 첫 단계의 유찰률)
# ---------------------------------------------------------------------------
# own_count는 가장 구체적인 자기 조합의 표본수입니다.
# 최종 사용 표본수(sample_count)와 다를 수 있으며, 대체 이유를 설명하는 진단값입니다.
own = lookup[lookup['stage'] == 1][KEYS + ['sample_count']].rename(columns={'sample_count': 'own_count'})
test = test.merge(own, on=KEYS, how='left')
test['own_count'] = test['own_count'].fillna(0).astype(int)
test['fail_rate'] = float('nan')
test['sample_count'] = 0
test['risk_stage'] = 0
test['applied_combo'] = ''

# 위험도가 아직 없는 공고만 처리합니다. 10건 이상인 첫 단계가 결정되면 이후 대체하지 않습니다.
# SC-1의 3단계 순차보정은 이 파일에는 없습니다.
for stage, cols in STAGE_KEYS.items():
    todo = test['fail_rate'].isna()
    if not todo.any():
        break
    ref = lookup[(lookup['stage'] == stage) & (lookup['sample_count'] >= MIN_SAMPLE)]
    if cols:
        # 원래 공고 행의 인덱스를 보존해 병합된 조회 결과를 같은 공고 위치에 다시 기록합니다.
        m = test.loc[todo, cols].reset_index().merge(ref[cols + ['fail_rate', 'sample_count']],
                                                     on=cols, how='left').set_index('index')
        m = m[m['fail_rate'].notna()]
        combo = m[cols].astype(str).agg(' / '.join, axis=1)
    else:
        m = pd.DataFrame(index=test.index[todo])
        m['fail_rate'] = ref['fail_rate'].iloc[0]
        m['sample_count'] = ref['sample_count'].iloc[0]
        combo = pd.Series('전체', index=m.index)
    test.loc[m.index, 'fail_rate'] = m['fail_rate']
    test.loc[m.index, 'sample_count'] = m['sample_count']
    test.loc[m.index, 'risk_stage'] = stage
    test.loc[m.index, 'applied_combo'] = combo

test['sample_count'] = test['sample_count'].astype(int)

# 단계별 분포
stage_summary = (
    test.assign(단계=test['risk_stage'].map(STAGE_LABELS))
    .groupby('단계')
    .agg(건수=('fail_yn', 'size'), 유찰=('fail_yn', 'sum'),
         학습기간_0건_조합=('own_count', lambda s: int((s == 0).sum())))
    .reindex(list(STAGE_LABELS.values()), fill_value=0)
)
stage_summary['비율(%)'] = (stage_summary['건수'] / len(test) * 100).round(2)

# risk_stage_2024.csv는 대체된 공고(2~4단계)만 저장하며 전체 공고별 순위 파일이 아닙니다.
stage_cols = (['notice_key', 'pblancDate', 'ornt'] + KEYS
              + ['own_count', 'risk_stage', 'applied_combo', 'sample_count', 'fail_rate', 'bidNm', 'bidResult', 'fail_yn'])
test[test['risk_stage'] != 1][stage_cols].sort_values(['risk_stage', 'notice_key']) \
    .to_csv(STAGE_PATH, index=False, encoding='utf-8-sig')

# 고위험 순 정렬: 위험도 내림차순 → 표본 수 내림차순 → 공고키 오름차순
# 최종 정렬 기준을 추가하거나 바꾸지 않았습니다. 같은 위험도에서는 참조 표본수가 큰 공고가 먼저입니다.
# 공고키까지 같은 경우를 포함한 입력 순서도 이전 통합판과 동일하게 유지합니다.
ranked = test.sort_values(['fail_rate', 'sample_count', 'notice_key'],
                          ascending=[False, False, True]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 4. KPI 1: 고위험 공고 사전 식별률
# ---------------------------------------------------------------------------
# KPI1 분모는 평가 전체의 실제 유찰 수, 분자는 상위 20% 안의 실제 유찰 수입니다.
# 고위험군 안의 유찰 비중(정밀도)과 혼동하지 않습니다.
n_total = len(ranked)
n_top = int(round(n_total * TOP_RATIO))
total_fail = int(ranked['fail_yn'].sum())
top = ranked.iloc[:n_top]
top_fail = int(top['fail_yn'].sum())
kpi1 = top_fail / total_fail * 100

# 경계 동점 구간: 동점 안의 순서는 위험도와 무관하므로 상한·하한·기대값을 함께 본다
cut = top['fail_rate'].iloc[-1]
above = ranked[ranked['fail_rate'] > cut]
tie = ranked[ranked['fail_rate'] == cut]
tie_in = n_top - len(above)
above_fail, tie_fail = int(above['fail_yn'].sum()), int(tie['fail_yn'].sum())
# 아래 하한·상한은 경계의 같은 주 위험도를 전부 제외/포함한 민감도 값입니다.
# 각각 선택 공고 수가 달라지므로, 상위 20%를 고정한 엄밀한 최소/최대 범위는 아닙니다.
lower = above_fail / total_fail * 100
upper = (above_fail + tie_fail) / total_fail * 100
expected_fail = above_fail + tie_fail * tie_in / len(tie)
expected = expected_fail / total_fail * 100

print('\n' + '=' * 70)
print('📊 [KPI 1] 고위험 공고 사전 식별률')
print('=' * 70)
print('[위험도 산정 단계별 분포]')
print(stage_summary.to_string())
print(f'  • 상위 조합 대체 공고 목록: {STAGE_PATH}')
print('-' * 70)
print(f'  • 검증 공고 {n_total:,}건, 실제 유찰 {total_fail:,}건')
print(f'  • 고위험군(상위 20%) {n_top:,}건, 그중 실제 유찰 {top_fail:,}건')
print(f'  • 경계 위험도 {cut:.4f}: 동점 {len(tie):,}건 중 {tie_in:,}건 포함')
print('-' * 70)
print(f'  상한 (동점 전부 포함): {len(above) + len(tie):,}건, {above_fail + tie_fail:,}/{total_fail:,} = {upper:.2f}%')
print(f'  보고값 (동점 {tie_in:,}건):  {n_top:,}건, {top_fail:,}/{total_fail:,} = {kpi1:.2f}%')
print(f'  기대값 (동점 비례 배분): {n_top:,}건, 약 {expected_fail:.0f}/{total_fail:,} = {expected:.2f}%')
print(f'  하한 (동점 전부 제외): {len(above):,}건, {above_fail:,}/{total_fail:,} = {lower:.2f}%')
print('-' * 70)
print(f'  🎯 [KPI 1 식별률]: {kpi1:.2f}%  (목표 {KPI1_TARGET}%, 허용범위 1%p)')


# ---------------------------------------------------------------------------
# 5. KPI 2: 위험도 10분위 단조성
# ---------------------------------------------------------------------------
# 순위 위치로 10분위를 나누며 같은 위험도도 여러 분위에 걸칠 수 있습니다.
# 따라서 동점 내부 정렬 방식도 KPI2에 영향을 줄 수 있습니다.
asc = ranked.iloc[::-1].reset_index(drop=True)   # 1등급 = 가장 낮은 위험
asc['decile'] = pd.qcut(asc.index, 10, labels=range(1, 11))
decile = asc.groupby('decile', observed=True).agg(
    total_count=('fail_yn', 'count'),
    actual_fail_count=('fail_yn', 'sum'),
    risk_min=('fail_rate', 'min'),
    risk_max=('fail_rate', 'max'),
    risk_mean=('fail_rate', 'mean'),          # 평균 예측 위험도
).reset_index()
decile['actual_fail_rate(%)'] = (decile['actual_fail_count'] / decile['total_count'] * 100).round(2)
decile['pred_risk_mean(%)'] = (decile['risk_mean'] * 100).round(2)
# KPI2는 분위 번호와 소수 2자리로 반올림한 실제 유찰률(%)의 순위 상관입니다.
# 개별 공고의 위험도와 정답을 직접 상관 계산하는 방식이 아닙니다.
rho, p_value = spearmanr(decile['decile'].astype(int), decile['actual_fail_rate(%)'])

print('\n' + '=' * 70)
print('📊 [KPI 2] 위험도 10분위 구간별 단조성')
print('=' * 70)
print(decile.to_string(index=False))
print('-' * 70)
print(f'  • 서로 다른 위험도 값: {ranked["fail_rate"].nunique()}개')
print(f'  🎯 [스피어만 Rho]: {rho:.4f}  (목표 {KPI2_TARGET} 이상, p-value {p_value:.4e}) '
      f'{"✅" if rho >= KPI2_TARGET else "❌"}')
print('=' * 70)
print(f'📁 룩업 표 저장: {LOOKUP_PATH}')


# ---------------------------------------------------------------------------
# 6. KPI 2 진단: 분위 표·선그래프·꺾임 구간 조합·동점 분포
# ---------------------------------------------------------------------------
decile.drop(columns='risk_mean').to_csv(DECILE_PATH, index=False, encoding='utf-8-sig')

# 꺾임 등급: 실제 유찰률이 앞 등급보다 낮아지는 등급
rates = decile['actual_fail_rate(%)'].tolist()
dips = [i + 1 for i in range(1, 10) if rates[i] < rates[i - 1]]

# 분위 × 적용 조합
asc['combo'] = asc['org_group'] + ' / ' + asc['mthd_group'] + ' / ' + asc['busiDivs']
# 각 분위에 어떤 3변수 조합이 많이 들어갔는지 확인해 단조성이 꺾이는 이유를 살펴봅니다.
# 이 진단 표는 이미 계산된 위험도와 순위를 변경하지 않습니다.
combo_tbl = (asc.groupby(['decile', 'combo'], observed=True)
             .agg(건수=('fail_yn', 'size'), 실제유찰=('fail_yn', 'sum'), 예측위험도=('fail_rate', 'mean'))
             .reset_index())
combo_tbl['예측위험도(%)'] = (combo_tbl.pop('예측위험도') * 100).round(2)
combo_tbl['실제유찰률(%)'] = (combo_tbl['실제유찰'] / combo_tbl['건수'] * 100).round(2)
combo_tbl['차이(%p)'] = (combo_tbl['실제유찰률(%)'] - combo_tbl['예측위험도(%)']).round(2)
combo_tbl = combo_tbl.sort_values(['decile', '건수'], ascending=[True, False])
combo_tbl.to_csv(DECILE_COMBO_PATH, index=False, encoding='utf-8-sig')

print('\n' + '=' * 70)
print('🔎 [KPI 2 진단] 실제 유찰률이 앞 등급보다 낮아지는 구간')
print('=' * 70)
for d in dips:
    print(f'  ▶ {d - 1}등급 {rates[d - 2]:.2f}% → {d}등급 {rates[d - 1]:.2f}%')
    for dd in (d - 1, d):
        top3 = combo_tbl[combo_tbl['decile'] == dd].head(3)
        for _, r in top3.iterrows():
            print(f'     {dd}등급 | {r["combo"]} | {r["건수"]:,}건 | '
                  f'예측 {r["예측위험도(%)"]:.1f}% → 실제 {r["실제유찰률(%)"]:.1f}%')

# 동점: 2개 이상 등급에 걸친 위험도 값
span = asc.groupby('fail_rate')['decile'].nunique()
print('\n🔎 [KPI 2 진단] 여러 등급에 걸친 같은 위험도')
for v in span[span > 1].index:
    sub = asc[asc['fail_rate'] == v].groupby('decile', observed=True)['fail_yn'].agg(['size', 'mean'])
    parts = ', '.join(f'{int(d)}등급 {int(r["size"]):,}건({r["mean"] * 100:.1f}%)' for d, r in sub.iterrows())
    print(f'  • 위험도 {v:.4f}: {parts}')

# 선그래프: 실제 유찰률 vs 평균 예측 위험도
# 그래프 파일만 생성하는 Agg 방식입니다. 한글 폰트가 있으면 우선 선택합니다.
# 설치된 한글 폰트가 없으면 그래프 글자가 깨질 수 있지만 수치 계산과 CSV는 별개입니다.
_installed = {f.name for f in font_manager.fontManager.ttflist}
for _font in ['Malgun Gothic', 'Noto Sans CJK KR', 'NanumGothic', 'AppleGothic']:
    if _font in _installed:
        plt.rcParams['font.family'] = _font
        break
plt.rcParams['axes.unicode_minus'] = False

x = decile['decile'].astype(int)
fig, ax = plt.subplots(figsize=(8.5, 4.5), dpi=150)
ax.plot(x, decile['actual_fail_rate(%)'], marker='o', color='#2a78d6', linewidth=2, label='실제 유찰률')
ax.plot(x, decile['pred_risk_mean(%)'], marker='s', color='#52514e', linewidth=1.5,
        linestyle='--', label='평균 예측 위험도')
for d in dips:
    ax.scatter(d, rates[d - 1], s=140, facecolors='none', edgecolors='#eb6834', linewidths=2, zorder=3)
for xi, yi in zip(x, decile['actual_fail_rate(%)']):
    ax.annotate(f'{yi:.1f}', (xi, yi), textcoords='offset points', xytext=(0, 8),
                ha='center', fontsize=8, color='#0b0b0b')
ax.set_xticks(range(1, 11))
ax.set_xlabel('위험도 등급 (1 = 가장 낮음)')
ax.set_ylabel('%')
ax.set_title(f'{EVAL_YEAR}년 위험도 10분위별 실제 유찰률 (Spearman ρ = {rho:.4f})', loc='left', fontsize=12)
ax.grid(axis='y', color='#e4e3df', linewidth=0.8)
for side in ['top', 'right']:
    ax.spines[side].set_visible(False)
ax.legend(frameon=False, loc='upper left')
fig.text(0.01, 0.01, '주황 원 = 실제 유찰률이 앞 등급보다 낮아진 등급', fontsize=8, color='#52514e')
fig.tight_layout(rect=(0, 0.03, 1, 1))
fig.savefig(DECILE_FIG_PATH)
plt.close(fig)

print(f'\n📁 분위 표: {DECILE_PATH}')
print(f'📁 분위 그래프: {DECILE_FIG_PATH}')
print(f'📁 분위×조합 표: {DECILE_COMBO_PATH}')