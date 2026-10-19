import os

import pandas as pd
import streamlit as st

# ============================================================
# 1. 기본 설정
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "../..", "data", "dashboard")

TABLE_FILES = {
    "procure_plan": "procure_plan.parquet",
    "bid_notice": "bid_notice.parquet",
    "bid_result": "bid_result.parquet",
    "contract_info": "contract_info.parquet",
    "bid_item": "bid_item.parquet",
}


# ============================================================
# 2. 데이터 로드
# ============================================================

@st.cache_data(show_spinner="Parquet 데이터를 읽는 중입니다...")
def _read_parquet(path, modified_time, columns=None):
    """파일 수정 시간이 달라지면 캐시를 갱신합니다."""
    return pd.read_parquet(path, engine="pyarrow", columns=columns)


def load_data(name, columns=None):
    """테이블 이름으로 Parquet 파일을 읽습니다."""
    if name not in TABLE_FILES:
        raise ValueError(f"지원하지 않는 테이블: {name}")

    path = os.path.join(DATA_DIR, TABLE_FILES[name])
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"Parquet 파일이 없습니다: {path}\n"
            "먼저 csv_to_parquet.py를 실행해 주세요."
        )

    selected_columns = tuple(columns) if columns is not None else None
    return _read_parquet(path, os.path.getmtime(path), selected_columns)


def load_procure_plan(columns=None):
    return load_data("procure_plan", columns)


def load_notice(columns=None):
    return load_data("bid_notice", columns)


def load_result(columns=None):
    return load_data("bid_result", columns)


def load_contract(columns=None):
    return load_data("contract_info", columns)


def load_item(columns=None):
    return load_data("bid_item", columns)
