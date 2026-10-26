# -*- coding: utf-8 -*-
"""데이터 항목정의서 6.3: 품목명세서 금액 검증.

단가(ordrUtpr) x 수량(dlvrQnty)과 총금액(totlAmnt)의 일치 여부를 구분한다.
불일치를 오류로 단정하지 않고 구분만 한다.
이전 금액 분석(기초금액 3구간 버전)에 있던 검증 부분을 그대로 옮긴 것이다.

실행: 프로젝트 루트에서 python scripts/preprocessing/check_item_amount.py
입력: data/clean/bid_item_clean_20210101_20251231.csv
출력: data/analysis/item_amount_check/
  - item_amount_check_summary.csv  정확 일치 / 반올림 오차 / 불일치 / 계산 불가 행 수
  - item_duplicate_key_rows.csv    사업키·조달요구번호·납품순위가 겹치는 행
"""
import os

import pandas as pd


# ============================================================
# 1. 경로 설정
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "../..", "data", "clean")
OUT_DIR = os.path.join(BASE_DIR, "../..", "data", "analysis", "item_amount_check")

ITEM_FILE = os.path.join(
    DATA_DIR,
    "bid_item_clean_20210101_20251231.csv",
)


# ============================================================
# 2. 품목명세서 불러오기
# ============================================================
def load_item():
    item = pd.read_csv(
        ITEM_FILE,
        dtype="string",
        encoding="utf-8-sig",
        usecols=[
            "businessKey", "prcmRnum", "dlvrPrrk",
            "ordrUtpr", "dlvrQnty", "totlAmnt",
        ],
        keep_default_na=False,
    )
    for col in item.columns:
        item[col] = item[col].str.strip().replace("", pd.NA)

    for col in ["ordrUtpr", "dlvrQnty", "totlAmnt"]:
        item[f"{col}_num"] = pd.to_numeric(item[col], errors="coerce")

    print(f"\n품목명세서 {len(item):,}행")
    return item


# ============================================================
# 3. 단가 x 수량 vs 총금액 검증
# ============================================================
def check_item_amount(item):
    """단가 x 수량과 총금액을 비교한다. 불일치를 오류로 단정하지 않고 구분만 한다."""
    nums = ["ordrUtpr_num", "dlvrQnty_num", "totlAmnt_num"]
    computable = item[nums].notna().all(axis=1)
    diff = (item.ordrUtpr_num * item.dlvrQnty_num - item.totlAmnt_num).abs()

    status = pd.Series("계산 불가", index=item.index)
    status[computable & diff.eq(0)] = "정확 일치"
    status[computable & diff.gt(0) & diff.lt(1)] = "반올림 오차"
    status[computable & diff.ge(1)] = "불일치"

    labels = ["정확 일치", "반올림 오차", "불일치", "계산 불가"]
    summary = pd.DataFrame({
        "category": labels,
        "criterion": [
            "차이 0", "0 < 차이 < 1원", "차이 >= 1원", "단가·수량·총액 중 결측",
        ],
        "row_count": [int(status.eq(label).sum()) for label in labels],
    })
    summary.loc[len(summary)] = ["합계", "", len(item)]
    summary.to_csv(
        os.path.join(OUT_DIR, "item_amount_check_summary.csv"),
        index=False,
        encoding="utf-8-sig",
    )

    # 고유해야 하는 사업키·조달요구번호·납품순위가 겹치면 합산 전 검토 대상
    dup_keys = ["businessKey", "prcmRnum", "dlvrPrrk"]
    dup = item[item.duplicated(subset=dup_keys, keep=False)]
    dup[dup_keys + ["ordrUtpr", "dlvrQnty", "totlAmnt"]].sort_values(dup_keys).to_csv(
        os.path.join(OUT_DIR, "item_duplicate_key_rows.csv"),
        index=False,
        encoding="utf-8-sig",
    )

    print("\n[품목명세서 금액 검증]")
    print(summary.to_string(index=False))
    print(f"사업키·조달요구번호·납품순위 중복 행: {len(dup):,}건")


# ============================================================
# 4. 메인 실행 함수
# ============================================================
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    item = load_item()
    check_item_amount(item)
    print("\nCSV 저장 위치:", os.path.abspath(OUT_DIR))


if __name__ == "__main__":
    main()
