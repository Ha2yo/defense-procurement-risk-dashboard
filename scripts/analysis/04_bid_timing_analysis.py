#!/usr/bin/env python3
"""A4. 공고 시기별 유찰률 분석 (Python 3.11+)

[VS Code 실행 버튼만 누르는 경우]
현재 파일을 저장소의 scripts/analysis/ 폴더에 놓고 실행합니다.
data/clean/의 두 CSV를 자동으로 읽고 data/analysis/bid_timing/에 결과를 저장합니다.

[분석 모집단]
- A4의 시기는 '공고일(pblancDate)' 기준입니다. 요구년도나 개찰일이 아닙니다.
- 주 분석: 2021~2023년 결과가 확인된 경쟁성 공고(수의계약 제외).
- 확인용: 2024년, 보조 추세: 2025년. 이들 기간을 학습에 섞지 않습니다.
- 결과 데이터는 품목별 행이 있을 수 있으므로 고유 공고키(noticeKey) 기준으로
  한 번 합친 후 분석합니다. 유찰=1, 낙찰/순위확정=0입니다.

[A1 팀원 결과와의 차이]
A1의 21,130건은 개찰일 기준 '결과 품목 행'입니다. A4의 21,092건은
공고일 기준 '고유 공고'이며 다른 모집단이므로 강제로 맞추지 않습니다.
두 기준은 a4_11_a1_a3_cohort_reconciliation.csv에서 함께 보여줍니다.

[터미널 선택 실행]
python scripts/analysis/04_bid_timing_analysis.py
python scripts/analysis/04_bid_timing_analysis.py --skip-plots
python scripts/analysis/04_bid_timing_analysis.py --notice 다른공고.csv --result 다른결과.csv
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency, chi2
from scipy.stats import norm

# __file__은 현재 Python 파일의 실제 위치입니다.
# parents[0] = statistical, parents[1] = analysis, parents[2] = 프로젝트 루트.
# VS Code의 현재 작업 디렉터리가 어디여도 같은 CSV를 찾도록 절대경로를 구성합니다.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NOTICE = PROJECT_ROOT / "data" / "clean" / "bid_notice_clean_20210101_20251231.csv"
DEFAULT_RESULT = PROJECT_ROOT / "data" / "clean" / "bid_result_clean_20210101_20251231.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "analysis" / "bid_timing"

# 실제 집계에 사용할 결과 상태. '판단보류'와 '결과 미확인'은 분모에서 제외합니다.
CONFIRMED = ["유찰", "낙찰", "순위확정"]
# 아래 값은 원본 데이터를 변형하지 않았는지 확인하는 감사 기준입니다.
# 통계값을 강제로 이 숫자에 맞추는 용도로 사용하지 않습니다.
EXPECTED = dict(notice_rows=61142, result_rows=38114, result_keys=38025,
                direct_matches=38025, 유찰=9845, 낙찰=26339, 순위확정=1839, 판단보류=2)

def read_csv(path, columns):
    """문자열로 CSV를 읽고 필수 컬럼 검증.

    사업번호·공고번호의 앞자리 0이 사라지지 않도록 문자열 dtype을 사용합니다.
    잘못된 파일이라면 부분적으로 분석을 계속하지 않고 즉시 예외를 발생시킵니다.
    """
    df = pd.read_csv(path, dtype="string", encoding="utf-8-sig", low_memory=False)
    missing = set(columns) - set(df.columns)
    if missing:
        raise ValueError(f"{path}: required columns missing: {missing}")
    return df

def classify(s):
    """품목별 여러 입찰결과를 하나의 공고 결과로 통합.

    예) 같은 noticeKey에 ['낙찰','순위확정'] -> '낙찰'
        ['유찰','낙찰'] -> '판단보류' (유찰률 분모에서 제외)
    """
    values = set(s.dropna().astype(str).str.strip()) - {""}
    if values == {"유찰"}: return "유찰"
    if values in ({"낙찰"}, {"낙찰", "순위확정"}): return "낙찰"
    if values == {"순위확정"}: return "순위확정"
    if not values: return "결과 미확인"
    return "판단보류" if "유찰" in values else "검토 필요"

def prepare(notice, result):
    """데이터를 공고키로 연결하고 분석용 날짜·결과 변수를 생성.

    공고를 기준으로 LEFT JOIN하므로 결과가 없는 공고도 데이터 품질 분석에는
    남아 있습니다. 유찰률에 사용할 때만 미확정 건을 제외합니다.
    """
    if notice.noticeKey.isna().any() or notice.noticeKey.duplicated().any():
        raise ValueError("공지 noticeKey 결측 또는 중복")
    if result.noticeKey.isna().any():
        raise ValueError("입찰결과 noticeKey 결측")
    # 동일 공고의 결과 품목 행들을 하나의 결과로 먼저 요약합니다.
    statuses = result.groupby("noticeKey")["bidResult"].agg(classify)
    n = notice.merge(statuses.rename("status"), left_on="noticeKey", right_index=True,
                     how="left", validate="one_to_one")
    n["direct_match"] = n.status.notna()
    n["status"] = n.status.fillna("결과 미확인")
    # 날짜 변환 실패는 NaT로 남겨 관련 기간 집계에서만 제외합니다.
    n["date"] = pd.to_datetime(n.pblancDate, format="%Y%m%d", errors="coerce")
    n["year"],n["month"],n["quarter"] = n.date.dt.year,n.date.dt.month,n.date.dt.quarter
    n["year_month"] = n.date.dt.strftime("%Y-%m")
    n["year_quarter"] = n.date.dt.strftime("%Y-Q") + n.quarter.astype("Int64").astype("string")
    # 전체 결과확정 모집단과 경쟁성 결과확정 모집단을 분리합니다.
    n["confirmed"] = n.status.isin(CONFIRMED)
    n["competitive"] = n.confirmed & n.cntrctMth.ne("수의계약").fillna(False)
    n["fail"] = pd.Series(pd.NA, index=n.index, dtype="Int64")
    n.loc[n.confirmed,"fail"] = n.loc[n.confirmed,"status"].eq("유찰").astype(int)
    return n

def subset(n,pop,start,end):
    """모집단(pop)과 공고연도 구간을 동시에 적용해 분석 표본 반환."""
    valid = n.confirmed if pop=="all_confirmed" else n.competitive
    return n.loc[valid & n.year.between(start,end).fillna(False)].copy()

def describe(n,group,pop):
    """연·월·분기 그룹별 공고 수, 결과 연결률, 실제 유찰률 산출.

    all_notices는 결과 없는 공고도 포함하지만, analysis_n은 결과확정만 포함.
    fail_rate_pct = 유찰 건수 / analysis_n × 100 입니다.
    연도별 유찰률의 단순 평균으로 분기 유찰률을 계산하지 않습니다.
    """
    base = n[n.year.between(2021,2025).fillna(False)]
    agg = base.groupby(group,dropna=False).agg(
        all_notices=("noticeKey","size"),direct_matches=("direct_match","sum"),
        confirmed_count=("confirmed","sum"),
        unresolved=("confirmed",lambda x:int((~x).sum()))).reset_index()
    valid = base[base.confirmed if pop=="all_confirmed" else base.competitive]
    actual = valid.groupby(group,dropna=False).agg(
        analysis_n=("noticeKey","size"), fail_n=("fail","sum")).reset_index()
    out = agg.merge(actual,on=group,how="left",validate="one_to_one")
    out[["analysis_n","fail_n"]] = out[["analysis_n","fail_n"]].fillna(0).astype(int)
    out["nonfail_n"] = out.analysis_n-out.fail_n
    out["fail_rate_pct"] = 100*out.fail_n/out.analysis_n.replace(0,np.nan)
    out["connection_pct"] = 100*out.direct_matches/out.all_notices
    out["confirmation_pct"] = 100*out.confirmed_count/out.all_notices
    out["population_id"],out["period"] = pop,"2021-2025"
    return out

def chi_test(n,pop,period):
    """분기(1~4) × 유찰여부(0/1) 카이제곱 독립성 검정.

    H0: 분기와 유찰 여부가 독립이다.
    p<0.05는 '관련성이 통계적으로 감지됨'을 의미할 뿐 인과관계를 뜻하지
    않습니다. Cramér's V는 연관성 크기를 따로 보여줍니다.
    기대빈도 5 미만 셀이 있으면 경고만 남기고 결론은 신중히 해석합니다.
    """
    table = pd.crosstab(n.quarter,n.fail).reindex(columns=[0,1],fill_value=0)
    table = table.loc[table.sum(axis=1)>0]
    row = dict(population_id=pop,period=period,analysis_n=len(n),chi2=np.nan,
               dof=np.nan,p_value=np.nan,cramers_v=np.nan,min_expected=np.nan,
               expected_lt5_cells=np.nan,expected_lt5_pct=np.nan,status="not_testable")
    if table.shape[0]<2 or (table.sum(axis=0)==0).any():return row
    stat,p,dof,expected = chi2_contingency(table,correction=False)
    row.update(chi2=stat,dof=dof,p_value=p,
               cramers_v=np.sqrt(stat/(len(n)*min(table.shape[0]-1,1))),
               min_expected=expected.min(),expected_lt5_cells=int((expected<5).sum()),
               expected_lt5_pct=100*(expected<5).mean(),
               status="expected_frequency_warning" if (expected<5).any() else "ok")
    return row

def regress(n):
    """연도를 통제한 분기 효과 보조 분석: logit(fail) ~ year + quarter.

    scipy로 이항 로지스틱 회귀를 최적화합니다.
    2021년 및 1분기가 기준범주이며 OR=exp(회귀계수)입니다.
    동일 businessKey의 반복공고가 독립 관측이 아닐 수 있으므로
    사업 단위 군집강건 샌드위치 표준오차를 계산합니다.
    overall_quarter_lrt_p는 분기 항 전체의 우도비 검정 결과입니다.
    개별 계수 p-value와 검정 방법이 다름에 유의하세요.
    이 회귀는 인과 추론이나 A6 예측 KPI 평가를 대신하지 않습니다.
    """
    from scipy.optimize import minimize
    from scipy.special import expit
    x=n[["fail","year","quarter","businessKey"]].dropna().copy()
    y=x.fail.to_numpy(dtype=float)
    years=pd.get_dummies(x.year.astype(str),drop_first=True,dtype=float)
    quarters=pd.get_dummies(x.quarter.astype(str),drop_first=True,dtype=float)
    # 원-핫 인코딩으로 연도·분기를 범주형 변수로 처리하고 절편을 추가합니다.
    design=np.column_stack([np.ones(len(x)),years.to_numpy(),quarters.to_numpy()])
    reduced=design[:,:1+years.shape[1]]
    terms=["Intercept"]+[f"year_{z}" for z in years]+[f"quarter_{z}" for z in quarters]
    def fit(matrix):
        """음의 로그우도 최소화. 그래디언트를 명시해 수치 최적화를 안정화."""
        def loss(beta):
            z=matrix@beta
            return np.logaddexp(0,z).sum()-y@z
        def grad(beta):
            return matrix.T@(expit(matrix@beta)-y)
        return minimize(loss,np.zeros(matrix.shape[1]),jac=grad,method="BFGS",
                        options={"gtol":1e-5,"maxiter":1000}),loss
    full,full_loss=fit(design)
    null,null_loss=fit(reduced)
    # 연도만 사용한 모형과 연도+분기 모형을 비교한 우도비 검정입니다.
    overall_p=chi2.sf(max(0,2*(null_loss(null.x)-full_loss(full.x))),
                      design.shape[1]-reduced.shape[1])
    prob=expit(design@full.x)
    info=design.T@((prob*(1-prob))[:,None]*design)
    bread=np.linalg.pinv(info)
    residual=y-prob
    clusters=x.businessKey.astype(str).to_numpy()
    score=design*residual[:,None]
    # 동일 사업 내 여러 공고의 점수(score)를 합쳐 군집강건 분산을 계산합니다.
    summed=pd.DataFrame(score).groupby(clusters).sum().to_numpy()
    meat=summed.T@summed
    g=len(summed); n_obs=len(x); k=design.shape[1]
    correction=(g/(g-1))*((n_obs-1)/(n_obs-k)) if g>1 and n_obs>k else 1
    cov=correction*bread@meat@bread
    se=np.sqrt(np.maximum(0,np.diag(cov)))
    pvalues=2*norm.sf(np.abs(np.divide(full.x,se,out=np.full_like(full.x,np.nan),where=se>0)))
    return pd.DataFrame([dict(term=term,coefficient=beta,odds_ratio=np.exp(beta),
        ci_lower=np.exp(beta-1.96*s),ci_upper=np.exp(beta+1.96*s),
        p_value=pv,overall_quarter_lrt_p=overall_p,analysis_n=len(x),
        se_method="businessKey_cluster_sandwich",converged=bool(full.success),
        optimizer_message=str(full.message))
        for term,beta,s,pv in zip(terms,full.x,se,pvalues)])

def charts(annual,monthly,quarterly,out):
    """보고서용 그래프 6개 생성. GUI가 없어도 실행되도록 Agg 백엔드 사용."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns
    try:import koreanize_matplotlib
    except ImportError:pass
    a=annual[annual.population_id=="competitive"]
    m=monthly[monthly.population_id=="competitive"]
    q=quarterly[quarterly.population_id=="competitive"]
    def save(name):
        """현재 그림을 PNG로 저장한 후 닫아 메모리 누수를 방지."""
        plt.tight_layout();plt.savefig(out/name,dpi=170,bbox_inches="tight");plt.close()
    plt.figure(figsize=(9,4));plt.plot(a.year,a.fail_rate_pct,marker="o")
    plt.title("Annual failure rate (competitive)");plt.ylabel("Failure rate (%)")
    save("a4_annual_rate.png")
    plt.figure(figsize=(13,4));plt.plot(m.year_month,m.fail_rate_pct)
    plt.xticks(rotation=90,fontsize=6);plt.ylabel("Failure rate (%)")
    save("a4_monthly_rate.png")
    plt.figure(figsize=(8,4));sns.heatmap(q.pivot(index="year",columns="quarter",values="fail_rate_pct"),
                                       annot=True,fmt=".1f",cmap="YlOrRd")
    save("a4_quarter_heatmap.png")
    plt.figure(figsize=(8,4))
    for y,g in q.groupby("year"):plt.plot(g.quarter,g.fail_rate_pct,marker="o",label=str(y))
    plt.xticks([1,2,3,4]);plt.legend();plt.ylabel("Failure rate (%)")
    save("a4_quarter_by_year.png")
    g=q[q.year.between(2021,2023)].groupby("quarter")[["fail_n","analysis_n"]].sum()
    # Wilson 95% 신뢰구간: 단순 ±1.96*표준오차보다 비율 양끝에서 안정적.
    rate0=g.fail_n/g.analysis_n
    z=1.959963984540054
    den=1+z*z/g.analysis_n
    center=(rate0+z*z/(2*g.analysis_n))/den
    half=z*np.sqrt(rate0*(1-rate0)/g.analysis_n+z*z/(4*g.analysis_n*g.analysis_n))/den
    low,high=center-half,center+half
    rate=100*g.fail_n/g.analysis_n
    plt.figure(figsize=(7,4));plt.errorbar(g.index,rate,yerr=[rate-100*low,100*high-rate],
                                         marker="o",capsize=4)
    plt.xticks([1,2,3,4]);plt.ylabel("Failure rate (%)")
    save("a4_quarter_95ci.png")
    plt.figure(figsize=(10,4));plt.plot(q.year_quarter,q.confirmation_pct,marker="o")
    plt.xticks(rotation=90);plt.ylabel("Confirmed / all notices (%)")
    save("a4_confirmation.png")

def main():
    """입력 → 공고키 결과 통합 → 기술통계 → 검정 → 파일 저장의 실행 진입점."""
    parser=argparse.ArgumentParser(description=__doc__)
    # required=True를 사용하지 않습니다. 실행 버튼만 누르면 기본 경로를 사용하고,
    # 다른 데이터로 시험할 때만 --notice/--result를 직접 전달합니다.
    parser.add_argument("--notice",type=Path,default=DEFAULT_NOTICE,
                        help=f"입찰공고 CSV (기본: {DEFAULT_NOTICE})")
    parser.add_argument("--result",type=Path,default=DEFAULT_RESULT,
                        help=f"입찰결과 CSV (기본: {DEFAULT_RESULT})")
    parser.add_argument("--output",type=Path,default=DEFAULT_OUTPUT,
                        help=f"결과 폴더 (기본: {DEFAULT_OUTPUT})")
    parser.add_argument("--skip-plots",action="store_true")
    args=parser.parse_args()
    print("[A4] 공고 파일:",args.notice)
    print("[A4] 결과 파일:",args.result)
    print("[A4] 출력 폴더:",args.output)

    # 실제 입력 CSV는 읽기 전용으로 사용합니다. 이 스크립트가 CSV를 수정하지 않습니다.
    n0=read_csv(args.notice,["noticeKey","businessKey","pblancDate","cntrctMth"])
    # A1 비교표에도 필요한 개찰일 및 계약방법 컬럼까지 확인합니다.
    r0=read_csv(args.result,["noticeKey","businessKey","bidResult","opengDt","cntrctMth"])
    n=prepare(n0,r0)
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)

    # STEP 1. 원본 기준값과 일치하는지 검사합니다.
    # 데이터가 업데이트되면 기준값과 달라질 수 있으므로 값은 조정하지 않습니다.
    actual=dict(notice_rows=len(n0),result_rows=len(r0),result_keys=r0.noticeKey.nunique(),
                direct_matches=int(n.direct_match.sum()))
    actual.update(n.status.value_counts().to_dict())
    validation=pd.DataFrame([dict(item=k,expected=v,actual=actual.get(k,0),
                                  matches=v==actual.get(k,0)) for k,v in EXPECTED.items()])
    # STEP 2. 전체 공고 중 어느 정도에 입찰결과가 연결됐는지 품질을 살펴봅니다.
    quality=n.groupby("year",dropna=False).agg(
        all_notices=("noticeKey","size"),direct_matches=("direct_match","sum"),
        confirmed=("confirmed","sum"),competitive=("competitive","sum")).reset_index()
    quality["match_pct"]=100*quality.direct_matches/quality.all_notices
    quality["confirmed_pct"]=100*quality.confirmed/quality.all_notices
    # STEP 3. 수의계약 제외 전후의 모집단 차이를 기록합니다.
    population=quality.copy()
    population["unresolved"]=population.all_notices-population.confirmed
    population["private_confirmed"]=population.confirmed-population.competitive
    # STEP 4. 전체 결과확정 공고와 경쟁성 공고를 구분해 연·월·분기별 집계.
    annual=pd.concat([describe(n,["year"],p) for p in ("all_confirmed","competitive")],ignore_index=True)
    monthly=pd.concat([describe(n,["year","month","year_month"],p) for p in ("all_confirmed","competitive")],ignore_index=True)
    quarterly=pd.concat([describe(n,["year","quarter","year_quarter"],p) for p in ("all_confirmed","competitive")],ignore_index=True)
    # STEP 5. 2021~2023년만 주 검정에 사용합니다.
    # 2024년은 별도의 재현성 확인 기간이므로 주검정에 섞지 않습니다.
    train=subset(n,"competitive",2021,2023)
    chi_rows=[chi_test(train,"competitive","2021-2023")]
    for y in (2021,2022,2023,2024):
        chi_rows.append(chi_test(subset(n,"competitive",y,y),"competitive",str(y)))
    # 각 연도에서 유찰률이 몇 번째로 높은 분기인지 비교합니다.
    # 최고 위험분기가 해마다 같은지 확인하되 이 순위만으로 인과성을 주장하지 않습니다.
    pattern=quarterly[quarterly.population_id=="competitive"].copy()
    pattern["rank_within_year"]=pattern.groupby("year").fail_rate_pct.rank(ascending=False,method="min")
    # STEP 6. 수의계약 포함 여부와 기간을 바꾼 민감도 분석.
    sens=pd.DataFrame([chi_test(subset(n,p,a,b),p,label)
        for p in ("all_confirmed","competitive")
        for label,a,b in [("2021-2023",2021,2023),("2024",2024,2024),
                          ("2025",2025,2025),("2021-2025",2021,2025)]])
    # A1 팀원 보고서는 '개찰일 2021~2023, 결과 원본 품목 행'을 사용.
    # A4 주 분석('공고일, 고유 공고')과 혼용하지 않고 감사용으로만 비교한다.
    # STEP 7. 팀원 A1 보고서 수치 재현: 개찰일·입찰결과 '품목 행'을 사용.
    # 주 분석의 noticeKey 단위 통계에 절대 합산하거나 대체하지 않습니다.
    result_open_year=pd.to_datetime(r0.opengDt.str.slice(0,8),format="%Y%m%d",errors="coerce").dt.year
    opening_rows=r0.loc[
        result_open_year.between(2021,2023).fillna(False)
        & r0.cntrctMth.ne("수의계약").fillna(False)
    ]
    a1_baseline=pd.DataFrame([
        dict(definition="A1: opening year, result item rows, non-private",
             analysis_n=len(opening_rows),
             fail_n=int(opening_rows.bidResult.eq("유찰").sum()),
             fail_rate_pct=100*opening_rows.bidResult.eq("유찰").mean(),
             date_basis="result.opengDt",unit="item row"),
        dict(definition="A4: notice year, distinct noticeKey, non-private confirmed",
             analysis_n=len(train),fail_n=int(train.fail.sum()),
             fail_rate_pct=100*train.fail.mean(),date_basis="notice.pblancDate",
             unit="unique noticeKey"),
    ])
    # STEP 8. CSV 결과는 모두 UTF-8 BOM 인코딩으로 Excel 한글 호환성을 확보.
    datasets={
        "a4_01_data_quality.csv":quality,"a4_02_population_audit.csv":population,
        "a4_03_yearly_summary.csv":annual,"a4_04_monthly_summary.csv":monthly,
        "a4_05_quarterly_summary.csv":quarterly,"a4_06_chi_square_results.csv":pd.DataFrame(chi_rows),
        "a4_07_yearly_pattern.csv":pattern,"a4_08_logistic_results.csv":regress(train),
        "a4_09_sensitivity.csv":sens,"a4_10_validation.csv":validation}
    datasets["a4_11_a1_a3_cohort_reconciliation.csv"]=a1_baseline
    for filename,df in datasets.items():
        df.to_csv(out/filename,index=False,encoding="utf-8-sig")
        print(filename,len(df))
    n.loc[n.date.isna()].to_csv(out/"a4_invalid_dates.csv",index=False,encoding="utf-8-sig")
    # STEP 9. 재현성 검증: 유찰/비유찰 건수와 분기 합계가 원 모집단과 같아야 합니다.
    assert train.fail.notna().all()
    assert int(train.fail.sum())+int((1-train.fail).sum())==len(train)
    for y in range(2021,2026):
        q=quarterly[(quarterly.population_id=="competitive") & (quarterly.year==y)]
        assert int(q.analysis_n.sum())==len(subset(n,"competitive",y,y))
    # --skip-plots를 전달한 경우 그래프는 생략하고 CSV만 생성합니다.
    if not args.skip_plots:charts(annual,monthly,quarterly,out)
    def hash_file(path):
        h=hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda:f.read(1024*1024),b""):h.update(chunk)
        return h.hexdigest()
    # STEP 10. 누가 언제 어떤 CSV로 실행했는지 추적할 수 있게 해시 및 설정을 저장.
    metadata=dict(generated_utc=datetime.now(timezone.utc).isoformat(),
                  notice_file=str(args.notice.resolve()),result_file=str(args.result.resolve()),
                  notice_sha256=hash_file(args.notice),result_sha256=hash_file(args.result),
                  train_period="2021-2023",validation_period="2024",auxiliary_period="2025",
                  population_rule="confirmed and cntrctMth != 수의계약",
                  outcome_rule="유찰=1, 낙찰/순위확정=0, 판단보류/미확인 제외",
                  caution="2024 결과는 변수 선택에 사용하지 않음; 관찰자료의 연관성은 인과효과가 아님")
    (out/"a4_run_metadata.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"TRAIN n={len(train)}, failures={int(train.fail.sum())}, rate={100*train.fail.mean():.2f}%")
    print(pd.DataFrame(chi_rows).to_string(index=False))
    print("\nA1/A4 cohort reconciliation:")
    print(a1_baseline.to_string(index=False))
    print("Output:",out)

if __name__=="__main__":main()
