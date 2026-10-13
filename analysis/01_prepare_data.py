# %%
"""[분석계획 2단계] 분석용 데이터 준비 (기존 KPI 파일과 별개)

분석 단위: 입찰 결과 품목 1건 = 1행
  키 = 요구년도(demandYear) + 기관코드(orntCode) + 판단번호(dcsNo) + 공고번호(pblancNo) + 차수(pblancOdr) + 품목번호(iemNo)

기존 merge_bid_data.py와 다른 점
  - 공고 파일과 결과 파일을 요구년도+기관코드+판단번호+공고번호+차수로 병합한다.
  - 발주기관 그룹을 기관코드 첫 글자 기준으로 분류한다 (기존 '기타' 그룹 해체, 방위사업청 분리).
    (공고번호+차수만으로 병합하면 번호가 재사용된 공고에 다른 공고의 사업구분이 붙는다)
  - 결과는 data-analysis/ 폴더에 새 이름으로 저장하므로 기존 KPI 결과에 영향이 없다.
"""
from pathlib import Path
import pandas as pd

try:
    BASE_DIR = Path(__file__).resolve().parent.parent
except NameError:
    BASE_DIR = Path.cwd()

RAW_DIR = BASE_DIR / 'data' / 'raw'
OUT_DIR = BASE_DIR / 'data' / 'analysis'
OUT_DIR.mkdir(parents=True, exist_ok=True)

NOTICE_PATH = RAW_DIR / 'bid_notice_20210101_20251231.csv'
RESULT_PATH = RAW_DIR / 'bid_result_20210101_20251231.csv'
OUTPUT_PATH = OUT_DIR / 'analysis_master_2021_2023.csv'

JOIN_KEY = ['demandYear', 'orntCode', 'dcsNo', 'pblancNo', 'pblancOdr']
UNIT_KEY = JOIN_KEY + ['iemNo']
YEARS = (2021, 2023)

# 순위확정 처리: 프로젝트 정의(유찰=1, 낙찰·순위확정=0)에 따라 포함한다. True로 바꾸면 제외
EXCLUDE_RANK_CONFIRMED = False


ARMY_NAME = ('육군', '보병사단', '기갑여단', '포병여단', '화력여단', '공병여단', '기동사단', '군수지원여단', '군단')
DAPA_NAME = ('방위사업청', '국방기술품질원', '국방과학연구소')


def classify_org(name, code):
    """발주기관 그룹 분류 (기관코드 첫 글자 기준, 기관명에 군 이름이 있으면 우선).

    기관코드 첫 글자: L=육군, H=공군, S=해군/해병대, E=방위사업청, M·U·N·D=국방부직할/합동
    검토용 대응표: analysis/org_group_map.csv
    """
    name, code = str(name), str(code)
    if '공군' in name:
        return '공군'
    if '해군' in name or '해병' in name:
        return '해군/해병대'
    if any(k in name for k in DAPA_NAME) or code.startswith('E'):
        return '방위사업청'
    if code.startswith('L'):
        return '육군'
    if code.startswith('H'):
        return '공군'
    if code.startswith('S'):
        return '해군/해병대'
    if any(k in name for k in ARMY_NAME):
        return '육군'
    return '국방부직할/합동'


# 1. 원본 로드 (번호 앞자리 0이 사라지지 않도록 문자열로 읽음)
print('📂 원본 로드 중...')
notice = pd.read_csv(NOTICE_PATH, encoding='utf-8-sig', dtype=str)
result = pd.read_csv(RESULT_PATH, encoding='utf-8-sig', dtype=str)
notice.columns = notice.columns.str.strip()
result.columns = result.columns.str.strip()
print(f'  - 공고 {len(notice):,}행 / 결과 {len(result):,}행')

# 2. 키 점검: 공고 파일은 JOIN_KEY로, 결과 파일은 UNIT_KEY로 1행씩이어야 함
n_dup = notice.duplicated(JOIN_KEY).sum()
r_dup = result.duplicated(UNIT_KEY).sum()
print(f'  - 공고 파일 키 중복: {n_dup}행 / 결과 파일 단위 키 중복: {r_dup}행')
if n_dup or r_dup:
    raise SystemExit('❌ 키가 고유하지 않습니다. 병합 기준을 다시 확인하세요.')

# 3. 병합 (공고 파일에서 사업구분만 가져옴)
df = result.merge(notice[JOIN_KEY + ['busiDivs']], on=JOIN_KEY, how='left', validate='many_to_one')
print(f'  - 병합 후 {len(df):,}행, 사업구분 매칭 실패 {df["busiDivs"].isna().sum():,}행')

# 4. 파생 변수
df['year'] = df['opengDate'].str[:4].astype(int)
df['org_group'] = [classify_org(n, c) for n, c in zip(df['ornt'], df['orntCode'])]
df['fail_yn'] = (df['bidResult'] == '유찰').astype(int)

# 5. 분석 대상 필터
before = len(df)
df = df[df['year'].between(*YEARS)]
print(f'\n🧹 필터: {YEARS[0]}~{YEARS[1]}년 개찰 → {len(df):,}행 (제외 {before - len(df):,})')

before = len(df)
df = df[~df['cntrctMth'].str.contains('수의', na=False)]
print(f'🧹 필터: 수의계약 제외 → {len(df):,}행 (제외 {before - len(df):,})')

if EXCLUDE_RANK_CONFIRMED:
    before = len(df)
    df = df[df['bidResult'].isin(['낙찰', '유찰'])]
    print(f'🧹 필터: 순위확정 제외 → {len(df):,}행 (제외 {before - len(df):,})')

# 6. 저장
print('\n🏷️ 기관 그룹별 건수')
print(df['org_group'].value_counts().to_string())

cols = UNIT_KEY + ['year', 'ornt', 'org_group', 'cntrctMth', 'busiDivs', 'bidResult', 'fail_yn']
df = df[cols].sort_values(UNIT_KEY).reset_index(drop=True)
df.to_csv(OUTPUT_PATH, index=False, encoding='utf-8-sig')

print(f'\n✨ 분석 대상 {len(df):,}건, 유찰 {df["fail_yn"].sum():,}건 (유찰률 {df["fail_yn"].mean():.2%})')
print(f'🚀 저장: {OUTPUT_PATH}')
