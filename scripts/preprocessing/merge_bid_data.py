#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLEAN 공고/결과 -> 순위확정만 제외한 공고 단위 마스터.

입력 원본은 읽기만 합니다. 공고의 모든 속성을 보존하고 결과는 먼저 집계합니다.
"""
from pathlib import Path
import argparse
import hashlib
import json
import pandas as pd

# 공고 식별 단위: 아래 5개 값이 모두 같아야 같은 공고입니다.
# 같은 사업이라도 공고번호(pblancNo) 또는 차수(pblancOdr)가 다르면 별도 공고입니다.
HERE = Path(__file__).resolve().parent
KEYS = ['demandYear', 'orntCode', 'dcsNo', 'pblancNo', 'pblancOdr']
# 후속 모델이 올바른 전처리 결과인지 확인하는 버전 표식입니다. 임의 변경하지 않습니다.
SCHEMA = 'rank_only_excluded_v1'


# [입력 공통 처리] 식별자는 문자열로 읽어 선행 0을 보존합니다.
# 공고키의 공백과 끝의 .0을 정리하고, 키 누락이 있으면 병합 전에 중단합니다.
def read_clean(path):
    df = pd.read_csv(path, dtype='string', encoding='utf-8-sig', low_memory=False)
    df.columns = df.columns.str.strip()
    for col in KEYS:
        if col not in df:
            raise ValueError(f'{path}: 필수 컬럼 누락: {col}')
        df[col] = df[col].str.strip().str.replace(r'\.0$', '', regex=True)
    if df[KEYS].isna().any().any() or df[KEYS].eq('').any().any():
        raise ValueError(f'{path}: 공고키에 결측/빈 문자열이 있습니다.')
    return df


# [공고 결과 판정] a=낙찰 존재, f=유찰 존재, r=순위확정 존재 여부입니다.
# 반환값은 (대표 결과명, 유찰 정답)이며 정답 0=낙찰, 1=유찰, None=학습·평가 제외입니다.
# 기타 결과가 하나라도 있으면 우선 보류하고, 낙찰·유찰이 함께 있어도 보류합니다.
# 순위확정+유찰 → 유찰 / 순위확정+낙찰 → 낙찰 / 순위확정만 → 제외합니다.
def classify(a, f, r, unknown=False):
    """순위확정은 낙찰/유찰이 있으면 판정을 덮어쓰지 않는다."""
    if unknown:
        return '기타결과', None
    if a and f:
        return '판단보류', None
    if f:
        return '유찰', 1
    if a:
        return '낙찰', 0
    if r:
        return '순위확정만', None
    return '결과미확인', None


# [전처리 본체] 결과 여러 행을 공고별로 집계한 뒤 공고 원본에 1:1로 붙입니다.
# 결과 첫 행만 남기면 낙찰+순위확정 등 혼합 결과가 사라지므로, 집계가 먼저입니다.
# 입력 CSV는 수정하지 않습니다. 같은 출력 경로의 기존 결과 파일은 실행 시 갱신됩니다.
def build_master(notice_path, result_path, output):
    notice_path, result_path, output = map(Path, (notice_path, result_path, output))
    if output.resolve() in {notice_path.resolve(), result_path.resolve()}:
        raise ValueError('출력 경로를 입력 CSV와 다르게 지정하세요.')
    notice, result = read_clean(notice_path), read_clean(result_path)
    # 두 모델과 금액·공고기간 계산에 필요한 공고 속성이 있는지 먼저 검사합니다.
    needed = {'bidNm', 'ornt', 'cntrctMth', 'busiDivs', 'pblancDate',
              'pblancSe', 'bsicExpt', 'bidPartcptRegistClosDt'}
    if needed - set(notice):
        raise ValueError(f'입찰공고 필수 컬럼 누락: {sorted(needed-set(notice))}')
    if not {'bidResult', 'opengDate'}.issubset(result):
        raise ValueError('입찰결과에 bidResult와 opengDate가 필요합니다.')
    # 서로 다른 공고 속성을 임의로 첫 행으로 덮어쓰지 않는다.
    notice = notice.drop_duplicates().copy()
    if notice.duplicated(KEYS).any():
        raise ValueError('같은 공고키에 서로 다른 공고 행이 있습니다. 원본을 확인하세요.')
    result = result.copy()
    # 결과 행별 상태를 불리언으로 바꿉니다. 품목별 여러 결과를 시간 순 전환으로 해석하지 않습니다.
    # 원본 행 순서는 기존 모델의 완전 동점 재현을 위해 별도로 기록합니다.
    status = result.bidResult.str.strip()
    result['_award'] = status.eq('낙찰').fillna(False)
    result['_fail'] = status.eq('유찰').fillna(False)
    result['_rank'] = status.eq('순위확정').fillna(False)
    result['_unknown'] = ~status.isin(['낙찰', '유찰', '순위확정'])
    result['_source_order'] = range(len(result))
    # max(불리언)는 해당 결과가 한 번이라도 있는지, sum은 해당 결과 행 수입니다.
    # result_last_date는 해당 공고의 최대 개찰일입니다. 실제 결과 공개·확정일은 아닙니다.
    # 학습 시점 제한에서 결과 가용 시점의 대용값으로 사용합니다.
    summary = result.groupby(KEYS, dropna=False, sort=False).agg(
        has_award=('_award', 'max'), has_fail=('_fail', 'max'), has_rank=('_rank', 'max'),
        unknown_result_rows=('_unknown', 'sum'), result_rows=('bidResult', 'size'),
        award_rows=('_award', 'sum'), fail_rows=('_fail', 'sum'), rank_rows=('_rank', 'sum'),
        result_last_date=('opengDate', 'max'), result_source_order=('_source_order', 'min')
    ).reset_index()
    # 대표 행은 메타데이터 보존용이다. 정답은 반드시 위의 전체 집계로 판정한다.
    metadata = [c for c in ['bidMth', 'sucbidrDecsnMth', 'opengDt', 'contractLinkKey']
                if c in result and c not in notice]
    if metadata:
        first = result.drop_duplicates(KEYS, keep='first')[KEYS + metadata]
        summary = summary.merge(first, on=KEYS, validate='one_to_one', how='left')
    # 공고의 금액, 마감일 등 전체 속성을 남긴다. 미확인은 감사표에 보존한다.
    # 공고를 기준으로 LEFT JOIN하므로 결과가 직접 연결되지 않은 공고도 여기에는 남습니다.
    # validate=one_to_one은 중복으로 공고 수가 불어나는 병합을 막습니다.
    all_notices = notice.merge(summary, on=KEYS, how='left', validate='one_to_one')
    for c in ['has_award', 'has_fail', 'has_rank']:
        all_notices[c] = all_notices[c].fillna(False).astype(bool)
    for c in ['unknown_result_rows', 'result_rows', 'award_rows', 'fail_rows', 'rank_rows']:
        all_notices[c] = all_notices[c].fillna(0).astype(int)
    # 판정에는 대표 메타데이터 행이 아닌 공고 전체의 집계 플래그를 사용합니다.
    # 결측 결과는 나중에 제외 목록에 저장하므로 조용히 버리지 않습니다.
    classified = [classify(a, f, r, u > 0) for a, f, r, u in
                  all_notices[['has_award', 'has_fail', 'has_rank', 'unknown_result_rows']].itertuples(index=False, name=None)]
    all_notices['bidResult'] = [v[0] for v in classified]
    # 여기의 미연결에는 취소공고 등도 포함된다. 문서의 '결과 미확인'과 구분한다.
    all_notices.loc[all_notices.result_rows.eq(0), 'bidResult'] = '결과직접연결없음'
    all_notices['fail_yn'] = pd.array([v[1] for v in classified], dtype='Int64')
    # 내부 고유키는 5개 식별자를 모두 포함합니다. 문서용 키(공고번호-차수)는 표시·기존 정렬용입니다.
    all_notices['noticeKey_local'] = all_notices[KEYS].agg('||'.join, axis=1)
    all_notices['notice_key_document'] = all_notices.pblancNo + '-' + all_notices.pblancOdr
    dates = pd.to_datetime(all_notices.pblancDate, format='%Y%m%d', errors='coerce')
    all_notices['year'] = dates.dt.year.astype('Int64')
    if dates.isna().any():
        raise ValueError('공고일 형식을 확인하세요. pblancDate는 YYYYMMDD여야 합니다.')
    all_notices['master_schema'] = SCHEMA
    # 정답이 0/1로 확정된 공고만 최종 마스터에 포함합니다.
    # 수의계약은 이 단계에서 보존하고, 두 모델이 같은 조건으로 각각 제외합니다.
    master = all_notices[all_notices.fail_yn.notna()].copy()
    # 원본 기존 모델과 같은 완전 동점 순서 재현에 사용한다.
    master = master.sort_values('result_source_order', kind='stable').reset_index(drop=True)
    assert not master.duplicated(KEYS).any()
    assert master.fail_yn.isin([0, 1]).all()
    # [산출물] 마스터 + 제외 공고 목록 + 공고 원본에 없는 결과키 + 병합 감사 요약을 저장합니다.
    # CSV는 Excel에서 한글을 읽기 편하도록 UTF-8 BOM(utf-8-sig)을 사용합니다.
    output.parent.mkdir(parents=True, exist_ok=True)
    master.to_csv(output, index=False, encoding='utf-8-sig')
    audit_cols = KEYS + ['noticeKey_local', 'bidNm', 'ornt', 'cntrctMth', 'pblancDate',
                        'bidResult', 'fail_yn', 'has_award', 'has_fail', 'has_rank',
                        'award_rows', 'fail_rows', 'rank_rows', 'unknown_result_rows', 'result_rows']
    all_notices[all_notices.fail_yn.isna()][audit_cols].to_csv(
        output.parent / 'excluded_notices.csv', index=False, encoding='utf-8-sig')
    # 이 표는 결과→공고 방향 미연결입니다. 앞의 결과직접연결없음은 공고→결과 방향입니다.
    # 두 목록은 기준 집합이 다르므로 건수를 서로 같은 것으로 보지 않습니다.
    unmatched = summary.merge(notice[KEYS], on=KEYS, how='left', indicator=True)
    unmatched = unmatched[unmatched['_merge'].eq('left_only')].drop(columns='_merge')
    unmatched.to_csv(output.parent / 'unmatched_result_keys.csv', index=False, encoding='utf-8-sig')
    # 입력 파일 해시(SHA-256)는 어떤 원본으로 생성했는지 재현·추적하기 위한 기록입니다.
    # state_counts_all_notices는 최종 마스터만이 아니라 공고 원본 전체의 판정 분포입니다.
    info = {'schema': SCHEMA, 'input_notice_rows': len(notice), 'input_result_rows': len(result),
            'unique_result_keys': len(summary), 'unmatched_result_keys': len(unmatched),
            'master_rows': len(master), 'master_failed': int(master.fail_yn.sum()),
            'state_counts_all_notices': all_notices.bidResult.value_counts().to_dict(),
            'private_contracts': 'preserved in master, excluded by both model scripts',
            'input_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in (notice_path, result_path)}}
    (output.parent / 'merge_audit.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'마스터 저장: {output.resolve()} / {len(master):,}공고', flush=True)
    print('전체 공고 결과 분포:', info['state_counts_all_notices'], flush=True)
    return output


# [명령행 옵션] scripts/preprocessing에 배치했을 때 parents[2]가 프로젝트 루트입니다.
# --notice/--result/--output이 있으면 각 기본 경로보다 우선합니다.
# --input-dir은 원본 폴더만 바꾸고 출력 기본 폴더는 바꾸지 않습니다.
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project-dir', type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument('--input-dir', type=Path)
    ap.add_argument('--notice', type=Path)
    ap.add_argument('--result', type=Path)
    ap.add_argument('--output', type=Path)
    args = ap.parse_args()
    project = args.project_dir.expanduser().resolve()
    clean = args.input_dir or project / 'data' / 'clean'
    notice = args.notice or clean / 'bid_notice_clean_20210101_20251231.csv'
    result = args.result or clean / 'bid_result_clean_20210101_20251231.csv'
    output = args.output or project / 'data' / 'kpi' / 'bid_master_2021_2025.csv'
    build_master(notice, result, output)


if __name__ == '__main__':
    main()
