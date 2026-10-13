"""공고기간 분포 / 구간별 유찰률 / 카이제곱 독립성 검정."""
from pathlib import Path
import argparse
import pandas as pd
from scipy.stats import chi2_contingency

ORDER = ['5일 이하', '5일 초과~10일', '10일 초과']

def main():
    ap = argparse.ArgumentParser(description='공고기간 유찰률 및 카이제곱 검정')
    ap.add_argument('--input-file', type=Path, default=Path(__file__).resolve().parent / 'data' / 'analysis' / 'bid_period_preprocessed.csv')
    ap.add_argument('--output-dir', type=Path, default=Path(__file__).resolve().parent / 'data' / 'analysis' / 'period_reports')
    ap.add_argument('--start-year', type=int, default=2021)
    ap.add_argument('--end-year', type=int, default=2024,
                    help='4팀 문서의 카이제곱 검정 기간 기본값은 2021~2024')
    args = ap.parse_args()
    if args.start_year > args.end_year:
        raise ValueError('시작 연도는 종료 연도보다 늦을 수 없습니다.')
    df = pd.read_csv(args.input_file, dtype={'noticeKey':'string'})
    required = {'noticeKey','공고연도','공고기간_일','기간구간','is_failed','분석대상'}
    if required - set(df.columns):
        raise ValueError(f'전처리 파일에 누락된 컬럼: {sorted(required - set(df.columns))}')
    if df['noticeKey'].isna().any() or df['noticeKey'].duplicated().any():
        raise ValueError('전처리 파일의 공고키 중복/결측 확인 필요')
    sample = df.loc[df['분석대상'].eq(True) & df['공고연도'].between(args.start_year,args.end_year)].copy()
    if sample.empty:
        raise ValueError('지정 기간 내 분석 가능한 공고가 없습니다.')
    if not sample['is_failed'].isin([0,1]).all():
        raise ValueError('분석대상에 결과 미확정 상태가 혼입되었습니다.')

    stats = sample['공고기간_일'].describe(percentiles=[.25,.5,.75])
    desc = pd.DataFrame({'지표':['건수','평균','중앙값','표준편차','최소','1사분위수(Q1)','3사분위수(Q3)','최대'],
                         '값':[stats['count'],stats['mean'],stats['50%'],stats['std'],stats['min'],stats['25%'],stats['75%'],stats['max']]})
    contingency = pd.crosstab(sample['기간구간'], sample['is_failed']).reindex(index=ORDER, columns=[0,1], fill_value=0)
    contingency.columns = ['비유찰건수','유찰건수']
    comparison = contingency.copy()
    comparison['공고건수'] = comparison['비유찰건수'] + comparison['유찰건수']
    comparison['유찰률(%)'] = (comparison['유찰건수'] / comparison['공고건수'].replace(0, float('nan')) * 100).round(2)
    comparison = comparison[['공고건수','비유찰건수','유찰건수','유찰률(%)']]
    if (comparison['공고건수'] == 0).any():
        raise ValueError('빈 공고기간 구간이 있습니다. 구간 정의를 점검하세요.')
    chi2, p, dof, expected = chi2_contingency(contingency, correction=False)
    if (expected < 5).any():
        print('주의: 기대빈도 5 미만 셀이 있어 카이제곱 근사가 부정확할 수 있습니다.')
    cramers_v = (chi2 / (len(sample) * min(contingency.shape[0]-1, contingency.shape[1]-1))) ** .5
    test = pd.DataFrame([{'시작연도':args.start_year,'종료연도':args.end_year,'분석건수':len(sample),
                          '카이제곱통계량':chi2,'자유도':dof,'p_value':p,'최소기대빈도':expected.min(),
                          'Cramers_V':cramers_v,'유의수준_0.05': '유의' if p < .05 else '유의하지 않음'}])
    expected_df = pd.DataFrame(expected, index=ORDER,columns=['기대_비유찰','기대_유찰'])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {'period_distribution.csv':desc, 'period_failure_comparison.csv':comparison.reset_index(),
               'period_chi_square.csv':test,'period_expected_counts.csv':expected_df.reset_index(names='기간구간')}
    for name, table in outputs.items():
        table.to_csv(args.output_dir / name,index=False,encoding='utf-8-sig')
    print(f'분석연도: {args.start_year}~{args.end_year} | 분석 공고: {len(sample):,}건')
    print('\n공고기간 기술통계:\n', desc.to_string(index=False))
    print('\n공고기간 구간별 유찰률:\n',comparison.to_string())
    print(f'\nChi-square={chi2:.4f}, df={dof}, p={p:.8g}, 최소 기대빈도={expected.min():.2f}, Cramers V={cramers_v:.4f}')
    print('주의: 이는 연관성 검정이며 공고기간의 유찰 인과효과를 증명하지 않습니다.')
    print('저장 폴더:',args.output_dir)

if __name__ == '__main__':
    main()
