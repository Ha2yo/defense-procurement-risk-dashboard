#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""순차보정모델-1(SC-1). 공고기간을 통일한 B-2와 계산 방식이 같다.

배치 위치: 프로젝트/scripts/sc1/run_sc1.py (sc1_core.py와 같은 폴더)
기본 경로: 프로젝트/data/kpi/bid_master_2021_2025.csv -> 프로젝트/data/sc1/
"""
# 팀원용 안내: 이 파일은 경로와 실행 옵션만 담당합니다. 계산식은 sc1_core.py를 읽으세요.
# 읽는 순서: main() → sc1_core.run_model() → load_master() → score() → evaluate().
# 전체 설명: docs/SC1_CODE_GUIDE.md. run_sc1.py와 sc1_core.py는 같은 폴더에 둡니다.
from pathlib import Path
import argparse
from sc1_core import run_model


# 실행 진입점: 입력 경로를 확정한 뒤 2024년·2025년 평가를 한 번에 요청합니다.
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    # scripts/sc1/run_sc1.py 기준: parents[0]=sc1, parents[1]=scripts,
    # parents[2]=프로젝트 루트입니다. 터미널의 현재 위치와 관계없이 계산합니다.
    # 예: C:/code/project_start/defense-procurement-risk-dashboard
    # --project-dir만 지정하면 아래 세 경로의 기본값이 모두 해당 프로젝트를 따릅니다.
    ap.add_argument('--project-dir', type=Path, default=Path(__file__).resolve().parents[2])
    # 개별 파일/폴더 옵션을 지정하면 해당 항목의 기본 경로보다 우선합니다.
    # 직접 지정한 상대 경로는 터미널의 현재 작업 폴더를 기준으로 해석됩니다.
    ap.add_argument('--master', type=Path)
    ap.add_argument('--mapping', type=Path)
    ap.add_argument('--outdir', type=Path)
    args = ap.parse_args()
    root = args.project_dir.expanduser().resolve()
    master = args.master or root / 'data' / 'kpi' / 'bid_master_2021_2025.csv'
    mapping = args.mapping or root / 'data' / 'analysis' / 'ornt_code' / 'orntCode_codeVld4_all.csv'
    outdir = args.outdir or root / 'data' / 'sc1'
    # 전처리는 여기서 자동 실행하지 않습니다. 먼저 merge_big_data.py로 마스터를 준비합니다.
    # 모델의 입력 검증·학습·평가·결과 저장은 sc1_core.py가 담당합니다.
    run_model('sc1', master, mapping, outdir)


# 다른 코드에서 import할 때는 실행하지 않고, 이 파일을 직접 실행할 때만 main()을 호출합니다.
if __name__ == '__main__':
    main()
