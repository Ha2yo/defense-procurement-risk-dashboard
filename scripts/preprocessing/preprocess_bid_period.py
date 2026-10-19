"""공고기간 분석용 데이터 생성. 기존 CLEAN 파일은 변경하지 않습니다."""
from pathlib import Path
import argparse
import sys
import pandas as pd

# 일부 Windows 콘솔이 이모지를 지원하지 않아도 출력 오류로 중단되지 않도록 처리
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(errors='replace')

# scripts/preprocessing 폴더에서 두 단계 올라간 위치를 프로젝트 루트로 사용합니다.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 공고와 결과를 연결할 공통 키 및 입력 CSV 파일명을 고정합니다.
KEY = 'noticeKey'
SOURCES = ('bid_notice_clean_20210101_20251231.csv',
           'bid_result_clean_20210101_20251231.csv')



def section(icon, title):
    """터미널에서 분석 단계가 한눈에 보이도록 구분선을 출력합니다."""
    print('\n' + '─' * 54)
    print(f'{icon} {title}')
    print('─' * 54)



def main():
    # 실행 옵션으로 입력 CLEAN 폴더와 전처리 결과 저장 폴더를 지정합니다.
    ap = argparse.ArgumentParser(description='국방조달 공고기간 분석용 전처리')
    ap.add_argument('--input-dir', type=Path, default=PROJECT_ROOT / 'data' / 'clean')
    ap.add_argument('--output-dir', type=Path, default=PROJECT_ROOT / 'data' / 'analysis' / 'bid_period')
    args = ap.parse_args()
    section('🛡️', '국방조달 공고기간 분석 | 1단계: 데이터 전처리')
    print(f'📂 CLEAN 데이터 위치 : {args.input_dir}')
    print(f'📂 분석 결과 저장 위치 : {args.output_dir}')
    print('ℹ️ 원본 CLEAN 파일은 읽기만 하며 변경하지 않습니다.')

    section('📥', '1/3  원본 CLEAN 데이터 불러오기')
    # 원본 파일이 모두 존재하는지 먼저 확인해 중간에 분석이 끊기지 않게 합니다.
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
    # 식별키의 앞자리 0 등이 사라지지 않도록 모든 열을 문자열로 읽습니다.
    notice = pd.read_csv(args.input_dir / SOURCES[0], dtype='string', encoding='utf-8-sig')
    result = pd.read_csv(args.input_dir / SOURCES[1], dtype='string', encoding='utf-8-sig')
    print(f'✅ 입찰공고 {len(notice):,}건, 입찰결과 {len(result):,}행을 불러왔습니다.')
    # 뒤에서 사용하는 열이 빠지지 않았는지 입력 데이터 구조를 검증합니다.
    for name, data, fields in [
        ('공고', notice, [KEY, 'pblancDate', 'bidPartcptRegistClosDt', 'pblancSe', 'cntrctMth']),
        ('결과', result, [KEY, 'bidResult'])
    ]:
        missing = set(fields) - set(data.columns)
        if missing:
            raise ValueError(f'{name} 필수 컬럼 누락: {sorted(missing)}')
    # 공고는 키가 유일해야 하며, 결과는 키의 결측 여부를 확인합니다.
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
    # 공고 전체를 유지하는 왼쪽 병합으로 결과 상태와 매칭 여부를 붙입니다.
    df = notice.merge(statuses, how='left', on=KEY, validate='one_to_one', indicator=True)
    df['결과매칭'] = df['_merge'].eq('both')
    df = df.drop(columns=['_merge'])

    print('📅 공고일부터 참가등록 마감일까지의 날짜 차이를 계산합니다...')
    # 공고일은 YYYYMMDD, 참가등록마감은 YYYYMMDDHHMM 형식으로 읽습니다.
    df['공고일'] = pd.to_datetime(df['pblancDate'], format='%Y%m%d', errors='coerce')
    df['참가등록마감'] = pd.to_datetime(df['bidPartcptRegistClosDt'], format='%Y%m%d%H%M', errors='coerce')
    # 공고일자와 등록마감일자의 날짜 차이(시각은 공고일에 존재하지 않으므로 일 단위).
    df['공고기간_일'] = (df['참가등록마감'].dt.normalize() - df['공고일']).dt.days
    df['공고연도'] = df['공고일'].dt.year.astype('Int64')
    # 공고기간을 5일 이하 / 5일 초과~10일 / 10일 초과 세 구간으로 나눕니다.
    df['기간구간'] = pd.cut(df['공고기간_일'], bins=[-1, 5, 10, float('inf')],
                          labels=['5일 이하', '5일 초과~10일', '10일 초과'])

    # 취소공고, 수의계약, 음수·결측 기간은 별도의 제외 조건으로 표시합니다.
    cancel = df['pblancSe'].eq('취소공고').fillna(False)
    private = df['cntrctMth'].eq('수의계약').fillna(False)
    valid_duration = df['공고기간_일'].ge(0).fillna(False)
    # 결과미확인/판단보류/기타 결과값은 0으로 치환하지 않는다.
    ok = df['결과상태'].isin(['유찰', '낙찰', '순위확정'])
    # 유찰=1, 낙찰·순위확정=0, 그 외 결과는 결측(판단 대상 제외)으로 둡니다.
    df['is_failed'] = pd.Series(pd.NA, index=df.index, dtype='Int64')
    df.loc[df['결과상태'].eq('유찰').fillna(False), 'is_failed'] = 1
    df.loc[df['결과상태'].isin(['낙찰','순위확정']), 'is_failed'] = 0
    # 여러 배제 사유를 서로 덮어쓰지 않고 각각 독립적으로 표시.
    df['제외_취소공고'] = cancel
    df['제외_수의계약'] = private
    df['제외_결과미확정'] = ~ok
    df['제외_기간오류결측'] = ~valid_duration
    # 네 가지 필수 조건을 모두 만족한 공고만 통계 분석에 사용합니다.
    df['분석대상'] = ~cancel & ~private & ok & valid_duration
    df['상태구분'] = df['결과상태'].fillna('결과 미확인')

    section('📊', '3/3  전처리 결과 확인')
    # 전체 공고와 결과 매칭 수를 출력해 전처리 범위를 확인합니다.
    print(f'📌 전체 공고: {len(df):,}건')
    print(f'🔗 결과가 직접 연결된 공고: {int(df["결과매칭"].sum()):,}건')

    print('\n📋 공고별 결과 상태')
    # 결과 상태별 공고 건수를 확인합니다(미확인도 별도 표시).
    for status, count in df['상태구분'].value_counts(dropna=False).items():
        print(f'   • {status}: {count:,}건')

    print('\n🔎 분석에서 제외하는 사유')
    # 제외 사유는 서로 중복될 수 있어 각각의 건수를 독립적으로 출력합니다.
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

    # CLEAN 원본을 덮어쓰지 않고, 별도 분석 폴더에 CSV를 저장합니다.
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / 'bid_period_preprocessed.csv'
    # CSV에서 pandas categorical dtype과 결측 값 호환
    print('\n💾 전처리 결과를 CSV로 저장하고 있습니다...')
    df.to_csv(path, index=False, encoding='utf-8-sig')
    print(f'✅ 저장 완료: {path}')
    print('🎉 전처리가 끝났습니다! 이제 analyze_bid_period.py를 실행하면 됩니다.')


# 이 파일을 직접 실행했을 때만 전처리 작업을 시작합니다.
if __name__ == '__main__':
    main()
