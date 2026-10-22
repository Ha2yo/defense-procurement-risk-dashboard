# -*- coding: utf-8 -*-
"""
군수품 조달: CLEAN 데이터로 유찰 위험도·2024년 KPI를 처음부터 재현하는 스크립트

[설치]  python -m pip install pandas numpy
[실행]  python scripts/kpi/kpi_2024_validation.py

[입력]  bid_notice_clean_20210101_20251231.csv
        bid_result_clean_20210101_20251231.csv

[출력]  <프로젝트 루트>/data/kpi/ 폴더에 핵심 6개 파일만 저장:
        01_population_audit.csv       전체 공고·기관그룹·결과 상태·제외 사유
        02_notice_risks_2024.csv     검증 공고별 3~6변수 위험도·대체 단계·순위
        03_model_comparison.csv      보고서용 4개 조합 KPI 및 대체율 통합표
        04_diagnostics.csv           동점·10분위·소표본·연도별·품질 진단 통합
        05_report.md                 복사 가능한 프로젝트 정의서 수정 표·해석
        execution.log                실행 단계 및 오류 기록
        ※ 원본 CLEAN 파일은 읽기만 하며 기존 출력 파일을 자동 삭제하지 않습니다.

주의:
- 기관명만으로 소속을 단정하기 어려운 기관은 '미분류'로 보존합니다.
  미분류는 확정된 일곱 번째 기관그룹이 아닙니다.
- 기존 문서의 6,813건/1,521건과 다른 모집단이 나오면 이를 숨기지 않습니다.
- 2024년 실제 결과(y)는 위험도 *계산·정렬*에 사용하지 않고 평가에만 사용합니다.
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime
from pathlib import Path
import re
import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# 0단계: 사용자 설정 및 기준값
# ─────────────────────────────────────────────────────────────────────────────
TRAIN_YEARS = (2021, 2022, 2023)  # 과거 위험도 기준표 작성 연도
TEST_YEAR = 2024                  # 평가 연도, 학습에 포함하면 미래정보 누수
MIN_SAMPLE = 10                   # 각 조건에서 최소 과거공고 건수
TOP_RATIO = 0.20                  # 평가 공고의 상위 정확히 20% (반올림)
# 공고기간 <=5 / 6~10 / >=11일; 기초금액 <=1000만 / <=1억 / >1억
DURATION_BINS = [-np.inf, 5, 10, np.inf]
AMOUNT_BINS = [-np.inf, 10_000_000, 100_000_000, np.inf]

# 위험도 단계: 순서 자체가 업무 규칙입니다. 임의로 정렬하거나 바꾸지 마세요.
STEPS_5 = [
    ['기관그룹', 'busiDivs', 'cntrctMth', '공고기간구간', '기초금액구간'],  # 1: 전체 5개
    ['기관그룹', 'busiDivs', 'cntrctMth', '공고기간구간'],                   # 2: 금액 제거
    ['기관그룹', 'busiDivs', 'cntrctMth'],                                 # 3: 기간 제거
    ['기관그룹', 'busiDivs'],                                              # 4
    ['기관그룹'],                                                          # 5
    [],                                                                    # 6: 전체
]
STEPS_4 = STEPS_5[1:]
STEPS_3 = STEPS_5[2:]
# 공고 시기는 공고일 기준 분기(Q1~Q4). 월말 여부가 아닌 기존 확장 노트북의 정의를 유지.
STEPS_6 = [
    ['기관그룹', 'busiDivs', 'cntrctMth', '공고기간구간', '기초금액구간', '공고시기구간'],
    *STEPS_5,
]
MODELS = {
    '3변수': STEPS_3,
    '4변수(기간)': STEPS_4,
    '5변수(기간+금액)': STEPS_5,
    '6변수(기간+금액+분기)': STEPS_6,
}
DOC_VALUES = {'확장안 비교표': 37.32, '수행 목표표': 35.37, 'KPI 정의서': 33.60}


def log(msg=''):
    print(msg, flush=True)


def read_csv_safe(path: Path) -> pd.DataFrame:
    """한글 윈도우/Excel UTF-8 양쪽을 고려한 읽기.
    low_memory=False: 섞인 자료형 자동추론 경고를 줄임.
    """
    last_exc = None
    for enc in ('utf-8-sig', 'utf-8', 'cp949'):
        try:
            return pd.read_csv(path, encoding=enc, low_memory=False,
                               dtype={'orntCode': 'string', 'noticeKey': 'string'})
        except UnicodeDecodeError as exc:
            last_exc = exc
    raise ValueError(f'파일 인코딩 확인 필요: {path}: {last_exc}')


def locate_file(folder: Path, stem: str) -> Path:
    """원본 파일명에 (2) 같은 숫자가 붙어도 해당 파일을 찾도록 처리."""
    paths = sorted(folder.glob(stem + '*.csv'))
    if not paths:
        raise FileNotFoundError(f'{folder} 폴더에서 {stem}*.csv 파일을 찾지 못했습니다.')
    if len(paths) > 1:
        raise ValueError(f'입력 파일 후보가 여러 개입니다. 명시적 --notice/--result 경로를 사용하세요: {[str(x) for x in paths]}')
    return paths[0]


# ─────────────────────────────────────────────────────────────────────────────
# 1단계: 기관코드 + 실제 명칭을 활용한 분류 (잠정 규칙)
# ─────────────────────────────────────────────────────────────────────────────
# '제xxxx부대'만으로 어느 군인지 알 수 없으므로 M/U 계열 무리한 배정 금지.
# 단순 키워드 규칙은 완전한 행정기관 분류표가 아니므로 사후 감사표를 반드시 확인.
GROUPS = ['육군', '해군·해병', '공군', '국방부·국군·합동', '방위사업청', '기타']

def classify_org(code, name):
    code = '' if pd.isna(code) else str(code).strip().upper()
    name = '' if pd.isna(name) else re.sub(r'\s+', '', str(name))

    # 순서는 중요함. HCC(해군제1수리창)와 U계열의 타군 예외를 먼저 검사.
    # 특정 기관코드에 대한 사전 검증 예외(코드와 이름이 모순되면 실제 이름 재검토)
    if code == 'HCC' and '해군' in name:
        return '해군·해병', '검증된 예외: HCC 해군제1수리창'
    if code in {'UHC','UHD'} and '공군' in name:
        return '공군', '검증된 예외: U계열 공군 기관'
    if code == 'UEK' and '기갑여단' in name:
        return '육군', '검증된 예외: UEK 제2기갑여단'
    if '방위사업청' in name:
        return '방위사업청', '기관명: 방위사업청'
    if any(x in name for x in ('해군', '해병', '해군제1수리창')):
        return '해군·해병', '기관명: 해군·해병'
    if any(x in name for x in ('공군', '비행단', '항공우주작전', '공중기동',
                                '방공관제', '공중전투', '항공과학')):
        return '공군', '기관명: 공군'
    if any(x in name for x in ('육군', '보병사단', '기갑여단', '군수지원', '군단',
                                '육군훈련소', '특수전사령부', '탄약지원사령부')):
        return '육군', '기관명: 육군'
    if any(x in name for x in ('국방부', '국군', '합동', '국방대학교', '국방전산')):
        return '국방부·국군·합동', '기관명: 국방부·국군·합동'
    # 접두는 보조 규칙. 공식 기관명 근거와 다를 수 있어 감사표에 분리 기록.
    if code.startswith('E'):
        return '방위사업청', '보조 추정: E 접두'
    if code.startswith('L'):
        return '육군', '보조 추정: L 접두'
    if code.startswith('S'):
        return '해군·해병', '보조 추정: S 접두'
    if code.startswith('H'):
        return '공군', '보조 추정: H 접두'
    # 기타는 기관 소속이 '기타'임을 확인했을 때만 사용: 불명확하면 미분류.
    return '미분류', '수동 확인 필요: 소속 명칭/코드 불충분'


def make_org_table(notice, mapping_path):
    org = notice[['orntCode', 'ornt']].drop_duplicates().copy()
    org['orntCode'] = org['orntCode'].astype('string').str.strip().str.upper()
    assigned = org.apply(lambda row: classify_org(row['orntCode'], row['ornt']), axis=1)
    org[['기관그룹', '분류근거']] = pd.DataFrame(assigned.tolist(), index=org.index)
    if mapping_path:
        manual = read_csv_safe(mapping_path)
        required = {'orntCode', '기관그룹'}
        if not required.issubset(manual.columns):
            raise ValueError(f'매핑 파일에 필수 열 {required}가 없습니다: {list(manual.columns)}')
        manual = manual[['orntCode', '기관그룹']].dropna(subset=['orntCode']).copy()
        manual['orntCode'] = manual['orntCode'].astype('string').str.strip().str.upper()
        manual['기관그룹'] = manual['기관그룹'].astype('string').str.strip().replace({
            '해군/해병대':'해군·해병', '해군/해병':'해군·해병',
            '국방부직할/합동':'국방부·국군·합동',
        })
        # 공백 코드는 실제 기관키가 아니므로 중단.
        if manual['orntCode'].eq('').any():
            raise ValueError('기관그룹 매핑표에 빈 기관코드가 있습니다.')
        if manual['orntCode'].duplicated().any():
            raise ValueError('매핑 파일에 중복 orntCode가 있습니다. 기관별 1행으로 정리하세요.')
        unknown_group = set(manual['기관그룹'].dropna()) - set(GROUPS) - {'미분류'}
        if unknown_group:
            raise ValueError(f'매핑 파일에서 허용되지 않는 기관그룹: {unknown_group}')
        org = org.merge(manual.rename(columns={'기관그룹': '수동지정'}), on='orntCode', how='left',
                        validate='many_to_one')
        mask = org['수동지정'].notna()
        org.loc[mask, '기관그룹'] = org.loc[mask, '수동지정']
        org.loc[mask, '분류근거'] = '사용자 매핑표: 우선 적용'
        org = org.drop(columns='수동지정')
    org['classification_method'] = org['분류근거'].str.split(':').str[0]
    org['needs_manual_review'] = org['기관그룹'].eq('미분류') | org['분류근거'].str.contains('보조 추정',na=False)
    org['institution_group'] = org['기관그룹']
    org['classification_reason'] = org['분류근거']
    # 하나의 기관코드에 다수 기관명이 있을 수 있으므로 그룹 충돌은 절대 조용히 흡수하지 않음.
    conflicts = org.groupby('orntCode')['기관그룹'].nunique(dropna=False)
    if conflicts.gt(1).any():
        bad = conflicts.index[conflicts.gt(1)].tolist()
        raise ValueError(f'동일 기관코드의 기관그룹이 충돌합니다. 매핑표 검토 필요: {bad[:20]}')
    # 한 기관코드는 반드시 한 행이어야 모든 공고 병합의 다대일 조건이 성립.
    org = org.sort_values(['orntCode','ornt']).drop_duplicates('orntCode',keep='first')
    return org.sort_values(['기관그룹','orntCode', 'ornt'])


# ─────────────────────────────────────────────────────────────────────────────
# 2단계: 공고키별 입찰결과 정리
# ─────────────────────────────────────────────────────────────────────────────
def status_from_results(values):
    """같은 공고에 여러 품목 결과가 있으면 공고 단위로 판정.
    - 유찰 + 낙찰/순위확정 동시 존재 = 판단보류 (임의 비유찰 처리 금지)
    - 낙찰 + 순위확정 동시 존재 = 비유찰
    - 유찰만 = 유찰
    - 낙찰/순위확정만 = 비유찰
    - 그 밖의 상태 = 기타/결과미확인 (분석 대상 제외)
    """
    vals = set(values.dropna().astype(str).str.strip())
    fail = '유찰' in vals
    success = bool(vals & {'낙찰', '순위확정'})
    if fail and success:
        return '판단보류'
    if fail:
        return '유찰'
    if success:
        return '비유찰'
    return '기타결과'


def parse_yyyymmdd(series):
    """YYYYMMDD 또는 YYYYMMDDhhmm ... 의 앞 8자리만 읽음."""
    return pd.to_datetime(series.astype('string').str.replace(r'\D', '', regex=True).str[:8],
                          format='%Y%m%d', errors='coerce')


def make_analysis_table(notice, results, org):
    if notice['noticeKey'].isna().any() or notice['noticeKey'].duplicated().any():
        raise ValueError('입찰공고의 noticeKey가 비어 있거나 중복됩니다. 원본 확인 필요.')
    by_key = (results.groupby('noticeKey', dropna=False)['bidResult']
              .apply(status_from_results).rename('결과상태').reset_index())
    log(f'입찰결과 고유 공고키: {len(by_key):,}개')
    a = notice.merge(org.drop(columns=['ornt'], errors='ignore'), on='orntCode', how='left', validate='many_to_one')
    a = a.merge(by_key, on='noticeKey', how='left', validate='one_to_one')
    # 공고키 연결 실패에 대한 데이터 항목정의서의 상호배타적 상태 판정 우선순위:
    # 1) 직접 연결, 2) 취소공고, 3) 동일사업 다른 공고에 결과 있음, 4) 결과 미확인.
    result_business = set(results.loc[results['noticeKey'].notna(), 'businessKey'].dropna())
    missing = a['결과상태'].isna()
    a.loc[missing & a['pblancSe'].eq('취소공고'), '결과상태'] = '취소공고'
    missing = a['결과상태'].isna()
    a.loc[missing & a['businessKey'].isin(result_business), '결과상태'] = '동일사업_다른공고결과'
    a['결과상태'] = a['결과상태'].fillna('결과미확인')
    a['공고일'] = parse_yyyymmdd(a['pblancDate'])
    a['공고시기구간'] = ('Q' + a['공고일'].dt.quarter.astype('Int64').astype('string')).astype('object')
    a.loc[a['공고일'].isna(), '공고시기구간'] = np.nan
    a['연도'] = a['공고일'].dt.year
    end = parse_yyyymmdd(a['bidPartcptRegistClosDt'])
    a['공고기간일'] = (end - a['공고일']).dt.days
    valid_days = a['공고기간일'].where(a['공고기간일'].ge(0))
    a['공고기간구간'] = pd.cut(valid_days, DURATION_BINS,
                          labels=['5일 이하','6~10일','11일 이상']).astype('object')
    a['기초금액숫자'] = pd.to_numeric(a['bsicExpt'], errors='coerce')
    # 0원/음수는 일반적으로 정상 금액 입력으로 간주하지 않음: 검토할 수 있도록 결측.
    positive_amount = a['기초금액숫자'].where(a['기초금액숫자'].gt(0))
    a['기초금액구간'] = pd.cut(positive_amount, AMOUNT_BINS,
                           labels=['1천만 원 이하','1천만~1억 원','1억 원 초과']).astype('object')
    a['유찰여부'] = a['결과상태'].map({'유찰':1, '비유찰':0}).astype('float64')
    a['수의계약여부'] = a['cntrctMth'].astype('string').str.contains('수의', na=False)
    a['제외사유'] = np.select(
        [a['공고일'].isna(),a['noticeKey'].isna(),a['수의계약여부'],a['결과상태'].eq('판단보류'),
         a['결과상태'].eq('취소공고'),a['결과상태'].eq('동일사업_다른공고결과'),
         a['결과상태'].eq('결과미확인'),a['유찰여부'].isna()],
        ['공고일 파싱 실패','공고키 누락','수의계약','판단보류','취소공고','동일사업 다른 공고 결과',
         '결과 미확인','기타 확정되지 않은 결과'],default='포함')
    a['평가가능'] = a['제외사유'].eq('포함')
    return a


# ─────────────────────────────────────────────────────────────────────────────
# 3단계: 학습-검증 분리 및 표본부족 6단계 백오프
# ─────────────────────────────────────────────────────────────────────────────
def score_model(train, test, steps, minimum=MIN_SAMPLE):
    """미래 결과를 열어보지 않고 조건별 과거 유찰률만으로 검증 공고 위험도 부여.

    모든 행에서 가장 구체적인 1단계부터 확인.
    10건 미만 또는 필요한 입력값 결측이면 다음 단계로 이동.
    조건을 모두 제거한 6단계는 학습 전체 유찰률을 사용.
    """
    scored = test.copy()
    scored['위험도'] = np.nan
    scored['근거공고수'] = pd.Series(pd.NA,index=scored.index,dtype='Int64')
    scored['근거유찰건수'] = pd.Series(pd.NA,index=scored.index,dtype='Int64')
    scored['적용단계'] = pd.Series(pd.NA,index=scored.index,dtype='Int64')
    scored['적용조건'] = ''
    # 최초 모델의 변수에 결측이 있는지와 '표본수 부족'을 명확히 분리하여 기록.
    first_cols = steps[0]
    scored['최초조합_결측'] = scored[first_cols].isna().any(axis=1)
    # 상세 조합의 결측이 우선 원인. 조합이 유효할 때만 표본 부족으로 집계한다.
    scored['최초조합_입력오류'] = scored['최초조합_결측']
    scored['최초조합_표본수'] = pd.Series(pd.NA,index=scored.index,dtype='Int64')
    if first_cols:
        initial = (train.dropna(subset=first_cols).groupby(first_cols, observed=True)['유찰여부']
                   .size().rename('_n').reset_index())
        lookup_init = scored[first_cols].assign(_idx=scored.index).merge(initial,how='left',on=first_cols)
        scored.loc[lookup_init['_idx'].to_numpy(),'최초조합_표본수'] = lookup_init['_n'].fillna(0).astype('int64').to_numpy()
    for step_no, cols in enumerate(steps, 1):
        pending = scored['위험도'].isna()
        if not pending.any():
            break
        if not cols:
            n = len(train)
            fails = int(train['유찰여부'].sum())
            scored.loc[pending,'위험도'] = fails/n
            scored.loc[pending,'근거공고수'] = n
            scored.loc[pending,'근거유찰건수'] = fails
            scored.loc[pending,'적용단계'] = step_no
            scored.loc[pending,'적용조건'] = '전체'
            break
        # 범주가 비어 있는 행은 구체적 조건의 비교 불가 → 단계 완화.
        complete = scored.loc[pending, cols].notna().all(axis=1)
        idx_valid = complete.index[complete]
        if len(idx_valid)==0:
            continue
        grp = (train.dropna(subset=cols)
               .groupby(cols, observed=True, dropna=True)['유찰여부']
               .agg(근거공고수='size', 근거유찰건수='sum').reset_index())
        lookup = scored.loc[idx_valid,cols].copy()
        lookup['_row_index'] = lookup.index
        matched = lookup.merge(grp,on=cols,how='left',validate='many_to_one').set_index('_row_index')
        ok = matched['근거공고수'].ge(minimum)
        selected = matched.loc[ok]
        selected_idx = selected.index
        if len(selected_idx)==0:
            continue
        scored.loc[selected_idx,'위험도'] = (selected['근거유찰건수']/selected['근거공고수']).to_numpy()
        scored.loc[selected_idx,'근거공고수'] = selected['근거공고수'].astype('int64').to_numpy()
        scored.loc[selected_idx,'근거유찰건수'] = selected['근거유찰건수'].astype('int64').to_numpy()
        scored.loc[selected_idx,'적용단계'] = step_no
        scored.loc[selected_idx,'적용조건'] = ' × '.join(cols)
    scored['완화사유'] = np.where(scored['적용단계'].eq(1),'완화 없음',
                       np.where(scored['최초조합_결측'],'입력 결측·오류','표본 부족'))
    # 3·4변수 모델은 상대적인 단계 번호이며, 5변수 모델에서는 1~6단계.
    assert scored['위험도'].notna().all(), '일부 공고의 위험도 미산정'
    assert (scored['근거공고수'] >= minimum).all(), '근거 표본수 부족'
    return scored


def evaluate(scored):
    """20% 경계 동점 공고 선택에서 실제 유찰 여부를 사용하지 않음.
    같은 위험도에서는 근거표본 큰 순 → 공고키 사전순으로 정렬.
    반올림: 0.5 이상 올림(파이썬 round의 bankers rounding 미사용).
    """
    sorted_ = scored.sort_values(['위험도','근거공고수','noticeKey'],
                                 ascending=[False,False,True],kind='stable').copy()
    count = len(sorted_)
    k = int(np.floor(count*TOP_RATIO + 0.5))
    sorted_['순위'] = np.arange(1,count+1)
    sorted_['상위20pct'] = sorted_['순위'].le(k)
    captured = int(sorted_.loc[sorted_['상위20pct'],'유찰여부'].sum())
    actual = int(sorted_['유찰여부'].sum())
    kpi = 100*captured/actual if actual else np.nan
    # 경계에 같은 위험도를 가진 공고가 있다면 선택 순서에 따라 KPI가 달라질 수 있음.
    border_risk = float(sorted_.iloc[k-1]['위험도']) if k else np.nan
    tied = sorted_.loc[sorted_['위험도'].eq(border_risk)]
    above = sorted_.loc[sorted_['위험도'].gt(border_risk)]
    tie_selected = int(tied['상위20pct'].sum())
    tie_report = {
        '경계위험도':border_risk, '경계동점전체':len(tied),'경계동점선정':tie_selected,
        '경계동점내_포착유찰':int(tied.loc[tied['상위20pct'],'유찰여부'].sum()),
        '동점전부제외_식별률':100*above['유찰여부'].sum()/actual if actual else np.nan,
        '동점전부포함_식별률':100*(above['유찰여부'].sum()+tied['유찰여부'].sum())/actual if actual else np.nan,
    }
    return sorted_, {'검증공고수':count, '전체유찰':actual, '상위20pct공고수':k,
                     '포착유찰':captured, '사전식별률_pct':kpi}, tie_report



def kpi2_deciles(ranked: pd.DataFrame, model: str):
    """KPI 2: 위험도 하위부터 상위 10분위의 실제 유찰률 Spearman 순위상관.

    기존 KPI 2 정의처럼 공고 수가 동일한 10개 구간을 사용한다.
    위험도 동점이 분위 경계를 가로지르면 그 수를 별도 기록한다.
    """
    df = ranked.sort_values(['위험도','근거공고수','noticeKey'],
                            ascending=[True,True,True],kind='stable').reset_index(drop=True).copy()
    df['분위'] = (np.floor(np.arange(len(df))*10/len(df)).astype(int)+1)
    agg = df.groupby('분위',sort=True).agg(
        검증공고수=('유찰여부','size'), 유찰건수=('유찰여부','sum'),
        위험도최솟값=('위험도','min'), 위험도최댓값=('위험도','max')).reset_index()
    agg['실제유찰률_pct'] = 100*agg['유찰건수']/agg['검증공고수']
    agg.insert(0,'model',model)
    rho = float(agg['분위'].corr(agg['실제유찰률_pct'],method='spearman')) if len(agg)>1 else np.nan
    boundary = [i for i in range(1,len(df)) if df.at[i,'분위']!=df.at[i-1,'분위'] and
                df.at[i,'위험도']==df.at[i-1,'위험도']]
    return agg, rho, len(boundary)


# ─────────────────────────────────────────────────────────────────────────────
# 4단계: 실행 및 검증 결과 저장
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# 5단계: 실행 전 스키마·품질 검증 및 회귀 테스트
# ─────────────────────────────────────────────────────────────────────────────
def validate_inputs(notice: pd.DataFrame, result: pd.DataFrame) -> pd.DataFrame:
    """파일별 품질 점검표를 반환하며 치명적인 키 오류가 있으면 중단한다."""
    needed_n={'noticeKey','businessKey','orntCode','ornt','demandYear','dcsNo',
              'pblancNo','pblancOdr','pblancDate','pblancSe','bidPartcptRegistClosDt',
              'busiDivs','cntrctMth','bsicExpt'}
    needed_r={'noticeKey','businessKey','bidResult'}
    for label,df,required in [('공고',notice,needed_n),('결과',result,needed_r)]:
        missing=required-set(df.columns)
        if missing: raise ValueError(f'{label} 파일 필수 컬럼 누락: {sorted(missing)}; 실제 컬럼={df.columns.tolist()}')
    audits=[]
    for label,df in [('공고',notice),('결과',result)]:
        log(f'  [{label}] {len(df):,}행 × {len(df.columns)}열; 주요 컬럼 미리보기: {list(df.columns)}')
        for col in df.columns:
            audits.append({'데이터':label,'컬럼':col,'dtype':str(df[col].dtype),
                           '전체행':len(df),'결측수':int(df[col].isna().sum()),
                           '결측률_pct':round(100*df[col].isna().mean(),4),
                           '고유값수':df[col].nunique(dropna=True)})
    if notice['noticeKey'].isna().any() or notice['noticeKey'].duplicated().any():
        raise ValueError('공고 noticeKey 누락/중복 발견. 원본 감사 필요')
    if result['noticeKey'].isna().any():
        raise ValueError('결과 noticeKey 누락 발견. 원본 감사 필요')
    # CLEAN의 businessKey는 만들어진 식별키이며 문자열을 재조립해서 구성 검증.
    for label,df in [('공고',notice),('결과',result)]:
        if not {'businessKey','pblancNo','pblancOdr','noticeKey'}.issubset(df.columns):
            log(f'  [{label}] 키 재구성용 원본 3개 열이 없어 사전 검사는 생략(고유 키 연결은 검증)')
            continue
        expected=(df['businessKey'].astype('string')+'_'+df['pblancNo'].astype('string')
                  +'_'+df['pblancOdr'].astype('string'))
        inconsistent=int(expected.ne(df['noticeKey']).fillna(True).sum())
        log(f'  [{label}] noticeKey ≠ businessKey + 공고번호 + 차수: {inconsistent:,}건')
        if inconsistent:
            # 일부 API 데이터에서 공고차수가 실수형으로 읽히는 것을 회피하기 위해
            # 정수 의미의 값만 1.0 → 1로 표준화해도 차이가 남는지 확인.
            odr=pd.to_numeric(df['pblancOdr'],errors='coerce')
            normalized=odr.map(lambda x: str(int(x)) if pd.notna(x) and float(x).is_integer() else str(x))
            expected=(df['businessKey'].astype('string')+'_'+df['pblancNo'].astype('string')+'_'+normalized)
            inconsistent=int(expected.ne(df['noticeKey']).fillna(True).sum())
        if inconsistent:
            log(f'  [경고] {label} 키 구성 불일치 {inconsistent:,}건: CSV 원문 확인 필요; 기존 noticeKey 연결 유지')
    return pd.DataFrame(audits)


def self_tests() -> None:
    """실데이터와 독립적인 최소한의 중요 경계조건 자동 테스트."""
    assert status_from_results(pd.Series(['낙찰','순위확정'])) == '비유찰'
    assert status_from_results(pd.Series(['유찰','낙찰'])) == '판단보류'
    assert status_from_results(pd.Series(['유찰'])) == '유찰'
    assert status_from_results(pd.Series(['취소'])) == '기타결과'
    assert status_from_results(pd.Series(['낙찰', '순위확정', '유찰'])) == '판단보류'
    assert set(MODELS) == {'3변수', '4변수(기간)', '5변수(기간+금액)', '6변수(기간+금액+분기)'}
    assert STEPS_6[1:] == STEPS_5
    assert int(np.floor(12.5+0.5)) == 13  # 명시적 half-up
    cols=STEPS_5[0]
    def record(key,i,amount='소액'):
        d={'noticeKey':key,'기관그룹':'육군','busiDivs':'물품','cntrctMth':'제한경쟁',
           '공고기간구간':'6~10일','기초금액구간':amount,'유찰여부':int(i%3==0)}
        return d
    train=pd.DataFrame([record(f't{i}',i) for i in range(10)])
    test=pd.DataFrame([record('good',0),record('missing',1,None)])
    scored=score_model(train,test,STEPS_5)
    assert scored.iloc[0]['적용단계']==1  # 정확히 10건인 조합 채택
    assert scored.iloc[1]['적용단계']==2  # 금액 결측 시 2단계
    train9=train.iloc[:9].copy()
    try:
        score_model(train9,test,STEPS_5)
    except (AssertionError, ValueError):
        pass  # 9건은 전체 표본까지 부족하므로 명시적으로 실패해야 정상
    else:
        raise AssertionError('전체 표본이 10건 미만인데 계산이 성공했습니다')
    empty=pd.DataFrame(columns=train.columns)
    # 전체 학습 건수 0일 때는 main 진입 전에 오류를 발생시킨다.
    assert empty.empty
    # 6변수 조합의 분기 결측은 5변수 조합으로 하향해야 한다.
    train6=train.assign(공고시기구간='Q1')
    test6=test.assign(공고시기구간=['Q1',None],기초금액구간='소액')
    scored6=score_model(train6,test6,STEPS_6)
    assert scored6.iloc[0]['적용단계']==1
    assert scored6.iloc[1]['적용단계']==2
    assert scored6.iloc[1]['완화사유']=='입력 결측·오류'
    log('  [테스트 통과] 혼재 결과, 10건 기준, 6변수/결측 완화, 반올림, 결과 판정')


def save_csv(df: pd.DataFrame, dest: Path) -> None:
    """원본 CLEAN에 쓰지 않고 새 출력폴더에 UTF-8-SIG 저장."""
    dest.parent.mkdir(parents=True,exist_ok=True)
    df.to_csv(dest,index=False,encoding='utf-8-sig')


# ─────────────────────────────────────────────────────────────────────────────
# 경로 설정: VS Code / PowerShell / 다른 작업 폴더에서도 일관되게 동작
# ─────────────────────────────────────────────────────────────────────────────
def detect_project_root() -> Path:
    """현재 코드가 scripts/kpi/에 있을 때 프로젝트 최상위 폴더를 찾는다.

    찾는 조건: 루트/data/clean, 루트/scripts/kpi 폴더가 동시에 존재.
    이 구조가 없다면 자동으로 임의의 출력 위치를 생성하지 않고 오류를 낸다.
    """
    script_dir = Path(__file__).resolve().parent
    candidates = [*script_dir.parents, Path.cwd().resolve(), *Path.cwd().resolve().parents]
    for folder in dict.fromkeys(candidates):
        if (folder / 'data' / 'clean').is_dir() and (folder / 'scripts' / 'kpi').is_dir():
            return folder
    raise FileNotFoundError(
        '프로젝트 루트를 자동으로 찾지 못했습니다. '
        '루트 폴더에 data/clean 및 scripts/kpi가 있는지 확인하거나 '
        '--project-root "C:/workspace/defense-procurement-risk-dashboard"를 지정하세요.'
    )


def resolve_project_path(path: Path, project_root: Path) -> Path:
    """명시적 경로가 상대 경로라면 현재 작업 폴더가 아닌 프로젝트 루트 기준."""
    path = path.expanduser()
    return (path if path.is_absolute() else project_root / path).resolve()


def main() -> None:
    """전체 분석 진입점. 실패 시 메시지와 로그에 단계·원인을 남긴다."""
    parser = argparse.ArgumentParser(description='국방 조달: 2024 KPI 및 차이 원인 독립 재검증')
    parser.add_argument('--project-root', type=Path, default=None,
                        help='선택: 프로젝트 루트. 생략 시 파일 위치와 폴더 구조로 자동 탐색')
    parser.add_argument('--notice', type=Path, default=None, help='선택: 공고 CLEAN CSV 경로')
    parser.add_argument('--result', type=Path, default=None, help='선택: 결과 CLEAN CSV 경로')
    parser.add_argument('--mapping', type=Path, default=None,
                        help='선택: 기관그룹 매핑 CSV (프로젝트 루트 기준 상대 경로 가능)')
    parser.add_argument('--output-dir', type=Path, default=None,
                        help='선택: 결과 폴더 (기본값: <프로젝트 루트>/data/kpi)')
    args = parser.parse_args()

    root = (args.project_root.expanduser().resolve() if args.project_root
            else detect_project_root())
    clean = root / 'data' / 'clean'
    if not clean.is_dir():
        raise FileNotFoundError(f'CLEAN 데이터 폴더가 없습니다: {clean}')
    notice_path = (resolve_project_path(args.notice, root) if args.notice
                   else clean / 'bid_notice_clean_20210101_20251231.csv')
    result_path = (resolve_project_path(args.result, root) if args.result
                   else clean / 'bid_result_clean_20210101_20251231.csv')
    mapping_path = resolve_project_path(args.mapping, root) if args.mapping else None
    out = (resolve_project_path(args.output_dir, root) if args.output_dir
           else root / 'data' / 'kpi')

    # 입력 파일이나 데이터 폴더 자체를 덮어쓰지 않도록 최소한의 안전 검사.
    input_paths = {notice_path.resolve(), result_path.resolve()}
    if mapping_path:
        input_paths.add(mapping_path)
    output_names = ('01_population_audit.csv', '02_notice_risks_2024.csv',
                    '03_model_comparison.csv', '04_diagnostics.csv',
                    '05_report.md', 'execution.log')
    if any((out / name).resolve() in input_paths for name in output_names):
        raise ValueError('출력 파일 경로가 입력 데이터와 충돌합니다. --output-dir를 변경하세요.')
    if not all((out / name).resolve().is_relative_to(out.resolve()) for name in output_names):
        raise ValueError('출력 파일 경로를 확인하세요.')
    out.mkdir(parents=True, exist_ok=True)
    log(f'[경로] 프로젝트 루트: {root}')
    log(f'[경로] 입력 CLEAN: {clean}')
    log(f'[경로] 결과 저장: {out}')
    logging.basicConfig(filename=out/'execution.log',level=logging.INFO,encoding='utf-8',
                        format='%(asctime)s %(levelname)s %(message)s',force=True)
    t_all=time.perf_counter()
    def step(number,label,fn):
        log(f'\n[STEP {number:02d}] {label} 시작')
        logging.info('STEP %02d 시작: %s',number,label)
        t=time.perf_counter()
        try: value=fn()
        except Exception:
            logging.exception('STEP %02d 실패: %s',number,label)
            raise
        elapsed=time.perf_counter()-t
        log(f'[STEP {number:02d}] 완료 ({elapsed:.2f}초)')
        logging.info('STEP %02d 완료 %.3f초',number,elapsed)
        return value

    def load():
        for label,path in [('입찰공고',notice_path),('입찰결과',result_path)]:
            if not path.is_file():
                raise FileNotFoundError(f'{label} 파일이 없습니다: {path}\n파일명을 확인하거나 --notice/--result로 지정하세요.')
        n=read_csv_safe(notice_path);r=read_csv_safe(result_path)
        n['orntCode']=n['orntCode'].astype('string').str.strip().str.upper()
        if 'orntCode' in r.columns:
            r['orntCode']=r['orntCode'].astype('string').str.strip().str.upper()
        log(f'  공고 {len(n):,}행 / 결과 {len(r):,}행')
        return n,r
    notice,results=step(1,'CLEAN 파일 로드',load)
    def audit():
        report=validate_inputs(notice,results)
        self_tests()
        return report
    step(2,'스키마·키 품질검증 및 단위 테스트',audit)
    def group():
        org=make_org_table(notice,mapping_path)
        log('  '+str(org.groupby('기관그룹')['orntCode'].nunique().to_dict()))
        conflicts=org.groupby('orntCode')['기관그룹'].nunique()
        if conflicts.gt(1).any():
            log('  [경고] 기관코드에 그룹 충돌 있음. 충돌 감사표 확인 필요')
        return org
    org=step(3,'기관명 기반 예외 및 잠정 기관 분류',group)
    def join():
        a=make_analysis_table(notice,results,org)
        if len(a)!=len(notice):raise ValueError('공고·결과 연결 후 행 증가 발생')
        # 전체 공고의 포함·제외 근거와 기관그룹 분류를 한 파일로 기록한다.
        # 결과가 없는 공고도 보존하므로 모집단 차이를 공고 단위로 추적 가능.
        audit_cols=['noticeKey','businessKey','orntCode','ornt','기관그룹','분류근거',
                    'needs_manual_review','공고일','연도','결과상태','유찰여부',
                    'cntrctMth','제외사유','평가가능']
        save_csv(a[[c for c in audit_cols if c in a.columns]],out/'01_population_audit.csv')
        status=a.groupby(['연도','결과상태','제외사유'],dropna=False).size().reset_index(name='공고건수')
        log('  상태별 건수(전체):\n'+status.to_string(index=False))
        log(f'  원본 공고 {len(notice):,}행 → 연결 후 {len(a):,}행')
        log('  제외사유별:\n'+a['제외사유'].value_counts().to_string())
        log(f'  연도별 공고일: {a["연도"].value_counts().sort_index().to_dict()}')
        return a
    data=step(4,'공고·결과 연결, 공고단위 결과 판정',join)
    def population():
        train=data.loc[data['연도'].isin(TRAIN_YEARS) & data['평가가능']].copy()
        test=data.loc[data['연도'].eq(TEST_YEAR) & data['평가가능']].copy()
        if train.empty or test.empty:raise ValueError('학습 또는 검증 모집단이 비었습니다')
        if train['연도'].max()>=2024 or not test['연도'].eq(2024).all():raise ValueError('시간 누수 검사 실패')
        if not train['유찰여부'].isin([0,1]).all() or not test['유찰여부'].isin([0,1]).all():
            raise ValueError('이진 목표변수 검사 실패')
        log(f'  학습: {len(train):,}건, 검증: {len(test):,}건, 실제 유찰: {int(test.유찰여부.sum()):,}건')
        log(f'  기존 문서 모집단 차이: {len(test)-6813:+,}건 / 유찰 차이: {int(test.유찰여부.sum())-1521:+,}건')
        return train,test
    train,test=step(5,'2021~2023 학습 및 2024 검증 모집단 확정',population)
    # 핵심 출력은 4개 CSV + 보고서 1개 + 실행 로그 1개로 통합한다.
    # diag_rows는 여러 진단을 한 파일의 분석구분 컬럼으로 식별한다.
    diag_rows=[]
    def add_diagnostic(kind, rows):
        if isinstance(rows,pd.DataFrame):
            rows=rows.to_dict('records')
        for row in rows:
            diag_rows.append({'분석구분':kind,**row})

    def scoring():
        results_summary=[];per_notice=None
        for model,steps in MODELS.items():
            scored=score_model(train,test,steps)
            ranked,metric,tie=evaluate(scored)
            dtable,rho,split_boundaries=kpi2_deciles(ranked,model)
            if not ranked['위험도'].between(0,1).all():raise ValueError('위험도 범위 벗어남')
            if metric['상위20pct공고수']!=int(np.floor(len(test)*TOP_RATIO+0.5)):
                raise ValueError('상위 20% 선정 수 불일치')
            if not ranked['noticeKey'].is_unique:raise ValueError('검증 공고키 중복')
            sparse=int(ranked['완화사유'].eq('표본 부족').sum())
            invalid=int(ranked['완화사유'].eq('입력 결측·오류').sum())
            exact=int(ranked['적용단계'].eq(1).sum())
            result={'model':model,**metric,
                    '정확조합건수':exact,'표본부족대체건수':sparse,'결측오류대체건수':invalid,
                    '표본부족대체율_pct':round(100*sparse/len(test),4),
                    '결측오류대체율_pct':round(100*invalid/len(test),4),
                    '전체대체율_pct':round(100*(sparse+invalid)/len(test),4),
                    '근거공고수중앙값':float(ranked['근거공고수'].median()),
                    'Precision_at20_pct':round(100*metric['포착유찰']/metric['상위20pct공고수'],4),
                    'Lift_at20':round((metric['포착유찰']/metric['전체유찰'])/
                                       (metric['상위20pct공고수']/len(test)),4),
                    'KPI2_spearman':rho,'동점분위경계수':split_boundaries}
            assert exact+sparse+invalid==len(test),'완화 사유별 공고 건수 합계 불일치'
            results_summary.append(result)
            add_diagnostic('상위20pct_동점',[{'model':model,**tie}])
            add_diagnostic('KPI2_10분위',dtable)
            for (stage,why),count in ranked.groupby(['적용단계','완화사유'],dropna=False).size().items():
                add_diagnostic('단계별_대체',[{'model':model,'적용단계':stage,'완화사유':why,'건수':count}])
            # 중복되는 모델별 CSV 네 개를 공고키 기준 단일 비교 파일로 묶는다.
            # 같은 공고에서 모델별 위험도·근거수·적용단계·순위를 한 줄에 비교 가능.
            suffix={'3변수':'3v','4변수(기간)':'4v',
                    '5변수(기간+금액)':'5v','6변수(기간+금액+분기)':'6v'}[model]
            fields=['noticeKey','위험도','근거공고수','근거유찰건수','적용단계',
                    '적용조건','완화사유','최초조합_표본수','최초조합_결측','순위','상위20pct']
            part=ranked[fields].rename(columns={c:f'{suffix}_{c}' for c in fields if c!='noticeKey'})
            if per_notice is None:
                info=['noticeKey','공고일','연도','기관그룹','busiDivs','cntrctMth',
                      '공고기간구간','기초금액구간','공고시기구간','유찰여부']
                per_notice=ranked[[c for c in info if c in ranked.columns]].merge(
                    part,on='noticeKey',how='left',validate='one_to_one')
            else:
                per_notice=per_notice.merge(part,on='noticeKey',how='left',validate='one_to_one')
            log(f'  {model}: 포착 {metric["포착유찰"]:,}/{metric["전체유찰"]:,}건 = {metric["사전식별률_pct"]:.4f}%')
        summary=pd.DataFrame(results_summary)
        save_csv(summary,out/'03_model_comparison.csv')
        save_csv(per_notice,out/'02_notice_risks_2024.csv')
        return summary
    summary=step(6,'모델별 단계적 위험도 산정 및 KPI 평가',scoring)
    def final():
        known_train=train.loc[train['기관그룹'].ne('미분류')].copy()
        known_test=test.loc[test['기관그룹'].ne('미분류')].copy()
        if len(known_train)<MIN_SAMPLE or known_test.empty or not known_test['유찰여부'].sum():
            km={'사전식별률_pct':np.nan}
        else:
            _,km,_=evaluate(score_model(known_train,known_test,STEPS_5))
        full=summary.loc[summary['model'].eq('5변수(기간+금액)')].iloc[0]
        add_diagnostic('기관미분류_민감도',[
            {'구분':'미분류 유지(잠정)','검증공고':len(test),
             '전체유찰':int(test.유찰여부.sum()),'사전식별률_pct':full['사전식별률_pct']},
            {'구분':'미분류 제외','검증공고':len(known_test),
             '전체유찰':int(known_test.유찰여부.sum()),'사전식별률_pct':km['사전식별률_pct']}])
        # 과거 문서값은 서로 다른 연도·모델인 경우가 있어 '차이'만 기록하고
        # 성능 우열 근거로 해석하지 않는다.
        add_diagnostic('과거문서_비교',[{'문서':k,'문서_식별률_pct':v,
            '재계산_식별률_pct':full['사전식별률_pct'],
            '차이_pp':round(full['사전식별률_pct']-v,4),
            '비교조건':'평가연도·기관변수·모델 불일치 가능'} for k,v in DOC_VALUES.items()])
        # 기관그룹 감사는 전체 공고 파일에 포함되어 있고, 충돌은 진단표로 병합한다.
        grp_conflict=org.groupby('orntCode')['기관그룹'].nunique()
        add_diagnostic('기관그룹_충돌',[{'orntCode':c,'충돌그룹수':int(v)}
            for c,v in grp_conflict[grp_conflict.gt(1)].items()])
        # 컬럼별 결측률·스키마 품질 점검도 별도 파일을 만들지 않고 통합 진단.
        quality=validate_inputs(notice,results)
        add_diagnostic('원본데이터_품질',quality)
        log('\n===== 최종 요약 =====\n'+summary.to_string(index=False))
        log('\n[주의] 기존 문서 6,813/1,521과 불일치 시 원인 확인 전 KPI 확정 금지.')
    step(7,'기관 미분류 민감도 및 과거 문서 비교',final)

    # ─────────────────────────────────────────────────────────────────────────
    # 8단계: 최소 과거 표본 5·10·20건에 따른 민감도 분석
    #       이 결과는 기준 모델을 대체하지 않는 참고 실험이다.
    # ─────────────────────────────────────────────────────────────────────────
    def minimum_sensitivity():
        rows=[]
        for threshold in (5, 10, 20):
            if len(train)<threshold:
                continue
            for model,steps in MODELS.items():
                scored=score_model(train,test,steps,minimum=threshold)
                _,metric,_=evaluate(scored)
                rows.append({'최소표본':threshold,'model':model,
                    '평가공고수':metric['검증공고수'],'전체유찰':metric['전체유찰'],
                    '포착유찰':metric['포착유찰'],'사전식별률_pct':metric['사전식별률_pct'],
                    '표본부족대체율_pct':round(100*scored['완화사유'].eq('표본 부족').mean(),4),
                    '입력결측대체율_pct':round(100*scored['완화사유'].eq('입력 결측·오류').mean(),4)})
        add_diagnostic('최소표본_민감도',rows)
        return len(rows)
    step(8,'최소 표본 5·10·20건 민감도 분석',minimum_sensitivity)

    # ─────────────────────────────────────────────────────────────────────────
    # 9단계: 연도별 순차 검증. 연도 t의 학습에는 t 이전 공고만 포함.
    #       과거 결과의 실제 확정/공개일 부재라는 한계는 그대로 남는다.
    # ─────────────────────────────────────────────────────────────────────────
    def rolling_validation():
        rows=[]
        for year in (2022, 2023, 2024, 2025):
            history = data.loc[data['연도'].ge(2021) & data['연도'].lt(year) & data['평가가능']].copy()
            holdout = data.loc[data['연도'].eq(year) & data['평가가능']].copy()
            if len(history)<MIN_SAMPLE or holdout.empty or int(holdout['유찰여부'].sum())==0:
                rows.append({'검증연도':year,'model':'검증 불가',
                    '학습공고수':len(history),'평가공고수':len(holdout),
                    '비고':'학습표본/검증표본 또는 유찰 수 부족'})
                continue
            for model,steps in MODELS.items():
                ranked,metric,_=evaluate(score_model(history,holdout,steps))
                rows.append({'검증연도':year,'model':model,'학습공고수':len(history),
                    '평가공고수':metric['검증공고수'],'전체유찰':metric['전체유찰'],
                    '포착유찰':metric['포착유찰'],'사전식별률_pct':metric['사전식별률_pct'],
                    '표본부족대체율_pct':round(100*ranked['완화사유'].eq('표본 부족').mean(),4),
                    '비고':'결과확정일 가용성 별도 검증 필요'})
        add_diagnostic('연도별_순차검증',rows)
        return len(rows)
    step(9,'2022~2025년 순차 평가(탐색적)',rolling_validation)

    # ─────────────────────────────────────────────────────────────────────────
    # 10단계: 모든 진단·교수님 제출용 표를 파일 2개로 최종 출력.
    #         원본 CLEAN을 변경하지 않으며 출력 폴더의 다른 기존 파일은 건드리지 않음.
    # ─────────────────────────────────────────────────────────────────────────
    def finalize_outputs():
        save_csv(pd.DataFrame(diag_rows),out/'04_diagnostics.csv')
        lines=['# 2024년 KPI 확장 후보안 비교','',
               '학습: 2021.01.01~2023.12.31 / 검증: 2024.01.01~2024.12.31 (공고일 기준)',
               '', '| 위험도 산정 방식 | 표본 부족 대체율 | 사전 식별률 |',
               '|---|---:|---:|']
        titles={'3변수':'기존 3개 변수(기관그룹, 사업유형, 계약·경쟁방식)',
                '4변수(기간)':'공고기간 추가',
                '5변수(기간+금액)':'공고기간 + 기초금액',
                '6변수(기간+금액+분기)':'공고기간 + 기초금액 + 공고 시기'}
        for _,r in summary.iterrows():
            lines.append(f"| {titles[r['model']]} | {r['표본부족대체율_pct']:.2f}% | {r['사전식별률_pct']:.2f}% |")
        a5=summary.loc[summary['model'].eq('5변수(기간+금액)')].iloc[0]
        a6=summary.loc[summary['model'].eq('6변수(기간+금액+분기)')].iloc[0]
        lines.extend(['','## 공고 시기 추가 영향',
                      f"표본 부족 대체율 변화: {a6['표본부족대체율_pct']-a5['표본부족대체율_pct']:+.2f}%p",
                      f"사전 식별률 변화: {a6['사전식별률_pct']-a5['사전식별률_pct']:+.2f}%p",
                      '', '## 산출 기준 및 주의',
                      '- 표본 부족 대체율은 최초 조합의 필수 입력이 유효하나 과거 공고가 10건 미만인 비율.',
                      '- 입력 결측·오류 대체율은 03_model_comparison.csv에서 별도 확인.',
                      '- 공고기간은 공고일~참가등록마감일의 날짜 차이로, 법정 공고기간과 동일하다고 단정할 수 없음.',
                      '- 금액/기간 구간 경계는 제도 기준으로 검증되지 않은 탐색 구간.',
                      '- 2025년 확장 분석 수치(37.32% 등)를 본 2024년 결과와 혼용하지 말 것.',
                      '- 기관그룹의 잠정 분류와 결과 확인 가능 시점에 관한 한계가 남아 있음.',
                      '', '## 진단 자료 활용',
                      '- 01_population_audit.csv: 공고별 제외 사유와 기관그룹 분류',
                      '- 02_notice_risks_2024.csv: 공고별 네 조합의 위험도·순위 비교',
                      '- 03_model_comparison.csv: 네 모델의 공식 비교 수치',
                      '- 04_diagnostics.csv: 분석구분별 동점·10분위·민감도·연도별 진단'])
        (out/'05_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
        log('\n[출력 요약] 인구집단 감사 / 공고별 위험도 / 모델비교 / 통합진단 / 보고서 / 실행로그 (총 6개)')
        log(f'총 소요시간: {time.perf_counter()-t_all:.2f}초 | 출력: {out}')
        logging.info('완료 / 출력폴더=%s',out)
    step(10,'최소 핵심 출력 6개 파일 생성',finalize_outputs)




if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(f'\n[실행 실패] {type(exc).__name__}: {exc}',flush=True)
        raise