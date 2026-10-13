"""KPI 스크립트들이 함께 쓰는 공통 함수 모음.

기관 분류, 데이터 필터, 위험도 부여, 정렬 규칙을 한 곳에 두어
룩업 테이블 생성과 KPI 검증이 항상 같은 기준을 쓰도록 한다.
"""
from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DATA_KPI_DIR = BASE_DIR / 'data-kpi'
MASTER_PATH = DATA_KPI_DIR / 'bid_master_2021_2025.csv'
LOOKUP_PATH = DATA_KPI_DIR / 'risk_lookup_table_absorbed_2021_2023.csv'

KEYS = ['org_group', 'mthd_group', 'busiDivs']
TRAIN_YEARS = (2021, 2023)
MIN_SAMPLE = 10


ARMY_NAME = ('육군', '보병사단', '기갑여단', '포병여단', '화력여단', '공병여단', '기동사단', '군수지원여단', '군단')
DAPA_NAME = ('방위사업청', '국방기술품질원', '국방과학연구소')


def classify_org(name, code):
    """발주기관 그룹 분류 (analysis/01_prepare_data.py와 같은 규칙).

    기관코드 첫 글자: L=육군, H=공군, S=해군/해병대, E=방위사업청, M·U·N·D=국방부직할/합동
    기관명에 공군·해군·해병·방위사업청이 명시되면 코드보다 우선한다.
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


def load_master():
    """마스터 파일을 읽어 그룹 컬럼을 붙이고 수의계약을 제외한다."""
    df = pd.read_csv(MASTER_PATH, encoding='utf-8-sig')
    df['org_group'] = [classify_org(n, c) for n, c in zip(df['ornt'], df['orntCode'])]
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


def attach_risk(df_eval, df_lookup, fallback_rate):
    """룩업 테이블의 위험도를 붙인다.

    학습 데이터에 없던 조합은 학습 기간 전체 유찰률(fallback_rate)로 채운다.
    """
    out = pd.merge(
        df_eval,
        df_lookup[KEYS + ['fail_rate', 'sample_count']],
        on=KEYS,
        how='left',
    )
    out['is_fallback'] = out['fail_rate'].isna()
    out['fail_rate'] = out['fail_rate'].fillna(fallback_rate)
    out['sample_count'] = out['sample_count'].fillna(0)
    return out


def sort_by_risk(df):
    """고위험 순 정렬 (KPI1·KPI2 공통).

    1순위 위험도 내림차순, 2순위 표본 수 내림차순, 3순위 공고키 오름차순.
    """
    return df.sort_values(
        by=['fail_rate', 'sample_count', 'notice_key'],
        ascending=[False, False, True],
    ).reset_index(drop=True)
