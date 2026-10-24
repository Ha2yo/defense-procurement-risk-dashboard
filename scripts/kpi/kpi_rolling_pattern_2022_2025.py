# -*- coding: utf-8 -*-
"""공식 금액 5분위 4변수: 반기별 안정성 검증 + 2024 예비 + 2025 별도 평가.

필수 파일: 같은 폴더의 kpi_2024_validation.py (최신 공식 4변수 버전)
실행: python scripts/kpi/kpi_rolling_pattern_2022_2025.py

중요: 이 코드는 과거에 사용한 3/4기간/5/6변수 연도별 승자 선정 코드가 아니다.
공식 문서(4변수_공식기준.md)의 고정된 4변수와 3변수 기준선을 주 분석 대상으로 한다.
2025년 결과에 따라 금액 경계·모델 구성·10건 규칙을 수정하지 않는다.
기존에 2025년 결과를 확인했으므로 '최초 블라인드 평가'라고 주장하지 않는다.
"""
from __future__ import annotations
import argparse
import importlib.util
import math
from pathlib import Path
import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# 0단계: 사전 확정된 평가 설정 (성과를 보고 변경하지 않음)
# ─────────────────────────────────────────────────────────────────────────────
MAIN_MODELS = ('3변수_기준선','4변수_공식')
PERIODS = [
    ('2023H1_안정성', (2021,2022), 2023, 1, '반기 안정성'),
    ('2023H2_안정성', (2021,2022), 2023, 2, '반기 안정성'),
    ('2024H1_안정성', (2021,2022), 2024, 1, '반기 안정성'),
    ('2024H2_안정성', (2021,2022), 2024, 2, '반기 안정성'),
    ('2024_예비검증', (2021,2022,2023), 2024, 0, '연간 예비 검증'),
    ('2025_별도평가', (2021,2022,2023,2024), 2025, 0, '2025 별도 평가'),
]


def load_core():
    """분석 함수를 공식 2024 KPI 코드에서 직접 재사용해 규칙 불일치를 방지."""
    file = Path(__file__).resolve().with_name('kpi_2024_validation.py')
    if not file.is_file():
        raise FileNotFoundError(f'공통 공식 KPI 코드가 없습니다: {file}')
    spec = importlib.util.spec_from_file_location('official4_kpi_core',file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    expected=['prepare_population','derive_amount','quintile_boundaries','apply_quintiles',
              'score_model','evaluate','kpi2_deciles','tie_sensitivity','find_input',
              'read_csv_safe','STEPS_3','STEPS_4','kpi2_tie_sensitivity']
    absent=[name for name in expected if not hasattr(mod,name)]
    if absent:
        raise ImportError('최신 공식 4변수 kpi_2024_validation.py 필요: '+', '.join(absent))
    return mod


def markdown(df:pd.DataFrame)->str:
    """tabulate 설치 없이 Markdown 표 출력."""
    head=[str(x).replace('|','\\|') for x in df.columns]
    rows=[]
    for values in df.itertuples(index=False,name=None):
        rows.append('| '+' | '.join('' if pd.isna(v) else str(v).replace('|','\\|') for v in values)+' |')
    return '\n'.join(['| '+' | '.join(head)+' |','| '+' | '.join(['---']*len(head))+' |',*rows])


def model_period(core, money_pop:pd.DataFrame, scope:tuple, include_exploratory:bool, tie_reps:int, tie_seed:int):
    label,train_years,test_year,half,purpose=scope
    train=money_pop.loc[money_pop['연도'].isin(train_years)&money_pop['제외사유'].eq('포함')].copy()
    mask=money_pop['연도'].eq(test_year)&money_pop['제외사유'].eq('포함')
    if half:
        mask &= money_pop['공고일'].dt.month.between(1,6) if half==1 else money_pop['공고일'].dt.month.between(7,12)
    test=money_pop.loc[mask].copy()
    if train.empty or test.empty:
        raise ValueError(f'{label}: 학습/검증 모집단 비어 있음 (학습 {len(train)}, 검증 {len(test)})')
    # ─────────────────────────────────────────────────────────────────────────
    # 2단계: 훈련연도에 속한 결과확정 경쟁성 공고 중 유효 사업규모금액만으로
    #         20·40·60·80 분위수를 계산. 평가 대상의 금액 분포/결과 사용 금지.
    # ─────────────────────────────────────────────────────────────────────────
    bounds=core.quintile_boundaries(train)
    train=core.apply_quintiles(train,bounds)
    test=core.apply_quintiles(test,bounds)
    models={'3변수_기준선':core.STEPS_3,'4변수_공식':core.STEPS_4}
    if include_exploratory:
        # 보고서 5-4 참고 실험: 공식 사업규모금액과 달리 공고기간/기초금액/분기 사용.
        # 2025 성능으로 공식 모델을 재선택하거나 분위 경계를 조정하지 않는다.
        train=core.derive_exploratory_features(train)
        test=core.derive_exploratory_features(test)
        models.update(core.EXPLORATORY_MODELS)
    rows=[]; boundaries=[]; scored=[]; kpi2_ties=[]
    for name,steps in models.items():
        ranked,metrics=core.evaluate(core.score_model(train,test,steps))
        deciles,rho,splits=core.kpi2_deciles(ranked,name)
        ties=core.tie_sensitivity(ranked,name)
        # 2-4단계: KPI 2 동점 순서 민감도(공식 2024 코드와 동일한 구현).
        # 동일 위험도 내에서만 무작위 재배치하고 학습/평가·기본 KPI는 변경하지 않는다.
        tie2=core.kpi2_tie_sensitivity(ranked,name,reps=tie_reps,seed=tie_seed)
        kpi2_ties.append({'검증구간':label,'용도':purpose,'학습연도':','.join(map(str,train_years)),
                          '검증연도':test_year,'검증반기':('연간' if not half else f'H{half}'),**tie2})
        rows.append({'검증구간':label,'용도':purpose,'학습연도':','.join(map(str,train_years)),
                     '검증연도':test_year,'검증반기':('연간' if not half else f'H{half}'),
                     '모델':name,'모델구분':('공식' if name=='4변수_공식' else '기준선' if name=='3변수_기준선' else '5-4 탐색'),
                     '학습공고수':len(train),'학습유효금액공고수':int(train['사업규모금액'].notna().sum()),
                     **metrics,'KPI2_spearman':rho,'KPI2_동점분위경계수':splits,
                     'Precision_at20_pct':100*metrics['포착유찰']/metrics['상위20pct공고수'],
                     'Lift_at20':(metrics['포착유찰']/metrics['상위20pct공고수'])/(metrics['검증유찰건수']/metrics['검증공고수'])
                     if metrics['검증유찰건수'] else np.nan,
                     '동점무작위기대Recall_pct':ties['동점무작위기대Recall_pct'],
                     '동점순서최소Recall_pct':ties['동점순서최소Recall_pct'],
                     '동점순서최대Recall_pct':ties['동점순서최대Recall_pct'],
                     '동점경계전체건수':ties['경계동점전체건수']})
        if purpose!='반기 안정성':
            item=ranked[['noticeKey','businessKey','유찰여부','위험도','근거공고수','근거유찰건수',
                         '적용단계','완화사유','순위','상위20pct']].copy()
            item.insert(0,'검증구간',label);item.insert(1,'모델',name)
            scored.append(item)
    for q,b in zip((20,40,60,80),bounds):
        boundaries.append({'검증구간':label,'학습연도':','.join(map(str,train_years)),
                           '학습유효금액공고수':int(train['사업규모금액'].notna().sum()),
                           '분위수_pct':q,'경계금액_원':b,'포함방식':'오른쪽 포함(이하)','보간법':'pandas linear'})
    return rows,boundaries,scored,kpi2_ties


def plot_trend(df:pd.DataFrame, destination:Path, mode:str):
    """학습 데이터가 같은 평가끼리만 연결한다.

    mode='half': 2021~2022 고정 학습 2023/2024 반기 4개.
    mode='annual': 학습기간이 서로 다른 2024/2025 연간 2개를 *선 없이* 비교.
    matplotlib가 없어도 CSV/보고서 작성은 완료하도록 한다.
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    if mode=='half':
        periods=['2023H1_안정성','2023H2_안정성','2024H1_안정성','2024H2_안정성']
        labels=['2023 H1','2023 H2','2024 H1','2024 H2']
        subtitle='Fixed training: 2021–2022'
    elif mode=='annual':
        periods=['2024_예비검증','2025_별도평가']
        labels=['2024 preliminary','2025 separate']
        subtitle='Different training: 2021–2023 vs 2021–2024'
    else:
        raise ValueError(f'잘못된 차트 종류: {mode}')
    fig,ax=plt.subplots(figsize=(10,5.3))
    for model,caption,shift in [('3변수_기준선','3-var baseline',-0.09),
                                ('4변수_공식','4-var official',0.09)]:
        sel=df.loc[df['모델'].eq(model)].set_index('검증구간')
        y=[float(sel.loc[p,'사전식별률_pct']) if p in sel.index else np.nan for p in periods]
        if mode=='half':
            ax.plot(range(len(labels)),y,'o-',lw=2,label=caption)
        else:
            # 서로 다른 연도별 학습 모델을 하나의 시계열로 연결하지 않는다.
            ax.scatter(np.arange(len(labels))+shift,y,s=95,label=caption,zorder=3)
        for i,v in enumerate(y):
            if np.isfinite(v):
                ax.annotate(f'{v:.2f}%',(i+(shift if mode=='annual' else 0),v),
                            xytext=(0,9 if model=='4변수_공식' else -17),
                            textcoords='offset points',ha='center',fontsize=9)
    ax.set_xticks(range(len(labels)),labels)
    ax.set_title('Recall@20% | '+subtitle)
    ax.set_ylabel('Recall@20% (%)'); ax.set_xlabel('Validation period')
    ax.grid(axis='y',alpha=.25);ax.legend();fig.tight_layout()
    fig.savefig(destination,dpi=160);plt.close(fig)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 입력 사전 집계: Python 그룹별 callback 대신 벡터화 그룹 집계
# 유효값이 오직 한 개면 채택, 복수 다른 유효값이면 보류하는 공식 규칙을 보존한다.
# ─────────────────────────────────────────────────────────────────────────────
def accelerated_plan(core, plan):
    core.require_columns(plan,['businessKey','bidMth','budgetAmount'],'조달계획')
    p=plan[['businessKey','bidMth','budgetAmount']].copy()
    p['bidMth']=core.normalize_method(p['bidMth'])
    p['budgetAmount']=pd.to_numeric(p['budgetAmount'],errors='coerce')
    p['budgetAmount']=p['budgetAmount'].where(p['budgetAmount'].gt(0))
    g=p.groupby('businessKey',dropna=False,sort=False)
    z=g.agg(계획행수=('businessKey','size'),_mcount=('bidMth','nunique'),
            _acount=('budgetAmount','nunique'),
            계획입찰방법=('bidMth','first'),계획예산=('budgetAmount','first')).reset_index()
    z.loc[z['_mcount'].ne(1),'계획입찰방법']=pd.NA
    z.loc[z['_acount'].ne(1),'계획예산']=np.nan
    return core.unique_by_key(z.drop(columns=['_mcount','_acount']), 'businessKey','사업별 조달계획')


def accelerated_result_method(core, results):
    core.require_columns(results,['noticeKey','bidMth'],'입찰결과')
    r=results[['noticeKey','bidMth']].copy()
    r['bidMth']=core.normalize_method(r['bidMth'])
    g=r.groupby('noticeKey',dropna=False,sort=False)
    z=g.agg(_size=('bidMth','size'),_count=('bidMth','count'),
            _distinct=('bidMth','nunique'),결과복원입찰방법=('bidMth','first')).reset_index()
    z.loc[z['_size'].ne(z['_count'])|z['_distinct'].ne(1),'결과복원입찰방법']=pd.NA
    return z[['noticeKey','결과복원입찰방법']]


def run(args):
    core=load_core()
    root=Path(args.project_root).resolve() if args.project_root else core.project_root()
    paths={
        'notice':core.find_input(root,args.notice,'bid_notice_clean_20210101_20251231','notice'),
        'result':core.find_input(root,args.result,'bid_result_clean_20210101_20251231','result'),
        'plan':core.find_input(root,args.plan,'procure_plan_clean_20210101_20251231','plan'),
        'items':core.find_input(root,args.items,'bid_item_clean_20210101_20251231','items'),
        'mapping':core.find_input(root,args.mapping,'orntCode_codeVld4_all','mapping',False)
    }
    if paths['mapping'] is None:
        other=root/'data'/'analysis'/'ornt_code'/'orntCode_codeVld4_all.csv'
        if other.is_file(): paths['mapping']=other
        else: raise FileNotFoundError('공식 기관그룹 매핑 파일이 없습니다: data/clean 또는 data/analysis/ornt_code/orntCode_codeVld4_all.csv')
    inputs={k:core.read_csv_safe(v) for k,v in paths.items()}
    out=Path(args.output_dir) if args.output_dir else root/'data'/'kpi'/'rolling_validation'
    if not out.is_absolute(): out=root/out
    print('[1/5] 실제 CLEAN 파일 로드 및 공고 단위 결과 판정',flush=True)
    pop=core.prepare_population(inputs['notice'],inputs['result'],inputs['mapping'])
    # ─────────────────────────────────────────────────────────────────────────
    # 3단계: 학습연도별 금액 복원 가능 범위 고정: (2021,22), (2021~23), (2021~24)
    #         반기 검증 2023/2024 공고에 입찰결과의 bidMth를 사용하지 않는 것이 핵심.
    # ─────────────────────────────────────────────────────────────────────────
    # 계획/품목/결과의 사업·공고키 집계는 학습기간에 따라 달라지지 않으므로 한 번만 수행.
    # 금액 복원 허용 여부(훈련 연도별)는 이후 derive_amount()에서 따로 평가한다.
    p_agg=accelerated_plan(core,inputs['plan'])
    i_agg=core.items_by_business(inputs['items'])
    r_agg=accelerated_result_method(core,inputs['result'])
    core.plan_by_business=lambda unused:p_agg
    core.items_by_business=lambda unused:i_agg
    core.result_method_by_notice=lambda unused:r_agg
    unique_trains=list(dict.fromkeys(x[1] for x in PERIODS))
    cache={}
    for years in unique_trains:
        print(f'[2/5] 사업규모금액 산정 및 입찰방법 복원 허용 학습연도: {years}',flush=True)
        cache[years]=core.derive_amount(pop,inputs['plan'],inputs['items'],inputs['result'],train_years=years)
    results=[];boundaries=[]; scored=[]; tie2_results=[]
    for scope in PERIODS:
        label,years,*_=scope
        print(f'[3/5] {label} 검증 중',flush=True)
        r,b,s,t=model_period(core,cache[years],scope,args.include_exploratory,args.tie_reps,args.tie_seed)
        results.extend(r);boundaries.extend(b);scored.extend(s);tie2_results.extend(t)
    result_df=pd.DataFrame(results)
    boundary_df=pd.DataFrame(boundaries)
    tie2_df=pd.DataFrame(tie2_results)
    out.mkdir(parents=True,exist_ok=True)
    result_df.to_csv(out/'01_period_model_results.csv',index=False,encoding='utf-8-sig')
    boundary_df.to_csv(out/'02_amount_quintile_boundaries_by_train.csv',index=False,encoding='utf-8-sig')
    tie2_df.to_csv(out/'06_kpi2_tie_sensitivity_by_period.csv',index=False,encoding='utf-8-sig')
    if scored:
        pd.concat(scored,ignore_index=True).to_csv(out/'03_yearly_notice_risks.csv',index=False,encoding='utf-8-sig')
    print('[4/5] 반기 안정성·2024 예비·2025 별도 결과 요약',flush=True)
    preview=result_df[['검증구간','모델','검증공고수','검증유찰건수','포착유찰','사전식별률_pct','대체율_pct','KPI2_spearman']].copy()
    for col in ['사전식별률_pct','대체율_pct','KPI2_spearman']:
        preview[col]=preview[col].map(lambda v: f'{v:.3f}' if pd.notna(v) else 'NA')
    report=['# 공식 4변수 금액 5분위 반기 안정성 및 연간 평가','',
            '- 공식 4변수: 기관그룹 × 계약방법 × 사업구분 × 학습기간 내 사업규모금액 5분위',
            '- 비교 기준선: 기관그룹 × 계약방법 × 사업구분',
            '- 2023H1/H2, 2024H1/H2는 **2021~2022년 학습 데이터 고정**',
            '- 2024 예비: 2021~2023년 학습. 2025 별도: 2021~2024년 학습.',
            '- 검증연도 결과로 5분위 경계·위험도 기준표를 조정하지 않음.',
            '- 학습기간에만 결과 기반 입찰방법 복원 허용; 평가연도는 사후 복원 금지.',
            '- 2025년 자료는 기존 프로젝트에서 확인된 이력이 있어 독립적 최초 블라인드 평가로 간주하지 않음.',
            '- **2024년 연간 예비 검증과 2024H1/H2는 학습기간이 다르므로 직접 동일 모델 실험으로 합산하지 않음.**',
            '- 공고일 이전 계획예산·품목정보 가용성은 추가 검증이 필요.',
            '- 표의 탐색모델은 옵션을 켰을 때만 계산하며 공식 모델 선정에 사용하지 않음.','',
            '## 모델별 결과','',markdown(preview),'',
            '## 금액 5분위 학습 경계','',markdown(boundary_df),'',
            '## KPI 2 동점 순서 민감도','',
            f'- 각 기간·모델에서 동일 위험도 내 순서를 {args.tie_reps}회 변경 (시드 {args.tie_seed}).',
            '- 하위/상위 2.5%는 동점 순서 민감도 구간이며 통계적 95% 신뢰구간이 아님.',
            '',markdown(tie2_df[['검증구간','모델','기존KPI2_spearman','무작위KPI2_평균',
                                '무작위KPI2_표준편차','무작위KPI2_하위2_5pct',
                                '무작위KPI2_상위97_5pct','동점분위경계수']].round(4)),'',
            '## 해석 주의','',
            '- Recall@20%는 각 검증구간 공고의 위험도 상위 20%에서 포착한 유찰 비율.',
            '- KPI 2는 동일 규모 10분위의 위험도 오름차순 순위와 실제 유찰률 Spearman.',
            '- 동점이 발생하면 같은 위험도 공고를 근거공고수→공고키로 결정적 정렬함.',
            '- 경계 동점 최소/최대 Recall은 민감도 극단값이며 신뢰구간이 아님.',
            '- 위험도는 유사 과거공고 관측 유찰률이며 공고의 확정 유찰 확률은 아님.','']
    (out/'04_rolling_report.md').write_text('\n'.join(report),encoding='utf-8')
    half_chart=plot_trend(result_df,out/'05_recall_trend_halfyear.png','half')
    annual_chart=plot_trend(result_df,out/'07_recall_comparison_annual.png','annual')
    print('[5/5] 완료:',out,'| 반기 그래프:',half_chart,'| 연간 그래프:',annual_chart,flush=True)
    return result_df


def main():
    parser=argparse.ArgumentParser(description='공식 4변수 금액5분위 반기별 안정성/연간 검증')
    for opt in ('project-root','notice','result','plan','items','mapping','output-dir'):
        parser.add_argument('--'+opt,default=None)
    parser.add_argument('--tie-reps',type=int,default=300,help='KPI 2 동점 순서 무작위 반복(기본 300회)')
    parser.add_argument('--tie-seed',type=int,default=2026,help='KPI 2 반복 난수시드(기본 2026)')
    parser.add_argument('--include-exploratory',action='store_true',help='기존 5-4 탐색 4·5·6변수도 함께 계산')
    args=parser.parse_args()
    if args.tie_reps < 2: parser.error('--tie-reps는 2 이상이어야 합니다.')
    run(args)

if __name__=='__main__':
    main()