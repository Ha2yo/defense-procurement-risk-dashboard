# -*- coding: utf-8 -*-
"""군수품 조달 유찰 위험도: 공식 금액5분위 4변수 + 3변수 기준선 / 별도 5-4 탐색 비교.

공식 기준: 4변수_공식기준.md (2026-10-23 제공본)
실행 예시 (프로젝트 루트에서):
  python scripts/kpi/kpi_2024_validation.py
  python scripts/kpi/kpi_2024_validation.py --notice data/clean/bid_notice_clean_20210101_20251231.csv \
       --result data/clean/bid_result_clean_20210101_20251231.csv \
       --plan data/clean/조달계획_clean.csv --items data/clean/품목명세서_clean.csv

공식 데이터 5종(공고/결과/조달계획/품목명세/기관코드)을 실제 CLEAN 파일명으로 자동 탐색한다.
입력 누락 또는 스키마 불일치 시 진단표만 생성하고 공식을 임의로 대체하지 않는다.
누락된 컬럼을 임의 생성하거나 기초금액으로 대신하지 않는다.
주의: 현재 스크립트는 2024년 예비 검증 전용이며 2025년 결과로 기준을 조정하지 않는다.
"""
from __future__ import annotations

import argparse
import logging
import math
from pathlib import Path
import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# 0단계: 공식 설정값 (성과를 보고 조정하지 않음)
# ─────────────────────────────────────────────────────────────────────────────
TRAIN_YEARS = (2021, 2022, 2023)
TEST_YEAR = 2024
MIN_SAMPLE = 10
TOP_RATIO = 0.20
AMOUNT_DISAGREEMENT = 0.10
BASE_COLS = ['기관그룹', 'cntrctMth', 'busiDivs']
AMOUNT_COL = '금액5분위'
STEPS_4 = [BASE_COLS + [AMOUNT_COL], BASE_COLS,
           ['기관그룹', 'busiDivs'], ['기관그룹'], []]
STEPS_3 = [BASE_COLS, ['기관그룹', 'busiDivs'], ['기관그룹'], []]
# 5-4 표의 확장 실험은 공식 금액 5분위 모델과 별도로 유지한다.
# 기초금액(bsicExpt)은 공식 사업규모금액 산정에는 절대 사용하지 않는다.
# 아래 탐색 모델에서만 과거 5-4 비교표와의 정의 일치를 위해 사용한다.
DURATION_BINS = [-np.inf, 5, 10, np.inf]
AMOUNT_BINS = [-np.inf, 10_000_000, 100_000_000, np.inf]
PERIOD_COL = '공고기간구간'
BASIC_AMOUNT_COL = '기초금액구간'
QUARTER_COL = '공고시기구간'
STEPS_4_PERIOD = [BASE_COLS + [PERIOD_COL], *STEPS_3]
STEPS_5_EXP = [BASE_COLS + [PERIOD_COL, BASIC_AMOUNT_COL], *STEPS_4_PERIOD]
STEPS_6_EXP = [BASE_COLS + [PERIOD_COL, BASIC_AMOUNT_COL, QUARTER_COL], *STEPS_5_EXP]
OFFICIAL_MODELS = {'3변수_기준선': STEPS_3, '4변수_공식': STEPS_4}
EXPLORATORY_MODELS = {
    '4변수_기간_탐색': STEPS_4_PERIOD,
    '5변수_기간금액_탐색': STEPS_5_EXP,
    '6변수_기간금액분기_탐색': STEPS_6_EXP,
}
MODELS = {**OFFICIAL_MODELS, **EXPLORATORY_MODELS}



# ─────────────────────────────────────────────────────────────────────────────
# 공통 유틸리티: Markdown 표 생성 (외부 tabulate 패키지 불필요)
# ─────────────────────────────────────────────────────────────────────────────
def dataframe_to_markdown(df: pd.DataFrame) -> str:
    """DataFrame을 Markdown 표로 변환한다.

    pandas.DataFrame.to_markdown()은 별도 tabulate 설치를 요구하므로
    표의 제목/열을 유지하면서 표준 라이브러리만으로 변환한다.
    """
    columns = [str(col).replace("|", r"\|").replace("\n", " ") for col in df.columns]
    def clean(value):
        if pd.isna(value):
            return ""
        return str(value).replace("|", r"\|").replace("\r", " ").replace("\n", " ")
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join(["---"] * len(columns)) + " |"
    records = [
        "| " + " | ".join(clean(v) for v in row) + " |"
        for row in df.itertuples(index=False, name=None)
    ]
    return "\n".join([header, separator, *records])


def read_csv_safe(path: Path) -> pd.DataFrame:
    for encoding in ('utf-8-sig', 'utf-8', 'cp949'):
        try:
            return pd.read_csv(path, encoding=encoding, low_memory=False,
                               dtype={'noticeKey': 'string', 'businessKey': 'string', 'orntCode': 'string'})
        except UnicodeDecodeError:
            continue
    raise ValueError(f'문자 인코딩 확인 필요: {path}')


def require_columns(df: pd.DataFrame, cols, label: str) -> None:
    missing = sorted(set(cols) - set(df.columns))
    if missing:
        raise ValueError(f'{label}: 필수 컬럼 누락 {missing} (실제 컬럼: {list(df.columns)})')


def unique_by_key(df: pd.DataFrame, key: str, name: str) -> pd.DataFrame:
    if df[key].isna().any() or df[key].astype('string').str.strip().eq('').any():
        raise ValueError(f'{name}: {key} 공백/결측 발견')
    if df[key].duplicated().any():
        raise ValueError(f'{name}: {key} 중복 발견. 임의 첫 행 선택 금지')
    return df


def parse_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.astype('string').str.replace(r'\D', '', regex=True).str[:8],
                          format='%Y%m%d', errors='coerce')


# ─────────────────────────────────────────────────────────────────────────────
# 1단계: 공고 단위 모집단 / 결과 상태 / 기관코드 공식 매핑
# ─────────────────────────────────────────────────────────────────────────────
def result_status(series: pd.Series) -> str:
    values = set(series.dropna().astype('string').str.strip())
    fail = '유찰' in values
    success = bool(values & {'낙찰', '순위확정'})
    if fail and success:
        return '판단보류'
    if fail:
        return '유찰'
    if success:
        return '비유찰'
    return '결과미확인'


def prepare_population(notice: pd.DataFrame, results: pd.DataFrame,
                       mapping: pd.DataFrame) -> pd.DataFrame:
    require_columns(notice, ['noticeKey','businessKey','pblancDate','pblancSe',
                             'cntrctMth','busiDivs','orntCode'], '입찰공고')
    require_columns(results, ['noticeKey','bidResult','bidMth'], '입찰결과')
    require_columns(mapping, ['orntCode','기관그룹'], '공식 기관코드 매핑')
    notice = unique_by_key(notice.copy(), 'noticeKey', '입찰공고')
    map2 = mapping[['orntCode','기관그룹']].copy()
    for df in (notice, map2):
        df['orntCode'] = df['orntCode'].astype('string').str.strip().str.upper()
    map2 = unique_by_key(map2, 'orntCode', '공식 기관코드 매핑')
    map2['기관그룹'] = map2['기관그룹'].astype('string').str.strip()
    if map2['기관그룹'].isna().any() or map2['기관그룹'].eq('').any():
        raise ValueError('공식 기관코드 매핑의 기관그룹에 결측/공백이 있습니다')
    # 공고별 판정은 38,000여 결과행을 그룹별 Python 함수로 순회하지 않고
    # 벡터화된 상태 플래그로 집계해 속도 및 재현성을 확보한다.
    rstatus = results[['noticeKey', 'bidResult']].copy()
    rstatus['bidResult'] = rstatus['bidResult'].astype('string').str.strip()
    rstatus['_fail'] = rstatus['bidResult'].eq('유찰').fillna(False)
    rstatus['_success'] = rstatus['bidResult'].isin(['낙찰', '순위확정'])
    flags = rstatus.groupby('noticeKey', dropna=False)[['_fail','_success']].any()
    status = pd.Series(np.select([
        flags['_fail'] & flags['_success'],flags['_fail'],flags['_success']],
        ['판단보류','유찰','비유찰'],default='결과미확인'),
        index=flags.index,name='결과상태')
    out = notice.merge(map2, how='left', on='orntCode', validate='many_to_one')
    out = out.merge(status, left_on='noticeKey', right_index=True, how='left', validate='one_to_one')
    out['결과상태'] = out['결과상태'].fillna('결과미확인')
    out['공고일'] = parse_date(out['pblancDate'])
    out['연도'] = out['공고일'].dt.year
    out['유찰여부'] = out['결과상태'].map({'유찰':1,'비유찰':0})
    cancel = out['pblancSe'].astype('string').str.contains('취소', na=False)
    private = out['cntrctMth'].astype('string').str.contains('수의', na=False)
    out['제외사유'] = np.select([
        out['공고일'].isna(), cancel, private, out['결과상태'].eq('판단보류'),
        out['유찰여부'].isna(), out['기관그룹'].isna(),
        out['cntrctMth'].isna() | out['busiDivs'].isna()],
        ['공고일 미확인','취소공고','수의계약','판단보류','결과 미확인',
         '기관그룹 미매핑','기본 조건 결측'], default='포함')
    # 기관코드 매핑 없을 경우 기관명으로 추정하지 않는다. 감사표에 그대로 남긴다.
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 2단계: 조달계획의 입찰방법/예산을 사업키 기준으로 단일화
# ─────────────────────────────────────────────────────────────────────────────
def normalize_method(s: pd.Series) -> pd.Series:
    cleaned = s.astype('string').str.strip()
    return cleaned.where(cleaned.isin(['총액제','단가제']))


def plan_by_business(plan: pd.DataFrame) -> pd.DataFrame:
    require_columns(plan, ['businessKey','bidMth','budgetAmount'], '조달계획')
    p = plan[['businessKey','bidMth','budgetAmount']].copy()
    p['bidMth'] = normalize_method(p['bidMth'])
    p['budgetAmount'] = pd.to_numeric(p['budgetAmount'], errors='coerce')
    p['budgetAmount'] = p['budgetAmount'].where(p['budgetAmount'].gt(0))
    # 사업별 복수 계획이 존재할 수 있다. 서로 다른 유효값이 있으면 연결을 보류한다.
    def single(series):
        vals = series.dropna().unique()
        return vals[0] if len(vals)==1 else pd.NA
    g = (p.groupby('businessKey', dropna=False)
         .agg(계획입찰방법=('bidMth',single), 계획예산=('budgetAmount',single),
              계획행수=('businessKey','size')).reset_index())
    return unique_by_key(g, 'businessKey', '사업별 조달계획')


# ─────────────────────────────────────────────────────────────────────────────
# 3단계: 품목명세 전체 행의 유효성 확인 후 사업 단위 총액 산정
# ─────────────────────────────────────────────────────────────────────────────
def items_by_business(items: pd.DataFrame) -> pd.DataFrame:
    require_columns(items, ['businessKey','totlAmnt'], '품목명세서')
    x = items[['businessKey','totlAmnt']].copy()
    x['_value'] = pd.to_numeric(x['totlAmnt'], errors='coerce')
    x['_invalid'] = x['_value'].isna() | x['_value'].lt(0)
    g = (x.groupby('businessKey',dropna=False)
         .agg(품목행수=('_value','size'), 품목무효행수=('_invalid','sum'),
              _sum=('_value','sum')).reset_index())
    g['유효품목합계'] = g['_sum'].where(g['품목무효행수'].eq(0) & g['_sum'].gt(0))
    return unique_by_key(g.drop(columns='_sum'), 'businessKey', '사업별 품목합계')


def result_method_by_notice(results: pd.DataFrame) -> pd.DataFrame:
    require_columns(results, ['noticeKey','bidMth'], '입찰결과')
    r = results[['noticeKey','bidMth']].copy()
    r['bidMth'] = normalize_method(r['bidMth'])
    # 결과행이 섞였거나 유효하지 않은 방식이 포함된 경우 복원하지 않는다.
    def one_consistent(s):
        if s.isna().any() or s.nunique(dropna=True)!=1:
            return pd.NA
        return s.iloc[0]
    return (r.groupby('noticeKey',dropna=False)['bidMth'].apply(one_consistent)
            .rename('결과복원입찰방법').reset_index())


# ─────────────────────────────────────────────────────────────────────────────
# 4단계: 총액제/단가제에 따른 사업규모금액 산정 + 출처 및 사유 감사
# ─────────────────────────────────────────────────────────────────────────────
def derive_amount(pop: pd.DataFrame, plan: pd.DataFrame,
                  items: pd.DataFrame, results: pd.DataFrame,
                  train_years=TRAIN_YEARS) -> pd.DataFrame:
    x = pop.merge(plan_by_business(plan), on='businessKey',how='left',validate='many_to_one')
    x = x.merge(items_by_business(items),on='businessKey',how='left',validate='many_to_one')
    x = x.merge(result_method_by_notice(results),on='noticeKey',how='left',validate='one_to_one')
    if len(x)!=len(pop):
        raise AssertionError('데이터 연결 후 공고 행 수가 변경되었습니다')
    planmethod = x['계획입찰방법'].astype('string')
    recovered = x['결과복원입찰방법'].astype('string')
    train_flag = x['연도'].isin(train_years) & x['결과상태'].isin(['유찰','비유찰'])
    use_recovery = planmethod.isna() & train_flag & recovered.notna()
    x['입찰방법'] = planmethod.mask(use_recovery,recovered)
    x['입찰방법출처'] = np.select([planmethod.notna(),use_recovery],
                                  ['조달계획','입찰결과_사후복원'],default='미확인')
    # 검증 공고에 입찰결과의 사후 정보를 위험도 입력 변수로 사용하지 않았는지 방어.
    assert not (x.loc[~x['연도'].isin(train_years),'입찰방법출처']=='입찰결과_사후복원').any()
    budget = pd.to_numeric(x['계획예산'],errors='coerce')
    itemtotal = pd.to_numeric(x['유효품목합계'],errors='coerce')
    has_both = budget.gt(0) & itemtotal.gt(0)
    x['금액차이율'] = ((budget-itemtotal).abs()/budget).where(has_both & x['입찰방법'].eq('총액제'))
    mismatch = x['입찰방법'].eq('총액제') & x['금액차이율'].gt(AMOUNT_DISAGREEMENT)
    total = x['입찰방법'].eq('총액제') & ~mismatch
    unit = x['입찰방법'].eq('단가제')
    amount = pd.Series(np.nan,index=x.index,dtype='float64')
    amount.loc[total & budget.gt(0)] = budget[total & budget.gt(0)]
    amount.loc[total & ~budget.gt(0) & itemtotal.gt(0)] = itemtotal[total & ~budget.gt(0) & itemtotal.gt(0)]
    amount.loc[unit & itemtotal.gt(0)] = itemtotal[unit & itemtotal.gt(0)]
    x['사업규모금액'] = amount
    # pandas nullable BooleanArray에 pd.NA가 존재하면 numpy.select가 오류를 내므로
    # 금액 결측 조건은 False로 확정한 순수 bool 배열로 전달한다.
    x['금액출처'] = np.select([
        cond.fillna(False).to_numpy(dtype=bool) for cond in (
            mismatch, total & budget.gt(0),
            total & ~budget.gt(0) & itemtotal.gt(0),
            unit & itemtotal.gt(0))],
        ['총액제_불일치10pct초과','계획예산','총액제_품목대체','단가제_품목합계'],default='미확인')
    # 공식: 위험도에 사용할 금액은 조달계획·품목명세에서만 생성한다.
    # 입찰공고 bsicExpt 컬럼은 이 함수의 입력 금액 산정 경로에서 참조하지 않는다.
    return x


# ─────────────────────────────────────────────────────────────────────────────
# 5단계: 학습기간의 유효 금액으로만 20/40/60/80 분위수 계산
# ─────────────────────────────────────────────────────────────────────────────
def quintile_boundaries(train: pd.DataFrame) -> np.ndarray:
    values = pd.to_numeric(train['사업규모금액'],errors='coerce').dropna()
    if values.empty:
        raise ValueError('학습기간 내 유효한 사업규모금액이 없습니다. 5분위 생성 불가')
    bounds = values.quantile([.2,.4,.6,.8]).to_numpy(dtype=float)
    if np.any(np.diff(bounds)<=0):
        raise ValueError('금액 5분위 경계가 중복되었습니다. pd.cut 구간 생성 불가: '+str(bounds.tolist()))
    return bounds


def apply_quintiles(df: pd.DataFrame, bounds: np.ndarray) -> pd.DataFrame:
    out=df.copy()
    out['금액5분위'] = pd.cut(out['사업규모금액'],bins=[-np.inf,*bounds,np.inf],
                            labels=['Q1','Q2','Q3','Q4','Q5'],right=True).astype('object')
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 5-1단계: 기존 프로젝트 정의서 5-4 탐색 모델용 파생변수
# ─────────────────────────────────────────────────────────────────────────────
def derive_exploratory_features(pop: pd.DataFrame) -> pd.DataFrame:
    """기존 비교표의 기간/기초금액/분기 정의 유지 (공식 4변수에 미사용)."""
    require_columns(pop, ['bidPartcptRegistClosDt', 'bsicExpt'], '5-4 탐색 모델 입력')
    out = pop.copy()
    cutoff = parse_date(out['bidPartcptRegistClosDt'])
    out['공고기간일'] = (cutoff - out['공고일']).dt.days
    days = out['공고기간일'].where(out['공고기간일'].ge(0))
    out[PERIOD_COL] = pd.cut(days, bins=DURATION_BINS,
        labels=['5일 이하', '6~10일', '11일 이상'], right=True).astype('object')
    original = pd.to_numeric(out['bsicExpt'], errors='coerce')
    out['기초금액숫자_탐색전용'] = original
    valid = original.where(original.gt(0))
    out[BASIC_AMOUNT_COL] = pd.cut(valid, bins=AMOUNT_BINS,
        labels=['1천만 원 이하', '1천만~1억 원', '1억 원 초과'], right=True).astype('object')
    out[QUARTER_COL] = ('Q' + out['공고일'].dt.quarter.astype('Int64').astype('string')).astype('object')
    out.loc[out['공고일'].isna(), QUARTER_COL] = np.nan
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 6단계: 10건 이상 과거 조합 우선, 없으면 공식 백오프 단계 적용
# ─────────────────────────────────────────────────────────────────────────────
def score_model(train: pd.DataFrame, test: pd.DataFrame, steps: list[list[str]]) -> pd.DataFrame:
    if len(train)<MIN_SAMPLE:
        raise ValueError(f'학습 표본이 최소 {MIN_SAMPLE}건보다 적습니다')
    out=test.copy()
    out['위험도']=np.nan
    out['근거공고수']=pd.Series(pd.NA,index=out.index,dtype='Int64')
    out['근거유찰건수']=pd.Series(pd.NA,index=out.index,dtype='Int64')
    out['적용단계']=pd.Series(pd.NA,index=out.index,dtype='Int64')
    out['적용조건']=''
    official = steps is STEPS_4
    missing_amount = out['사업규모금액'].isna() if official else pd.Series(False,index=out.index)
    for num,cols in enumerate(steps,1):
        pending=out['위험도'].isna()
        if not pending.any():
            break
        if not cols:
            n=len(train); f=int(train['유찰여부'].sum())
            out.loc[pending,['위험도','근거공고수','근거유찰건수','적용단계','적용조건']] = [f/n,n,f,num,'전체']
            break
        valid=out.loc[pending,cols].notna().all(axis=1)
        idx=valid.index[valid]
        if not len(idx):
            continue
        lookup=train.dropna(subset=cols).groupby(cols,observed=True)['유찰여부'].agg(
            근거공고수='size',근거유찰건수='sum').reset_index()
        joined=(out.loc[idx,cols].assign(_idx=idx).merge(lookup,on=cols,how='left',validate='many_to_one')
                .set_index('_idx'))
        ok=joined['근거공고수'].ge(MIN_SAMPLE)
        chosen=joined.loc[ok]; ids=chosen.index
        if not len(ids):
            continue
        out.loc[ids,'위험도']=(chosen['근거유찰건수']/chosen['근거공고수']).to_numpy()
        out.loc[ids,'근거공고수']=chosen['근거공고수'].astype(int).to_numpy()
        out.loc[ids,'근거유찰건수']=chosen['근거유찰건수'].astype(int).to_numpy()
        out.loc[ids,'적용단계']=num
        out.loc[ids,'적용조건']=' × '.join(cols)
    # 공식 4변수의 금액 미확인과 표본 부족을 구별한다.
    # 탐색용 확장모델은 결측 입력으로 최초 단계가 불가능한 경우도 별도 표기한다.
    first_cols = steps[0]
    exploratory_missing = (out[first_cols].isna().any(axis=1)
                           if not official else pd.Series(False, index=out.index))
    out['완화사유'] = np.select(
        [out['적용단계'].eq(1).fillna(False), missing_amount.fillna(False), exploratory_missing.fillna(False)],
        ['완화 없음', '금액 미확인', '탐색변수 미확인'], default='표본 부족')
    if out['위험도'].isna().any() or out['근거공고수'].lt(MIN_SAMPLE).any():
        raise AssertionError('일부 공고의 위험도/근거수가 공식 기준을 충족하지 않습니다')
    return out


def evaluate(scored: pd.DataFrame) -> tuple[pd.DataFrame,dict]:
    ranked=scored.sort_values(['위험도','근거공고수','noticeKey'],
                              ascending=[False,False,True],kind='stable').copy()
    k=math.floor(len(ranked)*TOP_RATIO+.5)
    ranked['순위']=np.arange(1,len(ranked)+1)
    ranked['상위20pct']=ranked['순위'].le(k)
    positives=int(ranked['유찰여부'].sum())
    caught=int(ranked.loc[ranked['상위20pct'],'유찰여부'].sum())
    return ranked, {'검증공고수':len(ranked),'검증유찰건수':positives,
                    '상위20pct공고수':k,'포착유찰':caught,
                    '사전식별률_pct':100*caught/positives if positives else np.nan,
                    '1단계적용률_pct':100*ranked['적용단계'].eq(1).mean(),
                    '대체율_pct':100*ranked['적용단계'].gt(1).mean(),
                    '금액미확인완화율_pct':100*ranked['완화사유'].eq('금액 미확인').mean(),
                    '표본부족완화율_pct':100*ranked['완화사유'].eq('표본 부족').mean(),
                    '탐색변수미확인완화율_pct':100*ranked['완화사유'].eq('탐색변수 미확인').mean(),
                    '금액미확인율_pct':100*ranked['사업규모금액'].isna().mean()}


# ─────────────────────────────────────────────────────────────────────────────
# 6-1단계: 위험도 동점 민감도 (정렬 방식은 기존 기준 유지)
# ─────────────────────────────────────────────────────────────────────────────
def tie_sensitivity(ranked: pd.DataFrame, model: str) -> dict:
    """상위 20% 경계의 동일 위험도 공고가 선택 순서에 민감한 정도를 계산한다.

    위험도가 다른 공고의 순서는 고정하고, 경계와 정확히 같은 위험도인
    공고들만 서로 위치를 교환할 수 있다고 가정한다. 이는 신뢰구간이 아니라
    '동점 공고 선정 순서를 바꿨을 때' 가능한 최저·최고 포착 건수이다.
    관측 결과를 이용하여 최적 순서를 선택하는 것은 금지한다.
    """
    n=len(ranked)
    k=math.floor(n*TOP_RATIO+.5)
    risk=float(ranked.iloc[k-1]['위험도'])
    above=ranked['위험도'].gt(risk)
    equal=ranked['위험도'].eq(risk)
    fixed=int(ranked.loc[above,'유찰여부'].sum())
    same_n=int(equal.sum())
    same_fail=int(ranked.loc[equal,'유찰여부'].sum())
    slots=k-int(above.sum())
    low=fixed+max(0,slots-(same_n-same_fail))
    high=fixed+min(slots,same_fail)
    expected=fixed+slots*same_fail/same_n
    actual=int(ranked.loc[ranked['상위20pct'],'유찰여부'].sum())
    return {'모델':model,'검증공고수':n,'상위20pct공고수':k,
            '경계위험도':risk,'경계동점전체건수':same_n,
            '경계동점선정건수':slots,'경계동점미선정건수':same_n-slots,
            '경계동점유찰건수':same_fail,'관측포착유찰':actual,
            '동점순서최소포착':low,'동점순서최대포착':high,
            '동점무작위기대포착':expected,
            '동점무작위기대Recall_pct':100*expected/int(ranked['유찰여부'].sum()),
            '관측Recall_pct':100*actual/int(ranked['유찰여부'].sum()),
            '동점순서최소Recall_pct':100*low/int(ranked['유찰여부'].sum()),
            '동점순서최대Recall_pct':100*high/int(ranked['유찰여부'].sum())}


# ─────────────────────────────────────────────────────────────────────────────
# 6-2단계: 기존 KPI 2 복원 — 동일 공고 수 10분위와 Spearman 순위상관
# ─────────────────────────────────────────────────────────────────────────────
def kpi2_deciles(ranked: pd.DataFrame, model: str) -> tuple[pd.DataFrame,float,int]:
    """위험도 오름차순 동일 건수 10분위의 실제 유찰률 상승 경향을 측정한다.

    각 분위의 표본 수가 최대 1건 차이가 나도록 배치한다. 같은 위험도가
    10분위 경계를 가로질러도 행은 분리되며, 그 경계 수는 별도 기록한다.
    KPI 2는 상위 20% 포착률(KPI 1)과 다른 순위 경향 진단 지표이다.
    """
    df=ranked.sort_values(['위험도','근거공고수','noticeKey'],
        ascending=[True,True,True],kind='stable').reset_index(drop=True).copy()
    n=len(df)
    df['분위']=np.floor(np.arange(n)*10/n).astype(int)+1
    tab=df.groupby('분위',sort=True).agg(
        검증공고수=('유찰여부','size'),유찰건수=('유찰여부','sum'),
        위험도최솟값=('위험도','min'),위험도최댓값=('위험도','max')).reset_index()
    tab['실제유찰률_pct']=100*tab['유찰건수']/tab['검증공고수']
    tab.insert(0,'모델',model)
    rho=float(tab['분위'].corr(tab['실제유찰률_pct'],method='spearman')) if len(tab)>1 else np.nan
    boundary=int(((df['분위'].diff().fillna(0)!=0)&(df['위험도'].diff().fillna(-1)==0)).sum())
    return tab,rho,boundary


# ─────────────────────────────────────────────────────────────────────────────
# 6-3단계: KPI 2 위험도 동점 순서 민감도 (기존 KPI 2를 변경하지 않음)
# ─────────────────────────────────────────────────────────────────────────────
def kpi2_tie_sensitivity(ranked: pd.DataFrame, model: str,
                         reps: int = 300, seed: int = 2026) -> dict:
    """동일 위험도 공고의 배치 순서만 무작위 변경하여 KPI 2의 민감도를 측정한다.

    - 서로 다른 위험도 간 순서는 유지하고 위험도가 정확히 같은 공고만 섞는다.
    - 10분위의 공고 수/경계는 기존 kpi2_deciles 함수와 동일하게 유지한다.
    - 순서 변경에 실제 유찰 결과를 사용하지 않는다(결과 누수 방지).
    - 분위별 관측 유찰률의 Spearman 상관을 재계산한다.
    - 2.5/97.5 분위는 동점 순서 재배치의 민감도 범위이며 95% 신뢰구간이 아니다.
    - seed를 고정하여 Windows/Python 환경에서도 재실행 가능한 결과를 만든다.
    """
    if reps < 2:
        raise ValueError('동점 민감도 반복 횟수는 최소 2회여야 합니다.')
    ordered = ranked.sort_values(['위험도', '근거공고수', 'noticeKey'],
        ascending=[True, True, True], kind='stable').reset_index(drop=True)
    risks = ordered['위험도'].to_numpy(dtype=float)
    y = ordered['유찰여부'].to_numpy(dtype=float)
    n = len(ordered)
    if n < 10 or not np.isfinite(risks).all() or not np.isfinite(y).all():
        raise ValueError(f'{model}: KPI 2 민감도에는 유효 공고 10건 이상이 필요합니다.')
    decile_index = np.floor(np.arange(n) * 10 / n).astype(int)
    counts = np.bincount(decile_index, minlength=10)
    starts = np.r_[0, np.flatnonzero(risks[1:] != risks[:-1]) + 1]
    ends = np.r_[starts[1:], n]
    # 전부 동일 분위에 있는 위험도 동점 그룹은 어떤 순서로 섞어도 결과에 영향이 없다.
    crossing = [(int(lo),int(hi)) for lo,hi in zip(starts,ends)
                if hi-lo > 1 and decile_index[lo] != decile_index[hi-1]]
    original_tab, observed, boundary_count = kpi2_deciles(ranked, model)
    rng = np.random.default_rng(seed)
    values = np.empty(reps, dtype=float)
    current = y.copy()
    for rep in range(reps):
        current[:] = y
        for lo, hi in crossing:
            current[lo:hi] = rng.permutation(y[lo:hi])
        rates = np.bincount(decile_index, weights=current, minlength=10)/counts
        values[rep] = pd.Series(np.arange(1,11)).corr(pd.Series(rates),method='spearman')
    finite = values[np.isfinite(values)]
    if not len(finite):
        raise ValueError(f'{model}: 동점 무작위 반복의 Spearman 값이 모두 미정의입니다.')
    return {'모델': model, '반복횟수': reps, '난수시드': seed,
            '동점분위경계수': boundary_count, '경계교차동점그룹수': len(crossing),
            '기존KPI2_spearman': observed,
            '무작위KPI2_평균': float(finite.mean()),
            '무작위KPI2_표준편차': float(finite.std(ddof=1)) if len(finite)>1 else 0.0,
            '무작위KPI2_최소':float(finite.min()),
            '무작위KPI2_하위2_5pct':float(np.percentile(finite,2.5)),
            '무작위KPI2_상위97_5pct':float(np.percentile(finite,97.5)),
            '무작위KPI2_최대':float(finite.max()),
            '유효반복수':len(finite)}


def pretty_report_table(df:pd.DataFrame) -> str:
    """표시용 반올림만 적용한다. CSV의 원래 계산 정밀도는 유지한다."""
    shown=df.copy()
    for col in shown.select_dtypes(include='number').columns:
        if ('pct' in col.lower() or col.endswith('_pp') or col.startswith('KPI2_')
                or col in ['경계위험도']):
            shown[col]=shown[col].round(2)
    return dataframe_to_markdown(shown)


# ─────────────────────────────────────────────────────────────────────────────
# 7단계: 파일 위치 및 출력 저장
# ─────────────────────────────────────────────────────────────────────────────
def project_root() -> Path:
    file_here=Path(__file__).resolve()
    for candidate in [Path.cwd(),*Path.cwd().parents,*file_here.parents]:
        if (candidate/'data').is_dir() and (candidate/'scripts'/'kpi').is_dir():
            return candidate
    raise FileNotFoundError('프로젝트 루트를 찾을 수 없습니다. --project-root로 지정하세요')


def find_input(root: Path, supplied: str | None, stem: str, label: str,
               required: bool = True) -> Path | None:
    """실제 이름을 기준으로 찾고, 여러 후보가 있으면 임의 선택하지 않는다.

    1) 사용자가 --옵션으로 준 파일을 최우선으로 사용한다.
    2) 미지정 시 data/clean/<stem>.csv를 탐색한다.
    3) 복수 후보 또는 누락된 필수 입력은 이유를 알기 쉬운 오류로 설명한다.
    """
    if supplied:
        path = Path(supplied)
        path = path if path.is_absolute() else root / path
        if not path.is_file():
            raise FileNotFoundError(f'{label} 지정 경로에 파일이 없습니다: {path}')
        return path
    path = root / 'data' / 'clean' / (stem + '.csv')
    if path.is_file():
        return path
    if required:
        raise FileNotFoundError(f'{label} 파일이 없습니다: {path}\n실제 파일명과 위치를 확인하여 --{label}로 지정하세요.')
    return None


def data_readiness_audit(inputs: dict[str, pd.DataFrame], paths: dict,
                         output: Path) -> bool:
    """실제 제공된 네 CLEAN 자료의 스키마를 점검한다.

    품목 파일이나 공식 매핑이 없더라도 준비도 CSV와 설명문을 저장한다.
    이 경우 공식 위험도/KPI는 계산하지 않는다 (0점으로 대체하지 않음).
    """
    expected = {
        'notice': ['noticeKey','businessKey','pblancDate','orntCode','cntrctMth','busiDivs',
                   'pblancSe','bidPartcptRegistClosDt','bsicExpt'],
        'result': ['noticeKey','bidResult','bidMth'],
        'plan': ['businessKey','bidMth','budgetAmount'],
        'contract': [],  # 계약정보는 참고/감사용이며 2024년 KPI 산출에 필수 아님
        'items': ['businessKey','totlAmnt'],
        'mapping': ['orntCode','기관그룹'],
    }
    rows=[]
    for name, cols in expected.items():
        frame=inputs.get(name)
        absent=[] if frame is None else sorted(set(cols)-set(frame.columns))
        rows.append({'자료':name,'파일경로':str(paths[name]) if paths.get(name) else '미제공',
                     '행수':len(frame) if frame is not None else 0,
                     '컬럼수':len(frame.columns) if frame is not None else 0,
                     '필수컬럼누락':', '.join(absent) if absent else '없음',
                     '상태':'미제공' if frame is None else ('컬럼 부족' if absent else '확인')})
    audit=pd.DataFrame(rows)
    output.mkdir(parents=True,exist_ok=True)
    save_csv(audit,output/'00_input_readiness.csv')
    ready=all(audit.set_index('자료').loc[k,'상태']=='확인' for k in ['notice','result','plan','items','mapping'])
    lines=['# 공식 4변수 분석: 실제 데이터 입력 점검','',
           '## 제공 자료 확인','',dataframe_to_markdown(audit),'',
           '## 공식 KPI 실행 가능 여부','',
           '**가능**' if ready else '**보류 — 필수 자료 미확보. KPI 결과를 생성하지 않음.**','',
           '- 공식 4변수는 조달계획 예산과 품목명세 `totlAmnt`를 규정에 따라 연결해야 합니다.',
           '- 기관그룹은 공식 `orntCode` 매핑표의 `기관그룹`만 사용할 수 있습니다.',
           '- 계약정보는 진단용이며 공식 사업규모금액에 임의로 넣지 않습니다.',
           '- 품목합계 결측은 값이 0이라는 뜻이 아니므로 가짜 품목 데이터로 보완하지 않습니다.',
           '- `bsicExpt`는 기존 5-4 탐색 비교에만 쓰고 공식 금액 5분위에 쓰지 않습니다.',
           ('- 필수 입력자료 5종의 스키마를 확인했습니다. 2024년 KPI 산출 단계로 진행합니다.'
            if ready else '- 필수 자료가 부족해 공식 KPI 산출을 보류합니다. 해당 파일을 확보한 뒤 재실행해야 합니다.')]
    (output/'00_input_readiness.md').write_text('\n'.join(lines),encoding='utf-8')
    # 데이터가 실제로 조인 가능한지: 키 단위 규모와 누락도 별도 보고한다.
    overview=[]
    notice=inputs.get('notice')
    result=inputs.get('result')
    plan=inputs.get('plan')
    if notice is not None:
        overview.append({'검사항목':'입찰공고 전체 행수','값':len(notice)})
        overview.append({'검사항목':'공고키 중복 건수','값':int(notice['noticeKey'].duplicated().sum())})
        overview.append({'검사항목':'공고 고유 사업키','값':int(notice['businessKey'].nunique())})
    if result is not None:
        overview.append({'검사항목':'입찰결과 전체 행수','값':len(result)})
        overview.append({'검사항목':'결과 고유 공고키','값':int(result['noticeKey'].nunique())})
    if plan is not None:
        overview.append({'검사항목':'조달계획 전체 행수','값':len(plan)})
        overview.append({'검사항목':'계획 사업키 중복 건수','값':int(plan['businessKey'].duplicated().sum())})
        overview.append({'검사항목':'계획 예산 양수 행수','값':int(pd.to_numeric(plan['budgetAmount'],errors='coerce').gt(0).sum())})
        overview.append({'검사항목':'총액제 계획 행수','값':int(plan['bidMth'].astype('string').eq('총액제').sum())})
        overview.append({'검사항목':'단가제 계획 행수','값':int(plan['bidMth'].astype('string').eq('단가제').sum())})
    if notice is not None and plan is not None:
        nkeys=notice['businessKey'].dropna().astype('string')
        pkeys=set(plan['businessKey'].dropna().astype('string'))
        overview.append({'검사항목':'조달계획 사업키 연결 가능 공고 수','값':int(nkeys.isin(pkeys).sum())})
    if notice is not None and result is not None:
        nkeys=notice['noticeKey'].dropna().astype('string')
        rkeys=set(result['noticeKey'].dropna().astype('string'))
        overview.append({'검사항목':'입찰결과 공고키 연결 가능 공고 수','값':int(nkeys.isin(rkeys).sum())})
    overview_df=pd.DataFrame(overview)
    save_csv(overview_df,output/'00_join_diagnostics.csv')
    report=output/'00_input_readiness.md'
    with report.open('a',encoding='utf-8') as f:
        f.write('\n\n## CLEAN 원자료 연결 진단\n\n')
        f.write(dataframe_to_markdown(overview_df))
        f.write('\n\n※ 연결 가능 수는 키 존재 여부만 확인한 수이며, 결과확정 KPI 모집단 수가 아닙니다.\n')
    if not ready:
        print('[입력 점검] 공식 KPI 산출 보류: 추가로 품목명세/기관그룹 매핑 또는 누락 컬럼이 필요합니다.')
        print(audit[['자료','상태','필수컬럼누락']].to_string(index=False))
        print('[입력 점검] 저장:',output/'00_input_readiness.md')
    return ready


def save_csv(df:pd.DataFrame,path:Path):
    path.parent.mkdir(parents=True,exist_ok=True)
    df.to_csv(path,index=False,encoding='utf-8-sig')


def run(notice: pd.DataFrame, result: pd.DataFrame, plan: pd.DataFrame,
        items: pd.DataFrame, mapping: pd.DataFrame, output: Path):
    print('[1/6] 결과 판정 및 기관그룹 공식 코드 매핑')
    pop=prepare_population(notice,result,mapping)
    print('[2/6] 총액제/단가제 및 사업규모금액 산정')
    pop=derive_amount(pop,plan,items,result,train_years=TRAIN_YEARS)
    pop=derive_exploratory_features(pop)
    train=pop.loc[pop['연도'].isin(TRAIN_YEARS)&pop['제외사유'].eq('포함')].copy()
    test=pop.loc[pop['연도'].eq(TEST_YEAR)&pop['제외사유'].eq('포함')].copy()
    if train.empty or test.empty:
        raise ValueError('학습 또는 2024년 검증 표본이 비어 있습니다. 입력/제외사유 점검 필요')
    print(f'[3/6] 학습 {len(train):,}건 / 검증 {len(test):,}건; 5분위는 학습에서만 계산')
    bounds=quintile_boundaries(train)
    train=apply_quintiles(train,bounds)
    test=apply_quintiles(test,bounds)
    print('[4/6] 기준선/공식 4변수 + 프로젝트 5-4 탐색 모델 위험도 산정')
    rows=[]
    scored={}
    ties=[]
    deciles=[]
    kpi2_tie_rows=[]
    for name,steps in MODELS.items():
        ranked,metrics=evaluate(score_model(train,test,steps))
        scored[name]=ranked
        ties.append(tie_sensitivity(ranked,name))
        dtable,rho,split_count=kpi2_deciles(ranked,name)
        deciles.append(dtable)
        kpi2_tie_rows.append(kpi2_tie_sensitivity(ranked,name))
        metrics.update({'KPI2_spearman':rho,'KPI2_동점분위경계수':split_count,
            'Precision_at20_pct':100*metrics['포착유찰']/metrics['상위20pct공고수'],
            'Lift_at20':(metrics['포착유찰']/metrics['상위20pct공고수'])/
                (metrics['검증유찰건수']/metrics['검증공고수'])})
        rows.append({'모델':name, '구분':'공식' if name=='4변수_공식' else ('기준선' if name=='3변수_기준선' else '5-4 탐색'),
                     '학습기간':'2021~2023','검증연도':2024,**metrics})
    print('[5/6] 결과 저장')
    output.mkdir(parents=True,exist_ok=True)
    # 분석 자격(결과 상태)와 해당 연도의 실제 학습/검증 역할은 다르다.
    pop['2024검증내역할']=np.select([pop['연도'].isin(TRAIN_YEARS)&pop['제외사유'].eq('포함'),
          pop['연도'].eq(TEST_YEAR)&pop['제외사유'].eq('포함')],
          ['학습','검증'],default='분석기간외 또는 제외')
    pop['분석대상자격']=pop['제외사유'].eq('포함').map({True:'충족',False:'미충족'})
    save_csv(pop[['noticeKey','businessKey','연도','결과상태','제외사유','분석대상자격','2024검증내역할','기관그룹',
                  '입찰방법','입찰방법출처','계획예산','유효품목합계','금액차이율',
                  '사업규모금액','금액출처']],output/'01_population_audit.csv')
    save_csv(pd.DataFrame({'경계':['Q1/Q2','Q2/Q3','Q3/Q4','Q4/Q5'],
                           '경계금액_원':bounds,'산정학습기간':['2021~2023']*4,
                           '유효금액학습공고수':[int(train['사업규모금액'].notna().sum())]*4,
                           '분위수_보간법':['pandas linear']*4,
                           '구간경계_포함방식':['오른쪽 포함 (이하)']*4}),
             output/'02_amount_quintile_boundaries.csv')
    save_csv(pd.DataFrame(rows),output/'03_model_comparison.csv')
    tie_df=pd.DataFrame(ties)
    save_csv(tie_df,output/'11_tie_sensitivity.csv')
    save_csv(pd.concat(deciles,ignore_index=True),output/'12_kpi2_deciles.csv')
    kpi2_tie_df=pd.DataFrame(kpi2_tie_rows)
    save_csv(kpi2_tie_df,output/'13_kpi2_tie_sensitivity.csv')
    filenames={'3변수_기준선':'04_notice_risk_baseline_2024.csv',
               '4변수_공식':'05_notice_risk_official4_2024.csv',
               '4변수_기간_탐색':'07_notice_risk_4period_exploratory_2024.csv',
               '5변수_기간금액_탐색':'08_notice_risk_5vars_exploratory_2024.csv',
               '6변수_기간금액분기_탐색':'09_notice_risk_6vars_exploratory_2024.csv'}
    for name,filename in filenames.items():
        save_csv(scored[name],output/filename)
    save_csv(pd.DataFrame(rows).query("구분 == '5-4 탐색' or 구분 == '기준선'"),
             output/'10_section_5_4_comparison.csv')
    lines=['# 공식 금액 5분위 4변수 모델: 2024년 예비 검증','',
           '- 공식 기준: 기관그룹 × 계약방법 × 사업구분 × 사업규모금액 5분위',
           '- 기준선: 기관그룹 × 계약방법 × 사업구분',
           '- 프로젝트 정의서 5-4의 4기간/5기간금액/6기간금액분기 모델은 **별도 탐색 비교**',
           '- 탐색 모델에서만 `bsicExpt` 사용; 공식 사업규모금액에는 절대 사용하지 않음',
           '- 사업규모금액은 조달계획/품목명세서 기준이며 `bsicExpt`는 사용하지 않음',
           '- 기관그룹은 공식 `orntCode` 매핑표만 사용',
           '- 2024년 결과는 위험도 계산/금액경계 산정에 사용하지 않고 평가에만 사용',
           '- 위험도는 과거 조건별 관측 유찰률이며 확정 유찰 확률이 아님','',
           '## 검증 결과','',pretty_report_table(pd.DataFrame(rows)),'',
           '## 프로젝트 정의서 5-4 탐색 비교 (공식 모델과 별도)','',
           pretty_report_table(pd.DataFrame(rows).query("구분 == '5-4 탐색' or 구분 == '기준선'")),'',
           '※ 5-4 확장모델의 금액은 입찰공고 기초금액(`bsicExpt`)이고 공식 4변수의 사업규모금액과 다름.','',
           '## 5분위 금액 경계 (학습기간에만 적합)','',
           ', '.join(f'{b:,.2f}원' for b in bounds),'',
           f'- 금액 5분위 학습 표본: {int(train["사업규모금액"].notna().sum()):,}건',
           '- 5분위 경계는 pandas 선형보간 방식이며 경계 이하 값은 아래 구간에 포함','',
           '## 상위 20% 경계 동점 민감도','',pretty_report_table(tie_df),'',
           '- 기본 정렬: 위험도 내림차순 → 근거공고수 내림차순 → noticeKey 오름차순.',
           '- 동점 최소·최대는 같은 경계 위험도 내 순서를 바꾸는 가상 극단값이며 신뢰구간이 아님.',
           '- 동점 무작위 기대값은 경계 위험도가 같은 공고에서 결과를 모른 채 무작위 선정할 때의 기대값.',
           '- KPI 1은 기존 결정적 정렬 규칙을 그대로 유지함.','',
           '## KPI 2: 위험도 오름차순 동일 규모 10분위와 실제 유찰률','',
           pretty_report_table(pd.concat(deciles,ignore_index=True)), '',
           '- KPI 2 Spearman: 순위 1~10과 분위별 실제 유찰률의 순위상관.',
           '- 동점 위험도에서 분위 경계가 갈라지는 횟수도 비교 CSV에 기록.', '',
           '## KPI 2 동점 순위 민감도 (300회, 시드 2026)','',
           pretty_report_table(kpi2_tie_df),'',
           '- 같은 위험도 내 공고 순서만 300회 무작위 변경하고, 기존 동일 건수 10분위를 재계산.',
           '- 서로 다른 위험도 순서, 기존 KPI 1, 기본 KPI 2 계산 규칙은 변경하지 않음.',
           '- 무작위 하위 2.5%~상위 97.5% 값은 동점 순서 민감도이며 통계적 신뢰구간이 아님.', '',
           '## 해석과 데이터 감사 주의사항','',
           '- 2020·2026년 등의 행은 원자료에 보존되지만 2024 검증 학습·평가에서는 제외.',
           '- `분석대상자격=충족`은 분석조건 통과를 뜻하며 실제 학습·검증 포함과 다름.',
           '- 입력자료의 사업계획·품목 정보가 공고일 당시 사용 가능했는지는 별도 확인 필요.',
           '- 공식 4변수와 기준선의 차이는 통계적 유의성 검증 전에는 우월성으로 단정 불가.',
           '- 계약정보는 위험도 및 사업규모금액 입력에 사용하지 않음.','',
           '※ 이 결과는 2024년 예비 검증이며 2025년 최종 평가를 대신하지 않음.']
    (output/'06_report.md').write_text('\n'.join(lines),encoding='utf-8')
    print('[6/6] KPI2·동점 분석·감사 보강 완료:',output)
    return pd.DataFrame(rows)


def self_tests():
    # ── 같은 공고의 유찰+낙찰 혼합은 판단보류, 낙찰+순위확정은 비유찰.
    assert result_status(pd.Series(['유찰','낙찰']))=='판단보류'
    assert result_status(pd.Series(['낙찰','순위확정']))=='비유찰'
    # ── 품목 0원 허용, 결측 하나라도 있으면 전체 사업합계 무효.
    sample=pd.DataFrame({'businessKey':['a','a','b','b','c','c'],
                         'totlAmnt':[0,10,100,np.nan,100,-1]})
    computed=items_by_business(sample).set_index('businessKey')
    assert computed.loc['a','유효품목합계']==10
    assert pd.isna(computed.loc['b','유효품목합계'])
    assert pd.isna(computed.loc['c','유효품목합계'])
    # ── 정확히 10%는 유효, 10% 초과면 보류.
    assert not (abs(100-90)/100 > AMOUNT_DISAGREEMENT)
    assert abs(100-89)/100 > AMOUNT_DISAGREEMENT
    # ── 5분위 오른쪽 경계는 해당 구간에 포함.
    z=apply_quintiles(pd.DataFrame({'사업규모금액':[20,21,80,np.nan]}),np.array([20,40,60,80]))
    assert z['금액5분위'].tolist()[:3]==['Q1','Q2','Q4']
    assert pd.isna(z['금액5분위'].iloc[-1])
    # ── 10건 이상 단계, 금액 결측이면 3변수 단계로 즉시 대체.
    train=pd.DataFrame({'noticeKey':[f't{i}' for i in range(10)],
         '기관그룹':['육군']*10,'cntrctMth':['제한경쟁']*10,
         'busiDivs':['물품']*10,'금액5분위':['Q1']*10,'사업규모금액':[100]*10,
         '유찰여부':[i%2 for i in range(10)]})
    test=train.iloc[:2].copy().reset_index(drop=True)
    test.loc[1,'사업규모금액']=np.nan
    test.loc[1,'금액5분위']=None
    scored=score_model(train,test,STEPS_4)
    assert scored.loc[0,'적용단계']==1
    assert scored.loc[1,'적용단계']==2
    assert scored.loc[1,'완화사유']=='금액 미확인'
    # ── 탐색 모델은 공식 4변수 산정과 분리해 5-4 단계 순서를 유지.
    assert STEPS_5_EXP[0] == BASE_COLS + [PERIOD_COL,BASIC_AMOUNT_COL]
    assert STEPS_6_EXP[1:] == STEPS_5_EXP
    assert STEPS_4[0] == BASE_COLS + [AMOUNT_COL]
    # ── 탐색 전용 파생 변수 경계가 기존 5-4 정의에 부합하는지 확인.
    probe=pd.DataFrame({'공고일':pd.to_datetime(['2024-01-01']*3),
        'bidPartcptRegistClosDt':['20240106','20240111','20240112'],
        'bsicExpt':[10000000,100000000,100000001]})
    e=derive_exploratory_features(probe)
    assert e[PERIOD_COL].tolist()==['5일 이하','6~10일','11일 이상']
    assert e[BASIC_AMOUNT_COL].tolist()==['1천만 원 이하','1천만~1억 원','1억 원 초과']
    probe_ranked,probe_metrics=evaluate(scored)
    t=tie_sensitivity(probe_ranked,'자체테스트')
    assert t['동점순서최소포착'] <= t['관측포착유찰'] <= t['동점순서최대포착']
    d,rho,n_split=kpi2_deciles(probe_ranked,'자체테스트')
    assert len(d)==min(10,len(probe_ranked)) and d['검증공고수'].sum()==len(probe_ranked)
    if len(probe_ranked) >= 10:
        q=kpi2_tie_sensitivity(probe_ranked,'자체테스트',reps=10)
        assert q['유효반복수']==10
    print('[자체 테스트 통과] 결과 판정 / 품목합계 / 금액 불일치 / 경계 / 백오프')


def main():
    ap=argparse.ArgumentParser(description='공식 금액 5분위 4변수: 2024년 예비 KPI 검증')
    ap.add_argument('--project-root',default=None)
    ap.add_argument('--notice',default=None)
    ap.add_argument('--result',default=None)
    ap.add_argument('--plan',default=None)
    ap.add_argument('--items',default=None)
    ap.add_argument('--contract',default=None)
    ap.add_argument('--mapping',default=None)
    ap.add_argument('--output-dir',default=None)
    ap.add_argument('--self-test',action='store_true')
    args=ap.parse_args()
    if args.self_test:
        self_tests()
        return
    root=Path(args.project_root).resolve() if args.project_root else project_root()
    # 업로드된 CLEAN 자료의 실제 파일명으로 자동 검색한다.
    # 계약정보는 참고 감사자료이며 품목명세나 사업규모금액을 대신하지 않는다.
    paths={
        'notice':find_input(root,args.notice,'bid_notice_clean_20210101_20251231','notice'),
        'result':find_input(root,args.result,'bid_result_clean_20210101_20251231','result'),
        'plan':find_input(root,args.plan,'procure_plan_clean_20210101_20251231','plan'),
        'contract':find_input(root,args.contract,'contract_info_clean_20210101_20251231','contract',False),
        'items':find_input(root,args.items,'bid_item_clean_20210101_20251231','items',False),
        'mapping':find_input(root,args.mapping,'orntCode_codeVld4_all','mapping',False),
    }
    # 사용자가 실제로 저장한 기관그룹 매핑은 data/clean 아래에 있다.
    # 공식 문서의 data/analysis/ornt_code 위치도 하위 호환으로 지원한다.
    # 두 파일이 동시에 존재하는 경우에는 실제 새 CLEAN 경로를 우선 사용한다.
    if args.mapping is None:
        clean_mapping=root/'data'/'clean'/'orntCode_codeVld4_all.csv'
        official_mapping=root/'data'/'analysis'/'ornt_code'/'orntCode_codeVld4_all.csv'
        paths['mapping'] = next((q for q in (clean_mapping,official_mapping) if q.is_file()), None)
    inputs={key:read_csv_safe(path) for key,path in paths.items() if path is not None}
    out=Path(args.output_dir) if args.output_dir else root/'data'/'kpi'/'kpi_validation'
    if not out.is_absolute():
        out=root/out
    ready=data_readiness_audit(inputs,paths,out)
    if not ready:
        return  # 명시적 보류. 기존 KPI 결과를 조작하거나 빈 결과를 만들지 않는다.
    run(inputs['notice'],inputs['result'],inputs['plan'],inputs['items'],inputs['mapping'],out)
    # KPI가 실제로 정상 산출된 이후에만 입력 준비도 보고서의 실행 상태를 완료로 변경.
    readiness_md=out/'00_input_readiness.md'
    report=readiness_md.read_text(encoding='utf-8')
    report=report.replace('- 필수 입력자료 5종의 스키마를 확인했습니다. 2024년 KPI 산출 단계로 진행합니다.',
       '- 필수 입력자료 5종의 스키마를 확인했고, 2024년 공식 4변수와 비교 모델 KPI 산출을 완료했습니다.\n'
       '- 결과: `03_model_comparison.csv`, `06_report.md`, `13_kpi2_tie_sensitivity.csv`.')
    readiness_md.write_text(report,encoding='utf-8')



if __name__=='__main__':
    main()