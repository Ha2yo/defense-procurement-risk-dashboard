"""KPI·분석 스크립트들이 함께 쓰는 공통 기준.

기관 분류, 데이터 필터, 위험도 부여, 정렬 규칙을 한 곳에 둔다.

[기관 분류 기준표 만들기]  이 파일을 직접 실행하면 기관 분류 기준표가 만들어진다.
    python scripts/kpi/kpi_common.py

    입력: data/kpi/bid_master_2021_2025.csv  (scripts/preprocessing/merge_bid_data.py 결과)
    출력: data/kpi/org_group_map.csv         ← 프로젝트 공통 기관 분류 기준

    모든 KPI·분석은 기관 그룹을 직접 계산하지 않고 이 표를 읽어서 붙인다.
    표는 기관코드(orntCode) 기준이며, 각자 코드에서 아래처럼 쓰면 된다.

        org = pd.read_csv('data/kpi/org_group_map.csv', encoding='utf-8-sig')
        df = df.merge(org[['orntCode', 'org_group']], on='orntCode', how='left')

    분류 규칙(classify_org)이나 확인 필요 근거(CHECK_NOTES)를 바꾸면
    이 파일을 다시 실행해 기준표를 새로 만든 뒤 KPI·분석을 실행한다.
"""
from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent.parent
DATA_KPI_DIR = PROJECT_DIR / 'data' / 'kpi'

MASTER_PATH = DATA_KPI_DIR / 'bid_master_2021_2025.csv'
LOOKUP_PATH = DATA_KPI_DIR / 'risk_lookup_table_absorbed_2021_2023.csv'
ORG_MAP_PATH = DATA_KPI_DIR / 'org_group_map.csv'

KEYS = ['org_group', 'mthd_group', 'busiDivs']
TRAIN_YEARS = (2021, 2023)
MIN_SAMPLE = 10


# ---------------------------------------------------------------------------
# 1. 기관 분류 규칙 (기준표를 만들 때만 사용)
# ---------------------------------------------------------------------------
ARMY_NAME = ('육군', '보병사단', '기갑여단', '포병여단', '화력여단', '공병여단', '기동사단', '군수지원여단', '군단')
DAPA_NAME = ('방위사업청', '국방기술품질원', '국방과학연구소')


def classify_org(name, code):
    """발주기관 그룹 분류. 위에서부터 순서대로 검사한다.

    1) 기관명에 공군 → 공군 / 해군·해병 → 해군/해병대
    2) 기관명에 방위사업청·국방기술품질원·국방과학연구소, 또는 코드 E → 방위사업청
    3) 기관코드 첫 글자: L=육군, H=공군, S=해군/해병대
    4) 기관명에 보병사단·기갑여단 등 육군 부대 유형 → 육군
    5) 나머지(코드 M·U·N·D) → 국방부직할/합동
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


def org_rule(name, code):
    """classify_org가 어떤 근거로 분류했는지 (검사 순서 동일)."""
    name, code = str(name), str(code)
    if any(k in name for k in ('공군', '해군', '해병') + DAPA_NAME):
        return '기관명'
    if code[:1] in ('E', 'L', 'H', 'S'):
        return f'코드({code[0]})'
    if any(k in name for k in ARMY_NAME):
        return '기관명(부대 유형)'
    return f'코드({code[:1]})'


# 분류에 판단이 들어간 기관과 그 근거 (기준표의 check·note 열)
CHECK_NOTES = {
    '국군제1386부대': '"국군" 직할 부대로 판단(코드 D). 입찰 품목(군 암호체계, 경계용 CCTV, 정보체계 유지보수)상 보안·정보 계열로 보이나, 외부 검색에서 의무대로 나오기도 해 소속 확인 필요',
    '한미연합군사령부': '한미 연합 사령부로 특정 군에 속하지 않아 국방부직할/합동으로 분류(코드 M)',
    '제3190부대': '기관명에 소속 단서가 없어 코드(U) 기준 국방부직할/합동으로 분류. 건수가 적어 품목으로도 판단 어려움',
    '화력여단': '이름상 육군 포병 계열 여단으로 판단했으나 코드는 국방부 계열(M). 입찰 품목(CCTV, 화상회의 장비)으로는 소속 확인 불가',
    '제2기갑여단': '이름상 육군 기갑여단으로 판단(전차 폭음장치 등 구매)했으나 코드는 국방부 계열(U)',
    '제3포병여단': '이름상 육군 포병여단으로 판단했으나 코드는 국방부 계열(M). 전체 기간 1건',
    '공중기동정찰사령부': '기관명에 군 명시가 없어 코드(H) 기준 공군으로 분류. 2021~2023년 1건',
    '국방과학연구소': '방위사업청 산하기관으로 판단해 방위사업청 그룹에 포함(코드는 M). 국방부직할/합동으로 볼 수도 있음',
    '국방기술품질원': '방위사업청 산하기관으로 판단해 방위사업청 그룹에 포함(코드는 M). 국방부직할/합동으로 볼 수도 있음',
}


# ---------------------------------------------------------------------------
# 2. 기관 분류 기준표 만들기 / 읽기 / 붙이기
# ---------------------------------------------------------------------------
def build_org_map():
    """병합 마스터의 모든 기관에 그룹을 정해 기준표(org_group_map.csv)로 저장한다."""
    if not MASTER_PATH.exists():
        raise SystemExit(
            f'❌ {MASTER_PATH} 가 없습니다. scripts/preprocessing/merge_bid_data.py 를 먼저 실행하세요.'
        )
    DATA_KPI_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(MASTER_PATH, encoding='utf-8-sig')
    df['year'] = df['opengDate'].astype(str).str[:4].astype(int)
    comp = df[~df['cntrctMth'].astype(str).str.contains('수의', na=False)]

    org_map = df[['ornt', 'orntCode']].drop_duplicates().reset_index(drop=True)
    if org_map['orntCode'].duplicated().any():
        raise SystemExit('❌ 같은 기관코드에 기관명이 여러 개 있습니다. 기준표 키를 확인하세요.')

    org_map['org_group'] = [classify_org(n, c) for n, c in zip(org_map['ornt'], org_map['orntCode'])]
    org_map['rule'] = [org_rule(n, c) for n, c in zip(org_map['ornt'], org_map['orntCode'])]

    # 건수는 경쟁성 입찰(수의계약 제외) 기준
    n_train = comp[comp['year'].between(*TRAIN_YEARS)].groupby('orntCode').size()
    n_all = comp.groupby('orntCode').size()
    org_map['n_2021_2023'] = org_map['orntCode'].map(n_train).fillna(0).astype(int)
    org_map['n_2021_2025'] = org_map['orntCode'].map(n_all).fillna(0).astype(int)

    org_map['check'] = org_map['ornt'].isin(CHECK_NOTES.keys()).map({True: '확인 필요', False: ''})
    org_map['note'] = org_map['ornt'].map(CHECK_NOTES).fillna('')

    org_map = org_map[['ornt', 'orntCode', 'org_group', 'rule', 'n_2021_2023', 'n_2021_2025', 'check', 'note']]
    org_map = org_map.sort_values(['org_group', 'n_2021_2023'], ascending=[True, False])
    org_map.to_csv(ORG_MAP_PATH, index=False, encoding='utf-8-sig')

    print(f'✨ 기관 분류 기준표 저장: {ORG_MAP_PATH} ({len(org_map)}개 기관)')
    print(org_map.groupby('org_group').agg(기관수=('ornt', 'size'), 건수_2021_2023=('n_2021_2023', 'sum')).to_string())
    return org_map


def load_org_map():
    """기관 분류 기준표를 읽는다. 없으면 먼저 만든다."""
    if not ORG_MAP_PATH.exists():
        print('ℹ️ 기관 분류 기준표가 없어 새로 만듭니다.')
        build_org_map()
    return pd.read_csv(ORG_MAP_PATH, encoding='utf-8-sig', dtype={'orntCode': str})


def attach_org_group(df):
    """기준표를 읽어 기관코드(orntCode)로 org_group 열을 붙인다."""
    org_map = load_org_map()[['orntCode', 'org_group']]
    out = df.drop(columns=['org_group'], errors='ignore').merge(org_map, on='orntCode', how='left')
    missing = out.loc[out['org_group'].isna(), 'orntCode'].unique()
    if len(missing):
        print(f'⚠️ 기준표에 없는 기관코드 {len(missing)}개: {list(missing)[:10]} → "미분류". '
              'kpi_common.py를 다시 실행해 기준표를 갱신하세요.')
        out['org_group'] = out['org_group'].fillna('미분류')
    return out


# ---------------------------------------------------------------------------
# 3. 데이터 공통 함수
# ---------------------------------------------------------------------------
def load_master():
    """마스터 파일을 읽어 기준표로 기관 그룹을 붙이고 수의계약을 제외한다."""
    df = pd.read_csv(MASTER_PATH, encoding='utf-8-sig')
    df = attach_org_group(df)
    df['mthd_group'] = df['cntrctMth']
    is_private = df['cntrctMth'].astype(str).str.contains('수의', na=False)
    return df[~is_private].copy()


def get_train(df):
    """2021~2023년 학습 데이터."""
    return df[df['year'].between(*TRAIN_YEARS)].copy()


def get_eval(df, year=2024):
    """검증 연도 데이터. 결과가 확정된 공고만 남기고 공고키를 만든다."""
    out = df[(df['year'] == year) & (df['fail_yn'].notna())].copy()
    out['notice_key'] = (
        out['pblancNo'].astype(str) + '-' + out['pblancOdr'].astype(str)
    )
    return out


# ---------------------------------------------------------------------------
# 4. 위험도 산정 단계 (룩업 테이블 생성과 2024년 위험도 부여에서 같은 규칙 사용)
# ---------------------------------------------------------------------------
STAGE_LABELS = {
    1: '1단계: 자기 조합 (10건 이상)',
    2: '2단계: 같은 기관그룹+계약방법 조합에 흡수',
    3: '3단계: 같은 기관그룹+사업구분 조합에 흡수',
    4: '4단계: 같은 기관그룹 최대 조합에 흡수',
}


def find_absorb_target(base, org, mthd, busi):
    """표본이 부족한(10건 미만, 0건 포함) 조합을 흡수할 기준 조합을 찾는다.

    base: 10건 이상 기준 조합 표 (sample_count 열 필요)
    반환: (기준 조합의 인덱스, 단계 2~4), 없으면 (None, None)
    """
    same_org = base['org_group'] == org
    candidates = [
        (2, base[same_org & (base['mthd_group'] == mthd)]),  # 기관+계약방법
        (3, base[same_org & (base['busiDivs'] == busi)]),    # 기관+사업구분
        (4, base[same_org]),                                 # 같은 기관
    ]
    for stage, cand in candidates:
        if not cand.empty:
            return cand['sample_count'].idxmax(), stage
    return None, None


def attach_risk(df_eval, df_lookup, fallback_rate=None):
    """룩업 테이블의 위험도를 붙인다.

    - 룩업에 있는 조합: 룩업의 위험도 (1단계 자기 조합 또는 2~4단계 흡수)
    - 학습 기간에 0건이라 룩업에 없는 조합: 같은 흡수 규칙(2~4단계)으로 기준 조합의 위험도
    - 흡수할 곳도 없으면 risk_status='미산정', fail_rate=NaN
      (fallback_rate를 넘기면 예전처럼 그 값으로 채운다. 비교용)

    붙는 열
      fail_rate     : 위험도
      sample_count  : 위험도 계산에 쓰인 과거 공고 수 (흡수 포함)
      own_count     : 그 조합 자체의 과거 공고 수 (0이면 학습 기간에 없던 조합)
      risk_stage    : 위험도 산정 단계 1~4
      applied_combo : 실제 위험도를 가져온 조합 (계약방법 / 사업구분)
      risk_status   : '산정' / '미산정'
    """
    if 'absorb_stage' not in df_lookup.columns:
        raise SystemExit('❌ 룩업 테이블에 absorb_stage 열이 없습니다. lookup_table_2021_2023.py를 다시 실행하세요.')

    cols = KEYS + ['fail_rate', 'sample_count', 'own_count', 'absorbed_into', 'absorb_stage']
    out = pd.merge(df_eval, df_lookup[cols], on=KEYS, how='left')
    out = out.rename(columns={'absorb_stage': 'risk_stage'})
    out['own_count'] = out['own_count'].fillna(0).astype(int)

    # 1단계는 자기 조합, 흡수된 조합은 흡수한 기준 조합 이름
    is_base = out['risk_stage'] == 1
    out['applied_combo'] = out['absorbed_into']
    out.loc[is_base, 'applied_combo'] = (
        out.loc[is_base, 'mthd_group'].astype(str) + ' / ' + out.loc[is_base, 'busiDivs'].astype(str)
    )
    out = out.drop(columns=['absorbed_into'])

    # 학습 기간에 없던 조합 → 같은 흡수 규칙 적용
    base = df_lookup[df_lookup['absorb_stage'] == 1].reset_index(drop=True)
    unseen = out['fail_rate'].isna()
    for key, idx in out[unseen].groupby(KEYS).groups.items():
        t, stage = find_absorb_target(base, *key)
        if t is None:
            continue
        tgt = base.loc[t]
        out.loc[idx, 'fail_rate'] = tgt['fail_rate']
        out.loc[idx, 'sample_count'] = tgt['sample_count']
        out.loc[idx, 'risk_stage'] = stage
        out.loc[idx, 'applied_combo'] = f"{tgt['mthd_group']} / {tgt['busiDivs']}"

    out['is_fallback'] = out['fail_rate'].isna()
    out['risk_status'] = out['is_fallback'].map({True: '미산정', False: '산정'})
    out['sample_count'] = out['sample_count'].fillna(0).astype(int)
    out['risk_stage'] = out['risk_stage'].astype('Int64')
    if fallback_rate is not None:
        out['fail_rate'] = out['fail_rate'].fillna(fallback_rate)
    return out


def split_unscored(df):
    """위험도 산정 공고와 미산정 공고를 나눈다. KPI는 산정 공고만으로 계산한다.

    현재 기관그룹 5개는 모두 10건 이상 조합이 있어 미산정은 0건이다 (안전장치).
    """
    is_unscored = df['risk_status'] == '미산정'
    return df[~is_unscored].copy(), df[is_unscored].copy()


def sort_by_risk(df):
    """고위험 순 정렬 (KPI1·KPI2 공통).

    1순위 위험도 내림차순, 2순위 표본 수 내림차순, 3순위 공고키 오름차순.
    """
    return df.sort_values(
        by=['fail_rate', 'sample_count', 'notice_key'],
        ascending=[False, False, True],
    ).reset_index(drop=True)


if __name__ == '__main__':
    build_org_map()