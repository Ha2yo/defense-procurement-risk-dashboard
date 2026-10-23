# %%
"""사업규모금액 산정 (유찰 위험도 공식 기준 3장)

입력 (data/clean/)
  - bid_notice_clean_20210101_20251231.csv  : 입찰공고 (공고키, pblancDate)
  - procure_plan_clean_20210101_20251231.csv: 조달계획 (bidMth 입찰방법, budgetAmount 예산금액)
  - bid_item_clean_20210101_20251231.csv    : 품목명세서 (totlAmnt 품목 총금액)
  - bid_result_clean_20210101_20251231.csv  : 입찰결과 (bidMth, bidResult) - 입찰방법 사후복원용

출력 (data/clean/)
  - business_amount_20210101_20251231.csv   : 공고 1건 = 1행, 사업규모금액과 출처

규칙 (공식 기준 3.2)
  ① 입찰방법: 조달계획 bidMth(총액제/단가제)를 사업키로 연결.
     없으면 공고일 2021~2024년 결과확정 공고에 한해 입찰결과 bidMth로 복원
     (같은 공고의 결과 행이 모두 같은 값일 때만). 2025년 공고는 복원하지 않음.
  ② 유효 품목합계: 사업의 모든 품목 행 totlAmnt가 숫자이고 0 이상일 때만 합산,
     합계가 0보다 커야 유효.
  ③ 총액제: 계획예산(양수) 우선, 없으면 품목합계.
     둘 다 있고 |예산 - 품목합계| / 예산 > 10% 이면 보류(미확인).
  ④ 단가제: 유효 품목합계만 사용.
  ⑤ 금액을 못 구한 공고도 빼지 않고 사업규모금액을 빈칸으로 둠.
  기초금액(bsicExpt)은 사용하지 않는다.

조달계획 여러 행 처리 (공식 기준에 없어 정한 규칙)
  - 한 사업의 계획 행 bidMth가 총액제·단가제로 섞이면 조달계획으로 판별하지 않고 다음 단계로 넘김
  - 한 사업의 budgetAmount가 여러 행이면 양수 값을 합산
"""
from pathlib import Path

import numpy as np
import pandas as pd

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

CLEAN_DIR = PROJECT_DIR / 'data' / 'clean'
NOTICE_PATH = CLEAN_DIR / 'bid_notice_clean_20210101_20251231.csv'
PLAN_PATH = CLEAN_DIR / 'procure_plan_clean_20210101_20251231.csv'
ITEM_PATH = CLEAN_DIR / 'bid_item_clean_20210101_20251231.csv'
RESULT_PATH = CLEAN_DIR / 'bid_result_clean_20210101_20251231.csv'
OUTPUT_PATH = CLEAN_DIR / 'business_amount_20210101_20251231.csv'

VALID_MTH = ['총액제', '단가제']
RESTORE_YEARS = (2021, 2024)     # 입찰결과 사후복원을 허용하는 공고일 연도 (2025년은 금지)
MISMATCH_LIMIT = 0.10            # 총액제 예산·품목합계 차이율 보류 기준 (초과만 보류)

BIZ = ['demandYear', 'orntCode', 'dcsNo']          # 사업키
NKEY = BIZ + ['pblancNo', 'pblancOdr']             # 공고키


def to_num(s):
    """'1,234.00' 같은 문자 금액을 숫자로. 변환 실패는 NaN."""
    return pd.to_numeric(s.astype(str).str.replace(',', '', regex=False).str.strip(), errors='coerce')


def make_key(df, cols):
    parts = [df[c].astype(str).str.strip().str.replace(r'\.0$', '', regex=True) for c in cols]
    key = parts[0]
    for p in parts[1:]:
        key = key + '_' + p
    return key


for path in [NOTICE_PATH, PLAN_PATH, ITEM_PATH, RESULT_PATH]:
    if not path.exists():
        raise SystemExit(f'❌ {path} 가 없습니다.')

notice = pd.read_csv(NOTICE_PATH, dtype=str, encoding='utf-8-sig')
plan = pd.read_csv(PLAN_PATH, dtype=str, encoding='utf-8-sig')
item = pd.read_csv(ITEM_PATH, dtype=str, encoding='utf-8-sig')
result = pd.read_csv(RESULT_PATH, dtype=str, encoding='utf-8-sig')
print(f'📂 공고 {len(notice):,}건 / 조달계획 {len(plan):,}행 / 품목명세서 {len(item):,}행 / 입찰결과 {len(result):,}행')

notice['biz'] = make_key(notice, BIZ)
notice['nkey'] = make_key(notice, NKEY)
notice['공고연도'] = pd.to_numeric(notice['pblancDate'].astype(str).str[:4], errors='coerce')
plan['biz'] = make_key(plan, BIZ)
item['biz'] = make_key(item, BIZ)
result['nkey'] = make_key(result, NKEY)

# ---------------------------------------------------------------------------
# 1. 조달계획: 사업별 입찰방법, 계획예산
# ---------------------------------------------------------------------------
plan_mth = plan['bidMth'].astype(str).str.strip()
plan_mth = plan_mth.where(plan_mth.isin(VALID_MTH))
mth_sets = plan_mth.groupby(plan['biz']).agg(lambda s: set(s.dropna()))
plan_bidmth = mth_sets[mth_sets.apply(len) == 1].apply(lambda s: next(iter(s)))
n_conflict = int((mth_sets.apply(len) > 1).sum())

budget = to_num(plan['budgetAmount'])
plan_budget = budget.where(budget > 0).groupby(plan['biz']).sum(min_count=1)
rows_per_biz = plan.groupby('biz').size()

print(f'\n[조달계획] 사업 {len(rows_per_biz):,}개, 계획 행이 2개 이상인 사업 {(rows_per_biz > 1).sum():,}개')
print(f'  - 입찰방법이 총액제·단가제로 섞인 사업 (조달계획 판별 제외): {n_conflict:,}개')

# ---------------------------------------------------------------------------
# 2. 품목명세서: 사업별 유효 품목합계
# ---------------------------------------------------------------------------
amt = to_num(item['totlAmnt'])
row_ok = amt.notna() & (amt >= 0)
biz_all_ok = row_ok.groupby(item['biz']).all()
biz_sum = amt.where(row_ok).groupby(item['biz']).sum(min_count=1)
item_valid_sum = biz_sum[biz_all_ok & (biz_sum > 0)]

print(f'\n[품목명세서] 사업 {biz_all_ok.size:,}개, 유효 품목합계 {len(item_valid_sum):,}개 '
      f'(무효 행이 있는 사업 {(~biz_all_ok).sum():,}개)')

# ---------------------------------------------------------------------------
# 3. 입찰결과: 공고별 판정 (결과확정 여부, 입찰방법 복원값)
# ---------------------------------------------------------------------------
res_mth = result['bidMth'].astype(str).str.strip()
res_mth = res_mth.where(res_mth.isin(VALID_MTH))
g = pd.DataFrame({'nkey': result['nkey'], 'mth': res_mth, 'res': result['bidResult']})
by_notice = g.groupby('nkey').agg(
    mth_set=('mth', lambda s: set(s)),          # NaN 포함 → 하나라도 비면 일치로 보지 않음
    res_set=('res', lambda s: set(s.dropna())),
)
by_notice['복원값'] = by_notice['mth_set'].apply(
    lambda s: next(iter(s)) if len(s) == 1 and next(iter(s)) in VALID_MTH else np.nan)
by_notice['결과확정'] = by_notice['res_set'].apply(
    lambda s: len(s) > 0 and not ('유찰' in s and len(s - {'유찰'}) > 0))   # 판단보류 제외

# ---------------------------------------------------------------------------
# 4. 공고별 입찰방법
# ---------------------------------------------------------------------------
notice['입찰방법'] = notice['biz'].map(plan_bidmth)
notice['입찰방법출처'] = np.where(notice['입찰방법'].notna(), '조달계획', '미확인')

notice['결과확정'] = notice['nkey'].map(by_notice['결과확정']).fillna(False).astype(bool)
restore = notice['nkey'].map(by_notice['복원값'])
can_restore = (notice['입찰방법'].isna() & restore.notna() & notice['결과확정']
               & notice['공고연도'].between(*RESTORE_YEARS))
notice.loc[can_restore, '입찰방법'] = restore[can_restore]
notice.loc[can_restore, '입찰방법출처'] = '입찰결과_사후복원'

# ---------------------------------------------------------------------------
# 5. 사업규모금액
# ---------------------------------------------------------------------------
notice['계획예산'] = notice['biz'].map(plan_budget)
notice['품목합계'] = notice['biz'].map(item_valid_sum)

lump = notice['입찰방법'].eq('총액제')
unit = notice['입찰방법'].eq('단가제')
has_budget = notice['계획예산'].notna()
has_item = notice['품목합계'].notna()

both = lump & has_budget & has_item
notice['금액차이율'] = np.nan
notice.loc[both, '금액차이율'] = (
    (notice.loc[both, '계획예산'] - notice.loc[both, '품목합계']).abs() / notice.loc[both, '계획예산'])
mismatch = both & (notice['금액차이율'] > MISMATCH_LIMIT)

src = pd.Series('미확인', index=notice.index)
amount = pd.Series(np.nan, index=notice.index)

c = lump & has_budget & ~mismatch
src[c], amount[c] = '계획예산', notice.loc[c, '계획예산']
c = lump & ~has_budget & has_item
src[c], amount[c] = '총액제_품목대체', notice.loc[c, '품목합계']
src[mismatch] = '총액제_불일치10pct초과'
c = unit & has_item
src[c], amount[c] = '단가제_품목합계', notice.loc[c, '품목합계']

notice['사업규모금액'] = amount
notice['금액출처'] = src

# ---------------------------------------------------------------------------
# 6. 요약
# ---------------------------------------------------------------------------
print('\n[입찰방법출처: 전체 공고]')
print(notice['입찰방법출처'].value_counts().to_string())
print('\n[금액출처: 전체 공고]')
print(notice['금액출처'].value_counts().to_string())

competitive = ~notice['cntrctMth'].astype(str).str.contains('수의', na=False)
pop = notice['결과확정'] & competitive
print(f'\n[결과확정 경쟁성 공고 {pop.sum():,}건] 연도 × 금액출처')
tbl = pd.crosstab(notice.loc[pop, '공고연도'], notice.loc[pop, '금액출처'], margins=True, margins_name='합계')
print(tbl.to_string())
n_ok = notice.loc[pop, '사업규모금액'].notna().sum()
print(f'→ 사업규모금액 확보 {n_ok:,}건 ({n_ok / pop.sum() * 100:.2f}%), '
      f'미확인 {pop.sum() - n_ok:,}건 ({(pop.sum() - n_ok) / pop.sum() * 100:.2f}%)')

# ---------------------------------------------------------------------------
# 7. 저장
# ---------------------------------------------------------------------------
out_cols = NKEY + ['pblancDate', '입찰방법', '입찰방법출처', '계획예산', '품목합계',
                   '금액차이율', '사업규모금액', '금액출처']
notice[out_cols].to_csv(OUTPUT_PATH, index=False, encoding='utf-8-sig')
print(f'\n🚀 저장: {OUTPUT_PATH}')