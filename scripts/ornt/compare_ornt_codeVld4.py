"""품목명세서 전체로 기관별 codeVld4(소요군) 분포를 집계한다.

실행: python scripts/ornt/compare_ornt_codeVld4.py
입력: ITEM_FILE (orntCode, codeVld4 열이 있는 품목명세서 CSV)  ← 아래에서 파일 경로 확인
      data/analysis/ornt_code/orntCode_unique.csv (기관명. 없으면 기관명 칸은 비움)
출력: data/analysis/ornt_code/orntCode_codeVld4_all.csv
"""
import os

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))   # scripts/ornt
ROOT = os.path.join(BASE_DIR, "..", "..")                # 프로젝트 루트

# ▼ 품목명세서 파일 경로 (파일 이름이 다르면 여기만 고치기)
ITEM_FILE = os.path.join(ROOT, "data", "raw", "bid_item_20210101_20251231.csv")
ORNT_FILE = os.path.join(ROOT, "data", "analysis", "ornt_code", "orntCode_unique.csv")
OUT_FILE = os.path.join(ROOT, "data", "analysis", "ornt_code", "orntCode_codeVld4_all.csv")

# codeVld4 값을 군 그룹으로 묶기 (여기에 없는 값은 국방부직할/합동으로 봄)
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

# codeVld4가 전부 비어 있는 기관
empty_codes = sorted(set(item["orntCode"]) - set(valid["orntCode"]))

# 2) 기관별 분포
dist = pd.crosstab(valid["orntCode"], valid["group"])
result = pd.DataFrame({
    "품목수": dist.sum(axis=1),
    "군종류수": dist.gt(0).sum(axis=1),          # 1이면 한 군, 2 이상이면 섞임
    "codeVld4분포": dist.apply(
        lambda r: ", ".join(f"{k} {v:,} ({v / r.sum():.1%})"
                            for k, v in r[r > 0].sort_values(ascending=False).items()),
        axis=1),
    "최다값": dist.idxmax(axis=1),
    "최다비율": (dist.max(axis=1) / dist.sum(axis=1)).round(3),
}).reset_index()

# 3) 기관명 붙이기
if os.path.exists(ORNT_FILE):
    names = pd.read_csv(ORNT_FILE, dtype="string", encoding="utf-8-sig", keep_default_na=False)
    names = names.apply(lambda s: s.str.strip()).drop_duplicates("orntCode")
    result = result.merge(names[["orntCode", "ornt"]], on="orntCode", how="left")
else:
    result["ornt"] = ""
result["ornt"] = result["ornt"].fillna("")

result = result[["ornt", "orntCode", "품목수", "군종류수", "codeVld4분포", "최다값", "최다비율"]]
result = result.sort_values(["최다비율", "품목수"], ascending=[True, False])
result.to_csv(OUT_FILE, index=False, encoding="utf-8-sig")

# 4) 요약 출력
print(f"\n집계된 기관 {len(result)}개")
print(f"codeVld4가 전부 비어 있어 빠진 기관 {len(empty_codes)}개: {', '.join(empty_codes)}")
print("\n[최다값별 기관 수]")
print(result["최다값"].value_counts().to_string())
print(f"\n저장: {os.path.abspath(OUT_FILE)}")