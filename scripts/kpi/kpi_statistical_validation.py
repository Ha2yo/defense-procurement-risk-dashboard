# -*- coding: utf-8 -*-
"""
군수품 조달: 2024·2025년 KPI 1 통계적 불확실성 및 모델 간 대응 비교

[배치]  scripts/kpi/kpi_statistical_validation.py
        scripts/kpi/kpi_2024_validation.py  (동일 폴더, 기존 파일 그대로 사용)
[실행]  python scripts/kpi/kpi_statistical_validation.py
        python scripts/kpi/kpi_statistical_validation.py --reps 2000 --seed 2026
        python scripts/kpi/kpi_statistical_validation.py --mapping data/analysis/ornt_code/mapping.csv

[입력]  data/clean/bid_notice_clean_20210101_20251231.csv
        data/clean/bid_result_clean_20210101_20251231.csv
[출력]  data/kpi/statistical_validation/
        01_model_confidence_intervals.csv  연도·모델별 KPI와 95% 신뢰구간
        02_paired_model_differences.csv 같은 공고에서 두 모델의 KPI 차이
        03_statistical_report.md      주요 결과 및 해석 유의사항
        execution.log                 실행 기록

주의:
- 모델은 매 부트스트랩마다 다시 학습하지 않음. 학습 데이터를 고정한 '조건부 검증 불확실성'.
- businessKey별 복원추출로 동일 사업 반복 공고의 의존성을 일부 반영.
- 각 복원추출 표본에서 위험도·근거공고수·공고키 순으로 재정렬하고 상위 20% 재선정.
- p-value를 공식 가설검정으로 산출하지 않음. 신뢰구간의 0 포함 여부는 탐색적 판단.
- 결과 공개일/확정일을 모르면 연도별 학습 데이터의 시점 누수 가능성이 남음.
- 실제 원본 CLEAN과 기존 KPI 출력 파일을 변경하지 않음.
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# 0단계: 분석 고정 기준
# ─────────────────────────────────────────────────────────────────────────────
START_YEAR = 2021
VALIDATION_YEARS = (2024, 2025)
TOP_RATIO = 0.20
MIN_SAMPLE = 10
MODEL_LABELS = ('3변수', '4변수(기간)', '5변수(기간+금액)', '6변수(기간+금액+분기)')
COMPARISONS = [('3변수', m) for m in MODEL_LABELS if m != '3변수']


def log(message=''):
    print(message, flush=True)


def detect_project_root():
    """실행 위치에 관계없이 scripts/kpi의 상위 프로젝트 루트를 찾는다."""
    folder = Path(__file__).resolve().parent
    for candidate in [folder, *folder.parents, Path.cwd(), *Path.cwd().parents]:
        if (candidate / 'data' / 'clean').is_dir() and (candidate / 'scripts' / 'kpi').is_dir():
            return candidate.resolve()
    raise FileNotFoundError('프로젝트 루트를 찾지 못했습니다. --project-root 경로를 입력하세요.')


def resolve_path(root, path):
    path = Path(path).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def locate_input(root, supplied, stem):
    if supplied:
        path = resolve_path(root, supplied)
        if not path.is_file():
            raise FileNotFoundError(f'입력 파일 없음: {path}')
        return path
    # 정확한 파일명 우선; 중복 파일 후보가 있으면 사용자에게 선택을 요청함.
    clean = root / 'data' / 'clean'
    target = clean / f'{stem}.csv'
    if target.is_file():
        return target
    matches = sorted(clean.glob(stem + '*.csv'))
    if len(matches) != 1:
        raise FileNotFoundError(f'{stem}: 파일 후보 {len(matches)}개. --notice/--result로 지정하세요: {matches}')
    return matches[0]


def load_base():
    """기존 KPI 코드와 동일한 모집단·위험도 계산 규칙을 재사용한다."""
    try:
        import kpi_2024_validation as base
    except ImportError as exc:
        raise ImportError('kpi_2024_validation.py를 이 스크립트와 같은 scripts/kpi 폴더에 배치하세요.') from exc
    if not hasattr(base, 'MODELS') or not hasattr(base, 'score_model'):
        raise ValueError('기존 KPI 코드 버전이 맞지 않습니다: MODELS / score_model 확인 필요')
    if tuple(base.MODELS) != MODEL_LABELS:
        raise ValueError(f'기존 KPI 모델 정의가 다릅니다: {list(base.MODELS)}')
    if base.MIN_SAMPLE != MIN_SAMPLE or base.TOP_RATIO != TOP_RATIO:
        raise ValueError('기존 코드의 최소 표본(10), 상위 선정 비율(20%) 설정 불일치')
    return base


def make_dataset(base, notice_path, result_path, mapping_path):
    """공고·결과 CLEAN → 기관 분류 → 공고 단위 이진 결과 판정."""
    notice = base.read_csv_safe(notice_path)
    results = base.read_csv_safe(result_path)
    for frame in (notice, results):
        if 'orntCode' in frame.columns:
            frame['orntCode'] = frame['orntCode'].astype('string').str.strip().str.upper()
    base.validate_inputs(notice, results)
    base.self_tests()
    org = base.make_org_table(notice, mapping_path)
    data = base.make_analysis_table(notice, results, org)
    if len(data) != len(notice) or data['noticeKey'].duplicated().any():
        raise ValueError('병합 후 공고 건수 또는 고유키가 일치하지 않습니다.')
    return data


def score_year(base, data, year):
    """연도 y를 검증할 때 학습은 2021~(y-1)까지만 사용."""
    train = data.loc[data['연도'].ge(START_YEAR) & data['연도'].lt(year) & data['평가가능']].copy()
    test = data.loc[data['연도'].eq(year) & data['평가가능']].copy()
    if len(train) < MIN_SAMPLE or test.empty or int(test['유찰여부'].sum()) == 0:
        raise ValueError(f'{year}: 학습/검증 데이터 또는 유찰 건수 부족')
    if not train['연도'].lt(year).all() or not test['연도'].eq(year).all():
        raise AssertionError('공고일 기준 학습·검증 연도 분리 실패')
    if test['businessKey'].isna().any() or test['noticeKey'].isna().any():
        raise ValueError(f'{year}: businessKey 또는 noticeKey 결측. 군집 검증 불가')
    model_scores = {}
    for model, steps in base.MODELS.items():
        scored = base.score_model(train, test, steps, minimum=MIN_SAMPLE)
        if not scored['noticeKey'].is_unique or not scored['위험도'].between(0, 1).all():
            raise AssertionError(f'{year} {model}: 공고키 또는 위험도 검증 실패')
        cols = ['noticeKey', '위험도', '근거공고수']
        model_scores[model] = scored[cols].set_index('noticeKey')
    key_order = test['noticeKey'].astype(str).sort_values().to_numpy()
    y = test.set_index('noticeKey').loc[key_order, '유찰여부'].to_numpy(dtype=np.int8)
    businesses = test.set_index('noticeKey').loc[key_order, 'businessKey'].astype(str).to_numpy()
    arrays = {}
    for model, df in model_scores.items():
        aligned = df.loc[key_order]
        arrays[model] = (aligned['위험도'].to_numpy(dtype=float),
                         aligned['근거공고수'].to_numpy(dtype=float))
    return len(train), y, businesses, key_order, arrays


def recall_top20(y, risk, evidence, key_rank):
    """같은 위험도에서 근거표본 큰 순 → 공고키 사전순, half-up 상위 20%."""
    n = len(y)
    if n == 0 or y.sum() == 0:
        return np.nan
    k = int(np.floor(n * TOP_RATIO + 0.5))
    order = np.lexsort((key_rank, -evidence, -risk))
    return float(100 * y[order[:k]].sum() / y.sum())


def bootstrap_year(year, base_n, y, businesses, keys, arrays, reps, rng):
    """사업키 군집 복원추출: 1회 복원추출에서 모든 모델을 함께 평가."""
    n = len(y)
    key_rank = np.arange(n, dtype=np.int64)  # keys는 이미 사전순 정렬됨
    observed = {m: recall_top20(y, risk, evidence, key_rank)
                for m, (risk, evidence) in arrays.items()}
    # businessKey가 같은 공고는 동일 군집에 묶음.
    _, inv = np.unique(businesses, return_inverse=True)
    sort_ix = np.argsort(inv, kind='stable')
    sizes = np.bincount(inv)
    offsets = np.r_[0, np.cumsum(sizes)]
    n_clusters = len(sizes)
    draws = {m: np.empty(reps, dtype=float) for m in MODEL_LABELS}
    for rep in range(reps):
        selected_clusters = rng.integers(0, n_clusters, size=n_clusters)
        # 복원추출한 사업의 모든 공고를 복제하고 표본 크기는 매번 달라질 수 있음.
        idx = np.concatenate([sort_ix[offsets[c]:offsets[c + 1]] for c in selected_clusters])
        sampled_y = y[idx]
        sampled_keys = key_rank[idx]
        for model, (risk, evidence) in arrays.items():
            draws[model][rep] = recall_top20(sampled_y, risk[idx], evidence[idx], sampled_keys)
    good = np.logical_and.reduce([np.isfinite(draws[m]) for m in MODEL_LABELS])
    if good.sum() < max(100, int(reps * 0.90)):
        raise ValueError(f'{year}년: 유효 부트스트랩 표본 부족 {good.sum()}/{reps}')
    draws = {m: v[good] for m, v in draws.items()}
    model_rows, comparison_rows = [], []
    for model in MODEL_LABELS:
        lo, hi = np.percentile(draws[model], [2.5, 97.5])
        model_rows.append({
            '검증연도': year, '학습기간': f'2021~{year-1}', 'model': model,
            '학습공고수': base_n, '검증공고수': n, '검증유찰건수': int(y.sum()),
            '사업군집수': n_clusters, '사전식별률_pct': observed[model],
            '95CI_하한_pct': lo, '95CI_상한_pct': hi,
            '유효반복수': int(good.sum()), '재표본추출단위': 'businessKey',
        })
    for a, b in COMPARISONS:
        delta = observed[a] - observed[b]
        samples = draws[a] - draws[b]
        lo, hi = np.percentile(samples, [2.5, 97.5])
        comparison_rows.append({
            '검증연도': year, '비교기준': a, '비교대상': b,
            '기준식별률_pct': observed[a], '대상식별률_pct': observed[b],
            '기준-대상_차이_pp': delta, '95CI_하한_pp': lo, '95CI_상한_pp': hi,
            '95CI_0포함': bool(lo <= 0 <= hi),
            '부트스트랩_차이양수비율': float(np.mean(samples > 0)),
            '유효반복수': int(good.sum()),
        })
    return model_rows, comparison_rows


def save_outputs(out, models, differences, reps, seed, notice_path, result_path, mapping_path):
    """서로 다른 통계표를 억지로 병합하지 않고 2개 CSV와 보고서로 저장."""
    models_df = pd.DataFrame(models)
    differences_df = pd.DataFrame(differences)
    models_df.to_csv(out / '01_model_confidence_intervals.csv', index=False, encoding='utf-8-sig')
    differences_df.to_csv(out / '02_paired_model_differences.csv', index=False, encoding='utf-8-sig')
    lines = [
        '# 2024~2025년 KPI 통계적 신뢰성 검증', '',
        f'- 부트스트랩 반복: {reps:,}회 / 난수 시드: {seed}',
        '- 검증: 2024년(2021~2023년 학습), 2025년(2021~2024년 학습)',
        '- 방법: 동일 사업키 군집 대응 부트스트랩, 매 반복마다 상위 20% 재선정',
        '- 신뢰구간: 분위수(percentile) 95%; 고정된 학습 모델의 검증 표본 불확실성',
        '- 실제 결과 확정일의 시점 가용성은 별도로 검증해야 함',
        '- 이미 선택/비교한 모델에 대한 사후 분석이므로 독립적인 확증 검정으로 해석하지 않음',
        f'- 입찰공고 입력: `{notice_path}`',
        f'- 입찰결과 입력: `{result_path}`',
        f'- 기관 매핑: `{mapping_path if mapping_path else "기존 코드의 잠정 분류"}`',
        '', '## 모델별 Recall@20%와 신뢰구간', '',
        '| 검증연도 | 모델 | Recall@20% | 95% CI |', '|---|---|---:|---:|',
    ]
    for row in models:
        lines.append(f"| {row['검증연도']} | {row['model']} | {row['사전식별률_pct']:.2f}% | "
                     f"{row['95CI_하한_pct']:.2f}~{row['95CI_상한_pct']:.2f}% |")
    lines.extend(['', '## 3변수 대비 성능 차이', '',
                  '| 검증연도 | 비교 | 차이(%p) | 95% CI(%p) | 0 포함 |',
                  '|---|---|---:|---:|---|'])
    for row in differences:
        lines.append(f"| {row['검증연도']} | {row['비교기준']} − {row['비교대상']} | "
                     f"{row['기준-대상_차이_pp']:+.2f} | {row['95CI_하한_pp']:+.2f}~"
                     f"{row['95CI_상한_pp']:+.2f} | {'예' if row['95CI_0포함'] else '아니오'} |")
    lines.extend(['', '## 해석 시 주의', '',
                  '- 신뢰구간이 0을 포함하면 해당 비교에서 우열을 확신하기 어렵습니다.',
                  '- 포함하지 않더라도 모델 탐색·비교를 반복한 사후 분석이라는 한계를 고려해야 합니다.',
                  '- 2024년과 2025년의 학습기간이 다르므로 성능 차이는 연도 효과만을 뜻하지 않습니다.',
                  '- 이 결과는 분기별 유찰률의 95% 신뢰구간과 분석 대상이 다릅니다.',
                  '- McNemar 검정은 단순 상위 20% 선정 일치율을 비교할 뿐, Recall 우위 검정의 대체재가 아닙니다.',
                  ''])
    (out / '03_statistical_report.md').write_text('\n'.join(lines), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description='2024·2025 KPI 대응 군집 부트스트랩 통계 검증')
    parser.add_argument('--project-root', type=Path)
    parser.add_argument('--notice', type=Path)
    parser.add_argument('--result', type=Path)
    parser.add_argument('--mapping', type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--reps', type=int, default=1000, help='부트스트랩 반복 횟수 (권장 2000)')
    parser.add_argument('--seed', type=int, default=2026)
    args = parser.parse_args()
    if args.reps < 200:
        raise ValueError('--reps는 200 이상으로 지정하세요. 권장값은 2000입니다.')
    t0 = time.perf_counter()
    root = resolve_path(Path.cwd(), args.project_root) if args.project_root else detect_project_root()
    notice = locate_input(root, args.notice, 'bid_notice_clean_20210101_20251231')
    result = locate_input(root, args.result, 'bid_result_clean_20210101_20251231')
    mapping = resolve_path(root, args.mapping) if args.mapping else None
    if mapping is not None and not mapping.is_file():
        raise FileNotFoundError(f'기관 매핑 파일 없음: {mapping}')
    out = resolve_path(root, args.output_dir) if args.output_dir else root / 'data' / 'kpi' / 'statistical_validation'
    output_names = ('01_model_confidence_intervals.csv', '02_paired_model_differences.csv',
                    '03_statistical_report.md', 'execution.log')
    if any((out / name).resolve() in {notice, result, mapping} for name in output_names):
        raise ValueError('입력 데이터와 출력 파일의 경로가 겹칩니다.')
    out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=out / 'execution.log', level=logging.INFO, encoding='utf-8',
                        format='%(asctime)s %(levelname)s %(message)s', force=True)
    log(f'[경로] 프로젝트 루트: {root}\n[경로] 출력: {out}')
    log('\n[STEP 01] 기존 KPI 엔진 불러오기 및 CLEAN 모집단 구성')
    base = load_base()
    data = make_dataset(base, notice, result, mapping)
    rng = np.random.default_rng(args.seed)
    all_models, all_differences = [], []
    for year in VALIDATION_YEARS:
        log(f'\n[STEP 02] {year}년: 2021~{year-1} 학습·{year} 검증 위험도 계산')
        train_n, y, businesses, keys, arrays = score_year(base, data, year)
        log(f'  학습 {train_n:,}건 / 검증 {len(y):,}건 / 유찰 {int(y.sum()):,}건')
        log(f'[STEP 03] {year}년: 사업 단위 대응 부트스트랩 {args.reps:,}회')
        models, differences = bootstrap_year(year, train_n, y, businesses, keys, arrays, args.reps, rng)
        all_models.extend(models)
        all_differences.extend(differences)
        for row in models:
            log(f"  {row['model']}: Recall {row['사전식별률_pct']:.2f}% "
                f"(95% CI {row['95CI_하한_pct']:.2f}~{row['95CI_상한_pct']:.2f}%)")
    log('\n[STEP 04] 통계 비교표·보고서 저장')
    save_outputs(out, all_models, all_differences, args.reps, args.seed, notice, result, mapping)
    log(f'\n[완료] 모델별 8행 + 비교 6행 / {time.perf_counter() - t0:.1f}초 / {out}')
    logging.info('완료: %s', out)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        logging.exception('실행 실패')
        log(f'\n[실행 실패] {type(exc).__name__}: {exc}')
        raise
