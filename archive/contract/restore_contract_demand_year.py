# -*- coding: utf-8 -*-
r"""계약정보 demandYear 복원 + 낙찰공고 연결 후보 검증 (순환 참조 방지).

프로젝트 배치: <root>/scripts/preprocessing/restore_contract_demand_year.py

복원 출처: bid_item + procure_plan_clean만 사용.
입찰결과의 demandYear / bidNm / 낙찰상태는 복원 단계에서 전혀 사용하지 않음.
입찰결과는 요구연도 복원 *이후* 낙찰공고 후보 매칭과 검토 상태 판정에만 사용.
공고-계약의 실제 동일성 증빙이 아니라 '데이터 기반 후보 탐지'다.

실행: python scripts/preprocessing/restore_contract_demand_year.py
자세한 설정: python scripts/preprocessing/restore_contract_demand_year.py --help
의존성: pandas, 선택사항 lxml (XLSX 대용량 처리 가속)
"""
from __future__ import annotations

import argparse
import re
import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pandas as pd

PERIOD = '20210101_20251231'
FOUR = ['dcsNo', 'ornt', 'cntrctMth', 'bidMth']


# ============================================================================
# 1. 공통 전처리 함수
#    - 문자열 공백/판단번호 표기/연도 형식 통일
#    - 모든 보정은 조회·분석용이며 원본의 의미를 임의로 바꾸지 않음
# ============================================================================

def clean(s: pd.Series) -> pd.Series:
    return s.astype('string').str.strip().replace('', pd.NA)


def normalized_dcs(s: pd.Series) -> pd.Series:
    t = clean(s).str.replace(r'^([0-9]+)\.0$', r'\1', regex=True)
    return t.mask(t.str.fullmatch(r'[0-9]+', na=False), t.str.lstrip('0').replace('', '0'))


def year(s: pd.Series) -> pd.Series:
    x = clean(s)
    return x.where(x.str.fullmatch(r'(19|20)[0-9]{2}', na=False))


def check(df: pd.DataFrame, cols: list[str], label: str):
    absent = [c for c in cols if c not in df]
    if absent:
        raise KeyError(f'{label}: 필수 컬럼 없음 {absent}')


# ============================================================================
# 2. 데이터 로딩
#    - CLEAN CSV는 모든 컬럼을 문자열로 읽어 식별번호 손실 방지
#    - bid_item은 CSV를 우선 사용하고, 없으면 XLSX에서 필요한 3열만 추출
# ============================================================================

def load_csv(path: Path, label: str) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f'{label}: {path}')
    d = pd.read_csv(path, dtype='string', encoding='utf-8-sig', low_memory=False)
    d.columns = d.columns.str.strip()
    print(f'{label}: {len(d):,}행', flush=True)
    return d


def read_item_xlsx(path: Path) -> pd.DataFrame:
    """415K행 대용량 XLSX에서 A/B/C 필드만 스트리밍 파싱."""
    try:
        from lxml import etree as parser
        accelerated = True
    except ImportError:
        parser = ET
        accelerated = False
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    with zipfile.ZipFile(path) as z:
        sheets = [s for s in z.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', s)]
        if len(sheets) != 1:
            raise ValueError('bid_item XLSX 시트가 하나여야 합니다. 여러 시트는 CSV로 변환 후 지정하세요.')
        strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            with z.open('xl/sharedStrings.xml') as f:
                iterator = parser.iterparse(f, events=('end',), tag=ns+'si') if accelerated else parser.iterparse(f, events=('end',))
                for _, elem in iterator:
                    if elem.tag != ns+'si':
                        continue
                    strings.append(''.join(t.text or '' for t in elem.iter(ns+'t')))
                    elem.clear()
                    if accelerated:
                        while elem.getprevious() is not None:
                            del elem.getparent()[0]
        data = []
        letters = None
        with z.open(sheets[0]) as f:
            iterator = parser.iterparse(f, events=('end',), tag=ns+'row') if accelerated else parser.iterparse(f, events=('end',))
            for _, row in iterator:
                if row.tag != ns+'row':
                    continue
                vals = {}
                for cell in row.iter(ns+'c'):
                    col = re.match(r'[A-Z]+', cell.attrib.get('r', ''))
                    if not col:
                        continue
                    col = col.group()
                    if letters is not None and col not in letters:
                        continue
                    v = cell.findtext(ns+'v')
                    if v is None:
                        inline = cell.find(ns+'is')
                        value = ''.join(t.text or '' for t in inline.iter(ns+'t')) if inline is not None else None
                    else:
                        value = strings[int(v)] if cell.get('t') == 's' else v
                    vals[col] = value
                if letters is None:
                    header = {value: col for col, value in vals.items()}
                    required = ['demandYear', 'dcsNo', 'orntCode']
                    missing = [k for k in required if k not in header]
                    if missing:
                        raise KeyError(f'bid_item XLSX 컬럼 누락: {missing}')
                    letters = {header[k]: k for k in required}
                else:
                    data.append({field: vals.get(col) for col, field in letters.items()})
                row.clear()
                if accelerated:
                    while row.getprevious() is not None:
                        del row.getparent()[0]
    return pd.DataFrame(data, columns=['demandYear', 'dcsNo', 'orntCode'], dtype='string')


def load_item(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f'bid_item: {path}')
    if path.suffix.lower() == '.xlsx':
        print('bid_item XLSX 3개 열 스트리밍 중...', flush=True)
        d = read_item_xlsx(path)
    elif path.suffix.lower() == '.csv':
        d = pd.read_csv(path, usecols=['demandYear', 'dcsNo', 'orntCode'], dtype='string', encoding='utf-8-sig')
    else:
        raise ValueError('bid_item은 xlsx 또는 csv만 지원')
    check(d, ['demandYear','dcsNo','orntCode'], 'bid_item')
    print(f'bid_item: {len(d):,}행', flush=True)
    return d


# ============================================================================
# 3. 요구연도 후보 집계 / 복원 (입찰결과는 아직 사용하지 않음)
#    - bid_item: 기관코드 + 정규화 판단번호 기준
#    - 조달계획: 기존 4개 계약연결키 기준
#    - 복수연도·출처 충돌은 자동 선택하지 않음
# ============================================================================

def summarize_years(d: pd.DataFrame, keys: list[str], prefix: str) -> pd.DataFrame:
    """조회키별 연도 다양성 보존. 1개만 선택 가능한 경우에도 원본 후보 수 기록."""
    usable = d.dropna(subset=keys + ['_year'])[keys + ['_year']].drop_duplicates()
    summary = usable.groupby(keys, sort=False)['_year'].agg(
        **{f'{prefix}_count': 'nunique', f'{prefix}_year': 'first'}).reset_index()
    summary[f'{prefix}_year'] = summary[f'{prefix}_year'].where(summary[f'{prefix}_count'].eq(1))
    return summary


def build_year_evidence(contracts: pd.DataFrame, item: pd.DataFrame,
                        plan: pd.DataFrame) -> pd.DataFrame:
    """입찰결과 사용 금지: 계약/품목/조달계획만으로 연도 후보 작성."""
    check(contracts, FOUR + ['contractLinkKey', 'cntrctNo', 'cntrctNm', 'cntrctDate'], '계약정보')
    check(plan, FOUR + ['orntCode', 'demandYear'], '조달계획')
    if 'demandYear' in contracts:
        raise ValueError('원본 CLEAN 계약정보를 입력해야 합니다 (이미 demandYear 존재).')

    # 기관명 → 코드 조회도 오직 조달계획에서 생성. 여러 코드이면 추정하지 않는다.
    # [3-1] 조달계획에 기록된 기관명-기관코드 관계를 참고한다.
    #        기관명에 코드가 여러 개면 추정하지 않고 조회 대상에서 제외한다.
    org = pd.DataFrame({'_ornt': clean(plan['ornt']), '_code': clean(plan['orntCode'])})
    org = org.dropna().drop_duplicates()
    org_counts = org.groupby('_ornt')['_code'].nunique()
    safe_names = set(org_counts[org_counts.eq(1)].index)
    org_unique = org.loc[org['_ornt'].isin(safe_names)].drop_duplicates('_ornt')

    c = contracts.copy()
    c['_row_id'] = range(len(c))
    c['_ornt'] = clean(c['ornt'])
    c['_dcs'] = normalized_dcs(c['dcsNo'])
    c = c.merge(org_unique, on='_ornt', how='left', validate='m:1', sort=False)

    # [3-2] bid_item: (기관코드, 판단번호)마다 서로 다른 요구연도 수를 세기
    #        item_count=1인 경우에만 item_year를 남긴다.
    i = pd.DataFrame({'_code': clean(item['orntCode']), '_dcs': normalized_dcs(item['dcsNo']),
                      '_year': year(item['demandYear'])})
    item_summary = summarize_years(i, ['_code', '_dcs'], 'item')
    c = c.merge(item_summary, on=['_code','_dcs'], how='left', validate='m:1', sort=False)

    # [3-3] 조달계획: 기존 계약연결키 4개 값별 단일 연도 후보 조사
    #        여기서 찾은 연도 역시 공고/계약 동일성을 증명하지 않는다.
    p = plan[FOUR + ['demandYear']].copy()
    p['_year'] = year(p['demandYear'])
    p = p.drop(columns='demandYear')
    plan_summary = summarize_years(p, FOUR, 'plan')
    c = c.merge(plan_summary, on=FOUR, how='left', validate='m:1', sort=False)
    if len(c) != len(contracts):
        raise AssertionError('연도 조회 과정에서 계약정보 행수가 변했습니다.')

    # [3-4] 두 출처의 후보 개수와 연도가 일치하는지 확인한다.
    #        같은 키에서 복수연도가 존재하면 보수적으로 보류한다.
    ib = c['item_count'].fillna(0).astype(int)
    ip = c['plan_count'].fillna(0).astype(int)
    both = ib.eq(1) & ip.eq(1)
    same = both & c['item_year'].eq(c['plan_year']).fillna(False)
    clash = both & ~same
    multi = ib.gt(1) | ip.gt(1)
    only_item = ib.eq(1) & ip.eq(0)
    only_plan = ip.eq(1) & ib.eq(0)

    status = pd.Series('조회없음', index=c.index, dtype='string')
    status.loc[multi] = '출처내_복수연도_보류'
    status.loc[clash] = '출처간_불일치_보류'
    status.loc[only_plan] = '조달계획_단독_추정'
    status.loc[only_item] = 'bid_item_단독_추정'
    status.loc[same] = '두출처_연도일치'
    # 조달계획 기관명이 복수코드에 해당하고 item 미확인인 경우 참고만 표기.
    bad_org = c['_ornt'].isin(set(org_counts[org_counts.gt(1)].index))
    status.loc[bad_org & ib.eq(0) & ip.eq(0)] = '기관코드_복수대응'
    c['demandYear_status'] = status

    # 출처 단일연도는 후보로 보존. 확장 키는 신뢰수준을 구분한 값으로 생성.
    # [3-5] candidate와 inferred를 분리한다.
    #        candidate = 단일 출처만 존재해도 보존하는 후보
    #        inferred  = 두 출처 일치 또는 bid_item 단독일 때만 5키 생성에 사용
    chosen = pd.Series(pd.NA, index=c.index, dtype='string')
    chosen.loc[same | only_item] = c.loc[same | only_item, 'item_year']
    chosen.loc[only_plan] = c.loc[only_plan, 'plan_year']
    c['demandYear_candidate'] = chosen
    c['demandYear_source'] = status.where(chosen.notna(), '미확정')
    # 본 키에 사용해도 되는 정책: 두 출처 일치 + bid_item 단독.
    # 조달계획 단독은 강한 식별근거가 없어 연도 후보만 보존한다.
    trusted = same | only_item
    c['demandYear_inferred'] = chosen.where(trusted)
    c['contractLinkKey_v2'] = (clean(c['contractLinkKey']) + '_' + c['demandYear_inferred']).where(
        c['demandYear_inferred'].notna() & clean(c['contractLinkKey']).notna())
    return c.sort_values('_row_id').drop(columns=['_row_id', '_ornt', '_dcs','_code']).reset_index(drop=True)


# ============================================================================
# 4. 낙찰공고와 계약정보의 연결 후보 검증
#    - 이 단계에서 처음으로 입찰결과 데이터 사용
#    - 4키 / 요구연도 포함 5키의 연결 후보 수를 비교
#    - 계약명·날짜·계약번호·키 공유 여부로 이상징후만 탐지
# ============================================================================

def simplify_title(x: object) -> str:
    if pd.isna(x):
        return ''
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', str(x)).casefold())


def verify_winners(result: pd.DataFrame, enriched: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """이 단계에서만 입찰결과를 로드·사용. 연도 복원 열을 변경하지 않음."""
    check(result, ['noticeKey','bidResult','contractLinkKey','demandYear','bidNm','opengDate'], '입찰결과')
    # [4-1] 입찰결과의 요구연도는 검증용 키를 만드는 데만 사용한다.
    #        계약정보의 inferred/candidate 연도는 수정하지 않는다.
    r = result.copy()
    r['_y'] = year(r['demandYear'])
    r['contractLinkKey_v2'] = (clean(r['contractLinkKey']) + '_' + r['_y']).where(r['_y'].notna())
    notice = r.dropna(subset=['noticeKey'])
    group = notice.groupby('noticeKey').agg(
        statuses=('bidResult',lambda x: x.nunique(dropna=False)),
        keys=('contractLinkKey_v2',lambda x: x.nunique(dropna=False)))
    invalid = group.index[group.statuses.gt(1) | group['keys'].gt(1)]
    winners = (notice[notice['bidResult'].eq('낙찰') & ~notice['noticeKey'].isin(invalid)]
               .drop_duplicates('noticeKey').copy())

    # [4-2] 기존 4키와 5키를 각각 비교하되 연결은 '후보'로만 기록한다.
    by4 = enriched.dropna(subset=['contractLinkKey']).groupby('contractLinkKey').size()
    winners['row_candidates_4key'] = winners['contractLinkKey'].map(by4).fillna(0).astype(int)
    winners['matched_4key'] = winners.row_candidates_4key.gt(0)

    candidates = enriched.dropna(subset=['contractLinkKey_v2'])[
        ['contractLinkKey_v2','cntrctNo','cntrctNm','cntrctDate','demandYear_status', 'demandYear_inferred']].copy()
    # 같은 계약번호가 여러 이력 행에 반복되어도 중복 연결 계약으로 취급하지 않는다.
    candidate_join = winners[['noticeKey','bidNm','opengDate','contractLinkKey_v2']].merge(
        candidates, on='contractLinkKey_v2', how='inner', validate='m:m')
    candidate_join['name_equal'] = (candidate_join['bidNm'].map(simplify_title)
                                    == candidate_join['cntrctNm'].map(simplify_title))
    candidate_join['open_date'] = pd.to_datetime(candidate_join['opengDate'].str[:8], format='%Y%m%d', errors='coerce')
    candidate_join['contract_date'] = pd.to_datetime(candidate_join['cntrctDate'].str[:8], format='%Y%m%d', errors='coerce')
    # 날짜 역전은 검토 신호, 오류 확정 아님.
    candidate_join['date_before_open'] = (candidate_join.contract_date.notna() & candidate_join.open_date.notna()
                                          & candidate_join.contract_date.lt(candidate_join.open_date))
    # [4-3] 공고 단위로 계약 후보의 일관성을 요약한다.
    #        같은 계약번호의 반복 이력 행이 포함될 수 있어 고유번호를 별도 계산.
    group = candidate_join.groupby('noticeKey').agg(
        candidate_rows=('noticeKey','size'),
        contract_numbers=('cntrctNo','nunique'),
        any_title_equal=('name_equal','any'),
        all_title_equal=('name_equal','all'),
        any_date_before_open=('date_before_open','any'),
        any_missing_date=('contract_date',lambda x:x.isna().any()),
    )
    winners = winners.merge(group, left_on='noticeKey', right_index=True, how='left', validate='1:1')
    for col in ['candidate_rows','contract_numbers']:
        winners[col] = winners[col].fillna(0).astype(int)
    for col in ['any_title_equal','all_title_equal','any_date_before_open','any_missing_date']:
        winners[col] = winners[col].astype('boolean').fillna(False).astype(bool)
    winners['matched_5key'] = winners.candidate_rows.gt(0)
    key_shares = winners.dropna(subset=['contractLinkKey_v2']).groupby('contractLinkKey_v2')['noticeKey'].nunique()
    winners['shared_notice_count'] = winners['contractLinkKey_v2'].map(key_shares).fillna(0).astype(int)
    # '엄격 내부 조건'은 외부 원문으로 검증된 계약 동일성과 다름.
    # [4-4] 엄격 내부 일관성: 고유 계약번호 1개, 키 비공유,
    #        명칭 전체 일치, 날짜 역전/결측 없음. 공식 동일성의 입증은 아님.
    winners['internal_consistency'] = (winners.matched_5key & winners.contract_numbers.eq(1)
                                       & winners.shared_notice_count.eq(1) & winners.all_title_equal
                                       & ~winners.any_date_before_open & ~winners.any_missing_date)
    winners['review_status'] = '4키_미연결'
    winners.loc[winners.matched_4key & ~winners.matched_5key, 'review_status'] = '연도미확정_5키보류'
    winners.loc[winners.matched_5key, 'review_status'] = '5키_후보_공식미검증'
    winners.loc[winners.internal_consistency, 'review_status'] = '내부일관성_통과_공식미검증'
    winners.loc[winners.matched_5key & ~winners.internal_consistency,'review_status'] = '이상징후_우선검토'
    return winners, candidate_join


# ============================================================================
# 5. 보고서 생성: 복원 후보와 연결 후보 발견율을 분리해 설명
# ============================================================================

def report(enriched: pd.DataFrame, winners: pd.DataFrame, n_item: int) -> str:
    total = len(winners)
    lines = ['요구연도 복원·낙찰공고 연결 후보 검증 보고서', '='*66,
             '복원 출처: bid_item + 조달계획 / 입찰결과 연도 복원 사용 금지',
             f'bid_item 행 수: {n_item:,}', f'계약정보 행 수: {len(enriched):,}',
             '[요구연도 후보 상태]']
    lines += [f'  {k}: {v:,}' for k, v in enriched.demandYear_status.value_counts().items()]
    lines += [f'연도 후보 조회(단일 출처 포함): {enriched.demandYear_candidate.notna().sum():,}',
              f'5키 생성에 사용한 연도: {enriched.demandYear_inferred.notna().sum():,}',
              '', '[입찰결과 독립 후속검사]', f'검증 대상 고유 낙찰공고: {total:,}']
    for label, col in [('4키 후보 연결', 'matched_4key'), ('5키 후보 연결','matched_5key'),
                       ('엄격 내부 일관성 통과','internal_consistency')]:
        count = int(winners[col].sum())
        lines.append(f'{label}: {count:,} ({count/total:.2%})' if total else f'{label}: 0')
    lines += [f'5키 연결 중 이상징후: {(winners.matched_5key & ~winners.internal_consistency).sum():,}',
              '주의: 모두 연결 후보 발견율이지 공식 계약 동일성 정확도가 아님.',
              '주의: 조달계획 단독 복원은 참고 후보로만 보관하며 5키에 사용하지 않음.',
              '주의: 공고명·날짜 일치는 동일 계약임을 증명하지 않음.']
    return '\n'.join(lines)+'\n'


# ============================================================================
# 6. 실행 인수 / 메인 파이프라인
#    프로젝트 루트: <root>/scripts/preprocessing/ 기준 두 단계 상위
#    처리 순서: 파일 경로 확인 -> 연도 복원 -> 입찰결과 후속 검증 -> 결과 저장
# ============================================================================

def args():
    a = argparse.ArgumentParser(description='순환 참조 없는 계약 요구연도 복원 및 5키 매칭')
    a.add_argument('--root', type=Path, help='프로젝트 루트')
    a.add_argument('--bid-item', type=Path, help='bid_item xlsx/csv')
    a.add_argument('--plan', type=Path, help='조달계획 CLEAN csv')
    a.add_argument('--contract', type=Path, help='계약정보 CLEAN csv')
    a.add_argument('--result', type=Path, help='입찰결과 CLEAN csv')
    a.add_argument('--output-dir', type=Path, help='출력 폴더 (상대경로=프로젝트 루트 기준)')
    return a.parse_args()


def main():
    a = args()
    here = Path(__file__).resolve().parent
    root = (a.root or (here.parent.parent if here.name.lower() == 'preprocessing' and here.parent.name.lower() == 'scripts' else here)).resolve()
    def path(given, default):
        p = given or Path(default)
        return (p if p.is_absolute() else root/p).resolve()
    # [6-0] 명시적 --bid-item 경로가 있으면 그대로 사용한다.
    #       지정하지 않았다면 raw 폴더의 CSV를 우선 사용하고, CSV가 없을 때 XLSX를 사용한다.
    bid_item_csv = path(None, f'data/raw/bid_item_{PERIOD}.csv')
    bid_item_xlsx = path(None, f'data/raw/bid_item_{PERIOD}.xlsx')
    if a.bid_item is not None:
        bid_item_path = path(a.bid_item, '')
    elif bid_item_csv.is_file():
        bid_item_path = bid_item_csv
    else:
        bid_item_path = bid_item_xlsx

    files = {
        'bid_item':bid_item_path,
        'plan':path(a.plan,f'data/clean/procure_plan_clean_{PERIOD}.csv'),
        'contract':path(a.contract,f'data/clean/contract_info_clean_{PERIOD}.csv'),
        'result':path(a.result,f'data/clean/bid_result_clean_{PERIOD}.csv'),
    }
    dest=path(a.output_dir,'data/analysis/contract_year_independent')
    for name,p in files.items():
        if not p.is_file():
            raise FileNotFoundError(f'{name}: {p}')
    if dest in {p.parent for p in files.values()}:
        raise ValueError('출력 위치는 원본 파일이 있는 폴더와 달라야 합니다.')
    print(f'bid_item 입력 파일: {files["bid_item"]}', flush=True)
    print('1. 계약정보, 조달계획, bid_item 로드 (입찰결과 아직 미사용)',flush=True)
    # [6-1] 복원에 사용할 계약정보, 조달계획, bid_item만 먼저 로드한다.
    contracts=load_csv(files['contract'],'계약정보')
    plan=load_csv(files['plan'],'조달계획')
    item=load_item(files['bid_item'])
    print('2. 입찰결과를 사용하지 않는 연도 복원',flush=True)
    enriched=build_year_evidence(contracts,item,plan)
    # 복원 완료 이후에야 결과 데이터 로드.
    print('3. 입찰결과 로드 및 연결 후보 검증',flush=True)
    # [6-2] 계약정보의 요구연도 복원 후에야 입찰결과를 읽는다.
    #        따라서 같은 입찰결과에서 연도를 복사한 후 재검증하는 순환을 피한다.
    results=load_csv(files['result'],'입찰결과')
    winners,candidates=verify_winners(results,enriched)
    if len(enriched)!=len(contracts) or not winners.noticeKey.is_unique:
        raise AssertionError('입력 계약 행수 또는 낙찰공고 키 유일성 위반')
    summary=report(enriched,winners,len(item))
    print(summary,flush=True)
    # [6-3] 입력 RAW/CLEAN은 그대로 유지한다. 결과만 별도 분석 폴더에 저장.
    #        동일 출력폴더에 재실행하면 과거 분석 산출물은 덮어쓸 수 있다.
    dest.mkdir(parents=True,exist_ok=True)
    enriched.to_csv(dest/'contract_info_with_year_evidence.csv',index=False,encoding='utf-8-sig')
    winners.to_csv(dest/'winner_link_audit.csv',index=False,encoding='utf-8-sig')
    candidates.to_csv(dest/'contract_candidates_audit.csv',index=False,encoding='utf-8-sig')
    enriched[enriched.demandYear_inferred.isna()].to_csv(dest/'unresolved_contract_year.csv',index=False,encoding='utf-8-sig')
    winners[winners.review_status.eq('이상징후_우선검토')].to_csv(dest/'priority_review.csv',index=False,encoding='utf-8-sig')
    (dest/'report.txt').write_text(summary,encoding='utf-8-sig')
    print('저장 완료:',dest)


if __name__=='__main__':
    main()
