#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""통계 기반 KPI1 경계 동점 처리 진단 (원본/기존 모델 불변).

Run: python kpi1_tiebreak_statistics.py --outdir kpi1_tiebreak_statistics_results
Train-only historical failure rates; 2023/24/25 chronological validation.
2023 boundary KPI1 cannot discriminate tie rules; 2024/2025 scores exploratory.
"""
import os
for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[k]='2'
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
import kpi1_statistics_stepwise as step
import kpi1_statistics_hierarchy as hier

ROOT=Path(__file__).resolve().parent
MODEL=hier.DESIGNS['agency_notice_amount']
CUTS_FINE=[-1,1e6,3e6,1e7,2e7,3e7,5e7,1e8,3e8,1e9,np.inf]
LABEL_FINE=['100만 이하','100만~300만','300만~1천만','1천만~2천만','2천만~3천만',
            '3천만~5천만','5천만~1억','1억~3억','3억~10억','10억 초과']
DAYS_CUT=[-1,3,5,7,10,14,21,30,60,180]
DAYS_LAB=['3일 이하','3~5일','5~7일','7~10일','10~14일','14~21일','21~30일','30~60일','60일 초과']

# 사전 명시 후보, 테스트 결과로 선택하거나 변경하지 않음.
METHODS={
  '기존_공고키':[],
  '과거_세부금액률':['fine_rate'],
  '과거_제출기간률':['period_rate'],
  '금액률_후_기간률':['fine_rate','period_rate'],
  '기간률_후_금액률':['period_rate','fine_rate'],
}

def enrich(df):
    d=df.copy()
    amount=pd.to_numeric(d['bsicExpt'],errors='coerce')
    amount=amount.where(amount>=0)
    d['fine_amount_band']=pd.cut(amount, CUTS_FINE,labels=LABEL_FINE).astype('string').fillna('금액미상')
    d['fine_period_band']=pd.cut(d['bid_days'],DAYS_CUT,labels=DAYS_LAB).astype('string').fillna('기간미상')
    return d

def historic(tr,te,stages):
    # 모든 상위 fallback도 train-only. 스무딩 기준은 역사 데이터 전체 유찰률.
    return step.lookup(tr,te,stages,minimum=10,shrink=20)

def score_components(tr,te):
    return {
      'fine_rate':historic(tr,te,[['orntCode','pblancSe','fine_amount_band'],
                        ['org_group','pblancSe','fine_amount_band'],['fine_amount_band'],[]]),
      'period_rate':historic(tr,te,[['orntCode','pblancSe','fine_period_band'],
                          ['org_group','pblancSe','fine_period_band'],['fine_period_band'],[]]),
    }

def order_for(primary,keys,components,method):
    # np.lexsort: 마지막 열이 최상위 우선순위. 예측 변수의 내림차순만 사용.
    nested=[np.asarray(keys,dtype=str)]
    for key in reversed(METHODS[method]):
        nested.append(-np.asarray(components[key],dtype=float))
    nested.append(-np.asarray(primary,dtype=float))
    return np.lexsort(tuple(nested))

def calc(y,score,order):
    y=np.asarray(y,dtype=int);score=np.asarray(score,dtype=float)
    n=len(y);k=round(n*0.2);nf=int(y.sum())
    hits=int(y[order[:k]].sum())
    cut=float(score[order[k-1]])
    above=score>cut; tie=score==cut
    slots=int(k-above.sum());tn=int(tie.sum())
    limit=int(y[above].sum()+min(slots,int(y[tie].sum())))
    expectation=(float(y[above].sum())+slots*float(y[tie].mean()))*100/nf
    # KPI2: 기존 프로젝트의 qcut(row order) 규칙과 같은 방식; 점수가 같은 공고도 분위로 나뉠 수 있음.
    ranked_y=y[order[::-1]]
    groups=pd.qcut(pd.Series(np.arange(n)),10,labels=False)+1
    decile_rates=pd.DataFrame({'decile':groups,'failed':ranked_y}).groupby('decile')['failed'].mean()
    rho=float(spearmanr(decile_rates.index.to_numpy(),decile_rates.to_numpy()).statistic)
    return {'n':n,'all_failed':nf,'top20_n':k,'captured_failures':hits,'kpi2_spearman':round(rho,6),
            'kpi1_pct':round(hits*100/nf,4),
            'cut_score':cut,'strictly_above_cut_n':int(above.sum()),
            'boundary_tied_n':tn,'boundary_selected_n':slots,
            'boundary_excluded_n':tn-slots,
            'boundary_tied_failed':int(y[tie].sum()),
            'max_possible_hits_oracle':limit,
            'expected_random_tie_pct':round(expectation,4)}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--outdir',type=Path,default=ROOT/'kpi1_tiebreak_statistics_results')
    args=parser.parse_args();args.outdir.mkdir(parents=True,exist_ok=True)
    data=enrich(step.prepare(step.load_data()))
    metrics=[]
    selected_method=None
    for year in (2023,2024,2025):
        tr,te=step.split(data,year)
        primary=hier.adjust(tr,te,step.lookup(tr,te,step.BASE_STAGES,minimum=10,shrink=0),
                            MODEL,min_n=5,shrink=10)
        extra=score_components(tr,te)
        keys=te.noticeKey_local.astype(str).to_numpy()
        y=te.fail_yn.to_numpy(dtype=int)
        orders={}
        for method in METHODS:
            order=order_for(primary,keys,extra,method)
            orders[method]=order
            metrics.append({'year':year,'method':method,**calc(y,primary,order)})
        if year==2023:
            # KPI1 동률이면 KPI2가 높은 방식을 보조선정 기준으로 사용 (2023년만).
            candidates=[m for m in metrics if m['year']==2023 and m['method']!='기존_공고키']
            selected_method=sorted(candidates,key=lambda m:(-m['kpi1_pct'],-m['kpi2_spearman'],m['method']))[0]['method']
            print('2023 selected tie-break',selected_method,'(KPI1 then KPI2)',flush=True)
        chosen=selected_method
        base_set=set(orders['기존_공고키'][:round(.2*len(te))])
        chosen_set=set(orders[chosen][:round(.2*len(te))])
        added=chosen_set-base_set;removed=base_set-chosen_set
        base_rank=np.empty(len(te),int);base_rank[orders['기존_공고키']]=np.arange(1,len(te)+1)
        changed_rank=np.empty(len(te),int);changed_rank[orders[chosen]]=np.arange(1,len(te)+1)
        cutoff=primary[orders[chosen][round(.2*len(te))-1]]
        atcut=np.flatnonzero(primary==cutoff)
        detail=te.iloc[atcut][['noticeKey_local','pblancDate','orntCode','pblancSe','excutTy','cntrctMth',
                               'busiDivs','bsicExpt','amount_band','bid_days','fail_yn']].copy()
        detail['risk_score']=primary[atcut]
        detail['fine_amount_band']=te['fine_amount_band'].iloc[atcut].to_numpy()
        detail['fine_period_band']=te['fine_period_band'].iloc[atcut].to_numpy()
        detail['past_fine_amount_failure_rate']=extra['fine_rate'][atcut]
        detail['past_bid_period_failure_rate']=extra['period_rate'][atcut]
        detail['original_rank']=base_rank[atcut];detail['revised_rank']=changed_rank[atcut]
        detail['original_top20']=np.isin(atcut,list(base_set))
        detail['revised_top20']=np.isin(atcut,list(chosen_set))
        detail.sort_values('revised_rank').to_csv(args.outdir/f'{year}_boundary_ties.csv',index=False,encoding='utf-8-sig')
        print(f'YEAR {year} baseline top {int(y[list(base_set)].sum())} revised top {int(y[list(chosen_set)].sum())} |'
              f' boundary {len(atcut)} selected {int((primary>cutoff).sum())} above |'
              f' +{len(added)}/-{len(removed)} | failures added {int(y[list(added)].sum()) if added else 0}'
              f' removed {int(y[list(removed)].sum()) if removed else 0}',flush=True)
    pd.DataFrame(metrics).to_csv(args.outdir/'2023_2025_tiebreak_comparison.csv',index=False,encoding='utf-8-sig')
    (args.outdir/'selected_rule.txt').write_text('선정 기간: 2023년\n선정 방식: '+str(selected_method)+'\n'
        '선정 기준: KPI1 최대, KPI1 동률이면 KPI2 우선. 2024/2025 점수 사용하지 않음.\n'
        '한계: 2023 상위 20% 경계 동점 2건 중 1건 선정, 두 건 모두 낙찰이므로 KPI1로 변별 불가.\n'
        'KPI2 역시 qcut 행 분할이므로 동일 위험 점수의 분위 분할에 민감할 수 있음.\n',encoding='utf-8')
    print(pd.DataFrame(metrics)[['year','method','kpi1_pct','captured_failures','boundary_tied_n','boundary_excluded_n']].to_string(index=False),flush=True)

if __name__=='__main__':main()
