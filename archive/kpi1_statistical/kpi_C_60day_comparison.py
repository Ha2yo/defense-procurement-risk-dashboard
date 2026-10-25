#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""국방조달 KPI 1 개선안 C: 과거 유찰률 + 과거 60일 재공고율.

원본 CLEAN 파일과 기존 KPI 파일은 변경하지 않는다.
기본 위치: 프로젝트 루트에서 실행하면 data/clean, data/analysis/ornt_code,
data/kpi/c_60day/를 자동 사용. 필요한 경우 --notice --result --org-groups
--outdir로 직접 지정한다.

원칙:
* 공고 단위 정답은 순수 낙찰(0)/유찰(1)만. 순위확정/복합결과/미확인 보류.
* 후속 재공고는 동일 사업·공고번호, 증가한 차수, 비역전 날짜, 재공고 유형.
* 공고번호가 바뀐 동일사업·동일공고명 후보는 보류·별도 CSV (자동 연결 금지).
* 과거 재공고율은 정상·긴급 원공고 중 60일 완전 관측분으로 산출.
* 2023년 검증에서 가중치 선정, 2024년 독립 평가.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

NOTICE_KEY = ['demandYear', 'orntCode', 'dcsNo', 'pblancNo', 'pblancOdr']
BUSINESS = ['demandYear', 'orntCode', 'dcsNo']
FAMILY = BUSINESS + ['pblancNo']
STAGES = [
    ['org_group', 'mthd_group', 'busiDivs'],
    ['org_group', 'busiDivs'],
    ['org_group'],
    [],
]
WEIGHTS = [0, .05, .10, .20, .30, .40, .50, .75, 1.0]
MIN_N = 10
WINDOW_DAYS = 60

OVERRIDE = {'LAZ': '국방부직할/합동', 'UDA': '육군'}
ARMY_NAME = ('육군', '보병사단', '기갑여단', '포병여단', '화력여단', '공병여단', '기동사단', '군수지원여단', '군단')
DAPA_NAME = ('방위사업청', '국방기술품질원', '국방과학연구소')
CODE_GROUP = {'L': '육군', 'H': '공군', 'S': '해군/해병대'}


def classify_org(name: str, code: str) -> str:
    """업로드된 compare_ornt_codeVld4.py의 기관그룹 분류 규칙과 동일."""
    name, code = str(name), str(code)
    if code in OVERRIDE: return OVERRIDE[code]
    if '공군' in name: return '공군'
    if '해군' in name or '해병' in name: return '해군/해병대'
    if any(k in name for k in DAPA_NAME) or code.startswith('E'): return '방위사업청'
    if code[:1] in CODE_GROUP: return CODE_GROUP[code[:1]]
    if any(k in name for k in ARMY_NAME): return '육군'
    return '국방부직할/합동'


def read_clean(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype='string', encoding='utf-8-sig', low_memory=False)
    df.columns = df.columns.str.strip()
    for c in NOTICE_KEY:
        if c not in df.columns:
            raise KeyError(f'{path.name}: 필수 공고키 컬럼 없음: {c}')
        df[c] = df[c].str.strip().str.replace(r'\.0$', '', regex=True)
    return df


def locate_root() -> Path:
    here = Path(__file__).resolve()
    for root in [Path.cwd(), *list(here.parents)[:4]]:
        if (root / 'data' / 'clean' / 'bid_notice_clean_20210101_20251231.csv').exists():
            return root
    return Path.cwd()


def merge_input(notice: pd.DataFrame, result: pd.DataFrame) -> pd.DataFrame:
    if notice.duplicated(NOTICE_KEY).any():
        raise ValueError('입찰공고에서 공고키 중복: 자동 first 삭제 대신 원자료 확인 필요')
    # 세 상태의 존재 여부를 벡터화 집계 (문자열 lambda 3.8만 회 실행 방지).
    result = result.copy()
    result['has_award'] = result.bidResult.eq('낙찰')
    result['has_fail'] = result.bidResult.eq('유찰')
    result['has_rank'] = result.bidResult.eq('순위확정')
    summary = result.groupby(NOTICE_KEY, dropna=False, sort=False).agg(
        has_award=('has_award','max'), has_fail=('has_fail','max'),
        has_rank=('has_rank','max'), result_last_date=('opengDate','max'),
        result_rows=('bidResult','size')).reset_index()
    c = summary[['has_award','has_fail','has_rank']].astype(int)
    only_award = c.has_award.eq(1) & c.has_fail.eq(0) & c.has_rank.eq(0)
    only_fail = c.has_fail.eq(1) & c.has_award.eq(0) & c.has_rank.eq(0)
    only_rank = c.has_rank.eq(1) & c.has_fail.eq(0) & c.has_award.eq(0)
    summary['states'] = np.select([only_award, only_fail, only_rank],
                                  ['낙찰', '유찰', '순위확정'], default='복합/상충결과')
    summary['fail_yn'] = pd.Series(pd.NA, index=summary.index, dtype='Int64')
    summary.loc[summary.states.eq('유찰'), 'fail_yn'] = 1
    summary.loc[summary.states.eq('낙찰'), 'fail_yn'] = 0
    keep = ['bidNm', 'ornt', 'busiDivs', 'cntrctMth', 'pblancDate', 'pblancSe']
    missing = [c for c in keep if c not in notice.columns]
    if missing: raise KeyError('입찰공고 필수 컬럼 없음: ' + str(missing))
    df = notice[NOTICE_KEY + keep].merge(summary, on=NOTICE_KEY, how='left', validate='one_to_one')
    df['pdate'] = pd.to_datetime(df['pblancDate'], format='%Y%m%d', errors='coerce')
    df['result_date'] = pd.to_datetime(df['result_last_date'], format='%Y%m%d', errors='coerce')
    df['year'] = df['pdate'].dt.year
    df['porder'] = pd.to_numeric(df['pblancOdr'], errors='coerce')
    if df[['pdate', 'porder']].isna().any().any():
        raise ValueError('공고일 또는 공고차수에서 형식 오류 확인됨')
    df['mthd_group'] = df['cntrctMth'].astype(str)
    df['noticeKey_local'] = df[NOTICE_KEY].fillna('').agg('||'.join, axis=1)
    return df


def apply_org_group(df: pd.DataFrame, group_path: Path | None):
    if group_path is not None and group_path.exists():
        grp = pd.read_csv(group_path, dtype='string', encoding='utf-8-sig')
        grp = grp[['orntCode', '기관그룹']].drop_duplicates('orntCode')
        df = df.merge(grp.rename(columns={'기관그룹': 'org_group'}), on='orntCode', how='left', validate='many_to_one')
        missing = int(df.org_group.isna().sum())
        df['org_group'] = df['org_group'].fillna('미분류')
        msg = f'프로젝트 기관그룹 기준표 적용 (미분류 {missing:,}행)'
    else:
        mapping = df[['orntCode', 'ornt']].drop_duplicates('orntCode').copy()
        mapping['org_group'] = [classify_org(n, c) for n, c in zip(mapping['ornt'], mapping['orntCode'])]
        df = df.merge(mapping[['orntCode', 'org_group']], on='orntCode', how='left', validate='many_to_one')
        msg = '기준표 부재: compare_ornt_codeVld4.py의 기관명/코드 분류규칙 임시 적용 (품목 분포 검증 불가)'
    return df, msg


def derive_rebid(df: pd.DataFrame):
    """벡터화된 동일번호 재공고 연결 + 변경번호 후속 후보(검토 전용)."""
    data = df.copy()
    source = data[data['pblancSe'].isin(['정상공고', '긴급공고'])].copy()
    data['rebid_60d'] = pd.Series(pd.NA, index=data.index, dtype='Int64')
    data['ambiguous_rebid'] = False

    # 재공고 출처별 공고번호 동일성, 차수 증가, 비역전 날짜와 60일 조건 모두 검증.
    dest = data[data['pblancSe'].eq('재공고')].copy()
    pair = source[FAMILY+['noticeKey_local','pdate','porder','pblancDate']].merge(
        dest[FAMILY+['noticeKey_local','pdate','porder','pblancDate']],
        on=FAMILY, suffixes=('_src', '_dst'))
    days = (pair['pdate_dst'] - pair['pdate_src']).dt.days
    pair = pair[(pair.porder_dst > pair.porder_src) & days.between(0, WINDOW_DAYS)].copy()
    pair['days_to_rebid'] = (pair.pdate_dst - pair.pdate_src).dt.days
    pair = pair.sort_values(['noticeKey_local_src','pdate_dst','porder_dst']).drop_duplicates('noticeKey_local_src')
    strict_links = pair[['noticeKey_local_src','noticeKey_local_dst','pblancDate_src','pblancDate_dst','days_to_rebid']].rename(
        columns={'noticeKey_local_src':'sourceKey','noticeKey_local_dst':'followKey',
                 'pblancDate_src':'source_date','pblancDate_dst':'follow_date'})
    hit = source.noticeKey_local.isin(strict_links.sourceKey)
    data.loc[source.index, 'rebid_60d'] = hit.astype(int).to_numpy()

    # 변경번호는 같은 사업·공고명·60일 후속이라는 '후보'일 뿐 자동 라벨 1 금지.
    nonhit = source.loc[~hit]
    if nonhit.empty:
        changes = pd.DataFrame(columns=['sourceKey','candidateKey','source_date','candidate_date','candidate_type','days_gap','bidNm'])
    else:
        c = nonhit[BUSINESS+['bidNm','pblancNo','noticeKey_local','pdate','pblancDate']].merge(
            data[BUSINESS+['bidNm','pblancNo','noticeKey_local','pdate','pblancDate','pblancSe']],
            on=BUSINESS+['bidNm'], suffixes=('_src','_dst'))
        c['days_gap'] = (c.pdate_dst - c.pdate_src).dt.days
        c = c[(c.pblancNo_src != c.pblancNo_dst) & c.days_gap.between(0,WINDOW_DAYS)].copy()
        changes = c[['noticeKey_local_src','noticeKey_local_dst','pblancDate_src','pblancDate_dst',
                     'pblancSe','days_gap','bidNm']].rename(columns={
            'noticeKey_local_src':'sourceKey','noticeKey_local_dst':'candidateKey',
            'pblancDate_src':'source_date','pblancDate_dst':'candidate_date',
            'pblancSe':'candidate_type'})
        data.loc[data.noticeKey_local.isin(changes.sourceKey),'ambiguous_rebid'] = True
    return data, strict_links, changes


def history(df: pd.DataFrame, start_year: int, end_year: int, cutoff: pd.Timestamp):
    # 수의계약 제외는 원래 KPI와 동일. 취소공고는 확정 정답 라벨 없음.
    sub = df[(df.year >= start_year) & (df.year <= end_year)
             & ~df.cntrctMth.str.contains('수의', na=False)].copy()
    # '결과 발표일'은 CLEAN에 없으므로 '개찰일'이 cutoff 이내인지로 보수적 근사.
    fail = sub[sub.fail_yn.notna() & (sub.result_date <= cutoff)].copy()
    # 최초 공고 유형 60일 완전 관측, 변경번호 후보는 음성에 넣지 않음.
    rebid = sub[sub.pblancSe.isin(['정상공고', '긴급공고'])
                & sub.pdate.le(cutoff - pd.Timedelta(days=WINDOW_DAYS))
                & ~sub.ambiguous_rebid].copy()
    rebid['rebid_60d'] = rebid['rebid_60d'].astype(int)
    return fail, rebid


def make_lookup(hist: pd.DataFrame, target: str) -> list[pd.DataFrame]:
    tables = []
    for cols in STAGES:
        if cols:
            g = (hist.groupby(cols, dropna=False)[target]
                 .agg(['size', 'sum']).rename(columns={'size':'sample_count', 'sum':'positive_count'}).reset_index())
        else:
            g = pd.DataFrame({'sample_count':[len(hist)], 'positive_count':[hist[target].sum()]})
        g['rate'] = g.positive_count / g.sample_count
        tables.append(g)
    return tables


def assign_rate(test: pd.DataFrame, tables: list[pd.DataFrame], prefix: str) -> pd.DataFrame:
    out = test[['noticeKey_local'] + STAGES[0]].copy().reset_index(drop=True)
    out[f'{prefix}_rate'] = np.nan
    out[f'{prefix}_n'] = 0
    out[f'{prefix}_stage'] = 0
    for i, (cols, table) in enumerate(zip(STAGES, tables), 1):
        todo = out[f'{prefix}_rate'].isna()
        if not todo.any(): break
        ref = table[table.sample_count >= MIN_N]
        if ref.empty: continue
        if cols:
            candidates = out.loc[todo, cols].reset_index().merge(
                ref[cols + ['rate', 'sample_count']], on=cols, how='left', validate='many_to_one').set_index('index')
            candidates = candidates[candidates.rate.notna()]
        else:
            candidates = pd.DataFrame(index=out.index[todo])
            candidates['rate'] = float(ref.iloc[0]['rate'])
            candidates['sample_count'] = int(ref.iloc[0]['sample_count'])
        out.loc[candidates.index, f'{prefix}_rate'] = candidates.rate.to_numpy()
        out.loc[candidates.index, f'{prefix}_n'] = candidates.sample_count.to_numpy()
        out.loc[candidates.index, f'{prefix}_stage'] = i
    if out[f'{prefix}_rate'].isna().any():
        raise ValueError(prefix + ': 유효한 위험률 적용 불가 (학습 표본 부족)')
    return out[['noticeKey_local',f'{prefix}_rate',f'{prefix}_n',f'{prefix}_stage']]


def score_eval(test: pd.DataFrame, w: float) -> dict:
    ranked = test.copy()
    ranked['score'] = (1 - w) * ranked.fail_rate + w * ranked.rebid_rate
    ranked = ranked.sort_values(['score', 'fail_n', 'noticeKey_local'],
                                ascending=[False, False, True], kind='mergesort').reset_index(drop=True)
    n, pos = len(ranked), int(ranked.fail_yn.sum())
    if n < 10 or pos == 0:
        raise ValueError('KPI에 필요한 검증 표본 및 유찰 건수 부족')
    top_n = int(round(n * .20))
    top = ranked.iloc[:top_n]
    hit = int(top.fail_yn.sum())
    cut = float(top.score.iloc[-1])
    above = ranked[ranked.score > cut]
    tie = ranked[ranked.score == cut]
    tie_in = top_n-len(above)
    tie_fails, tie_total = int(tie.fail_yn.sum()), len(tie)
    min_fail_in_ties = max(0, tie_in - (tie_total-tie_fails))
    max_fail_in_ties = min(tie_in, tie_fails)
    expected = int(above.fail_yn.sum()) + tie_fails * tie_in / tie_total
    # 기존 KPI2와 비교 가능하게 10개 동일 크기 행 순위 구간 사용 (동일점수 분할 문제 보고).
    asc = ranked.iloc[::-1].reset_index(drop=True)
    asc['decile'] = pd.qcut(asc.index, 10, labels=False) + 1
    deciles = (asc.groupby('decile').agg(count=('fail_yn','size'), actual=('fail_yn','mean'),
                                         risk_min=('score','min'), risk_max=('score','max')).reset_index())
    rho = spearmanr(deciles.decile, deciles.actual).statistic
    # score가 여러 decile에 걸쳤는지 검사
    split_ties = int(asc.groupby('score').decile.nunique().gt(1).sum())
    return dict(w=w, total=n, actual_fails=pos, top_n=top_n, hits=hit,
                kpi1=100*hit/pos, kpi1_expected=100*expected/pos,
                kpi1_fixed20_min=100*(int(above.fail_yn.sum())+min_fail_in_ties)/pos,
                kpi1_fixed20_max=100*(int(above.fail_yn.sum())+max_fail_in_ties)/pos,
                cutoff_tie_n=tie_total, cutoff_tie_selected=tie_in, kpi2_rho=float(rho),
                kpi2_split_ties=split_ties), ranked, deciles


def evaluate_split(df: pd.DataFrame, train_start: int, train_end: int, cutoff: pd.Timestamp, eval_year: int):
    hist_fail, hist_rebid = history(df, train_start, train_end, cutoff)
    test = df[(df.year == eval_year) & df.fail_yn.notna()
              & ~df.cntrctMth.str.contains('수의', na=False)].copy()
    fail_ref, rebid_ref = make_lookup(hist_fail,'fail_yn'), make_lookup(hist_rebid,'rebid_60d')
    if test.empty: raise ValueError(f'{eval_year}년 검증 표본이 없습니다')
    x = test.merge(assign_rate(test, fail_ref, 'fail'), on='noticeKey_local', validate='one_to_one')
    x = x.merge(assign_rate(test, rebid_ref, 'rebid'), on='noticeKey_local', validate='one_to_one')
    return x, {'train_fail':len(hist_fail), 'train_rebid':len(hist_rebid),
               'train_rebid_count':int(hist_rebid.rebid_60d.sum()),
               'test':len(test), 'test_fails':int(test.fail_yn.sum())}


def main():
    root = locate_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--notice', type=Path, default=root/'data/clean/bid_notice_clean_20210101_20251231.csv')
    parser.add_argument('--result', type=Path, default=root/'data/clean/bid_result_clean_20210101_20251231.csv')
    parser.add_argument('--org-groups', type=Path, default=root/'data/analysis/ornt_code/orntCode_codeVld4_all.csv')
    parser.add_argument('--outdir', type=Path, default=root/'data/kpi/c_60day')
    args = parser.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)
    print("입력 로드 시작", flush=True)
    notice, result = read_clean(args.notice), read_clean(args.result)
    print("정답 라벨 구성", flush=True)
    df = merge_input(notice, result)
    df, org_msg = apply_org_group(df, args.org_groups)
    print("60일 재공고 연결", flush=True)
    df, strict_links, candidates = derive_rebid(df)
    print("후속 연결 완료", len(strict_links),len(candidates), flush=True)
    strict_links.to_csv(args.outdir/'strict_rebid_links.csv', index=False, encoding='utf-8-sig')
    candidates.to_csv(args.outdir/'different_notice_number_candidates.csv', index=False, encoding='utf-8-sig')
    df.groupby('states', dropna=False).size().rename('notice_count').to_csv(
        args.outdir/'result_status_summary.csv', encoding='utf-8-sig')
    print("2023년 검증 준비", flush=True)
    val, val_counts = evaluate_split(df, 2021, 2022, pd.Timestamp('2022-12-31'), 2023)
    print("2023년 완료",val_counts,flush=True)
    final, final_counts = evaluate_split(df, 2021, 2023, pd.Timestamp('2023-12-31'), 2024)
    print("2024년 완료",final_counts,flush=True)
    tuning = []
    for w in WEIGHTS:
        m, _, _ = score_eval(val, w)
        tuning.append({k:v for k,v in m.items() if k not in ['cutoff_tie_n','cutoff_tie_selected']})
    tune = pd.DataFrame(tuning)
    # 동점 성능은 가장 단순한(작은) 가중치를 선택. 2024 결과는 선택에 관여하지 않음.
    chosen = float(tune.sort_values(['kpi1', 'w'], ascending=[False, True]).iloc[0].w)
    baseline, base_ranked, base_deciles = score_eval(final, 0)
    improved, imp_ranked, imp_deciles = score_eval(final, chosen)
    reports = pd.DataFrame([{'model':'baseline_fail_only', **baseline},
                            {'model':'C_with_60d_rebid', **improved}])
    tune.to_csv(args.outdir/'weight_tuning_2023.csv', index=False, encoding='utf-8-sig')
    reports.to_csv(args.outdir/'kpi_comparison_2024.csv', index=False, encoding='utf-8-sig')
    final[['noticeKey_local', 'pblancDate', 'ornt', 'pblancSe', 'bidNm', 'states', 'fail_yn',
           'org_group','mthd_group','busiDivs','fail_rate','fail_n','fail_stage',
           'rebid_rate','rebid_n','rebid_stage']].to_csv(args.outdir/'eval_scores_2024.csv',index=False,encoding='utf-8-sig')
    base_deciles.to_csv(args.outdir/'baseline_deciles_2024.csv',index=False,encoding='utf-8-sig')
    imp_deciles.to_csv(args.outdir/'improved_deciles_2024.csv',index=False,encoding='utf-8-sig')
    print('====== KPI C · 60일 재공고 이력 결합 검증 ======')
    print('기관그룹:', org_msg)
    print('후속 재공고 연결:',len(strict_links),'건 | 다른 공고번호 후보:',len(candidates),'쌍 (자동 연결하지 않음)')
    print('2023년 검증:',val_counts)
    print('2024년 독립평가:',final_counts)
    print('가중치 후보(2023년 유찰 KPI1):')
    print(tune[['w','kpi1','kpi2_rho']].to_string(index=False,float_format=lambda x:f'{x:.4f}'))
    print('선정 w:',chosen)
    print('2024년 KPI 비교:')
    print(reports[['model','total','actual_fails','top_n','hits','kpi1','kpi1_expected',
                   'kpi1_fixed20_min','kpi1_fixed20_max','kpi2_rho','kpi2_split_ties']].to_string(index=False,float_format=lambda x:f'{x:.4f}'))
    print('KPI1 목표: 35% 이상 (팀 보고서 기준). KPI2 목표: Spearman ≥ 0.85.')
    print('주의: 기준표 없는 기관그룹은 임시 분류. KPI2는 기존 등인원 10분위로 비교하며 동점 분할 있음.')
    print('저장 폴더:',args.outdir.resolve())

if __name__ == '__main__':
    main()
