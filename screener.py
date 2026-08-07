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
    import FinanceDataReader as fdr

    country = country.upper()

    if country == "KR":
        df = fdr.StockListing("KRX")
        # FinanceDataReader 버전에 따라 컬럼명이 'Marcap' 또는 'MarketCap' 등으로 다를 수 있어 방어적으로 처리
        marcap_col = next((c for c in ["Marcap", "MarketCap", "시가총액"] if c in df.columns), None)
        if marcap_col is None:
            raise ValueError("KRX 시가총액 컬럼을 찾을 수 없습니다. FinanceDataReader 버전을 확인하세요.")

        df = df.dropna(subset=[marcap_col]).sort_values(marcap_col, ascending=False)
        df = df.head(n).copy()

        def to_yf_ticker(row):
            market = str(row.get("Market", "")).upper()
            suffix = ".KQ" if "KOSDAQ" in market else ".KS"  # 기본은 KOSPI(.KS)
            return f"{row['Code']}{suffix}"

        df["ticker"] = df.apply(to_yf_ticker, axis=1)
        df = df.rename(columns={"Name": "name", marcap_col: "market_cap"})
        return df[["ticker", "name", "market_cap"]].reset_index(drop=True)

    elif country == "US":
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
