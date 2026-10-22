#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""순차보정모델-1(SC-1): 공고기간 정의 통일 B-2의 프로젝트 통합판."""
# 팀원용 읽기 순서: run_model(전체 흐름) → load_master(자료 준비) → score(위험도) → evaluate(KPI).
# 배치 위치: scripts/sc1/sc1_core.py. run_sc1.py도 같은 scripts/sc1 폴더에 둡니다.
# 이 모듈을 직접 실행해도 평가가 시작되지 않습니다. scripts/sc1/run_sc1.py를 실행하세요.
# 학습한 신경망이 아니라 과거 유찰률 조회·표본수 보정으로 위험도를 만드는 통계 모델입니다.
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
KEYS = ['demandYear', 'orntCode', 'dcsNo', 'pblancNo', 'pblancOdr']
SCHEMA = 'rank_only_excluded_v1'

# 기본 위험도: 가장 구체적인 조합부터 찾고 표본 10건 미만이면 다음 조합으로 대체합니다.
# 마지막 빈 리스트 []는 학습 전체 유찰률을 쓰는 최종 대체 단계입니다.
BASE_TIERS = [['org_group', 'cntrctMth', 'busiDivs'], ['org_group', 'busiDivs'], ['org_group'], []]
# 순차보정: 기본 위험도를 만든 다음 아래 3개 조합을 차례로 적용합니다.
# 기본 위험도의 단계별 대체와 달리, 한 단계에서 보정해도 다음 단계 보정을 계속합니다.
B_TIERS = [['orntCode'], ['orntCode', 'pblancSe'], ['orntCode', 'pblancSe', 'amount_band']]
# 금액 단위는 원입니다. pd.cut의 기본 right=True이므로 각 경계값은 아래 구간에 포함됩니다.
# 예: 정확히 30,000,000원은 3천만 이하, 100,000,000원은 3천만~1억입니다.
AMOUNT_BINS = [-.1, 3e7, 1e8, 3e8, 1e9, np.inf]
AMOUNT_LABELS = ['3천만 이하', '3천만~1억', '1억~3억', '3억~10억', '10억 초과']
# 세부 금액 구간은 주 위험도를 다시 보정하지 않고 동점 정렬용 보조 점수에만 사용합니다.
FINE_BINS = [-1, 1e6, 3e6, 1e7, 2e7, 3e7, 5e7, 1e8, 3e8, 1e9, np.inf]
FINE_LABELS = ['100만 이하', '100만~300만', '300만~1천만', '1천만~2천만', '2천만~3천만',
               '3천만~5천만', '5천만~1억', '1억~3억', '3억~10억', '10억 초과']
# 공고기간 구간도 보조 점수 전용입니다. 60일 초과 라벨의 실제 상한은 180일입니다.
# 180일 초과는 구간 밖이므로 기간미상으로 처리하며, 공고 자체는 남깁니다.
PERIOD_BINS = [-1, 3, 5, 7, 10, 14, 21, 30, 60, 180]
PERIOD_LABELS = ['3일 이하', '3~5일', '5~7일', '7~10일', '10~14일', '14~21일', '21~30일', '30~60일', '60일 초과']


def notice_period_days(publication_dates, registration_deadlines):
    """문서 기준: 참가등록마감 날짜 - 공고 날짜. 시각은 사용하지 않는다.

    날짜 해석 실패·음수 기간은 결측으로 보존하며 공고 자체는 삭제하지 않는다.
    """
    # 숫자만 남긴 앞 8자리(YYYYMMDD)를 날짜로 읽어 마감 시각의 영향을 제거합니다.
    # 예: 공고 20240101, 마감 202401041800 → 3일. 72시간 여부를 계산하는 방식이 아닙니다.
    def dates(series):
        digits = series.astype('string').str.replace(r'\D', '', regex=True).str[:8]
        return pd.to_datetime(digits, format='%Y%m%d', errors='coerce')
    days = (dates(registration_deadlines) - dates(publication_dates)).dt.days
    return days.where(days.ge(0))


# [1. 자료 준비] 새 전처리 스키마·공고키·정답을 검사하고 기관그룹과 파생변수를 붙입니다.
# 반환값은 수의계약 제외 후의 전체 연도 자료입니다. 학습/평가 분리는 run_model에서 합니다.
def load_master(master_path, mapping_path):
    d = pd.read_csv(master_path, dtype='string', encoding='utf-8-sig', low_memory=False)
    # 옛 마스터를 잘못 읽어 순위확정 처리나 기간 정의가 달라지는 것을 막는 필수 열 검사입니다.
    required = set(KEYS + ['noticeKey_local', 'notice_key_document', 'master_schema', 'fail_yn',
                          'pblancDate', 'result_last_date', 'result_source_order', 'cntrctMth',
                          'busiDivs', 'pblancSe', 'bsicExpt', 'bidPartcptRegistClosDt',
                          'has_award', 'has_fail', 'has_rank', 'bidNm', 'ornt'])
    if required - set(d):
        raise ValueError(f'새 merge_big_data.py로 만든 마스터가 필요합니다. 누락: {sorted(required-set(d))}')
    if d.empty or not d.master_schema.eq(SCHEMA).all() or d.noticeKey_local.duplicated().any():
        raise ValueError('마스터 버전·공고키 중복·빈 파일 여부를 확인하세요.')
    # CSV 문자열 True/False를 실제 불리언으로 복원하고 정답과 일치하는지 확인합니다.
    # has_rank=True 자체는 오류가 아닙니다. 낙찰/유찰과 함께 존재하는 경우는 허용합니다.
    for c in ['has_award', 'has_fail', 'has_rank']:
        d[c] = d[c].str.lower().map({'true': True, 'false': False, '1': True, '0': False})
        if d[c].isna().any():
            raise ValueError(f'{c}의 불리언 값이 올바르지 않습니다.')
    d['fail_yn'] = pd.to_numeric(d.fail_yn, errors='raise')
    if not d.fail_yn.isin([0, 1]).all():
        raise ValueError('마스터 fail_yn은 결측 없는 0/1이어야 합니다.')
    if (d.has_award & d.has_fail).any() or (~d.has_award & ~d.has_fail).any():
        raise ValueError('판단보류 또는 순위확정만인 공고가 마스터에 포함돼 있습니다.')
    if not np.array_equal(d.has_fail.to_numpy(dtype=int), d.fail_yn.to_numpy(dtype=int)):
        raise ValueError('결과 집계 플래그와 fail_yn이 일치하지 않습니다.')
    # 기관코드 1개에 기관그룹 1개만 연결되어야 공고 행이 중복 증가하지 않습니다.
    # 기준표에 없는 코드는 미분류로 남기며 settings.json에 공고 수를 기록합니다.
    org = pd.read_csv(mapping_path, dtype='string', encoding='utf-8-sig')
    if not {'orntCode', '기관그룹'}.issubset(org) or org.orntCode.duplicated().any():
        raise ValueError('기관 기준표는 기관코드별 1행, orntCode·기관그룹 열이 필요합니다.')
    d = d.drop(columns=['org_group'], errors='ignore').merge(
        org[['orntCode', '기관그룹']].rename(columns={'기관그룹': 'org_group'}),
        on='orntCode', how='left', validate='many_to_one')
    d['org_group'] = d.org_group.fillna('미분류')
    # 공통 분석 대상: 계약방법 문자열에 수의가 들어가는 공고를 제외합니다.
    # 해당 공고는 원본 마스터에 남아 있으므로 전처리 건수와 평가 건수가 다를 수 있습니다.
    d = d[~d.cntrctMth.str.contains('수의', na=False)].copy()
    # year는 수요연도(demandYear)가 아닌 공고일의 연도입니다.
    # result_date를 해석할 수 없으면 이후 학습 마감일 조건을 충족하지 못합니다.
    d['pdate'] = pd.to_datetime(d.pblancDate, format='%Y%m%d', errors='coerce')
    d['result_date'] = pd.to_datetime(d.result_last_date, format='%Y%m%d', errors='coerce')
    if d.pdate.isna().any():
        raise ValueError('공고일 형식이 올바르지 않습니다.')
    d['year'] = d.pdate.dt.year
    d['result_source_order'] = pd.to_numeric(d.result_source_order, errors='raise')
    d['notice_period_days'] = notice_period_days(d.pblancDate, d.bidPartcptRegistClosDt)
    d['bid_days'] = d.notice_period_days  # 기존 출력 열 호환. 의미는 이제 문서의 공고기간이다.
    # 금액의 결측·비숫자·음수는 금액미상으로 보존합니다. 공고를 삭제하지 않습니다.
    amount = pd.to_numeric(d.bsicExpt, errors='coerce').where(lambda v: v >= 0)
    # 원값으로 직접 구간화한다. log1p -> expm1의 경계 반올림 오류 방지.
    d['amount_band'] = pd.cut(amount, AMOUNT_BINS, labels=AMOUNT_LABELS).astype('string').fillna('금액미상')
    d['fine_amount_band'] = pd.cut(amount, FINE_BINS, labels=FINE_LABELS).astype('string').fillna('금액미상')
    d['fine_period_band'] = pd.cut(d.bid_days, PERIOD_BINS, labels=PERIOD_LABELS).astype('string').fillna('기간미상')
    for c in ['orntCode', 'pblancSe', 'cntrctMth', 'busiDivs']:
        d[c] = d[c].fillna('미상')
    # 아래 왕복 변환은 진단 건수 기록에만 사용합니다. 실제 금액 구간은 위의 원값 기준입니다.
    d.attrs['amount_roundtrip_band_difference_n'] = int((
        pd.cut(amount, AMOUNT_BINS, labels=False).fillna(-1) !=
        pd.cut(np.expm1(np.log1p(amount)), AMOUNT_BINS, labels=False).fillna(-1)).sum())
    return d


# [공통 조회 함수] 각 평가 공고에 표본수가 충분한 첫 단계의 과거 유찰률을 부여합니다.
# train만으로 집계하고 test에서는 조합값만 읽습니다. 평가 정답은 점수 계산에 사용하지 않습니다.
# shrink=0이면 단순 유찰률, shrink>0이면 학습 전체 유찰률 쪽으로 완화한 보조 점수입니다.
# 반환 배열 순서는 모두 test의 현재 행 순서와 같습니다: 위험도, 참조 표본수, 적용 단계.
def lookup(train, test, tiers, minimum=10, shrink=0):
    # prior는 전체 학습 유찰률입니다. 보조 점수의 완화 기준 및 마지막 대체값입니다.
    prior = float(train.fail_yn.mean())
    risk = np.full(len(test), np.nan)
    sample = np.full(len(test), np.nan)
    stages = np.zeros(len(test), dtype=int)
    for stage, cols in enumerate(tiers, 1):
        # 이미 위험도가 정해진 공고는 이후 단계에서 다시 덮어쓰지 않습니다.
        need = np.isnan(risk)
        if not need.any():
            break
        if not cols:
            risk[need], sample[need], stages[need] = prior, len(train), stage
            break
        g = train.groupby(cols, dropna=False).fail_yn.agg(['sum', 'count']).reset_index()
        g = g[g['count'] >= minimum].copy()
        # (유찰 수 + 가상 표본수×전체 유찰률) / (실제 표본수 + 가상 표본수).
        # shrink는 학습된 계수가 아니라 미리 정한 완화 강도입니다.
        g['rate'] = (g['sum'] + shrink * prior) / (g['count'] + shrink)
        m = test.loc[need, cols].reset_index(drop=True).merge(g, on=cols, how='left', validate='many_to_one')
        # 병합으로 생긴 임시 인덱스를 그대로 쓰지 않고 원래 test의 위치로 복원합니다.
        positions = np.flatnonzero(need)
        risk[positions], sample[positions] = m.rate.to_numpy(float), m['count'].to_numpy(float)
        stages[positions[m.rate.notna().to_numpy()]] = stage
    if not np.isfinite(risk).all():
        raise ValueError('위험도 계산에 실패했습니다.')
    return risk, sample, stages


# [2. 위험도 산정] 기본 위험도 → 3단계 순차보정 → 동점용 보조 점수 → 전체 순위입니다.
# detail은 중간 점수·표본수를 CSV에 남겨 팀원이 최종 위험도의 근거를 추적하게 합니다.
def score(train, test, model):
    risk, sample, stage = lookup(train, test, BASE_TIERS)
    detail = {'base_risk': risk.copy(), 'base_sample_n': sample, 'base_stage': stage}
    keys = test.noticeKey_local.astype(str).to_numpy()
    if model == 'sc1':
        # 보정 조합의 학습 표본이 5건 이상일 때만 적용합니다.
        # 표본이 부족하면 이전 위험도를 유지하고 다음 보정 단계는 계속 시도합니다.
        for num, cols in enumerate(B_TIERS, 1):
            g = train.groupby(cols, dropna=False).fail_yn.agg(['sum', 'count']).reset_index()
            g = g[g['count'] >= 5]
            m = test[cols].reset_index(drop=True).merge(g, on=cols, how='left', validate='many_to_one')
            n = m['count'].fillna(0).to_numpy(float)
            failures = m['sum'].fillna(0).to_numpy(float)
            # 새 위험도 = (해당 조합의 유찰 수 + 10×직전 위험도)/(해당 조합 표본수 + 10).
            # 앞 단계 결과가 다음 단계의 사전 위험도로 들어가므로 적용 순서는 의미가 있습니다.
            risk = np.where(m['count'].notna().to_numpy(), (failures + 10 * risk) / (n + 10), risk)
            detail[f'adjustment_{num}_n'] = n
            detail[f'adjustment_{num}_risk'] = risk.copy()
        # 보조 점수는 기간/세부금액 조합별 과거 유찰률을 최소 10건, 완화 강도 20으로 조회합니다.
        # 구체 조합이 부족하면 기관그룹 조합 → 구간 전체 → 학습 전체 순으로 대체합니다.
        period_tiers = [['orntCode', 'pblancSe', 'fine_period_band'],
                        ['org_group', 'pblancSe', 'fine_period_band'], ['fine_period_band'], []]
        amount_tiers = [['orntCode', 'pblancSe', 'fine_amount_band'],
                        ['org_group', 'pblancSe', 'fine_amount_band'], ['fine_amount_band'], []]
        period, _, _ = lookup(train, test, period_tiers, minimum=10, shrink=20)
        fine, _, _ = lookup(train, test, amount_tiers, minimum=10, shrink=20)
        detail['period_rate'], detail['fine_amount_rate'] = period, fine
        # np.lexsort는 마지막 인자가 최우선입니다. - 부호는 높은 점수가 먼저 오게 합니다.
        # 최종 순서: 주 위험도↓ → 기간 점수↓ → 세부금액 점수↓ → 전체 공고키↑.
        # 보조 점수는 주 위험도에 더하지 않으며, 주 위험도가 같을 때만 순서에 영향을 줍니다.
        order = np.lexsort((keys, -fine, -period, -risk))
    else:
        raise ValueError('모델은 sc1이어야 합니다.')
    return risk, order, detail


# [3. 평가] 여기서 처음 평가 정답을 사용해 선별 성과와 10분위 단조성을 계산합니다.
# risk/order는 앞 단계에서 계산된 값을 그대로 받고, 결과를 보고 순위를 조정하지 않습니다.
def evaluate(test, risk, order):
    y = test.fail_yn.to_numpy(dtype=int)
    # 고위험군 수는 전체 평가 공고의 20%를 Python round로 반올림한 정수입니다.
    # 정확히 .5인 경우 가장 가까운 짝수로 반올림하는 Python 규칙을 따릅니다.
    k = round(len(test) * .2)
    if len(test) < 10 or k < 1 or y.sum() == 0:
        raise ValueError('평가 공고 10건 이상 및 유찰 공고가 필요합니다.')
    selected = np.zeros(len(test), dtype=bool)
    selected[order[:k]] = True
    # 전체 순위를 뒤집어 낮은 위험도부터 10개의 거의 같은 크기 집단으로 나눕니다.
    # 점수 자체의 분위수가 아닌 정렬 위치로 나누므로 같은 위험도도 여러 분위에 걸칠 수 있습니다.
    asc = order[::-1]
    decile_ids = pd.qcut(pd.Series(np.arange(len(test))), 10, labels=False) + 1
    dec = pd.DataFrame({'decile': decile_ids, 'fail_yn': y[asc], 'risk': risk[asc]}).groupby('decile').agg(
        n=('fail_yn', 'size'), failed=('fail_yn', 'sum'), mean_risk=('risk', 'mean'),
        risk_min=('risk', 'min'), risk_max=('risk', 'max'))
    dec['actual_failure_pct'] = (100 * dec.failed / dec.n).round(2)
    # KPI2는 분위 번호(1=저위험, 10=고위험)와 실제 유찰률(소수 2자리 %)의 Spearman 상관입니다.
    rho = float(spearmanr(dec.index, dec.actual_failure_pct).statistic)
    cutoff = risk[order[k-1]]
    # 경계 동점은 주 위험도만 같은 집합입니다. 보조 점수까지 같은 집합과 구분합니다.
    boundary = risk == cutoff
    # KPI1(포착률)은 전체 유찰 중 고위험군에서 찾은 비율입니다.
    # precision은 고위험군 내부의 실제 유찰 비율이므로 분모가 KPI1과 다릅니다.
    met = {'evaluation_n': len(test), 'all_failed': int(y.sum()), 'top20_n': k,
           'captured_failures': int(y[selected].sum()), 'kpi1_pct': 100 * y[selected].sum() / y.sum(),
           'precision_pct': 100 * y[selected].mean(), 'kpi2_spearman': rho,
           'boundary_tied_n': int(boundary.sum()), 'boundary_selected_n': int((boundary & selected).sum()),
           'boundary_excluded_n': int((boundary & ~selected).sum()), 'cut_score': float(cutoff)}
    return met, dec, selected, boundary


# [전체 실행] 같은 2021~2023년 학습 집합을 고정해 2024·2025년을 각각 평가합니다.
# 2025년 평가에 2024년 정답을 새로 학습시키는 방식이 아닙니다.
def run_model(model, master_path, mapping_path, outdir):
    # 기본 경로는 같은 폴더의 run_sc1.py가 프로젝트 루트 기준으로 전달합니다.
    # 이 모듈의 위치를 기준으로 data 경로를 다시 계산하지 않습니다.
    # 기본 입력: 프로젝트/data/kpi/bid_master_2021_2025.csv
    # 기본 출력: 프로젝트/data/sc1/ (코드가 있는 scripts/sc1/와 구분)
    master_path, mapping_path, outdir = map(Path, (master_path, mapping_path, outdir))
    d = load_master(master_path, mapping_path)
    # 공고연도가 2021~2023이고 최대 개찰일이 2023-12-31 이하인 공고만 학습합니다.
    # 이 시점 제한은 사전 예측 원칙을 구현한 선택이며, 실제 결과 공개일을 확인한 것은 아닙니다.
    train = d[d.year.between(2021, 2023) & d.result_date.le(pd.Timestamp('2023-12-31'))].copy()
    if train.empty:
        raise ValueError('2021~2023년 고정 학습 자료가 없습니다.')
    outdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for year in (2024, 2025):
        test = d[d.year.eq(year)].copy().reset_index(drop=True)
        # 학습·평가 공고키가 겹치면 누출 가능성이 있으므로 실행을 중단합니다.
        if set(train.noticeKey_local) & set(test.noticeKey_local):
            raise ValueError('학습과 평가 공고가 겹칩니다.')
        risk, order, detail = score(train, test, model)
        met, dec, selected, boundary = evaluate(test, risk, order)
        row = {'model': 'SC-1', 'year': year,
               'training_n': len(train), 'training_failed': int(train.fail_yn.sum()), **met}
        if model == 'sc1':
            # 최종 포함 공고와 주 위험도·기간 점수·금액 점수까지 같은 경계 집합입니다.
            # 이 집합 안에서는 마지막 공고키 정렬이 포함 여부를 결정합니다.
            idx = order[met['top20_n']-1]
            tied = boundary & (detail['period_rate'] == detail['period_rate'][idx]) & (
                detail['fine_amount_rate'] == detail['fine_amount_rate'][idx])
            row.update(final_aux_tied_n=int(tied.sum()), final_aux_selected_n=int((tied & selected).sum()),
                       final_aux_excluded_n=int((tied & ~selected).sum()))
        rows.append(row)
        # order는 정렬된 원본 행 위치, ranks는 각 원본 행의 최종 등수(1부터)입니다.
        ranks = np.empty(len(test), dtype=int)
        ranks[order] = np.arange(1, len(test)+1)
        # 공고 속성·중간 점수·최종 위험도·순위·선별 여부를 함께 저장해 개별 공고를 설명합니다.
        keep = ['noticeKey_local', 'notice_key_document', 'bidNm', 'pblancDate', 'orntCode', 'ornt',
                'org_group', 'cntrctMth', 'busiDivs', 'pblancSe', 'bsicExpt', 'amount_band',
                'bidPartcptRegistClosDt', 'notice_period_days', 'bid_days', 'fine_period_band', 'bidResult', 'fail_yn']
        tab = test[keep].copy()
        for col, values in detail.items():
            tab[col] = values
        tab['risk'], tab['rank'], tab['top20'], tab['boundary_tie'] = risk, ranks, selected, boundary
        tab.sort_values('rank').to_csv(outdir / f'{model}_{year}_scores.csv', index=False, encoding='utf-8-sig')
        tab[boundary].sort_values('rank').to_csv(outdir / f'{model}_{year}_boundary.csv', index=False, encoding='utf-8-sig')
        dec.to_csv(outdir / f'{model}_{year}_deciles.csv', encoding='utf-8-sig')
    summary = pd.DataFrame(rows)
    summary.to_csv(outdir / f'{model}_summary.csv', index=False, encoding='utf-8-sig')
    # 설정값·입력 해시·결측 진단·해석 한계를 남깁니다. 같은 결과의 재현 여부를 확인할 때 사용합니다.
    # 이미 확인한 2025년 결과에 대한 후향적 평가이므로 미관측 미래 성능으로 단정하지 않습니다.
    settings = {'model': model, 'schema': SCHEMA, 'pipeline_revision': 'sc1_registration_period_20261022',
        'training': '2021-2023; opengDate<=2023-12-31; frozen',
        'evaluation': [2024, 2025], 'base_min_n': 10, 'base_tiers': BASE_TIERS,
        'correction_tiers': B_TIERS if model == 'sc1' else None, 'correction_min_n': 5, 'correction_shrink': 10,
        'aux_min_n': 10, 'aux_shrink': 20, 'amount_banding': 'raw amount; right-closed intervals',
        'notice_period': {'start': 'pblancDate', 'end': 'bidPartcptRegistClosDt',
                          'unit': 'calendar-date difference in integer days; time ignored',
                          'negative_or_invalid': 'missing, notice preserved',
                          'period_bins': PERIOD_BINS, 'period_labels': PERIOD_LABELS,
                          'outside_bins': '기간미상; notice preserved',
                          'used_by': 'SC-1 auxiliary tie score only'},
        'amount_roundtrip_band_difference_n': d.attrs['amount_roundtrip_band_difference_n'],
        'unmapped_eligible_notices': int(d.org_group.eq('미분류').sum()),
        'missing_result_date_eligible_notices': int(d.result_date.isna().sum()),
        'sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (master_path, mapping_path)},
        'limitations': ['결과가 관측된 공고만 평가', '개찰일은 결과 가용 시점의 대용값',
                       '후향적 평가이며 이미 확인한 2025년 결과 사용', 'KPI2는 동점 내 순서의 영향 가능']}
    (outdir / f'{model}_settings.json').write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
    print(summary[['model', 'year', 'training_n', 'evaluation_n', 'captured_failures', 'kpi1_pct', 'kpi2_spearman']].to_string(index=False), flush=True)
    return summary


