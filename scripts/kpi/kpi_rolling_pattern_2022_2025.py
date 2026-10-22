# -*- coding: utf-8 -*-
"""군수품 조달: 과거 누적 학습 → 다음 연도 검증(2022~2025)과 모델 일관성 평가.

[배치] scripts/kpi/kpi_rolling_pattern_2022_2025.py
       scripts/kpi/kpi_2024_validation.py (기존 코드, 수정/실행하지 않음)
[실행] python scripts/kpi/kpi_rolling_pattern_2022_2025.py
       python scripts/kpi/kpi_rolling_pattern_2022_2025.py --project-root "C:/workspace/defense-procurement-risk-dashboard"
[입력] data/clean 입찰공고/입찰결과 CLEAN (읽기 전용)
[출력] data/kpi/rolling_validation/ 아래 핵심 4개 파일
       01_yearly_model_results.csv   4개 검증연도 × 4개 조합 (16행)
       02_pattern_summary.csv        조합별 4개년 성능·안정성 요약 (4행)
       03_pattern_report.md          보고서용 연도별 비교·해석
       04_recall_trend.png            3~6변수 연도별 Recall@20% 선그래프
       execution.log                 실행 단계 및 오류

※ 기존 kpi_2024_validation.py의 결과 판정·기관 분류·위험도 백오프·KPI1 계산을 재사용.
※ 2021→2022, 2021~22→2023, 2021~23→2024, 2021~24→2025.
※ 검증연도 정답은 위험도 계산·선정 순서에 사용하지 않음.
※ 공고일로 연도를 구분함. 결과 확정일이 없으므로 연초 가용성 누수 위험은 남음.
※ 연도별 학습 표본이 누적되고 평가 모집단도 달라지므로 성능 변화=시장 변화로 단정 금지.
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # 화면 없이도 Windows/서버에서 PNG 저장
import matplotlib.pyplot as plt

try:
    import kpi_2024_validation as base
except ImportError as exc:
    raise SystemExit('kpi_2024_validation.py와 본 파일을 scripts/kpi/ 같은 폴더에 두세요.') from exc

YEARS = (2022, 2023, 2024, 2025)
START_YEAR = 2021
MIN_SAMPLE = 10
TOP_RATIO = 0.20


def log(message=''):
    print(message, flush=True)


def save_csv(frame: pd.DataFrame, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding='utf-8-sig')


def root_path(args):
    if args.project_root is not None:
        return args.project_root.expanduser().resolve()
    return base.detect_project_root()


def resolve(path: Path | None, root: Path, default: Path) -> Path:
    if path is None:
        return default.resolve()
    return base.resolve_project_path(path, root)


def spearman_no_scipy(x, y):
    """Spearman = 평균 동점순위의 Pearson 상관. scipy 없이 재현 가능."""
    xx = pd.Series(x, dtype='float64').rank(method='average')
    yy = pd.Series(y, dtype='float64').rank(method='average')
    if len(xx) < 2 or xx.nunique() <= 1 or yy.nunique() <= 1:
        return np.nan
    return float(np.corrcoef(xx.to_numpy(), yy.to_numpy())[0, 1])


def kpi2_without_scipy(ranked):
    """기존 코드와 같은 공고 순서·10분위 경계 사용; scipy 의존성만 제거."""
    df = ranked.sort_values(['위험도', '근거공고수', 'noticeKey'],
                            ascending=[True, True, True], kind='stable').reset_index(drop=True)
    n = len(df)
    if n < 10:
        return np.nan, 0
    df = df.copy()
    df['분위'] = np.floor(np.arange(n) * 10 / n).astype(int) + 1
    grouped = df.groupby('분위', sort=True)['유찰여부'].mean().reset_index()
    rho = spearman_no_scipy(grouped['분위'], grouped['유찰여부'])
    boundaries = int(sum(
        df.at[i, '분위'] != df.at[i - 1, '분위']
        and df.at[i, '위험도'] == df.at[i - 1, '위험도']
        for i in range(1, n)
    ))
    return rho, boundaries


def make_population(root, notice_path, result_path, mapping):
    # ─────────────────────────────────────────────────────────────────────────
    # 1단계: 원본 CLEAN 검증, 공고단위 결과판정, 기관그룹 일관성 검사
    # ─────────────────────────────────────────────────────────────────────────
    for label, p in [('입찰공고', notice_path), ('입찰결과', result_path)]:
        if not p.is_file():
            raise FileNotFoundError(f'{label} 파일이 없습니다: {p}')
    notice = base.read_csv_safe(notice_path)
    results = base.read_csv_safe(result_path)
    for frame in (notice, results):
        if 'orntCode' in frame:
            frame['orntCode'] = frame['orntCode'].astype('string').str.strip().str.upper()
    base.validate_inputs(notice, results)
    base.self_tests()
    org = base.make_org_table(notice, mapping)
    data = base.make_analysis_table(notice, results, org)
    if len(data) != len(notice) or data['noticeKey'].duplicated().any():
        raise ValueError('공고 병합 후 행 수 또는 공고키 고유성 불일치')
    return data


def evaluate_one(train, test, model, stages, year):
    # ─────────────────────────────────────────────────────────────────────────
    # 2단계: 동일 모델·최소 10건·상위 20% 선정으로 매년 평가
    # ─────────────────────────────────────────────────────────────────────────
    scored = base.score_model(train, test, stages, minimum=MIN_SAMPLE)
    ranked, metric, tie = base.evaluate(scored)
    rho, decile_ties = kpi2_without_scipy(ranked)
    n = int(metric['검증공고수'])
    caught = int(metric['포착유찰'])
    failures = int(metric['전체유찰'])
    top_n = int(metric['상위20pct공고수'])
    exact = int(ranked['적용단계'].eq(1).sum())
    sparse = int(ranked['완화사유'].eq('표본 부족').sum())
    invalid = int(ranked['완화사유'].eq('입력 결측·오류').sum())
    if exact + sparse + invalid != n:
        raise AssertionError(f'{year}년 {model}: 대체 사유 합계 불일치')
    if not ranked['noticeKey'].is_unique or not ranked['위험도'].between(0, 1).all():
        raise AssertionError(f'{year}년 {model}: 공고키·위험도 검사 실패')
    return {
        '검증연도': year, '학습시작연도': START_YEAR, '학습종료연도': year - 1,
        '학습공고수': len(train), '학습유찰수': int(train['유찰여부'].sum()),
        'model': model, '검증공고수': n, '전체유찰': failures,
        '검증유찰률_pct': 100 * failures / n,
        '상위20pct공고수': top_n, '포착유찰': caught,
        '사전식별률_pct': float(metric['사전식별률_pct']),
        'Precision_at20_pct': 100 * caught / top_n if top_n else np.nan,
        'Lift_at20': (caught / failures) / (top_n / n) if failures and top_n else np.nan,
        'KPI2_spearman': rho, '동점분위경계수': decile_ties,
        '정확조합건수': exact, '표본부족대체건수': sparse, '결측오류대체건수': invalid,
        '표본부족대체율_pct': 100 * sparse / n,
        '결측오류대체율_pct': 100 * invalid / n,
        '전체대체율_pct': 100 * (sparse + invalid) / n,
        '근거공고수중앙값': float(ranked['근거공고수'].median()),
        '경계위험도': tie['경계위험도'],
        '경계동점전체': tie['경계동점전체'],
        '경계동점선정': tie['경계동점선정'],
        '동점전부제외_식별률': tie['동점전부제외_식별률'],
        '동점전부포함_식별률': tie['동점전부포함_식별률'],
    }


def summarize_pattern(yearly):
    # ─────────────────────────────────────────────────────────────────────────
    # 3단계: 각 연도 내 모델 순위와 모델별 4개년 성능 변화
    # ─────────────────────────────────────────────────────────────────────────
    x = yearly.copy()
    # 점수 동률이면 같은 순위; 우수 모델 건수도 동률 포함.
    x['연도내_식별률순위'] = x.groupby('검증연도')['사전식별률_pct'].rank(
        ascending=False, method='min').astype(int)
    rows = []
    for model, g in x.groupby('model', sort=False):
        g = g.sort_values('검증연도')
        rates = g['사전식별률_pct'].to_numpy(dtype=float)
        fallback = g['표본부족대체율_pct'].to_numpy(dtype=float)
        rows.append({
            'model': model, '검증연도수': len(g),
            '평균식별률_pct': np.mean(rates), '식별률표준편차_pp': np.std(rates, ddof=0),
            '최저식별률_pct': np.min(rates), '최고식별률_pct': np.max(rates),
            '식별률범위_pp': np.ptp(rates), '첫해대비마지막해_pp': rates[-1] - rates[0],
            '1위연도수_동률포함': int(g['연도내_식별률순위'].eq(1).sum()),
            '평균표본부족대체율_pct': np.mean(fallback),
            '연도별증감방향': ', '.join('증가' if delta > 0 else '감소' if delta < 0 else '동일'
                               for delta in np.diff(rates)),
        })
    result = pd.DataFrame(rows)
    return x, result


# ─────────────────────────────────────────────────────────────────────────────
# 4단계: 연도별 사전 식별률(Recall@20%) 변화 선그래프
# ─────────────────────────────────────────────────────────────────────────────
def save_recall_trend_chart(yearly: pd.DataFrame, dest: Path) -> None:
    """기존 순차 검증 결과(검증연도 × model)로 PNG를 생성한다.

    - 순위·점수 재계산 없이 실제 CSV에 사용한 사전식별률_pct만 시각화.
    - 정확히 하나의 연도×모델이 존재하는지 검사해 잘못된 그래프를 방지.
    - 한국어 글꼴은 실행 PC에 존재하는 폰트 중 자동 선택.
    """
    required = {'검증연도', 'model', '사전식별률_pct'}
    missing = required - set(yearly.columns)
    if missing:
        raise ValueError(f'그래프 필수 컬럼 누락: {sorted(missing)}')
    if yearly.duplicated(['검증연도', 'model']).any():
        raise ValueError('검증연도×모델 조합이 중복되어 선그래프를 그릴 수 없습니다.')

    model_names = {
        '3변수': '3변수',
        '4변수(기간)': '4변수',
        '5변수(기간+금액)': '5변수',
        '6변수(기간+금액+분기)': '6변수',
    }
    data = yearly.copy()
    data['검증연도'] = pd.to_numeric(data['검증연도'], errors='raise').astype(int)
    data['사전식별률_pct'] = pd.to_numeric(data['사전식별률_pct'], errors='coerce')
    pivot = data.pivot(index='검증연도', columns='model', values='사전식별률_pct').sort_index()
    missing_models = set(model_names) - set(pivot.columns)
    if missing_models:
        raise ValueError(f'그래프에 필요한 모델 결과 누락: {sorted(missing_models)}')
    if pivot.loc[:, list(model_names)].isna().any().any():
        raise ValueError('그래프의 연도별 모델 식별률에 결측값이 있습니다.')
    if (pivot.loc[:, list(model_names)] < 0).any().any() or (pivot.loc[:, list(model_names)] > 100).any().any():
        raise ValueError('사전 식별률 값은 0~100% 이내여야 합니다.')

    from matplotlib import font_manager
    available_fonts = {f.name for f in font_manager.fontManager.ttflist}
    korean_fonts = ('Malgun Gothic', 'AppleGothic', 'NanumGothic', 'Noto Sans CJK KR', 'Noto Sans KR')
    font = next((name for name in korean_fonts if name in available_fonts), None)
    if font:
        plt.rcParams['font.family'] = font
        title, xlabel, ylabel = '연도별 사전 식별률 (Recall@20%)', '검증연도', '사전 식별률 (%)'
    else:
        # 한글 폰트가 없는 환경에서도 네모 상자나 글꼴 경고 없이 차트를 저장한다.
        title, xlabel, ylabel = 'Yearly Recall@20%', 'Validation year', 'Recall@20% (%)'
    plt.rcParams['axes.unicode_minus'] = False

    fig, ax = plt.subplots(figsize=(10, 6))
    try:
        for model, label in model_names.items():
            ax.plot(pivot.index.to_numpy(), pivot[model].to_numpy(),
                    marker='o', linewidth=2, markersize=6,
                    label=label if font else model.replace('변수', ' variables').split('(')[0])
        values = pivot[list(model_names)].to_numpy(dtype=float)
        ymin = min(20.0, float(np.floor(values.min() / 5) * 5))
        ymax = max(40.0, float(np.ceil(values.max() / 5) * 5))
        if ymin == ymax:
            ymax = ymin + 5
        ax.set_ylim(ymin, ymax)
        ax.set_yticks(np.arange(ymin, ymax + 0.1, 5))
        ax.set_xticks(pivot.index.to_numpy())
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(alpha=0.3)
        ax.legend(title='Model' if not font else '모델')
        fig.tight_layout()
        dest.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(dest, dpi=200, bbox_inches='tight')
    finally:
        plt.close(fig)


def report_text(yearly, patterns):
    def fmt(v):
        return f'{v:.2f}' if pd.notna(v) else 'N/A'
    y = yearly.pivot(index='검증연도', columns='model', values='사전식별률_pct')
    names = list(base.MODELS)
    lines = [
        '# 군수품 조달 유찰 KPI 연도별 순차 검증 및 패턴 안정성', '',
        '## 검증 설계', '',
        '| 학습 기간 | 검증 기간 |', '|---|---|',
    ]
    for yr in YEARS:
        lines.append(f'| 2021~{yr-1} | {yr} |')
    lines += [
        '', '공고일 기준 연도 분리, 결과확정 경쟁성 공고만 사용, 동일 기관그룹·조합 정의,',
        '최소 10건 조건 완화, 위험도↓ → 근거공고수↓ → 공고키↑, 상위 20% 반올림(half-up).',
        '', '## 1. 연도별 Recall@20% 비교', '',
        '| 검증연도 | 3변수 | 4변수 | 5변수 | 6변수 | 연도 최고 조합 |',
        '|---|---:|---:|---:|---:|---|',
    ]
    for yr in YEARS:
        v = y.loc[yr]
        best = max(v.values)
        winners = ' / '.join(name for name in names if np.isclose(v[name], best, rtol=0, atol=1e-10))
        lines.append(f'| {yr} | ' + ' | '.join(f'{fmt(v[name])}%' for name in names) + f' | {winners} |')
    lines += ['', '## 2. 모델별 안정성 요약', '',
              '| 모델 | 평균 식별률 | 최저 | 최고 | 연도간 범위 | 1위 연도수 | 평균 대체율 |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for _, r in patterns.iterrows():
        lines.append(f"| {r['model']} | {fmt(r['평균식별률_pct'])}% | "
                     f"{fmt(r['최저식별률_pct'])}% | {fmt(r['최고식별률_pct'])}% | "
                     f"{fmt(r['식별률범위_pp'])}%p | {int(r['1위연도수_동률포함'])} | "
                     f"{fmt(r['평균표본부족대체율_pct'])}% |")
    lines += ['', '## 3. 해석상 주의', '',
              '- 매년 검증 모집단·유찰 기저율·학습 공고 수가 다르므로 직접 비교한 성능 변화를 시장 변화나 모델 열화로 단정하지 않는다.',
              '- 변수 추가가 매 연도 KPI를 높이는지, 1위 모델이 매년 동일한지, 최저 연도와 연도간 범위가 어떤지 함께 판단한다.',
              '- 4개년 평균은 각 연도를 동일 가중한 단순 평균이다. 모집단 크기로 가중한 종합 성과가 아니다.',
              '- 각 검증연도 정답은 해당 연도 점수 산정·정렬에 사용되지 않는다.',
              '- 공고일로 학습 연도를 제한했지만 실제 결과 확정·공개일의 부재로 인한 시점 누수 위험은 남아 있다.',
              '- 공고기간은 공고일~참가등록마감일로 계산되고, 금액 구간은 탐색적인 경계이므로 법정 기준으로 해석하지 않는다.',
              '- 기관그룹 분류의 잠정성, 수의계약·미확인 결과 제외에 따른 선택 편향도 고려한다.',
              '', '## 결과 파일', '',
              '- `01_yearly_model_results.csv`: 연도별 4개 조합의 KPI 1·2, 대체율, 경계 동점 및 근거 표본.',
              '- `02_pattern_summary.csv`: 각 모델의 4개년 평균·최저·최고·변동폭·1위 횟수.',
              '- `03_pattern_report.md`: 본 보고서.',
              '- `04_recall_trend.png`: 3~6변수 연도별 Recall@20% 선그래프.',
              '', '## 연도별 성능 추이', '', '![연도별 사전 식별률](04_recall_trend.png)',
              '', '- `execution.log`: 실행 로그.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='2021~2024 누적학습 / 2022~2025 순차 검증')
    parser.add_argument('--project-root', type=Path, default=None)
    parser.add_argument('--notice', type=Path, default=None)
    parser.add_argument('--result', type=Path, default=None)
    parser.add_argument('--mapping', type=Path, default=None)
    parser.add_argument('--output-dir', type=Path, default=None)
    args = parser.parse_args()
    root = root_path(args)
    clean = root / 'data' / 'clean'
    out = resolve(args.output_dir, root, root / 'data' / 'kpi' / 'rolling_validation')
    notice = resolve(args.notice, root, clean / 'bid_notice_clean_20210101_20251231.csv')
    result = resolve(args.result, root, clean / 'bid_result_clean_20210101_20251231.csv')
    mapping = resolve(args.mapping, root, root) if args.mapping else None
    inputs = {p.resolve() for p in (notice, result)}
    if mapping is not None:
        inputs.add(mapping)
    outputs = [out / n for n in ('01_yearly_model_results.csv', '02_pattern_summary.csv',
                                 '03_pattern_report.md', '04_recall_trend.png', 'execution.log')]
    if any(p.resolve() in inputs for p in outputs):
        raise ValueError('출력 파일 경로와 입력 파일 경로가 충돌합니다.')
    out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=out / 'execution.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s',
                        encoding='utf-8', force=True)
    start = time.perf_counter()
    log(f'[경로] 프로젝트 루트: {root}')
    log(f'[경로] 결과 저장: {out}')
    log('[STEP 01] CLEAN 및 기관 분류 검증')
    data = make_population(root, notice, result, mapping)
    log(f'  전체 {len(data):,}건 / 결과확정 경쟁성 {int(data["평가가능"].sum()):,}건')
    rows = []
    for year in YEARS:
        # ─────────────────────────────────────────────────────────────────────
        # 4단계: 과거 누적 학습. 검증연도 결과는 train에 들어가지 않음.
        # ─────────────────────────────────────────────────────────────────────
        train = data.loc[data['연도'].ge(START_YEAR) & data['연도'].lt(year) & data['평가가능']].copy()
        test = data.loc[data['연도'].eq(year) & data['평가가능']].copy()
        if len(train) < MIN_SAMPLE or test.empty or not int(test['유찰여부'].sum()):
            raise ValueError(f'{year}년 검증 불가: 학습 {len(train)}, 평가 {len(test)}, 유찰 {test["유찰여부"].sum()}')
        if train['연도'].max() >= year or not test['연도'].eq(year).all():
            raise AssertionError(f'{year}년 학습·평가 기간 분리 실패')
        log(f'\n[STEP {year}] 학습 2021~{year-1}: {len(train):,}건 → 검증 {year}: {len(test):,}건')
        for model, steps in base.MODELS.items():
            row = evaluate_one(train, test, model, steps, year)
            rows.append(row)
            log(f"  {model}: Recall {row['사전식별률_pct']:.2f}% / "
                f"표본부족대체율 {row['표본부족대체율_pct']:.2f}% / KPI2 {row['KPI2_spearman']:.3f}")
    yearly, pattern = summarize_pattern(pd.DataFrame(rows))
    if len(yearly) != len(YEARS) * len(base.MODELS):
        raise AssertionError('연도×모델 결과 16행이 아닙니다.')
    save_csv(yearly, outputs[0])
    save_csv(pattern, outputs[1])
    outputs[2].write_text(report_text(yearly, pattern), encoding='utf-8')
    save_recall_trend_chart(yearly, outputs[3])
    log(f'  [그래프] {outputs[3]}')
    logging.info('완료: %s / %.2f초', out, time.perf_counter() - start)
    log(f'\n[완료] {len(yearly)}개 연도×조합 평가 / 결과: {out}')
    log(pattern[['model', '평균식별률_pct', '식별률범위_pp', '1위연도수_동률포함']].to_string(index=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        logging.exception('순차 검증 실패')
        log(f'[실행 실패] {type(exc).__name__}: {exc}')
        raise