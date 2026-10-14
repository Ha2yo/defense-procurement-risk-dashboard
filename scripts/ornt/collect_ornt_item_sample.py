"""기관별 군 구분(miltDivs, codeVld4) 확인용 품목명세서 수집 (팀 원본 collect_data_item.py와 별개).

실행 순서
  1) python scripts/ornt/orntCode_unique.py         → data/analysis/ornt_code/orntCode_unique.csv (ornt, orntCode)
  2) python scripts/ornt/collect_ornt_item_sample.py → data/analysis/ornt_code/orntCode_codeVld4.csv

기관 226개마다 공고 1건(공고 CLEAN 파일의 그 기관 첫 공고)의 품목명세서를 받아
기관명과 첫 품목의 miltDivs, codeVld4만 저장한다. 결과 파일은 1개만 만든다.
"""
import os
import time
import xml.etree.ElementTree as ET

import pandas as pd
import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))   # scripts/ornt
ROOT = os.path.join(BASE_DIR, "..", "..")                # 프로젝트 루트
load_dotenv(os.path.join(ROOT, ".env"))

API_KEY = os.getenv("API_KEY_DATA_PORTAL")
if not API_KEY:
    raise RuntimeError("API_KEY_DATA_PORTAL을 .env에서 읽지 못했습니다.")

URL_ITEM = "https://apis.data.go.kr/1690000/BidPblancInfoService/getDmstcCmpetBidPblancItem"

ORNT_FILE = os.path.join(ROOT, "data", "analysis", "ornt_code", "orntCode_unique.csv")
NOTICE_FILE = os.path.join(ROOT, "data", "clean", "bid_notice_clean_20210101_20251231.csv")
OUT_FILE = os.path.join(ROOT, "data", "analysis", "ornt_code", "orntCode_codeVld4.csv")

REQUEST_COLUMNS = ["demandYear", "dcsNo", "orntCode"]   # API 필수 요청값
SAVE_COLUMNS = ["ornt", "orntCode", "miltDivs", "codeVld4"]   # 저장할 열

REQUEST_DELAY = 0.2
MAX_RETRIES = 3


# 1) 요청 대상: 기관마다 첫 공고 1건
ornt = pd.read_csv(ORNT_FILE, dtype="string", encoding="utf-8-sig", keep_default_na=False)
ornt = ornt.apply(lambda s: s.str.strip())
codes = ornt["orntCode"]
name_of = dict(zip(ornt["orntCode"], ornt["ornt"]))   # 기관코드 → 기관명
notice = pd.read_csv(NOTICE_FILE, dtype="string", encoding="utf-8-sig", keep_default_na=False,
                     usecols=REQUEST_COLUMNS, low_memory=False)
notice = notice.apply(lambda s: s.str.strip())
targets = notice[notice["orntCode"].isin(codes)].drop_duplicates(subset="orntCode", keep="first")
print(f"기관 {codes.nunique()}개 중 공고를 찾은 기관 {len(targets)}개 → API {len(targets)}번 호출")


# 2) API 호출
def fetch_first_item(target):
    """공고 1건의 품목명세서에서 첫 품목을 돌려준다. 품목이 없거나 실패하면 빈 dict."""
    params = {"serviceKey": API_KEY, "pageNo": 1, "numOfRows": 1, **target}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            res = requests.get(URL_ITEM, params=params, timeout=(10, 60))
            res.raise_for_status()
            root = ET.fromstring(res.content)
            code = root.findtext(".//header/resultCode")
            if code and code != "00":
                raise RuntimeError(f"API 오류 {code} / {root.findtext('.//header/resultMsg') or ''}")
            item = root.find(".//body/items/item")
            return {} if item is None else {c.tag: (c.text or "").strip() for c in item}
        except Exception as e:
            print(f"  {target['orntCode']} 실패 ({attempt}/{MAX_RETRIES}): {e}")
            time.sleep(2 * attempt)
    return {}


rows = []
for i, target in enumerate(targets.to_dict("records"), 1):
    item = fetch_first_item(target)
    rows.append({"ornt": name_of.get(target["orntCode"], ""),
                 "orntCode": target["orntCode"],
                 "miltDivs": item.get("miltDivs", ""),
                 "codeVld4": item.get("codeVld4", "")})
    if i % 50 == 0:
        print(f"  {i}/{len(targets)}")
    time.sleep(REQUEST_DELAY)


# 3) 저장 (파일 1개)
result = pd.DataFrame(rows, columns=SAVE_COLUMNS)
result.to_csv(OUT_FILE, index=False, encoding="utf-8-sig")

print(f"\n저장: {os.path.abspath(OUT_FILE)} ({len(result)}행)")
print(f"codeVld4 없음(품목 없음·실패): {(result['codeVld4'] == '').sum()}개")
print(result["codeVld4"].replace("", "(없음)").value_counts().to_string())