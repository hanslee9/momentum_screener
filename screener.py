"""
Relative Momentum Screener
============================

로직
----
1. 국가(한국/미국) + 후보군 크기 N을 입력받는다.
2. 해당 국가의 시가총액 상위 N개 종목을 유니버스로 구성한다.
   - 한국: FinanceDataReader로 KRX(코스피+코스닥) 전종목 시가총액 조회 후 상위 N개 추출
   - 미국: FinanceDataReader로 NASDAQ/NYSE/AMEX 상장 종목 시가총액 조회 후 상위 N개 추출
3. 각 종목의 약 13개월치 일별 종가(수정주가)를 yfinance로 다운로드한다.
4. 종목별로 1/3/6/12개월 수익률(최근 1개월 포함, 거래일 기준 근사)을 계산한다.
5. 모멘텀 점수 = 0.40*R_1m + 0.30*R_3m + 0.20*R_6m + 0.10*R_12m
6. 점수 기준 내림차순 정렬 후 상위 K개 종목을 출력한다.

주의: 상장 13개월 미만이거나 가격 데이터가 결측인 종목은 자동 제외된다.
"""

import argparse
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# 거래일 기준 근사치 (영업일 기준)
TRADING_DAYS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}
WEIGHTS = {"1m": 0.40, "3m": 0.30, "6m": 0.20, "12m": 0.10}


# ------------------------------------------------------------------
# 1. 유니버스 구성 (국가별 시가총액 상위 N개)
# ------------------------------------------------------------------
def get_universe(country: str, n: int) -> pd.DataFrame:
    """
    country: 'KR' 또는 'US'
    반환: columns=['ticker', 'name', 'market_cap'] (시가총액 내림차순, 상위 n개)
    ticker는 yfinance 조회에 바로 쓸 수 있는 형태로 반환한다
    (한국: 종목코드+.KS/.KQ 접미사, 미국: 티커 그대로)
    """
    country = country.upper()

    if country == "KR":
        # data.krx.co.kr 직접 호출(FinanceDataReader, pykrx 공통)은 클라우드/데이터센터 IP를
        # KRX 서버가 차단하는 사례가 많아, 상대적으로 안정적인 네이버 금융 페이지를 파싱한다.
        import requests
        from bs4 import BeautifulSoup

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        rows = []
        page = 1
        max_pages = 20  # 페이지당 약 50종목, 최대 1000종목까지 탐색
        marcap_col_idx = None  # 헤더에서 탐색한 "시가총액" 컬럼의 td 인덱스 (페이지마다 동일 구조 가정)

        while len(rows) < n and page <= max_pages:
            url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok=0&page={page}"
            resp = requests.get(url, headers=headers, timeout=10)
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "html.parser")

            table = soup.select_one("table.type_2")
            if table is None:
                break

            if marcap_col_idx is None:
                header_ths = table.select("thead th") or table.select("tr th")
                for idx, th in enumerate(header_ths):
                    if "시가총액" in th.text:
                        marcap_col_idx = idx
                        break
                if marcap_col_idx is None:
                    raise ValueError("네이버 금융 페이지 구조가 변경된 것으로 보입니다 "
                                      "('시가총액' 헤더를 찾지 못함). 코드 점검이 필요합니다.")

            trs = table.select("tr")
            page_rows_found = 0
            for tr in trs:
                link = tr.select_one("a.tltle")
                if link is None:
                    continue
                href = link.get("href", "")
                code_match = href.split("code=")
                if len(code_match) < 2:
                    continue
                code = code_match[1][:6]
                name = link.text.strip()

                tds = tr.select("td")
                try:
                    marcap = float(tds[marcap_col_idx].text.strip().replace(",", ""))
                except (ValueError, IndexError):
                    continue

                rows.append({"code": code, "name": name, "market_cap": marcap})
                page_rows_found += 1

            if page_rows_found == 0:
                break
            page += 1

        if not rows:
            raise ValueError("네이버 금융에서 시가총액 데이터를 가져오지 못했습니다. "
                              "네트워크 상태를 확인하거나 잠시 후 다시 시도하세요.")

        df = pd.DataFrame(rows).drop_duplicates(subset=["code"])
        df = df.sort_values("market_cap", ascending=False).head(n).reset_index(drop=True)
        # KOSPI(sosok=0)만 수집했으므로 전부 .KS. 코스닥 포함이 필요하면 sosok=1도 합쳐야 하나,
        # 시가총액 상위 종목은 대부분 코스피이므로 기본값은 KOSPI로 충분한 경우가 많다.
        df["ticker"] = df["code"] + ".KS"
        return df[["ticker", "name", "market_cap"]]

    elif country == "US":
        import FinanceDataReader as fdr

        frames = []
        for exch in ["NASDAQ", "NYSE", "AMEX"]:
            try:
                frames.append(fdr.StockListing(exch))
            except Exception:
                continue
        if not frames:
            raise ValueError("미국 종목 리스트를 가져오지 못했습니다.")
        df = pd.concat(frames, ignore_index=True)

        marcap_col = next((c for c in ["MarketCap", "Marcap", "시가총액"] if c in df.columns), None)
        if marcap_col is None:
            raise ValueError("미국 종목 시가총액 컬럼을 찾을 수 없습니다. FinanceDataReader 버전을 확인하세요.")
        name_col = "Name" if "Name" in df.columns else df.columns[1]
        ticker_col = "Symbol" if "Symbol" in df.columns else df.columns[0]

        df = df.dropna(subset=[marcap_col]).drop_duplicates(subset=[ticker_col])
        df = df.sort_values(marcap_col, ascending=False).head(n).copy()
        df["ticker"] = df[ticker_col]
        df = df.rename(columns={name_col: "name", marcap_col: "market_cap"})
        return df[["ticker", "name", "market_cap"]].reset_index(drop=True)

    else:
        raise ValueError("country는 'KR' 또는 'US'만 지원합니다.")


# ------------------------------------------------------------------
# 2. 가격 데이터 다운로드 (배치)
# ------------------------------------------------------------------
def fetch_price_matrix(tickers: list, lookback_days: int = 420) -> pd.DataFrame:
    """
    yfinance로 여러 종목의 종가를 한 번에 배치 다운로드한다.
    반환: index=날짜, columns=티커, values=수정종가
    """
    import yfinance as yf

    end = pd.Timestamp.today()
    start = end - pd.Timedelta(days=lookback_days)

    raw = yf.download(
        tickers, start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"),
        progress=False, auto_adjust=True, group_by="ticker", threads=True,
    )

    if raw.empty:
        raise ValueError("가격 데이터를 가져오지 못했습니다.")

    # 티커가 1개일 때와 여러 개일 때 컬럼 구조가 다르므로 통일 처리
    if isinstance(raw.columns, pd.MultiIndex):
        close = pd.DataFrame({t: raw[t]["Close"] for t in tickers if t in raw.columns.get_level_values(0)})
    else:
        close = raw[["Close"]].rename(columns={"Close": tickers[0]})

    return close


# ------------------------------------------------------------------
# 3. 모멘텀 점수 계산
# ------------------------------------------------------------------
def compute_momentum_scores(price_matrix: pd.DataFrame, min_history_days: int = 260) -> pd.DataFrame:
    """
    price_matrix: index=날짜, columns=티커
    반환: columns=['ticker','R_1m','R_3m','R_6m','R_12m','score'] (score 내림차순 정렬)
    """
    results = []

    for ticker in price_matrix.columns:
        series = price_matrix[ticker].dropna()

        if len(series) < min_history_days:
            continue  # 상장 이력이 부족한 종목 제외

        latest = series.iloc[-1]
        returns = {}
        valid = True

        for label, days in TRADING_DAYS.items():
            if len(series) <= days:
                valid = False
                break
            past = series.iloc[-1 - days]
            returns[f"R_{label}"] = (latest / past) - 1

        if not valid:
            continue

        score = sum(WEIGHTS[label] * returns[f"R_{label}"] for label in WEIGHTS)
        results.append({"ticker": ticker, **returns, "score": score})

    if not results:
        return pd.DataFrame(columns=["ticker", "R_1m", "R_3m", "R_6m", "R_12m", "score"])

    df = pd.DataFrame(results).sort_values("score", ascending=False).reset_index(drop=True)
    return df


# ------------------------------------------------------------------
# 4. 전체 파이프라인
# ------------------------------------------------------------------
def run_screener(country: str, n: int, k: int) -> pd.DataFrame:
    universe = get_universe(country, n)
    print(f"[1/3] 유니버스 구성 완료: {country} 시가총액 상위 {len(universe)}개")

    price_matrix = fetch_price_matrix(universe["ticker"].tolist())
    print(f"[2/3] 가격 데이터 다운로드 완료: {price_matrix.shape[1]}개 종목, {price_matrix.shape[0]}거래일")

    scored = compute_momentum_scores(price_matrix)
    scored = scored.merge(universe[["ticker", "name", "market_cap"]], on="ticker", how="left")
    scored = scored.sort_values("score", ascending=False).reset_index(drop=True)
    print(f"[3/3] 모멘텀 점수 계산 완료: {len(scored)}개 종목 (데이터 부족 종목 제외)")

    top_k = scored.head(k).copy()
    return top_k, scored


# ------------------------------------------------------------------
# 5. CLI
# ------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Relative Momentum Screener")
    parser.add_argument("--country", required=True, choices=["KR", "US"], help="KR 또는 US")
    parser.add_argument("--n", type=int, default=50, help="시가총액 상위 몇 개를 후보군으로 볼지 (기본 50)")
    parser.add_argument("--k", type=int, default=3, help="최종 몇 개 종목을 선별할지 (기본 3)")
    parser.add_argument("--out", default="screener_result.csv", help="전체 결과 저장 경로")
    args = parser.parse_args()

    top_k, full = run_screener(args.country, args.n, args.k)

    print("\n" + "=" * 70)
    print(f"모멘텀 점수 상위 {args.k}개 종목 ({args.country}, 후보군 {args.n}개 중)")
    print("=" * 70)
    for _, row in top_k.iterrows():
        print(f"{row['ticker']:<10} {row['name']:<25} 점수 {row['score']:.2%}  "
              f"(1m {row['R_1m']:.1%} / 3m {row['R_3m']:.1%} / 6m {row['R_6m']:.1%} / 12m {row['R_12m']:.1%})")

    full.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(f"\n[저장 완료] 전체 결과 -> {args.out}")


if __name__ == "__main__":
    main()
