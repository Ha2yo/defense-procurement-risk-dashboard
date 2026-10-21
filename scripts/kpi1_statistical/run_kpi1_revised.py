#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""통계 기반 유찰 KPI 1 + 동점 순위 실험 (2023 선택, 2024·2025 검증).

필수 입력: bid_notice_clean_20210101_20251231.csv,
           bid_result_clean_20210101_20251231.csv

방법: 1) 기관그룹·계약방법·사업구분 과거 유찰률
      2) 기관코드 -> 기관코드·공고구분 -> 기관코드·공고구분·금액대 과거 유찰률로 보정
      3) 같은 위험점수만 제출기간 구간 유찰률 -> 세부금액대 유찰률 순으로 정렬

별도의 ML 라이브러리 불필요. 입력 CSV/기존 코드는 읽기만 함.
주의: opengDate는 결과 공개/확정일이 아닌 개찰일의 근사치.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import json
import numpy as np
import pandas as pd

import kpi1_statistics_stepwise as step
import kpi1_statistics_hierarchy as hierarchy
import kpi1_tiebreak_statistics as tiebreak

FILE_NOTICE = 'bid_notice_clean_20210101_20251231.csv'
FILE_RESULT = 'bid_result_clean_20210101_20251231.csv'
YEAR_RANGE = (2023, 2024, 2025)
BASELINE = '기존_공고키'
# 기존 분석에서 2023년 검증으로 결정한 설계를 고정한다.
HIERARCHY = 'agency_notice_amount'
MINIMUM = 5
SMOOTHING = 10


def find_inputs(arg: Path | None) -> Path:
    here = Path(__file__).resolve().parent
    candidates = ([arg] if arg is not None else [here, here/'data'/'clean',
                    here.parent, here.parent/'data'/'clean',
                    Path.cwd(), Path.cwd()/'data'/'clean'])
    for candidate in candidates:
        if candidate is None:
            continue
        directory = Path(candidate).expanduser().resolve()
        if (directory / FILE_NOTICE).is_file() and (directory / FILE_RESULT).is_file():
            return directory
    message = '\n'.join('  ' + str(p) for p in candidates if p is not None)
    raise FileNotFoundError(
        'CLEAN CSV 2개를 같은 폴더에서 찾을 수 없습니다.\n'
        f'필요한 파일: {FILE_NOTICE}, {FILE_RESULT}\n'
        f'확인한 경로:\n{message}\n'
        '예: python run_kpi1_revised.py --input-dir "C:\\project\\data\\clean"')


def make_scores(tr: pd.DataFrame, te: pd.DataFrame):
    base = step.lookup(tr, te, step.BASE_STAGES, minimum=10, shrink=0)
    risk = hierarchy.adjust(tr, te, base, hierarchy.DESIGNS[HIERARCHY],
                            min_n=MINIMUM, shrink=SMOOTHING)
    extras = tiebreak.score_components(tr, te)
    return base, risk, extras


def rank_positions(order: np.ndarray) -> np.ndarray:
    ranks = np.empty(len(order), dtype=int)
    ranks[order] = np.arange(1, len(order) + 1)
    return ranks


def evaluate_year(year, tr, te, result_dir: Path, selected_method: str | None):
    y = te.fail_yn.to_numpy(dtype=int)
    keys = te.noticeKey_local.astype(str).to_numpy()
    original_risk, risk, extras = make_scores(tr, te)
    orders = {method: tiebreak.order_for(risk, keys, extras, method)
              for method in tiebreak.METHODS}
    outcomes = {method: tiebreak.calc(y, risk, orders[method])
                for method in tiebreak.METHODS}
    if year == 2023:
        # 기존 실험의 선정 규칙 재현: 2023 KPI1 최대, 동일하면 KPI2 최대.
        # 주의: 2023에서는 경계 동점의 두 공고가 모두 낙찰이어서
        #       KPI1 자체로는 후보를 판별할 수 없다.
        candidates = [m for m in tiebreak.METHODS if m != BASELINE]
        selected_method = sorted(candidates, key=lambda m:
            (-outcomes[m]['kpi1_pct'], -outcomes[m]['kpi2_spearman'], m))[0]
    assert selected_method in orders
    summary_rows = []
    for method, numbers in outcomes.items():
        summary_rows.append({'year': year, 'method': method,
                             'selected_method': selected_method, **numbers})

    selected_order = orders[selected_method]
    old_order = orders[BASELINE]
    cut_n = round(len(te)*.20)
    cutoff = risk[selected_order[cut_n-1]]
    boundary_idx = np.flatnonzero(risk == cutoff)
    old_ranks = rank_positions(old_order)
    new_ranks = rank_positions(selected_order)

    # 공고별 위험도 및 정렬 결과. 실제 유찰 라벨은 오직 사후 평가에만 사용.
    score_cols = ['noticeKey_local', 'pblancDate', 'orntCode', 'ornt',
                  'cntrctMth','busiDivs','pblancSe','excutTy','bsicExpt',
                  'amount_band','bid_days', 'fail_yn']
    report = te[score_cols].copy().reset_index(drop=True)
    report['baseline_risk'] = original_risk
    report['main_risk'] = risk
    report['past_period_rate'] = extras['period_rate']
    report['past_fine_amount_rate'] = extras['fine_rate']
    report['previous_rank'] = old_ranks
    report['revised_rank'] = new_ranks
    report['previous_top20'] = old_ranks <= cut_n
    report['revised_top20'] = new_ranks <= cut_n
    report.sort_values('revised_rank').to_csv(
        result_dir / f'{year}_ranked_scores.csv', index=False, encoding='utf-8-sig')
    report.loc[boundary_idx].sort_values('revised_rank').to_csv(
        result_dir / f'{year}_boundary_ties.csv', index=False, encoding='utf-8-sig')

    pre, post = outcomes[BASELINE], outcomes[selected_method]
    print(f'[{year}] 표본 {len(te):,}, 유찰 {int(y.sum()):,}, 상위 20% {cut_n:,}', flush=True)
    print(f'  동점 {post["boundary_tied_n"]:,}건 / 제외 {post["boundary_excluded_n"]:,}건', flush=True)
    print(f'  기존: 유찰 {pre["captured_failures"]:,}건 / KPI1 {pre["kpi1_pct"]:.4f}% / KPI2 {pre["kpi2_spearman"]:.4f}', flush=True)
    print(f'  개선: 유찰 {post["captured_failures"]:,}건 / KPI1 {post["kpi1_pct"]:.4f}% / KPI2 {post["kpi2_spearman"]:.4f}', flush=True)
    return summary_rows, selected_method, {'year': year, 'n': len(te), 'failed': int(y.sum()),
        'top20': cut_n, 'boundary_tied': post['boundary_tied_n'],
        'boundary_excluded': post['boundary_excluded_n'],
        'baseline_hits': pre['captured_failures'], 'revised_hits': post['captured_failures'],
        'baseline_kpi1': pre['kpi1_pct'], 'revised_kpi1': post['kpi1_pct'],
        'baseline_kpi2': pre['kpi2_spearman'], 'revised_kpi2': post['kpi2_spearman']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path,
                        default=Path(__file__).resolve().parents[2] / 'data' / 'clean',
    help='CLEAN CSV 2개가 들어 있는 폴더')
    parser.add_argument('--outdir', type=Path,
                       default=Path(__file__).resolve().parents[2] / 'data' / 'kpi1_statistics',
                       help='새로운 결과 CSV/보고서를 저장할 폴더')
    args = parser.parse_args()
    src = find_inputs(args.input_dir)
    out = args.outdir.expanduser().resolve()
    # 결과를 원천 데이터와 같은 폴더에 직접 덮어쓰지 않도록 분리한다.
    out.mkdir(parents=True, exist_ok=True)
    orig_find_clean = step.find_clean
    try:
        # 원본 단계적 통계 코드의 파일 탐색 함수에 명시된 경로만 전달한다.
        step.find_clean = lambda filename: src / filename
        print('CLEAN 경로:', src, flush=True)
        print('출력 경로:', out, flush=True)
        print('사용 모델:', HIERARCHY, 'min_n=', MINIMUM, 'shrink=', SMOOTHING, flush=True)
        data = tiebreak.enrich(step.prepare(step.load_data()))
        metrics = []
        selected = None
        summaries = []
        for year in YEAR_RANGE:
            tr, te = step.split(data, year)
            if len(te) < 10 or int(te.fail_yn.sum()) == 0:
                raise ValueError(f'{year}년 검증 공고 또는 유찰 건수가 부족합니다.')
            rows, selected, mini = evaluate_year(year, tr, te, out, selected)
            metrics.extend(rows)
            summaries.append(mini)
            if year == 2023:
                print('2023년 선정 동점 규칙:', selected, flush=True)

        pd.DataFrame(metrics).to_csv(out/'all_tiebreak_methods.csv', index=False, encoding='utf-8-sig')
        pd.DataFrame(summaries).to_csv(out/'KPI_summary.csv', index=False, encoding='utf-8-sig')
        settings = {'baseline':'org_group × cntrctMth × busiDivs',
            'model':HIERARCHY, 'hierarchy_tiers':hierarchy.DESIGNS[HIERARCHY],
            'hierarchy_min_group_n':MINIMUM, 'hierarchy_smoothing':SMOOTHING,
            'tie_rule':selected, 'tie_candidates':tiebreak.METHODS,
            'tiebreak_group_min_n':10, 'tiebreak_smoothing':20,
            'rule_selected_by':'2023 KPI1 → KPI2; 2024/2025 rules frozen',
            'data_source':str(src), 'caveat':'opengDate는 결과 확정일이 아닌 개찰일 대용',
            'scope':'낙찰/유찰 확정, 수의계약 제외, 순위확정/미확인 제외'}
        (out/'model_settings.json').write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf-8')

        r24 = next(d for d in summaries if d['year']==2024)
        r25 = next(d for d in summaries if d['year']==2025)
        content = f'''# 통계 기반 KPI1 + 동점 처리 실행 보고서

- 입력: {src}
- 선택 기준: 2023년 KPI1 우선, 동률이면 KPI2 우선
- 2023년 선정 규칙: `{selected}`
- 1차 통계 위험도: `{HIERARCHY}`, 집단 최소표본 {MINIMUM}, 보정강도 {SMOOTHING}
- 동점 보조 통계: 최소표본 10, 과거 유찰률 보정강도 20

| 연도 | 검증 공고 | 실제 유찰 | 상위 20% | 기존 포착 | 변경 포착 | 기존 KPI1 | 변경 KPI1 | 기존 KPI2 | 변경 KPI2 | 경계 동점 | 경계 제외 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
'''
        for d in summaries:
            content += (f"| {d['year']} | {d['n']:,} | {d['failed']:,} | {d['top20']:,} | "
                f"{d['baseline_hits']} | {d['revised_hits']} | {d['baseline_kpi1']:.2f}% | "
                f"{d['revised_kpi1']:.2f}% | {d['baseline_kpi2']:.4f} | {d['revised_kpi2']:.4f} | "
                f"{d['boundary_tied']} | {d['boundary_excluded']} |\n")
        content += '''\n**해석 시 주의**

1. 동점 처리는 1차 위험도 점수가 정확히 같은 공고의 **순서만** 바꾼다. 2024년에는 경계 점수 동점이 모두 상위 20%에 들어가 유찰 포착 개선이 불가능하다.
2. 2023년 경계 동점은 2건 모두 낙찰로, KPI1로 동점 규칙의 우열을 판별할 수 없다. 2023년 KPI2는 동일 점수를 다른 분위로 쪼개는 방식이므로 선정이 불안정할 수 있다.
3. 2025년 KPI1은 후향적 검증의 결과이며, 다음 연도에서 일반화된 개선을 보장하지 않는다.
4. 현재 기관그룹은 코드·기관명 기반의 임시 분류이며, 프로젝트 공식 기준표와 다를 수 있다.
5. 결과 확인일 대신 `opengDate`(개찰일)를 근사치로 사용한다. 결과 확정일 부재에 따른 미래 정보 누출 위험을 완전히 제거하지 못했다.
6. 모델은 결과가 확정된 공고만 평가한다. 순위확정·결과 미확인 공고의 잠재적 편향에 유의한다.
'''
        (out/'실행결과_보고서.md').write_text(content, encoding='utf-8')
        print('\n완료:', out/'KPI_summary.csv', '\n', out/'실행결과_보고서.md', flush=True)
        if r24['revised_hits'] != r24['baseline_hits']:
            print('알림: 2024년 동점 처리로 유찰 포착이 달라졌습니다. 입력 데이터/분류를 확인하세요.',flush=True)
    finally:
        step.find_clean = orig_find_clean

if __name__ == '__main__':
    try:
        main()
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print('\n[실행 오류]', exc, file=sys.stderr)
        sys.exit(1)
