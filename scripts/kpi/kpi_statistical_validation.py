# -*- coding: utf-8 -*-
"""국방 조달 유찰 위험도 공식 4변수 모델 통계 검증.

[실행]
python scripts/kpi/kpi_statistical_validation.py --reps 2000 --seed 2026

[필수 입력] rolling_validation/03_yearly_notice_risks.csv
[참고 입력] rolling_validation/01_period_model_results.csv,
            rolling_validation/06_kpi2_tie_sensitivity_by_period.csv
[출력] data/kpi/statistical_validation/

※ 기존 위험도 기준표를 재학습하지 않고, 확정한 검증 공고의 위험도 점수에 대해
   사업키 군집 단위 대응 부트스트랩을 수행합니다.
※ 2025년은 과거에 결과를 살펴본 이력이 있으므로 독립적인 최초 블라인드 평가로 표시하지 않습니다.
※ 부트스트랩 신뢰구간은 KPI 2 동점 민감도 구간과 다릅니다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import copy
from pathlib import Path
import numpy as np
import pandas as pd

TOP_RATIO = 0.20
BASE = '3변수_기준선'
OFFICIAL = '4변수_공식'
PERIODS = ('2024_예비검증', '2025_별도평가')


def root_from_script():
    """스크립트가 scripts/kpi에 있으면 프로젝트 루트를 자동 판별합니다."""
    p = Path(__file__).resolve()
    for parent in [p.parent, *p.parents, Path.cwd(), *Path.cwd().parents]:
        if (parent / 'data' / 'kpi').is_dir():
            return parent
    raise FileNotFoundError('프로젝트 경로를 못 찾았습니다. --project-root 지정 필요')


def as_markdown(df: pd.DataFrame) -> str:
    """tabulate 설치 없이 Markdown 표를 생성합니다."""
    cols = [str(c) for c in df.columns]
    def cell(v):
        if pd.isna(v):
            return ''
        return str(v).replace('|', '\\|').replace('\n', ' ')
    return '\n'.join(['| ' + ' | '.join(cols) + ' |',
                      '| ' + ' | '.join(['---'] * len(cols)) + ' |',
                      *['| ' + ' | '.join(cell(x) for x in row) + ' |'
                        for row in df.itertuples(index=False, name=None)]])


def recall(y, risk, evidence, keyrank):
    """기존 KPI 정렬: 위험도 내림차순 → 근거공고수 내림차순 → 공고키 오름차순."""
    n = len(y)
    failures = int(y.sum())
    if n == 0 or failures == 0:
        return np.nan
    k = int(np.floor(n * TOP_RATIO + .5))
    order = np.lexsort((keyrank, -evidence, -risk))
    return float(100.0 * y[order[:k]].sum() / failures)


def prepare_period(df: pd.DataFrame, period: str):
    """공식 모델과 기준선을 같은 공고키로 정렬·병합하고 엄격하게 검증합니다."""
    d = df.loc[df['검증구간'].eq(period) & df['모델'].isin([BASE, OFFICIAL])].copy()
    if set(d['모델']) != {BASE, OFFICIAL}:
        raise ValueError(f'{period}: 기준선/공식 4변수 데이터 부족')
    models = {}
    for name in (BASE, OFFICIAL):
        sub = d.loc[d['모델'].eq(name)].copy()
        if sub['noticeKey'].isna().any() or not sub['noticeKey'].is_unique:
            raise ValueError(f'{period}, {name}: 공고키 결측 또는 중복')
        if not pd.to_numeric(sub['위험도'], errors='coerce').between(0, 1).all():
            raise ValueError(f'{period}, {name}: 위험도 범위 오류')
        if not (pd.to_numeric(sub['근거공고수'], errors='coerce') >= 10).all():
            raise ValueError(f'{period}, {name}: 근거공고수 최소 10건 미충족')
        models[name] = sub.set_index('noticeKey')
    keys = sorted(models[BASE].index.tolist())
    if set(keys) != set(models[OFFICIAL].index):
        raise ValueError(f'{period}: 모델 간 평가 모집단 불일치')
    a, b = [models[m].loc[keys] for m in (BASE, OFFICIAL)]
    if not (a['유찰여부'].to_numpy() == b['유찰여부'].to_numpy()).all():
        raise ValueError(f'{period}: 동일 공고 유찰 결과 불일치')
    if not (a['businessKey'].fillna('') .astype(str).to_numpy() ==
            b['businessKey'].fillna('').astype(str).to_numpy()).all():
        raise ValueError(f'{period}: 사업키 불일치')
    if a['businessKey'].isna().any() or a['businessKey'].astype(str).str.strip().eq('').any():
        raise ValueError(f'{period}: 사업키 결측 — 군집 부트스트랩 중단')
    y = a['유찰여부'].to_numpy(dtype=np.int8)
    if not np.isin(y, [0, 1]).all() or y.sum() == 0:
        raise ValueError(f'{period}: 이진 유찰 결과 또는 유찰 건수 오류')
    arrays = {name:(item['위험도'].to_numpy(dtype=float),
                    item['근거공고수'].to_numpy(dtype=float))
              for name,item in ((BASE,a),(OFFICIAL,b))}
    # 공고키 정렬을 기준으로 복원추출 때도 동일한 tie-break 우선순위 유지
    rank = np.arange(len(keys), dtype=np.int64)
    for name,item in ((BASE,a),(OFFICIAL,b)):
        actual = recall(y, *arrays[name], rank)
        recorded = int(item['상위20pct'].astype(bool).mul(item['유찰여부']).sum())
        expected = round(actual * int(y.sum()) / 100)
        if recorded != expected:
            raise ValueError(f'{period}, {name}: 공고별 상위20pct 결과 불일치 {recorded}!={expected}')
    return y, a['businessKey'].astype(str).to_numpy(), arrays, rank


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4. 감사: 입력 파일 지문과 Top20 경계 동점 민감도
# ─────────────────────────────────────────────────────────────────────────────
def sha256_file(path: Path) -> str:
    """입력 파일의 내용 자체를 SHA-256으로 식별합니다."""
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def tie_boundary(period, name, y, risk, evidence, rank):
    """상위 20% 경계의 동점 공고만 바꿨을 때 가능한 Recall 범위.

    이는 *정렬 민감도 범위*이며 통계적 신뢰구간이 아닙니다.
    """
    n, failures = len(y), int(y.sum())
    k = int(np.floor(n * TOP_RATIO + .5))
    order = np.lexsort((rank, -evidence, -risk))
    cutoff = float(risk[order[k - 1]])
    above = risk > cutoff
    tied = risk == cutoff
    a = int(above.sum())
    needed = k - a
    tied_y = int(y[tied].sum())
    tied_n = int(tied.sum())
    fixed_y = int(y[above].sum())
    min_hits = fixed_y + max(0, needed - (tied_n - tied_y))
    max_hits = fixed_y + min(needed, tied_y)
    observed_hits = int(y[order[:k]].sum())
    expected_hits = fixed_y + needed * tied_y / tied_n
    return dict(검증구간=period, 모델=name, 검증공고수=n,
                상위20pct공고수=k, 경계위험도=cutoff,
                경계동점전체건수=tied_n, 경계동점선정건수=needed,
                경계동점유찰건수=tied_y, 관측포착유찰=observed_hits,
                동점순서최소포착=min_hits, 동점순서최대포착=max_hits,
                동점무작위기대포착=expected_hits,
                관측Recall_pct=100 * observed_hits / failures,
                동점순서최소Recall_pct=100 * min_hits / failures,
                동점순서최대Recall_pct=100 * max_hits / failures,
                동점무작위기대Recall_pct=100 * expected_hits / failures)


def repeat_audit(period, y, businesses, arrays, rank, reps, rng, replay_check=25):
    """군집 대응 부트스트랩 실행 + 반복별 감사표 + 동일 시드 재현성 검사.

    - 같은 사업키에 속한 공고 행을 통째로 복원추출
    - 3변수와 공식 4변수에 동일 재표본 적용
    - 매 반복마다 상위 20%를 다시 선정
    - 첫 N회는 RNG 상태를 복제하여 다시 수행한 결과와 지문을 확인
    """
    _, inv = np.unique(businesses, return_inverse=True)
    ordered = np.argsort(inv, kind='stable')
    sizes = np.bincount(inv)
    cuts = np.r_[0, np.cumsum(sizes)]
    ng = len(sizes)
    observed = {m: recall(y, *arrays[m], rank) for m in (BASE, OFFICIAL)}
    state = copy.deepcopy(rng.bit_generator.state)
    rows, samples = [], {BASE: [], OFFICIAL: []}
    for i in range(reps):
        chosen = rng.integers(0, ng, ng)
        idx = np.concatenate([ordered[cuts[c]:cuts[c + 1]] for c in chosen])
        draw_hash = hashlib.blake2b(idx.tobytes(), digest_size=12).hexdigest()
        scores = {m: recall(y[idx], arrays[m][0][idx], arrays[m][1][idx], rank[idx])
                  for m in (BASE, OFFICIAL)}
        for m in samples:
            samples[m].append(scores[m])
        rows.append(dict(검증구간=period, 반복=i + 1,
                         복원추출사업군집수=ng, 고유추출사업군집수=int(np.unique(chosen).size),
                         재표본공고수=len(idx), 재표본유찰건수=int(y[idx].sum()),
                         재선정상위20pct=int(np.floor(len(idx) * TOP_RATIO + .5)),
                         기준선Recall_pct=scores[BASE], 공식4변수Recall_pct=scores[OFFICIAL],
                         공식기준_차이_pp=scores[OFFICIAL] - scores[BASE],
                         재표본인덱스지문=draw_hash))
    # 첫 N회 복원추출의 동일 시드 재현성을 직접 검증한다.
    replay_rng = np.random.default_rng()
    replay_rng.bit_generator.state = state
    ncheck = min(reps, replay_check)
    for i in range(ncheck):
        chosen = replay_rng.integers(0, ng, ng)
        idx = np.concatenate([ordered[cuts[c]:cuts[c + 1]] for c in chosen])
        if hashlib.blake2b(idx.tobytes(), digest_size=12).hexdigest() != rows[i]['재표본인덱스지문']:
            raise AssertionError(f'{period}: 반복 {i+1} 재표본 지문 불일치')
        for m in (BASE, OFFICIAL):
            got = recall(y[idx], arrays[m][0][idx], arrays[m][1][idx], rank[idx])
            if not (np.isclose(got, samples[m][i], rtol=0, atol=1e-12) or
                    (np.isnan(got) and np.isnan(samples[m][i]))):
                raise AssertionError(f'{period}: 반복 {i+1} {m} 재현 실패')
    valid = np.isfinite(samples[BASE]) & np.isfinite(samples[OFFICIAL])
    if valid.sum() < max(100, int(reps * .9)):
        raise ValueError(f'{period}: 유효 부트스트랩 부족 {valid.sum()}/{reps}')
    model_rows = []
    for m in (BASE, OFFICIAL):
        lo, hi = np.percentile(np.asarray(samples[m])[valid], [2.5, 97.5])
        model_rows.append(dict(검증구간=period, 모델=m, 검증공고수=len(y),
                               검증유찰건수=int(y.sum()), 사업군집수=ng,
                               Recall_at20_pct=observed[m], CI95_하한_pct=lo,
                               CI95_상한_pct=hi, 유효반복수=int(valid.sum())))
    delta = np.asarray(samples[OFFICIAL])[valid] - np.asarray(samples[BASE])[valid]
    lo, hi = np.percentile(delta, [2.5, 97.5])
    comp = dict(검증구간=period, 비교=f'{OFFICIAL} − {BASE}',
                공식4변수_Recall_pct=observed[OFFICIAL], 기준선_Recall_pct=observed[BASE],
                관측차이_pp=observed[OFFICIAL] - observed[BASE],
                CI95_차이하한_pp=lo, CI95_차이상한_pp=hi,
                CI95_0포함=bool(lo <= 0 <= hi),
                부트스트랩_공식우세비율=float(np.mean(delta > 0)),
                유효반복수=int(valid.sum()))
    audit = dict(검증구간=period, 난수시드='전역 인수 --seed', 전체반복수=reps,
                 유효반복수=int(valid.sum()), 사업군집수=ng,
                 재현성재실행검사횟수=ncheck, 재현성검사='통과',
                 동일재표본두모델적용=True, 반복별Top20재선정=True,
                 기준선관측Recall_pct=observed[BASE], 공식관측Recall_pct=observed[OFFICIAL])
    return model_rows, comp, rows, audit


def main():
    p=argparse.ArgumentParser(description='공식 4변수 vs 3변수: 사업키 대응 부트스트랩')
    p.add_argument('--project-root',type=Path)
    p.add_argument('--risks',type=Path,help='03_yearly_notice_risks.csv')
    p.add_argument('--period-results',type=Path,help='01_period_model_results.csv')
    p.add_argument('--tie-results',type=Path,help='06_kpi2_tie_sensitivity_by_period.csv')
    p.add_argument('--output-dir',type=Path)
    p.add_argument('--reps',type=int,default=2000)
    p.add_argument('--seed',type=int,default=2026)
    a=p.parse_args()
    if a.reps < 200: raise ValueError('--reps 200 이상 필수')
    root=a.project_root.resolve() if a.project_root else root_from_script()
    def path(arg, default):
        v=arg if arg is not None else Path(default)
        return v if v.is_absolute() else root/v
    risks=path(a.risks,'data/kpi/rolling_validation/03_yearly_notice_risks.csv')
    periods=path(a.period_results,'data/kpi/rolling_validation/01_period_model_results.csv')
    ties=path(a.tie_results,'data/kpi/rolling_validation/06_kpi2_tie_sensitivity_by_period.csv')
    out=path(a.output_dir,'data/kpi/statistical_validation')
    if not risks.is_file():raise FileNotFoundError(f'Rolling 공고별 위험도 파일 없음: {risks}')
    out.mkdir(parents=True,exist_ok=True)
    df=pd.read_csv(risks,encoding='utf-8-sig',low_memory=False)
    required={'검증구간','모델','noticeKey','businessKey','유찰여부','위험도','근거공고수','상위20pct'}
    if required-set(df.columns):raise ValueError(f'공고별 파일 필수컬럼 누락: {required-set(df.columns)}')
    rng=np.random.default_rng(a.seed)
    models,comparisons=[],[]
    repeat_rows, audit_rows, tie_rows = [], [], []
    for period in PERIODS:
        print(f'[검증] {period}: 사업군집 부트스트랩 {a.reps:,}회',flush=True)
        y,businesses,arrays,rank=prepare_period(df,period)
        m,c,individual,audit=repeat_audit(period,y,businesses,arrays,rank,a.reps,rng)
        models.extend(m);comparisons.append(c)
        repeat_rows.extend(individual);audit_rows.append(audit)
        for name in (BASE, OFFICIAL):
            tie_rows.append(tie_boundary(period,name,y,*arrays[name],rank))
        print(f"  공식4변수 {c['공식4변수_Recall_pct']:.2f}% / 기준선 {c['기준선_Recall_pct']:.2f}% / 차이 {c['관측차이_pp']:+.2f}%p",flush=True)
    mdf=pd.DataFrame(models);cdf=pd.DataFrame(comparisons)
    mdf.to_csv(out/'01_model_confidence_intervals.csv',index=False,encoding='utf-8-sig')
    cdf.to_csv(out/'02_paired_model_differences.csv',index=False,encoding='utf-8-sig')
    r_df=pd.DataFrame(repeat_rows); a_df=pd.DataFrame(audit_rows); t_df=pd.DataFrame(tie_rows)
    r_df.to_csv(out/'04_bootstrap_iteration_audit.csv',index=False,encoding='utf-8-sig')
    a_df.to_csv(out/'05_bootstrap_run_audit.csv',index=False,encoding='utf-8-sig')
    t_df.to_csv(out/'06_kpi1_tie_sensitivity.csv',index=False,encoding='utf-8-sig')
    # 입력 파일 내용 SHA-256 및 실행 옵션을 별도 JSON 감사 파일로 기록한다.
    manifest={"seed":a.seed,"bootstrap_reps":a.reps,"top_ratio":TOP_RATIO,
              "cluster_key":"businessKey", "same_draw_for_both_models":True,
              "reselect_top20_each_repeat":True,"rng":"numpy.default_rng (PCG64)",
              "percentile_ci":"2.5%, 97.5%", "validation_periods":list(PERIODS),
              "input_files":{str(x):sha256_file(x) for x in (risks,periods,ties) if x.is_file()},
              "replay_checks":{r['검증구간']:r['재현성재실행검사횟수'] for r in audit_rows}}
    (out/'07_reproducibility_manifest.json').write_text(
        json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 공식 4변수 vs 3변수: KPI 통계 검증', '',
           f'- 사업키 군집 대응 부트스트랩: {a.reps:,}회 / 시드 {a.seed}',
           '- 2024: 2021~2023 학습 / 2025: 2021~2024 학습',
           '- 모델을 부트스트랩마다 재학습하지 않고 검증 표본만 복원추출',
           '- 모델 선정 이후의 사후 분석이므로 독립 확증 검정으로 주장하지 않음',
           '- 학습 시점 계획/품목 정보의 가용성은 별도 감사 필요',
           '- 동점은 위험도 → 근거공고수 → 공고키의 결정적 정렬 순서 유지',
           '', '## 모델별 Recall@20% 95% 신뢰구간', '',
           as_markdown(mdf.round(4)), '', '## 공식 4변수 − 3변수의 대응 차이', '',
           as_markdown(cdf.round(4)), '']
    # STEP 6. 부트스트랩 실행감사와 동점 민감도를 본문에 명확히 구분하여 기록
    compact=t_df[['검증구간','모델','관측Recall_pct','동점순서최소Recall_pct',
                  '동점순서최대Recall_pct','동점무작위기대Recall_pct','경계동점전체건수']].copy()
    lines+=['## 부트스트랩 실행 감사','',
            '- 동일 사업키는 하나의 군집으로 복원추출; 두 모델에 **동일한 추출 인덱스** 적용.',
            '- 반복마다 공고 수가 변동하므로 상위 20% 선정 건수도 새로 계산.',
            '- 각 반복의 재표본 크기, 유찰 건수, 두 Recall, 성능 차이, 추출 지문은 `04_bootstrap_iteration_audit.csv` 기록.',
            '- 동일 RNG 상태에서 첫 25회를 재실행하여 추출 지문과 두 Recall 일치 여부 확인.',
            '- 입력 SHA-256, 반복 횟수·시드·알고리즘은 `07_reproducibility_manifest.json` 기록.',
            '', as_markdown(a_df), '',
            '## KPI 1 경계 동점 순위 민감도 (신뢰구간 아님)', '',
            as_markdown(compact.round(3)), '',
            '- 동점 최소·최대는 동일 경계 위험도 안에서 선택 순서를 바꾸는 가상 극단값.',
            '- 부트스트랩 95% 신뢰구간(표본 불확실성)과 동점 순위 범위(정렬 규칙 민감도)는 합치지 않음.',
            '', '## 통계 검증 종합 해석','']
    for _, row in cdf.iterrows():
        status=('0이 포함되어 우위가 확정되지 않는다' if row['CI95_0포함']
                else '0이 포함되지 않는다(사후 비교 한계 고려)')
        lines.append(f"- **{row['검증구간']}**: 공식 4변수 {row['공식4변수_Recall_pct']:.2f}% / "
                     f"기준선 {row['기준선_Recall_pct']:.2f}%, "
                     f"차이 {row['관측차이_pp']:+.2f}%p "
                     f"(95% CI {row['CI95_차이하한_pp']:+.2f}~{row['CI95_차이상한_pp']:+.2f}%p). "
                     f"차이의 신뢰구간에 {status}.")
    lines+=['', '- 두 연도의 표본 불확실성 및 경계 동점 민감도를 함께 고려하여, 사전 식별률만으로 공식 4변수의 우월성을 단정하지 않음.',
            '- Rolling 검증의 KPI 2 순위 일관성 결과는 보조 근거이며, 2025년은 최초 블라인드 평가가 아님.',
            '- **한계**: 기준표 재학습 불확실성과 공고일 당시 계획·품목정보의 시점 가용성은 아직 별도 검증 필요.', '']
    if periods.is_file():
        p_df=pd.read_csv(periods,encoding='utf-8-sig')
        sel=p_df.loc[p_df['모델'].isin([BASE,OFFICIAL]),
                     ['검증구간','모델','검증공고수','검증유찰건수','사전식별률_pct','KPI2_spearman']]
        lines+=['## 반기 및 연간 검증 관측 결과 (신뢰구간 아님)','',as_markdown(sel.round(3)),'',
                '- 반기 4구간은 2021~2022 고정 학습이며, 2024·2025 연간은 각각 학습기간이 다름.','']
    if ties.is_file():
        tie=pd.read_csv(ties,encoding='utf-8-sig')
        cols=['검증구간','모델','기존KPI2_spearman','무작위KPI2_평균','무작위KPI2_표준편차']
        lines+=['## KPI 2 동점 순서 민감도 (별도 분석)','',as_markdown(tie[cols].round(4)),'',
                '- 동점 순서 재배열 분포는 Recall 부트스트랩 95% 신뢰구간이 아님.','']
    lines+=['## 해석상 한계','',
            '- 신뢰구간에 0이 포함되면 해당 차이의 방향을 명확히 확정하기 어려움.',
            '- 2025년은 과거 확인 이력이 있으므로 완전히 새로운 블라인드 평가가 아님.',
            '- 모델의 학습 변동, 예산/품목의 시점 누수, 다중 모델 탐색에 따른 불확실성은 본 CI에 포함되지 않음.','']
    (out/'03_statistical_report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(f'[완료] {out}',flush=True)

if __name__=='__main__':
    main()
