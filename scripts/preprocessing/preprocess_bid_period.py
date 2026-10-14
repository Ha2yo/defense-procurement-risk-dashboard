"""공고기간 분석용 데이터 생성. 기존 CLEAN 파일은 변경하지 않습니다."""
from pathlib import Path
import argparse
import sys
import pandas as pd

# 일부 Windows 콘솔이 이모지를 지원하지 않아도 출력 오류로 중단되지 않도록 처리
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(errors='replace')

PROJECT_ROOT = Path(__file__).resolve().parents[2]

KEY = 'noticeKey'
SOURCES = ('bid_notice_clean_20210101_20251231.csv',
           'bid_result_clean_20210101_20251231.csv')


def section(icon, title):
    """터미널에서 분석 단계가 한눈에 보이도록 구분선을 출력합니다."""
    print('\n' + '─' * 54)
    print(f'{icon} {title}')
    print('─' * 54)


def main():
    ap = argparse.ArgumentParser(description='국방조달 공고기간 분석용 전처리')
    ap.add_argument('--input-dir', type=Path, default=PROJECT_ROOT / 'data' / 'clean')
    ap.add_argument('--output-dir', type=Path, default=PROJECT_ROOT / 'data' / 'analysis' / 'bid_period')
    args = ap.parse_args()
    section('🛡️', '국방조달 공고기간 분석 | 1단계: 데이터 전처리')
    print(f'📂 CLEAN 데이터 위치 : {args.input_dir}')
    print(f'📂 분석 결과 저장 위치 : {args.output_dir}')
    print('ℹ️ 원본 CLEAN 파일은 읽기만 하며 변경하지 않습니다.')

    section('📥', '1/3  원본 CLEAN 데이터 불러오기')
    files = [('입찰공고', args.input_dir / SOURCES[0]),
             ('입찰결과', args.input_dir / SOURCES[1])]
    for label, file_path in files:
        if not file_path.is_file():
            raise FileNotFoundError(
                f'❌ {label} 파일을 찾을 수 없습니다.\n'
                f'   확인할 경로: {file_path}\n'
                '   💡 --input-dir 옵션 또는 data/clean 폴더를 확인해 주세요.'
            )
    print('⏳ 입찰공고와 입찰결과 CSV 파일을 읽고 있습니다...')
    notice = pd.read_csv(args.input_dir / SOURCES[0], dtype='string', encoding='utf-8-sig')
    result = pd.read_csv(args.input_dir / SOURCES[1], dtype='string', encoding='utf-8-sig')
    print(f'✅ 입찰공고 {len(notice):,}건, 입찰결과 {len(result):,}행을 불러왔습니다.')
    for name, data, fields in [
        ('공고', notice, [KEY, 'pblancDate', 'bidPartcptRegistClosDt', 'pblancSe', 'cntrctMth']),
        ('결과', result, [KEY, 'bidResult'])
    ]:
        missing = set(fields) - set(data.columns)
        if missing:
            raise ValueError(f'{name} 필수 컬럼 누락: {sorted(missing)}')
    if notice[KEY].isna().any() or notice[KEY].duplicated().any():
        raise ValueError('공고 CLEAN의 noticeKey가 비어 있거나 중복입니다. 임의 중복 제거 없이 중단합니다.')
    if result[KEY].isna().any():
        raise ValueError('결과 CLEAN에 noticeKey 결측이 있습니다.')

    section('🔗', '2/3  공고와 결과 연결 및 기간 계산')
    print('⏳ 공고키(noticeKey)를 기준으로 결과 상태를 정리합니다...')
    # 하나의 공고키에 복수 행이 존재하더라도 상태가 동일하면 1개 공고로 집계.
    # 서로 다른 결과 상태가 2개 이상이면 판단보류로 분리.
    r = result.assign(bidResult=result['bidResult'].str.strip())
    grouped = r.groupby(KEY, dropna=False)['bidResult'].agg(
        lambda s: '|'.join(sorted(set(s.dropna().astype(str)))))
    statuses = grouped.reset_index(name='결과상태목록')
    statuses['결과상태'] = statuses['결과상태목록'].map(
        lambda x: '판단보류' if not x or '|' in x else x)
    df = notice.merge(statuses, how='left', on=KEY, validate='one_to_one', indicator=True)
    df['결과매칭'] = df['_merge'].eq('both')
    df = df.drop(columns=['_merge'])

    print('📅 공고일부터 참가등록 마감일까지의 날짜 차이를 계산합니다...')
    df['공고일'] = pd.to_datetime(df['pblancDate'], format='%Y%m%d', errors='coerce')
    df['참가등록마감'] = pd.to_datetime(df['bidPartcptRegistClosDt'], format='%Y%m%d%H%M', errors='coerce')
    # 공고일자와 등록마감일자의 날짜 차이(시각은 공고일에 존재하지 않으므로 일 단위).
    df['공고기간_일'] = (df['참가등록마감'].dt.normalize() - df['공고일']).dt.days
    df['공고연도'] = df['공고일'].dt.year.astype('Int64')
    df['기간구간'] = pd.cut(df['공고기간_일'], bins=[-1, 5, 10, float('inf')],
                          labels=['5일 이하', '5일 초과~10일', '10일 초과'])

    cancel = df['pblancSe'].eq('취소공고').fillna(False)
    private = df['cntrctMth'].eq('수의계약').fillna(False)
    valid_duration = df['공고기간_일'].ge(0).fillna(False)
    # 결과미확인/판단보류/기타 결과값은 0으로 치환하지 않는다.
    ok = df['결과상태'].isin(['유찰', '낙찰', '순위확정'])
    df['is_failed'] = pd.Series(pd.NA, index=df.index, dtype='Int64')
    df.loc[df['결과상태'].eq('유찰').fillna(False), 'is_failed'] = 1
    df.loc[df['결과상태'].isin(['낙찰','순위확정']), 'is_failed'] = 0
    # 여러 배제 사유를 서로 덮어쓰지 않고 각각 독립적으로 표시.
    df['제외_취소공고'] = cancel
    df['제외_수의계약'] = private
    df['제외_결과미확정'] = ~ok
    df['제외_기간오류결측'] = ~valid_duration
    df['분석대상'] = ~cancel & ~private & ok & valid_duration
    df['상태구분'] = df['결과상태'].fillna('결과 미확인')

    section('📊', '3/3  전처리 결과 확인')
    print(f'📌 전체 공고: {len(df):,}건')
    print(f'🔗 결과가 직접 연결된 공고: {int(df["결과매칭"].sum()):,}건')

    print('\n📋 공고별 결과 상태')
    for status, count in df['상태구분'].value_counts(dropna=False).items():
        print(f'   • {status}: {count:,}건')

    print('\n🔎 분석에서 제외하는 사유')
    for col, label in [
        ('제외_취소공고', '취소 공고'),
        ('제외_수의계약', '수의계약'),
        ('제외_결과미확정', '결과 미확정 또는 미확인'),
        ('제외_기간오류결측', '공고기간 오류 또는 결측'),
    ]:
        print(f'   • {label}: {int(df[col].sum()):,}건')
    print('   ℹ️ 한 공고에 제외 사유가 여러 개 겹칠 수 있습니다.')
    print(f'\n✅ 전체 기간 기준 분석 가능한 공고: {int(df["분석대상"].sum()):,}건')
    print('⚠️ 현 분석 규칙에서는 순위확정을 비유찰(0)로 처리합니다.')

    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / 'bid_period_preprocessed.csv'
    # CSV에서 pandas categorical dtype과 결측 값 호환
    print('\n💾 전처리 결과를 CSV로 저장하고 있습니다...')
    df.to_csv(path, index=False, encoding='utf-8-sig')
    print(f'✅ 저장 완료: {path}')
    print('🎉 전처리가 끝났습니다! 이제 analyze_bid_period.py를 실행하면 됩니다.')

if __name__ == '__main__':
    main()
