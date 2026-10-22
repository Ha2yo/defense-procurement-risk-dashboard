#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""통계 기반 세부 그룹 위험률 단계적 보정, 2023 선정/2024-25 고정 검증.
기존 CSV와 스크립트 변경 없음. 실행: python kpi1_statistics_hierarchy.py
"""
import os
for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):
    os.environ[k]='2'
import argparse,json
from pathlib import Path
import numpy as np,pandas as pd
import kpi1_statistics_stepwise as step
ROOT=Path(__file__).resolve().parent
DESIGNS={
 'base_plus_notice': [['org_group','pblancSe'],['org_group','busiDivs','cntrctMth','pblancSe']],
 'agency_notice':[['orntCode'],['orntCode','pblancSe']],
 'agency_notice_exec':[['orntCode'],['orntCode','pblancSe'],['orntCode','pblancSe','excutTy']],
 'agency_notice_amount':[['orntCode'],['orntCode','pblancSe'],['orntCode','pblancSe','amount_band']],
 'agency_notice_exec_amount':[['orntCode'],['orntCode','pblancSe'],['orntCode','pblancSe','excutTy'],['orntCode','pblancSe','excutTy','amount_band']],
 'agency_exec_notice':[['orntCode'],['orntCode','excutTy'],['orntCode','excutTy','pblancSe']],
 'agency_exec_notice_amount':[['orntCode'],['orntCode','excutTy'],['orntCode','excutTy','pblancSe'],['orntCode','excutTy','pblancSe','amount_band']],
 'group_notice_exec':[['org_group','pblancSe'],['org_group','pblancSe','excutTy'],['org_group','pblancSe','excutTy','amount_band']],
 'group_notice_method':[['org_group','pblancSe'],['org_group','pblancSe','cntrctMth'],['org_group','pblancSe','cntrctMth','excutTy']],
 'agency_notice_method':[['orntCode'],['orntCode','pblancSe'],['orntCode','pblancSe','cntrctMth']],
 'agency_notice_method_exec':[['orntCode'],['orntCode','pblancSe'],['orntCode','pblancSe','cntrctMth'],['orntCode','pblancSe','cntrctMth','excutTy']],
}


def adjust(tr,te,base,tiers,min_n=5,shrink=30):
    risk=base.copy()
    for keys in tiers:
        counts=tr.groupby(keys,dropna=False).fail_yn.agg(['sum','count']).reset_index()
        counts=counts[counts['count']>=min_n]
        if counts.empty:continue
        m=te[keys].reset_index(drop=True).merge(counts,on=keys,how='left',validate='many_to_one')
        valid=m['count'].notna().to_numpy()
        n=m['count'].fillna(0).to_numpy(float);succ=m['sum'].fillna(0).to_numpy(float)
        newer=(succ+shrink*risk)/(n+shrink)
        risk=np.where(valid,newer,risk)
    return risk


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--outdir',type=Path,default=ROOT/'kpi1_statistics_hierarchy_results')
    args=ap.parse_args();args.outdir.mkdir(parents=True,exist_ok=True)
    data=step.prepare(step.load_data())
    tr,te=step.split(data,2023)
    base=step.lookup(tr,te,step.BASE_STAGES,minimum=10,shrink=0)
    out=[]
    for design,tiers in DESIGNS.items():
        for shrink in [10,30,60]:
            for minn in [5,15]:
                risk=adjust(tr,te,base,tiers,minn,shrink)
                metric=step.rank_metric(te.fail_yn,risk,te.noticeKey_local)
                out.append({'design':design,'smooth_strength':shrink,'minimum_group_n':minn,'groups':len(tiers),**metric})
    val=pd.DataFrame(out).sort_values(['kpi1_tie_expected','kpi1','groups','design'],ascending=[False,False,True,True]).reset_index(drop=True)
    val.to_csv(args.outdir/'2023_hierarchy_selection.csv',index=False,encoding='utf-8-sig')
    selection=val.iloc[0];chosen=str(selection.design);strength=int(selection.smooth_strength);minimum=int(selection.minimum_group_n)
    print('SELECT',chosen,strength,minimum,'2023 KPI1',selection.kpi1,'tie expected',selection.kpi1_tie_expected,flush=True)
    rows=[]
    for year in [2024,2025]:
        tr,te=step.split(data,year)
        b=step.lookup(tr,te,step.BASE_STAGES,minimum=10,shrink=0)
        risk=adjust(tr,te,b,DESIGNS[chosen],minimum,strength)
        for name,r in [('original_baseline',b),('hierarchy_selected',risk)]:
            m=step.rank_metric(te.fail_yn,r,te.noticeKey_local)
            rows.append({'eval_year':year,'model':name,**m})
        out=te[['noticeKey_local','pblancDate','fail_yn','orntCode','org_group','cntrctMth','busiDivs','pblancSe','excutTy','amount_band']].copy()
        out['risk_score']=risk
        order=np.lexsort((te.noticeKey_local.astype(str).to_numpy(),-risk))
        ranks=np.empty(len(order),int);ranks[order]=np.arange(1,len(te)+1)
        out['rank']=ranks;out['top20']=ranks<=round(len(te)*.2)
        out.to_csv(args.outdir/f'{year}_hierarchy_selected_scores.csv',index=False,encoding='utf-8-sig')
    tab=pd.DataFrame(rows);tab.to_csv(args.outdir/'2024_2025_hierarchy_comparison.csv',index=False,encoding='utf-8-sig')
    (args.outdir/'selected_hierarchy.json').write_text(json.dumps({'selected_year':2023,'design':chosen,'tiers':DESIGNS[chosen],
        'smoothing_strength':strength,'min_group_n':minimum,'selection_rule':'max 2023 KPI1 boundary-tie expected',
        'group_aggregate_formula':'(group_failure_count + smooth_strength * preceding_group_risk) / (group_sample_count + smooth_strength)',
        'outcome':'explicit failed vs awarded; excludes rank/missing/mixed'},ensure_ascii=False,indent=2),encoding='utf8')
    print(tab.to_string(index=False),flush=True)
if __name__=='__main__':main()
