"""공고기간 분포 / 구간별 유찰률 / 카이제곱 독립성 검정."""
from pathlib import Path
import argparse
import sys
import pandas as pd
from scipy.stats import chi2_contingency

# 일부 Windows 콘솔이 이모지를 지원하지 않아도 출력 오류로 중단되지 않도록 처리
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(errors='replace')

# 기본 경로는 scripts/preprocessing 기준 두 단계 위 프로젝트 폴더입니다.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 공고기간 범주의 출력 순서를 일정하게 유지합니다.
ORDER = ['5일 이하', '5일 초과~10일', '10일 초과']



def section(icon, title):
    """터미널에서 분석 단계를 구분해 출력합니다."""
    print('\n' + '─' * 54)
    print(f'{icon} {title}')
    print('─' * 54)



def main():
    # 입력 CSV, 출력 폴더, 통계를 낼 시작·종료 연도를 실행 옵션으로 받습니다.
    ap = argparse.ArgumentParser(description='공고기간 유찰률 및 카이제곱 검정')
    ap.add_argument('--input-file', type=Path, default=PROJECT_ROOT / 'data' / 'analysis' / 'bid_period' / 'bid_period_preprocessed.csv')
    ap.add_argument('--output-dir', type=Path, default=PROJECT_ROOT / 'data' / 'analysis' / 'bid_period')
    ap.add_argument('--start-year', type=int, default=2024)
    ap.add_argument('--end-year', type=int, default=2024,
                    help='4팀 문서의 카이제곱 검정 기간 기본값은 2021~2023')
    args = ap.parse_args()
    section('🛡️', '국방조달 공고기간 분석 | 2단계: 통계 분석')
    print(f'📂 분석용 데이터: {args.input_file}')
    print(f'📂 결과 저장 폴더: {args.output_dir}')
    print(f'📅 분석 대상 연도: {args.start_year}~{args.end_year}년')
    # 잘못된 연도 범위와 입력 파일 부재를 분석 전에 검사합니다.
    if args.start_year > args.end_year:
        raise ValueError('❌ 시작 연도는 종료 연도보다 늦을 수 없습니다.')
    if not args.input_file.is_file():
        raise FileNotFoundError(
            f'❌ 전처리 결과 파일을 찾지 못했습니다.\n'
            f'   확인할 경로: {args.input_file}\n'
            '   💡 먼저 02_bid_period_preprocess.py를 실행해 주세요.'
        )
    section('📥', '1/4  전처리 데이터 확인')
    print('⏳ 분석에 사용할 CSV를 읽고 있습니다...')
    # 공고 식별키가 숫자로 변환되지 않도록 문자열형으로 읽습니다.
    df = pd.read_csv(args.input_file, dtype={'noticeKey':'string'})
    print(f'✅ 전처리 데이터 {len(df):,}건을 불러왔습니다.')
    # 필수 열과 유일한 공고키가 유지됐는지 확인합니다.
    required = {'noticeKey','공고연도','공고기간_일','기간구간','is_failed','분석대상'}
    if required - set(df.columns):
        raise ValueError(f'전처리 파일에 누락된 컬럼: {sorted(required - set(df.columns))}')
    if df['noticeKey'].isna().any() or df['noticeKey'].duplicated().any():
        raise ValueError('전처리 파일의 공고키 중복/결측 확인 필요')
    # 전처리 단계가 인정한 공고 중, 지정 연도에 속하는 행만 추출합니다.
    sample = df.loc[df['분석대상'].eq(True) & df['공고연도'].between(args.start_year,args.end_year)].copy()
    print(f'🔎 선택한 연도에서 분석 조건을 충족한 공고: {len(sample):,}건')
    if sample.empty:
        raise ValueError('지정 기간 내 분석 가능한 공고가 없습니다.')
    if not sample['is_failed'].isin([0,1]).all():
        raise ValueError('분석대상에 결과 미확정 상태가 혼입되었습니다.')

    section('📏', '2/4  공고기간의 기본 통계')
    # 평균·중앙값·표준편차와 사분위수 등 공고기간 요약 통계를 구합니다.
    stats = sample['공고기간_일'].describe(percentiles=[.25,.5,.75])
    desc = pd.DataFrame({'지표':['건수','평균','중앙값','표준편차','최소','1사분위수(Q1)','3사분위수(Q3)','최대'],
                         '값':[stats['count'],stats['mean'],stats['50%'],stats['std'],stats['min'],stats['25%'],stats['75%'],stats['max']]})
    print(f'📌 평균 공고기간: {stats["mean"]:.2f}일')
    print(f'📌 중앙값: {stats["50%"]:.2f}일 | 표준편차: {stats["std"]:.2f}일')
    print(f'📌 최소~최대: {stats["min"]:.0f}~{stats["max"]:.0f}일')
    print(f'📌 1사분위수~3사분위수: {stats["25%"]:.2f}~{stats["75%"]:.2f}일')

    section('📊', '3/4  공고기간 구간별 유찰률 비교')
    # 행=공고기간 구간, 열=비유찰(0)·유찰(1)인 교차표를 만듭니다.
    contingency = pd.crosstab(sample['기간구간'], sample['is_failed']).reindex(index=ORDER, columns=[0,1], fill_value=0)
    contingency.columns = ['비유찰건수','유찰건수']
    comparison = contingency.copy()
    comparison['공고건수'] = comparison['비유찰건수'] + comparison['유찰건수']
    # 유찰률(%) = 해당 구간의 유찰건수 / 해당 구간의 공고건수 × 100.
    comparison['유찰률(%)'] = (comparison['유찰건수'] / comparison['공고건수'].replace(0, float('nan')) * 100).round(2)
    comparison = comparison[['공고건수','비유찰건수','유찰건수','유찰률(%)']]
    # 관측값이 없는 구간은 카이제곱 검정 전에 오류로 처리합니다.
    if (comparison['공고건수'] == 0).any():
        raise ValueError('빈 공고기간 구간이 있습니다. 구간 정의를 점검하세요.')
    for group, row in comparison.iterrows():
        print(f'📌 {group}')
        print(f'   전체 {int(row["공고건수"]):,}건 | 유찰 {int(row["유찰건수"]):,}건 '
              f'| 비유찰 {int(row["비유찰건수"]):,}건 | 유찰률 {row["유찰률(%)"]:.2f}%')

    section('🧪', '4/4  카이제곱 독립성 검정')
    # 구간과 유찰 여부가 독립이라는 가설을 카이제곱 검정으로 확인합니다.
    chi2, p, dof, expected = chi2_contingency(contingency, correction=False)
    # 기대빈도가 작은 셀은 카이제곱 근사 결과의 해석에 주의가 필요합니다.
    if (expected < 5).any():
        print('⚠️ 기대빈도가 5 미만인 셀이 있어 검정 결과 해석에 주의가 필요합니다.')
    # Cramér의 V는 두 범주형 변수 간 연관성의 크기를 나타냅니다.
    cramers_v = (chi2 / (len(sample) * min(contingency.shape[0]-1, contingency.shape[1]-1))) ** .5
    test = pd.DataFrame([{'시작연도':args.start_year,'종료연도':args.end_year,'분석건수':len(sample),
                          '카이제곱통계량':chi2,'자유도':dof,'p_value':p,'최소기대빈도':expected.min(),
                          'Cramers_V':cramers_v,'유의수준_0.05': '유의' if p < .05 else '유의하지 않음'}])
    # 기대빈도는 두 변수가 독립일 때 예상되는 각 칸의 건수입니다.
    expected_df = pd.DataFrame(expected, index=ORDER,columns=['기대_비유찰','기대_유찰'])
    print(f'📐 카이제곱 통계량(χ²): {chi2:.4f}')
    print(f'📐 자유도(df): {dof}')
    print(f'📐 p값: {p:.8g} | 최소 기대빈도: {expected.min():.2f}')
    print(f'📐 연관성 크기(Cramér의 V): {cramers_v:.4f}')
    # p값이 0.05보다 작으면 통계적 연관성이 있다고 판정합니다(인과성 아님).
    if p < .05:
        print('✅ 유의수준 5%에서 두 변수의 통계적 연관성이 관찰됩니다.')
    else:
        print('ℹ️ 유의수준 5%에서 통계적으로 유의한 연관성을 확인하지 못했습니다.')
    print('⚠️ 이 검정만으로 공고기간이 유찰의 원인이라고 결론낼 수는 없습니다.')
    print('⚠️ 현재 데이터에는 순위확정이 비유찰(0)에 포함됩니다.')

    args.output_dir.mkdir(parents=True, exist_ok=True)
    # 분석 연도를 모든 출력 파일명에 반영합니다.
    # 동일 기간을 다시 분석하더라도 _v2, _v3 등을 붙여 기존 파일을 보존합니다.
    # 기본 통계·유찰률·검정 결과·기대빈도를 각각 CSV로 출력합니다.
    tables = {
        'period_distribution': desc,
        'period_failure_comparison': comparison.reset_index(),
        'period_chi_square': test,
        'period_expected_counts': expected_df.reset_index(names='기간구간'),
    }
    descriptions = {
        'period_distribution': '공고기간 기본 통계',
        'period_failure_comparison': '기간 구간별 유찰률',
        'period_chi_square': '카이제곱 검정 결과',
        'period_expected_counts': '카이제곱 기대빈도',
    }
    year_suffix = f'{args.start_year}_{args.end_year}'
    version = 1
    while True:
        run_suffix = year_suffix if version == 1 else f'{year_suffix}_v{version}'
        filenames = {name: f'{name}_{run_suffix}.csv' for name in tables}
        if not any((args.output_dir / fname).exists() for fname in filenames.values()):
            break
        version += 1

    section('💾', '분석 결과 CSV 저장')
    # 생성된 파일명별로 저장하며 기존 파일은 덮어쓰지 않습니다.
    for name, table in tables.items():
        filename = filenames[name]
        # x 모드: 파일이 이미 존재하면 덮어쓰지 않고 오류를 발생시킵니다.
        with (args.output_dir / filename).open('x', encoding='utf-8-sig', newline='') as f:
            table.to_csv(f, index=False)
        print(f'✅ {descriptions[name]} 저장: {filename}')
    print(f'📂 저장 위치: {args.output_dir}')
    print(f'🎉 분석 완료! {args.start_year}~{args.end_year}년 공고 {len(sample):,}건을 분석했습니다.')


# 직접 실행할 때만 main()으로 통계 분석을 시작합니다.
if __name__ == '__main__':
    main()
