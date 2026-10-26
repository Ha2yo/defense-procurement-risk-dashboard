#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""기술·추론통계 기반 KPI1 변수 단계별 개선 실험.

훈련(2021~22) → 변수 선택(2023) → 고정 설계 평가(2024, 2025).
숫자는 결과 확정(낙찰/유찰) 공고, 수의계약 제외에서만 산출.
학습 결과 확인 기준은 현재 보유한 개찰일(opengDate) 대리값이므로 한계 보고 필수.
원본 및 기존 코드를 수정하지 않는다.
실행: python kpi1_statistics_stepwise.py --outdir /.../statistics_kpi1_results
"""
import os
for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[k]='2'
from pathlib import Path
import argparse
import json
import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency, spearmanr
import kpi_C_60day_comparison as base

ROOT = Path(__file__).resolve().parent
FEATURES = ['notice','execution','amount','agency','period','notice_execution','agency_notice','agency_execution']
CATEGORICAL = {
    'notice':'pblancSe', 'execution':'excutTy', 'amount':'amount_band',
    'agency':'orntCode', 'period':'bid_days_band',
    'notice_execution':'pblancSe', 'agency_notice':'orntCode',
    'agency_execution':'orntCode',
}
# each source reflects observable historical group failure rate
SOURCE_STAGES={
 'notice':[['org_group','pblancSe'],['pblancSe']],
 'execution':[['org_group','excutTy'],['excutTy']],
 'amount':[['org_group','amount_band'],['amount_band']],
 'agency':[['orntCode'],['org_group']],
 'period':[['org_group','bid_days_band'],['bid_days_band']],
 'notice_execution':[['org_group','pblancSe','excutTy'],['pblancSe','excutTy'],['org_group','pblancSe'],['pblancSe']],
 'agency_notice':[['orntCode','pblancSe'],['orntCode'],['org_group','pblancSe'],['pblancSe']],
 'agency_execution':[['orntCode','excutTy'],['orntCode'],['org_group','excutTy'],['excutTy']],
}
BASE_STAGES=[['org_group','cntrctMth','busiDivs'],['org_group','busiDivs'],['org_group'],[]]



def find_clean(filename):
    here=Path(__file__).resolve().parent
    for root in [here,Path.cwd(),here.parent,Path.cwd().parent]:
        for folder in [root,root/'data'/'clean']:
            f=folder/filename
            if f.is_file():return f
    raise FileNotFoundError(filename + ': 스크립트와 같은 폴더 또는 data/clean 폴더에 두세요')


def load_data():
    notice=base.read_clean(find_clean('bid_notice_clean_20210101_20251231.csv'))
    result=base.read_clean(find_clean('bid_result_clean_20210101_20251231.csv'))
    lab=base.merge_input(notice,result)
    lab,org_msg=base.apply_org_group(lab,None)
    keep=['noticeKey_local','org_group','fail_yn','result_date','year','pdate']
    notice['noticeKey_local']=notice[base.NOTICE_KEY].fillna('').agg('||'.join,axis=1)
    x=notice.merge(lab[keep],on='noticeKey_local',how='left',validate='one_to_one')
    x=x[~x.cntrctMth.str.contains('수의',na=False)].copy()
    x['fail_yn']=pd.to_numeric(x.fail_yn,errors='coerce')
    amount=pd.to_numeric(x.bsicExpt,errors='coerce')
    x['log_amount']=np.log1p(amount.where(amount>=0))
    deadline=pd.to_datetime(x.biddocPresentnClosDt.str.slice(0,12),format='%Y%m%d%H%M',errors='coerce')
    x['bid_days']=(deadline-x.pdate).dt.total_seconds()/86400
    x.loc[~x.bid_days.between(-1,180),'bid_days']=np.nan
    print('원본 공고',len(x),'결과확정',x.fail_yn.notna().sum(),'기관그룹:',org_msg,flush=True)
    return x


def split(x,year):
    cutoff=pd.Timestamp(year-1,12,31)
    tr=x[x.year.between(2021,year-1) & x.fail_yn.notna() & x.result_date.le(cutoff)].copy()
    te=x[x.year.eq(year) & x.fail_yn.notna()].copy()
    print('SPLIT',year,'train',len(tr),'test',len(te),flush=True)
    return tr,te


def prepare(raw):
    df=raw.copy()
    amt=np.expm1(df.log_amount.astype(float))
    df['amount_band']=pd.cut(amt,[-.1,3e7,1e8,3e8,1e9,np.inf],labels=['3천만 이하','3천만~1억','1억~3억','3억~10억','10억 초과']).astype('string').fillna('금액미상')
    days=df.bid_days
    df['bid_days_band']=pd.cut(days,[-1,3,7,14,30,180],labels=['3일 이하','4~7일','8~14일','15~30일','30일 초과']).astype('string').fillna('기간미상')
    df['pblancSe']=df['pblancSe'].fillna('미상')
    df['excutTy']=df['excutTy'].fillna('미상')
    df['orntCode']=df['orntCode'].fillna('미상')
    df['org_group']=df['org_group'].fillna('미분류')
    df['cntrctMth']=df['cntrctMth'].fillna('미상')
    df['busiDivs']=df['busiDivs'].fillna('미상')
    return df


def lookup(tr, te, tiers, minimum=10, shrink=20):
    """위험도는 과거 조합별 유찰률; 표본 부족시 상위 조합으로 대체.

    mean smoothing: observed n / (n+shrink) * own_rate + shrink/(n+shrink)* whole historical mean.
    범주형 조합 통계만 사용. train-only result stats.
    """
    prior=float(tr.fail_yn.mean())
    res=np.full(len(te),np.nan,dtype='float64')
    for keys in tiers:
        need=np.isnan(res)
        if not need.any():break
        if not keys:
            res[need]=prior
            break
        counts=(tr.groupby(keys,dropna=False).fail_yn.agg(['sum','count']).reset_index())
        counts=counts[counts['count']>=minimum]
        if counts.empty:continue
        counts['rate']=(counts['sum']+shrink*prior)/(counts['count']+shrink)
        matched=te.loc[need,keys].reset_index(drop=True).merge(counts[keys+['rate']],on=keys,how='left',validate='many_to_one')['rate'].to_numpy()
        res[np.flatnonzero(need)]=matched
    res=np.where(np.isnan(res),prior,res)
    return res


def rank_metric(y, r, noticekey):
    y=np.asarray(y,dtype=int)
    r=np.asarray(r,dtype=float)
    if not np.isfinite(r).all():raise ValueError('nonfinite risk score')
    keys=np.asarray(noticekey,dtype=str)
    # score desc, fixed-key asc; no outcome use in ties
    ix=np.lexsort((keys,-r))
    n=len(y); npos=int(y.sum()); k=round(n*.2)
    top_ix=ix[:k]
    hits=int(y[top_ix].sum()); cut=r[ix[k-1]]
    above=r>cut; tie=r==cut
    n_above=int(above.sum()); n_tie=int(tie.sum()); choose=k-n_above
    exp_hits=float(y[above].sum()+choose*(y[tie].sum()/n_tie))
    ranked_y=y[ix[::-1]]
    groups=pd.qcut(pd.Series(np.arange(n)),10,labels=False)+1
    rates=pd.DataFrame({'d':groups,'y':ranked_y}).groupby('d').y.mean()
    rho=float(spearmanr(rates.index.to_numpy(),rates.to_numpy()).statistic)
    return dict(n=n, nfail=npos, k=k, hits=hits, kpi1=100*hits/npos,
                kpi1_tie_expected=100*exp_hits/npos, tie_boundary_n=n_tie,
                kpi2=rho, unique_risks=len(np.unique(r)))


def association(tr):
    # categorical association with outcome on TRAIN ONLY. Collapse rare levels for expected counts.
    records=[]
    for col in ['org_group','cntrctMth','busiDivs','pblancSe','excutTy','amount_band','bid_days_band','orntCode']:
        s=tr[col].astype('string').fillna('미상')
        vals=s.value_counts()
        s=s.where(s.map(vals)>=40,'희소범주 통합')
        cont=pd.crosstab(s, tr.fail_yn.astype(int))
        if cont.shape[0]<2 or cont.shape[1]!=2:continue
        chi2,p,dof,ex=chi2_contingency(cont)
        v=np.sqrt(chi2/(cont.to_numpy().sum()*min(cont.shape[0]-1,cont.shape[1]-1)))
        records.append({'variable':col,'training_n':int(cont.to_numpy().sum()),'levels':cont.shape[0],
                        'chi_square':chi2,'degrees_freedom':dof,'p_value':p,'cramers_v':v,
                        'expected_under_5_cells':int((ex<5).sum()),'minimum_expected':float(ex.min())})
    return pd.DataFrame(records).sort_values('cramers_v',ascending=False)


def design_candidates():
    candidates={'base_original': {}}
    # one-at-a-time, and staged combos with meaningful no-ML weighting
    candidates.update({f'base_plus_{f}_050': {f:.5} for f in FEATURES})
    candidates.update({f'base_plus_{f}_100': {f:1} for f in FEATURES})
    for name,weights in [
        ('notice_execution',{'notice':1,'execution':1}),
        ('notice_agency',{'notice':1,'agency':1}),
        ('notice_amount',{'notice':1,'amount':1}),
        ('notice_period',{'notice':1,'period':1}),
        ('notice_agency_execution',{'notice':1,'agency':1,'execution':1}),
        ('notice_agency_amount',{'notice':1,'agency':1,'amount':1}),
        ('notice_execution_amount',{'notice':1,'execution':1,'amount':1}),
        ('notice_agency_execution_amount',{'notice':1,'agency':1,'execution':1,'amount':1}),
        ('notice_agency_execution_amount_period',{'notice':1,'agency':1,'execution':1,'amount':1,'period':1}),
        ('notice_execution_joint',{'notice_execution':2}),
        ('agency_notice_joint',{'agency_notice':2}),
        ('agency_execution_joint',{'agency_execution':2}),
        ('agency_notice_plus_amount',{'agency_notice':2,'amount':.5}),
        ('agency_notice_plus_execution',{'agency_notice':2,'execution':.5}),
        ('agency_execution_plus_notice',{'agency_execution':2,'notice':.5}),
    ]:
        candidates[name]=weights
    return candidates


def get_scores(tr,te):
    score={'base':lookup(tr,te,BASE_STAGES,minimum=10,shrink=0)}
    for f in FEATURES:
        score[f]=lookup(tr,te,SOURCE_STAGES[f],minimum=20,shrink=20)
    return score


def blended(base_scores,weights):
    r=base_scores['base'].copy()
    denom=1.
    for s,w in weights.items():
        r+=w*base_scores[s]
        denom+=w
    return r/denom


def evaluate(tr,te,year,candidates):
    src=get_scores(tr,te)
    out=[]; details=[]
    for name,weights in candidates.items():
        scores=blended(src,weights)
        met=rank_metric(te.fail_yn,scores,te.noticeKey_local)
        out.append({'eval_year':year,'method':name,'features':'+'.join(weights) if weights else 'base',
                    'weights':json.dumps(weights,ensure_ascii=False),**met})
    return pd.DataFrame(out),src


def save_ranked(te,src,weights,output):
    risk=blended(src,weights)
    tab=te[['noticeKey_local','pblancDate','ornt','pblancSe','excutTy','busiDivs','cntrctMth','bsicExpt','fail_yn']].copy()
    tab['base_risk']=src['base'];tab['risk']=risk
    tab['risk_rank']=pd.Series(np.lexsort((te.noticeKey_local.astype(str).to_numpy(),-risk))).rank().to_numpy() if False else 0
    order=np.lexsort((te.noticeKey_local.astype(str).to_numpy(),-risk))
    ranks=np.empty(len(order),dtype=int);ranks[order]=np.arange(1,len(order)+1)
    tab['risk_rank']=ranks
    tab['top_20pct']=ranks<=round(.2*len(tab))
    tab.to_csv(output,index=False,encoding='utf-8-sig')


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--outdir',type=Path,default=ROOT/'kpi1_statistics_results')
    args=ap.parse_args();args.outdir.mkdir(parents=True,exist_ok=True)
    print('Loading and labeling...',flush=True)
    df=prepare(load_data())
    tr23,te23=split(df,2023)
    print('Chi-square checks training years 2021-2022...',flush=True)
    association(tr23).to_csv(args.outdir/'associations_train_2021_2022.csv',index=False,encoding='utf-8-sig')
    candidates=design_candidates()
    print('Selection on 2023, candidates',len(candidates),flush=True)
    val,src23=evaluate(tr23,te23,2023,candidates)
    # Choose maximum KPI1 expected-tie-break, with actual fixed-key KPI as secondary. Model complexity as last priority.
    # This avoids maximizing arbitrary tie-breaking gains as the PRIMARY selection criterion.
    val['complexity']=val.weights.apply(lambda w:len(json.loads(w)))
    val=val.sort_values(['kpi1_tie_expected','kpi1','complexity','method'],ascending=[False,False,True,True]).reset_index(drop=True)
    val.to_csv(args.outdir/'2023_candidate_selection.csv',index=False,encoding='utf-8-sig')
    chosen=val.iloc[0]['method']; win=candidates[chosen]
    print('CHOSEN',chosen,'val KPI1',val.iloc[0].kpi1,'val tie expected',val.iloc[0].kpi1_tie_expected,flush=True)
    rows=[]
    for year in [2024,2025]:
        tr,te=split(df,year)
        print('Evaluating',year,flush=True)
        comp,sc=evaluate(tr,te,year,candidates)
        comp.to_csv(args.outdir/f'{year}_all_candidate_diagnostic.csv',index=False,encoding='utf-8-sig')
        rows.append(comp[comp.method.isin(['base_original',chosen])])
        save_ranked(te,sc,win,args.outdir/f'{year}_selected_risk_scores.csv')
    results=pd.concat(rows,ignore_index=True)
    results.to_csv(args.outdir/'2024_2025_selected_comparison.csv',index=False,encoding='utf-8-sig')
    config={'selected_from':'2023 only, primary tie-expected KPI1','chosen_method':chosen,'blend_weights':win,
            'base_fallback':BASE_STAGES,'other_group_fallback':SOURCE_STAGES,'base_min_n':10,
            'other_min_n':20,'other_mean_shrink_n':20,
            'amount_bins_KRW':[0,30000000,100000000,300000000,1000000000],
            'bid_days_bins':[3,7,14,30,180], 'source':'CLEAN notice/result, pure awarded/failed only',
            'training_data_date_caveat':'results cutoff proxied by opengDate',
            'information_leakage_note':'aggregations train-only and evaluation held out by calendar year',
            'time_splits':{'2023':'train 2021-22','2024':'train 2021-23','2025':'train 2021-24'}}
    (args.outdir/'selected_design.json').write_text(json.dumps(config,indent=2,ensure_ascii=False),encoding='utf-8')
    print('FINAL',results[['eval_year','method','n','nfail','hits','kpi1','kpi1_tie_expected','kpi2']].to_string(index=False),flush=True)

if __name__=='__main__':main()
