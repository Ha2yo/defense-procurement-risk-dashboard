"""프로젝트 기관그룹 분류 기준표 — 기관별 최종 기관그룹과 품목명세서 codeVld4(소요군) 분포.

앞으로 기관그룹은 이 파일의 분류 규칙(classify)과 결과(orntCode_codeVld4_all.csv의 기관그룹 열)를 기준으로 한다.

실행: python scripts/ornt/compare_ornt_codeVld4.py
입력: ITEM_FILE (orntCode, codeVld4 열이 있는 품목명세서 CSV)  ← 아래에서 파일 경로 확인
      data/analysis/ornt_code/orntCode_unique.csv (전체 기관 목록 226개, 필수 — orntCode_unique.py 먼저 실행)
출력 1: data/analysis/ornt_code/orntCode_codeVld4_all.csv
출력 2: data/analysis/ornt_code/bid_orntCode_20210101_20251231.csv
"""
import os

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))   # scripts/ornt
ROOT = os.path.join(BASE_DIR, "..", "..")                # 프로젝트 루트

# ▼ 품목명세서 파일 경로 (파일 이름이 다르면 여기만 고치기)
ITEM_FILE = os.path.join(ROOT, "data", "clean", "bid_item_clean_20210101_20251231.csv")
ORNT_FILE = os.path.join(ROOT, "data", "analysis", "ornt_code", "orntCode_unique.csv")
OUT_FILE = os.path.join(ROOT, "data", "analysis", "ornt_code", "orntCode_codeVld4_all.csv")
RESULT_FILE = os.path.join(ROOT, "data", "analysis", "ornt_code", "bid_orntCode_20210101_20251231.csv")

# 아래 규칙과 다르게 정한 기관 (codeVld4·소속 확인 근거)
ORG_OVERRIDE = {
    "LAZ": "국방부직할/합동",   # 드론작전사령부: 합참 직할 합동부대, codeVld4 국방부직할/합동 100%
    "UDA": "육군",              # 제3190부대: codeVld4 육군 100%
}

# 기관그룹 분류 규칙 — 위에서부터 순서대로 검사
#   0) ORG_OVERRIDE에 있는 기관코드 → 지정한 그룹
#   1) 기관명에 공군 → 공군 / 해군·해병 → 해군/해병대
#   2) 기관명에 방위사업청·국방기술품질원·국방과학연구소, 또는 코드 E → 방위사업청
#   3) 기관코드 첫 글자: L=육군, H=공군, S=해군/해병대
#   4) 기관명에 보병사단·기갑여단 등 육군 부대 유형 → 육군
#   5) 나머지(코드 M·U·N·D) → 국방부직할/합동
ARMY_NAME = ("육군", "보병사단", "기갑여단", "포병여단", "화력여단", "공병여단", "기동사단", "군수지원여단", "군단")
DAPA_NAME = ("방위사업청", "국방기술품질원", "국방과학연구소")
CODE_GROUP = {"L": "육군", "H": "공군", "S": "해군/해병대"}


def classify(name, code):
    """(기관그룹, 분류근거)를 돌려준다."""
    name, code = str(name), str(code)
    if code in ORG_OVERRIDE:
        return ORG_OVERRIDE[code], "개별 지정"
    if "공군" in name:
        return "공군", "기관명"
    if "해군" in name or "해병" in name:
        return "해군/해병대", "기관명"
    if any(k in name for k in DAPA_NAME):
        return "방위사업청", "기관명"
    if code.startswith("E"):
        return "방위사업청", "코드(E)"
    if code[:1] in CODE_GROUP:
        return CODE_GROUP[code[0]], f"코드({code[0]})"
    if any(k in name for k in ARMY_NAME):
        return "육군", "기관명(부대 유형)"
    return "국방부직할/합동", f"코드({code[:1]})"


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

# 3) 전체 기관 목록(226개) 기준으로 붙이기 — codeVld4가 없는 기관도 포함
names = pd.read_csv(ORNT_FILE, dtype="string", encoding="utf-8-sig", keep_default_na=False)
names = names.apply(lambda s: s.str.strip()).drop_duplicates("orntCode")[["ornt", "orntCode"]]
result = names.merge(result, on="orntCode", how="left")

# 품목명세서 전체 행 수(빈칸 포함)도 같이 표시
result["전체품목행"] = result["orntCode"].map(item.groupby("orntCode").size()).fillna(0).astype(int)
no_vld4 = result["품목수"].isna()
result.loc[no_vld4, "codeVld4분포"] = "codeVld4 없음"
result["품목수"] = result["품목수"].fillna(0).astype(int)
result["군종류수"] = result["군종류수"].fillna(0).astype(int)
result["최다값"] = result["최다값"].fillna("")

# 최종 기관그룹 (위 분류 규칙)
result[["기관그룹", "분류근거"]] = [classify(n, c) for n, c in zip(result["ornt"], result["orntCode"])]

result = result[["ornt", "orntCode", "기관그룹", "분류근거", "전체품목행", "품목수",
                 "군종류수", "codeVld4분포", "최다값", "최다비율"]]
result = result.sort_values(["최다비율", "품목수"], ascending=[True, False])
result.to_csv(OUT_FILE, index=False, encoding="utf-8-sig")

result2 = result[["ornt", "orntCode", "기관그룹"]]
result2.rename(columns={'기관그룹':'orntGroup'}, inplace=True)
result2.to_csv(RESULT_FILE, index=False, encoding="utf-8-sig")
  
# 4) 요약 출력
print(f"\n전체 기관 {len(result)}개 (codeVld4 없음 {no_vld4.sum()}개 포함)")
print(f"codeVld4가 전부 비어 있는 기관 {len(empty_codes)}개: {', '.join(empty_codes)}")
print("\n[기관그룹별 기관 수]")
print(result["기관그룹"].value_counts().to_string())
print("\n[최다값별 기관 수]")
print(result["최다값"].replace("", "(codeVld4 없음)").value_counts().to_string())
print(f"\n저장: {os.path.abspath(OUT_FILE)}")
print(f"저장: {os.path.abspath(RESULT_FILE)}")