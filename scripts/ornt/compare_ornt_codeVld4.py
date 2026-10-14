"""품목명세서 전체로 기관별 codeVld4(소요군) 분포를 세고, 발주기관 분류(kpi_common)와 비교한다.

실행: python scripts/ornt/compare_ornt_codeVld4.py
입력: ITEM_FILE (orntCode, codeVld4 열이 있는 품목명세서 CSV)  ← 아래에서 파일 이름 확인
      data/analysis/ornt_code/orntCode_unique.csv (기관명. 없으면 기관명 칸은 비움)
출력: data/analysis/ornt_code/orntCode_codeVld4_all.csv
"""
import os
import sys

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))   # scripts/ornt
ROOT = os.path.join(BASE_DIR, "..", "..")                # 프로젝트 루트

# ▼ 40만 건 품목명세서 파일 경로 (파일 이름이 다르면 여기만 고치기)
ITEM_FILE = os.path.join(ROOT, "data", "raw", "bid_item_20210101_20251231.csv")
ORNT_FILE = os.path.join(ROOT, "data", "analysis", "ornt_code", "orntCode_unique.csv")
OUT_FILE = os.path.join(ROOT, "data", "analysis", "ornt_code", "orntCode_codeVld4_all.csv")

# 발주기관 분류 규칙 (KPI·분석과 같은 기준)
sys.path.insert(0, os.path.join(ROOT, "scripts", "kpi"))
from kpi_common import classify_org  # noqa: E402

# codeVld4 값을 발주기관 그룹 이름에 맞추기 (여기에 없는 값은 국방부직할/합동으로 봄)
CODEVLD4_GROUP = {
    "육군": "육군",
    "공군": "공군",
    "해군": "해군/해병대",
    "해병": "해군/해병대",
    "방위사업청": "방위사업청",
    "방위사업청(운영지원과)": "방위사업청",
    "ADD": "방위사업청",          # 국방과학연구소
    "기술품질원": "방위사업청",    # 국방기술품질원
}


# 1) 품목명세서 읽기 (필요한 열만)
item = pd.read_csv(ITEM_FILE, dtype="string", encoding="utf-8-sig", keep_default_na=False,
                   usecols=["orntCode", "codeVld4"], low_memory=False)
item = item.apply(lambda s: s.str.strip())
print(f"품목 {len(item):,}행, 기관 {item['orntCode'].nunique()}개")

item["group"] = item["codeVld4"].map(CODEVLD4_GROUP).fillna("국방부직할/합동")
item.loc[item["codeVld4"] == "", "group"] = ""
valid = item[item["group"] != ""]
print(f"codeVld4 빈 행: {(item['group'] == '').sum():,}행 (제외)")

# 2) 기관별 분포
dist = pd.crosstab(valid["orntCode"], valid["group"])
result = pd.DataFrame({
    "품목수": dist.sum(axis=1),
    "최다값": dist.idxmax(axis=1),
    "최다비율": (dist.max(axis=1) / dist.sum(axis=1)).round(3),
    "군종류수": dist.gt(0).sum(axis=1),          # 1이면 한 군, 2 이상이면 섞임
    "codeVld4분포": dist.apply(
        lambda r: ", ".join(f"{k} {v:,} ({v / r.sum():.1%})"
                            for k, v in r[r > 0].sort_values(ascending=False).items()),
        axis=1),
}).reset_index()

# 3) 기관명 붙이고 발주기관 분류와 비교
if os.path.exists(ORNT_FILE):
    names = pd.read_csv(ORNT_FILE, dtype="string", encoding="utf-8-sig", keep_default_na=False)
    names = names.apply(lambda s: s.str.strip()).drop_duplicates("orntCode")
    result = result.merge(names[["orntCode", "ornt"]], on="orntCode", how="left")
else:
    result["ornt"] = ""
result["ornt"] = result["ornt"].fillna("")

result["발주기관분류"] = [classify_org(n, c) for n, c in zip(result["ornt"], result["orntCode"])]
result["일치"] = (result["발주기관분류"] == result["최다값"]).map({True: "O", False: "X"})

result = result[["ornt", "orntCode", "발주기관분류", "품목수", "군종류수", "codeVld4분포", "최다값", "최다비율", "일치"]]
result = result.sort_values(["일치", "품목수"], ascending=[False, False])   # 다른 기관(X)이 위로
result.to_csv(OUT_FILE, index=False, encoding="utf-8-sig")

# 4) 요약 출력
print(f"\n기관 {len(result)}개 중 일치 {(result['일치'] == 'O').sum()}개, 다름 {(result['일치'] == 'X').sum()}개")
print("\n[발주기관 분류 × codeVld4 최다값] (기관 수)")
print(pd.crosstab(result["발주기관분류"], result["최다값"]).to_string())
print("\n[다른 기관]")
pd.set_option("display.width", 200)
pd.set_option("display.max_colwidth", 60)
print(result[result["일치"] == "X"].to_string(index=False))
print(f"\n저장: {os.path.abspath(OUT_FILE)}")