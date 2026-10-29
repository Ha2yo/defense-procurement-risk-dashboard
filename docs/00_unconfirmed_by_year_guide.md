# 00. 연도별 결과 미확인 공고 분석 — 실행 가이드

## 1. 개요

입찰공고와 입찰결과를 식별키로 연결하여 공고별 결과 확인 상태를 분류하고, 공고일 기준 연도별 결과 미확인 건수와 비율을 산출한다.

| 항목 | 내용 |
|---|---|
| 실행 파일 | [`scripts/analysis/00_unconfirmed_by_year.py`](../scripts/analysis/00_unconfirmed_by_year.py) |
| 분석 범위 | 공고일 기준 전체 연도(입력에 포함된 연도) |
| 분석 단위 | 입찰공고 행(완전 동일 행 중복 제거) |
| 기본 결과 위치 | `data/analysis/unconfirmed_by_year.csv` |

## 2. 입력 데이터

| 입력 | 경로 또는 파일명 | 주요 항목 |
|---|---|---|
| 입찰공고 | `data/clean/bid_notice_clean_20210101_20251231.csv` | `demandYear`, `orntCode`, `dcsNo`, `pblancNo`, `pblancOdr`, `pblancSe`, `pblancDate` |
| 입찰결과 | `data/clean/bid_result_clean_20210101_20251231.csv` | `demandYear`, `orntCode`, `dcsNo`, `pblancNo`, `pblancOdr` |

## 3. 처리 흐름과 산식

1. 식별키를 문자열로 읽어 양끝 공백과 빈 값을 정리하고 공고의 **완전 동일 행** 중복을 제거한다.
2. 공고키(`demandYear + orntCode + dcsNo + pblancNo + pblancOdr`)로 결과 직접매칭 여부를 판정한다.
3. 직접매칭되지 않은 공고 중 `pblancSe == "취소공고"`를 취소공고로 분류한다.
4. 나머지 공고 중 사업키(`demandYear + orntCode + dcsNo`)가 같은 다른 공고의 결과가 있으면 '동일사업 다른 공고 결과 존재'로 분류한다.
5. 위 세 조건에 해당하지 않는 공고를 **결과 미확인**으로 분류하고, `pblancDate` 연도별로 집계한다.

두 비율의 분모는 다르다.

```text
연도별 미확인률(%) = 연도별 결과 미확인 건수 / 해당 연도 전체 공고 수 × 100
미확인 구성비(%)   = 연도별 결과 미확인 건수 / 모든 연도 결과 미확인 건수 × 100
```

입력에 포함된 공고일을 그대로 사용하므로 2020년과 2026년 공고도 연도별 현황에 나타난다. '결과 미확인'은 **결과 직접매칭 실패 전체**를 뜻하지 않는다.

## 4. 실행 방법

프로젝트 루트(`C:\workspace\defense-procurement-risk-dashboard`)의 PowerShell 또는 VS Code 터미널에서 실행한다. 필요한 Python 패키지는 저장소의 `requirements.txt`를 사용한다.

```powershell
cd C:\workspace\defense-procurement-risk-dashboard
python -m pip install -r requirements.txt
python scripts/analysis/00_unconfirmed_by_year.py
```

## 5. 출력 파일

| 파일 | 내용 |
|---|---|
| `data/analysis/unconfirmed_by_year.csv` | 공고 연도, 전체 공고 수, 결과 미확인 수, 연도별 미확인률, 전체 미확인 중 구성비 |

## 6. 결과 및 해석

### 연도별 집계

| 공고 연도 | 전체 공고 | 결과 미확인 | 연도별 미확인률 |
|---|---:|---:|---:|
| 2020 | 118 | 42 | 35.59% |
| 2021 | 13,363 | 3,161 | 23.65% |
| 2022 | 14,023 | 3,322 | 23.69% |
| 2023 | 12,500 | 2,803 | 22.42% |
| 2024 | 11,281 | 2,435 | 21.58% |
| 2025 | 9,856 | 2,006 | 20.35% |
| 2026 | 1 | 0 | 0.00% |
| **합계** | **61,142** | **13,769** | **22.52%** |

### 상태별 분류

| 상태 | 공고 수 |
|---|---:|
| 결과 직접매칭 | 38,025 |
| 취소공고 | 5,578 |
| 동일사업 다른 공고 결과 존재 | 3,770 |
| 결과 미확인 | 13,769 |
| **합계** | **61,142** |

서로 배타적인 네 분류의 합계는 전체 공고 수와 같다. **결과 직접매칭 38,025건**은 결과확정 건수와 같은 개념이 아니다. 결과가 상충하는 공고는 후속 분석의 결과확정 기준에 따라 별도로 판정한다.

## 7. 실행 결과 확인

```powershell
Import-Csv -Encoding UTF8 data/analysis/unconfirmed_by_year.csv | Format-Table -AutoSize
```

필수 파일·컬럼이나 공고일 형식에 오류가 있으면 실행이 중단된다. 결과 미확인 건수가 기준값과 다를 때는 콘솔에 경고를 출력하며 현재 입력에 대한 집계값을 저장한다. **공고키 유일성 자체를 이 프로그램이 보증하는 것은 아니며**, 완전 동일 행 제거와 공고키 중복 검사는 서로 다른 절차다.
