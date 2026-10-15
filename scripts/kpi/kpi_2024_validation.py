# -*- coding: utf-8 -*-
"""
군수품 조달: CLEAN 데이터로 유찰 위험도·2024년 KPI를 처음부터 재현하는 스크립트

[설치]  python -m pip install pandas numpy
[실행]  python kpi_full_reproduction.py --data-dir "C:/Users/user/Downloads/clean"
        # 입력 파일을 스크립트와 같은 폴더에 두었다면: python kpi_full_reproduction.py

[입력]  bid_notice_clean_20210101_20251231(2).csv
        bid_result_clean_20210101_20251231(2).csv
        (선택) 직접 검토한 기관별 매핑표: --mapping 기관매핑.csv
               매핑표는 orntCode,기관그룹 열을 포함해야 함.
[출력]  output_kpi/ 폴더 아래 기관 분류 감사표, 검증대상, 모델별 위험도,
        모델비교, 동점분석, 불일치 검증표, 실행 요약 텍스트 등이 저장됩니다.

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
MODELS = {'3변수': STEPS_3, '4변수(기간)': STEPS_4, '5변수(기간+금액)': STEPS_5}
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
    assigned = org.apply(lambda row: classify_org(row['orntCode'], row['ornt']), axis=1)
    org[['기관그룹', '분류근거']] = pd.DataFrame(assigned.tolist(), index=org.index)
    if mapping_path:
        manual = read_csv_safe(mapping_path)
        required = {'orntCode', '기관그룹'}
        if not required.issubset(manual.columns):
            raise ValueError(f'매핑 파일에 필수 열 {required}가 없습니다: {list(manual.columns)}')
        manual = manual[['orntCode', '기관그룹']].dropna(subset=['orntCode']).copy()
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
    a = notice.merge(org, on=['orntCode','ornt'], how='left', validate='many_to_one')
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
def score_model(train, test, steps):
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
        ok = matched['근거공고수'].ge(MIN_SAMPLE)
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
    assert (scored['근거공고수'] >= MIN_SAMPLE).all(), '근거 표본수 부족'
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


def save_csv(df, dest):
    df.to_csv(dest,index=False,encoding='utf-8-sig')


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
    log('  [테스트 통과] 혼재 결과, 10건 기준, 입력 결측 완화, 반올림, 결과 판정')


def save_csv(df: pd.DataFrame, dest: Path) -> None:
    """원본 CLEAN에 쓰지 않고 새 출력폴더에 UTF-8-SIG 저장."""
    dest.parent.mkdir(parents=True,exist_ok=True)
    df.to_csv(dest,index=False,encoding='utf-8-sig')


def main() -> None:
    """전체 분석 진입점. 실패 시 메시지와 로그에 단계·원인을 남긴다."""
    project_root=Path('/Users/chaseungmin/Desktop/defense-procurement-risk-dashboard')
    parser=argparse.ArgumentParser(description='국방 조달: 2024 KPI 및 차이 원인 독립 재검증')
    parser.add_argument('--project-root',type=Path,default=project_root)
    parser.add_argument('--notice',type=Path,default=None,help='공고 CLEAN 전체 CSV 경로')
    parser.add_argument('--result',type=Path,default=None,help='결과 CLEAN 전체 CSV 경로')
    parser.add_argument('--mapping',type=Path,default=None,help='선택: 확인 완료된 기관코드-기관그룹 CSV')
    parser.add_argument('--output-dir',type=Path,default=None)
    args=parser.parse_args()
    clean=args.project_root/'data'/'clean'
    notice_path=args.notice or clean/'bid_notice_clean_20210101_20251231.csv'
    result_path=args.result or clean/'bid_result_clean_20210101_20251231.csv'
    out=args.output_dir or args.project_root/'data'/'processed'/'kpi_validation'
    out.mkdir(parents=True,exist_ok=True)
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
        log(f'  공고 {len(n):,}행 / 결과 {len(r):,}행')
        return n,r
    notice,results=step(1,'CLEAN 파일 로드',load)
    def audit():
        report=validate_inputs(notice,results)
        save_csv(report,out/'01_data_quality_report.csv')
        self_tests()
        return report
    step(2,'스키마·키 품질검증 및 단위 테스트',audit)
    def group():
        org=make_org_table(notice,args.mapping)
        save_csv(org,out/'02_institution_group_audit.csv')
        save_csv(org.loc[org['기관그룹'].eq('미분류')],out/'03_unclassified_institutions.csv')
        log('  '+str(org.groupby('기관그룹')['orntCode'].nunique().to_dict()))
        conflicts=org.groupby('orntCode')['기관그룹'].nunique()
        if conflicts.gt(1).any():
            save_csv(org.loc[org['orntCode'].isin(conflicts.index[conflicts.gt(1)])],
                     out/'03_institution_code_conflicts.csv')
            log('  [경고] 기관코드에 그룹 충돌 있음. 충돌 감사표 확인 필요')
        return org
    org=step(3,'기관명 기반 예외 및 잠정 기관 분류',group)
    def join():
        a=make_analysis_table(notice,results,org)
        if len(a)!=len(notice):raise ValueError('공고·결과 연결 후 행 증가 발생')
        save_csv(a[['noticeKey','businessKey','연도','결과상태','cntrctMth','제외사유','평가가능']],
                 out/'14_exclusion_audit.csv')
        status=a.groupby(['연도','결과상태','제외사유'],dropna=False).size().reset_index(name='공고건수')
        save_csv(status,out/'04_result_status_summary.csv')
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
        save_csv(test,out/'05_2024_validation_population.csv')
        log(f'  학습: {len(train):,}건, 검증: {len(test):,}건, 실제 유찰: {int(test.유찰여부.sum()):,}건')
        log(f'  기존 문서 모집단 차이: {len(test)-6813:+,}건 / 유찰 차이: {int(test.유찰여부.sum())-1521:+,}건')
        return train,test
    train,test=step(5,'2021~2023 학습 및 2024 검증 모집단 확정',population)
    def scoring():
        results_summary=[];ties=[];fallback=[]
        filenames={'3변수':'06_model_3vars_risk.csv','4변수(기간)':'07_model_4vars_risk.csv',
                   '5변수(기간+금액)':'08_model_5vars_risk.csv'}
        for model,steps in MODELS.items():
            scored=score_model(train,test,steps)
            ranked,metric,tie=evaluate(scored)
            if not ranked['위험도'].between(0,1).all():raise ValueError('위험도 범위 벗어남')
            if metric['상위20pct공고수']!=int(np.floor(len(test)*TOP_RATIO+0.5)):
                raise ValueError('상위 20% 선정 수 불일치')
            if not ranked['noticeKey'].is_unique:raise ValueError('검증 공고키 중복')
            save_csv(ranked,out/filenames[model])
            result={'model':model,**metric,
                    '표본부족대체율_pct':round(100*ranked['완화사유'].eq('표본 부족').mean(),4),
                    '결측오류대체율_pct':round(100*ranked['완화사유'].eq('입력 결측·오류').mean(),4)}
            results_summary.append(result)
            ties.append({'model':model,**tie})
            for (stage,why),count in ranked.groupby(['적용단계','완화사유'],dropna=False).size().items():
                fallback.append({'model':model,'적용단계':stage,'완화사유':why,'건수':count})
            log(f'  {model}: 포착 {metric["포착유찰"]:,}/{metric["전체유찰"]:,}건 = {metric["사전식별률_pct"]:.4f}%')
        summary=pd.DataFrame(results_summary)
        save_csv(summary,out/'09_model_comparison.csv')
        save_csv(pd.DataFrame(fallback),out/'10_fallback_diagnostics.csv')
        save_csv(pd.DataFrame(ties),out/'11_tie_boundary_analysis.csv')
        return summary
    summary=step(6,'모델별 단계적 위험도 산정 및 KPI 평가',scoring)
    def final():
        known_train=train.loc[train['기관그룹'].ne('미분류')].copy()
        known_test=test.loc[test['기관그룹'].ne('미분류')].copy()
        known,km,_=evaluate(score_model(known_train,known_test,STEPS_5))
        full=summary.loc[summary['model'].eq('5변수(기간+금액)')].iloc[0]
        save_csv(pd.DataFrame([{'구분':'미분류 유지(잠정)','검증공고':len(test),
                                '전체유찰':int(test.유찰여부.sum()),'사전식별률_pct':full['사전식별률_pct']},
                               {'구분':'미분류 제외','검증공고':len(known_test),
                                '전체유찰':int(known_test.유찰여부.sum()),'사전식별률_pct':km['사전식별률_pct']}]),
                 out/'12_unclassified_sensitivity.csv')
        reported=full['사전식별률_pct']
        comp=pd.DataFrame([{'문서':k,'문서_식별률_pct':v,'재계산_식별률_pct':reported,
                            '차이_pp':round(reported-v,4),'원인확정여부':'미확정: 기존 산출코드/매핑 비교 필요'}
                           for k,v in DOC_VALUES.items()])
        save_csv(comp,out/'13_document_discrepancy.csv')
        log('\n===== 최종 요약 =====\n'+summary.to_string(index=False))
        log('\n[주의] 문서 6,813/1,521과 불일치할 경우, 원인 확인 없이 KPI 확정 금지.')
        log('[주의] 기관코드 접두 기반 그룹 배정은 잠정 추정이며 감사표 검토 필요.')
        log(f'총 소요시간: {time.perf_counter()-t_all:.2f}초 | 출력: {out}')
        logging.info('완료 / 출력폴더=%s',out)
    step(7,'기관 미분류 민감도 및 문서 수치 차이 저장',final)


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(f'\n[실행 실패] {type(exc).__name__}: {exc}',flush=True)
        raise
