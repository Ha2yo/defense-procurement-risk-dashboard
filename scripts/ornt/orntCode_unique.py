import os
import pandas as pd

# 공고 CLEAN 파일에서 기관코드(orntCode) 기준으로 중복을 없앤 기관 목록을 만든다. (226개)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))   # scripts/ornt
ROOT = os.path.join(BASE_DIR, "..", "..")                # 프로젝트 루트
INPUT_DIR = os.path.join(ROOT, "data", "clean")
OUTPUT_DIR = os.path.join(ROOT, "data", "analysis", "ornt_code")
os.makedirs(OUTPUT_DIR, exist_ok=True)

NOTICE_FILE = os.path.join(INPUT_DIR, "bid_notice_clean_20210101_20251231.csv")
OUT_FILE = os.path.join(OUTPUT_DIR, "orntCode_unique.csv")

# 1) 공고 파일에서 orntCode 기준 중복 제거
df = pd.read_csv(NOTICE_FILE, dtype="string", encoding="utf-8-sig",
                 keep_default_na=False)
df = df.apply(lambda s: s.str.strip())
result = df.drop_duplicates(subset="orntCode", keep="first")

# 2) 필요한 열만 저장
result = result[["ornt", "orntCode","demandYear","dcsNo"]]
result.to_csv(OUT_FILE, index=False, encoding="utf-8-sig")

print(f"고유 orntCode: {len(result)}개")
print("저장:", os.path.abspath(OUT_FILE))
