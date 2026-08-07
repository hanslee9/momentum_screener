"""
Relative Momentum Screener
============================

로직
----
1. 국가(한국/미국) + 후보군 크기 N을 입력받는다.
2. 해당 국가의 시가총액 상위 N개 종목을 유니버스로 구성한다.
   - 한국: 네이버 금융 시가총액 페이지 파싱
   - 미국: 네이버 해외증시 API 조회
3. 각 종목의 약 13개월치 일별 종가(수정주가)를 yfinance로 다운로드한다.
4. 종목별로 1/3/6/12개월 수익률(최근 1개월 포함, 거래일 기준 근사)을 계산한다.
5. 모멘텀 점수 = 0.10*R_1m + 0.20*R_3m + 0.30*R_6m + 0.40*R_12m
   (장기 추세에 더 비중을 둔 설정 - 자주 리밸런싱하지 않는 장기투자자 성향에 맞춤)
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
# 장기(12개월)에 가장 큰 비중 - 단기 노이즈에 덜 민감하도록 설계
WEIGHTS = {"1m": 0.10, "3m": 0.20, "6m": 0.30, "12m": 0.40}


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
        # FinanceDataReader의 NASDAQ/NYSE/AMEX 리스팅은 Symbol/Name/Industry만 제공하고
        # 시가총액 컬럼이 없어(최신 버전 기준), 네이버 해외증시 API를 직접 호출한다.
        # 이 API는 marketValue(시가총액) 기준으로 이미 정렬되어 반환되는 것으로 확인됨.
        import requests

        headers = {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        exchanges = ["NASDAQ", "NYSE", "AMEX"]
        rows = []

        for exch in exchanges:
            page = 1
            fetched = 0
            # 거래소별로 골고루 담되, 전체적으로는 이후 시가총액 기준 재정렬하므로 여유 있게 수집
            while fetched < n and page <= 20:
                url = f"http://api.stock.naver.com/stock/exchange/{exch}/marketValue?page={page}&pageSize=60"
                try:
                    resp = requests.get(url, headers=headers, timeout=10)
                    jo = resp.json()
                except Exception:
                    break

                stocks = jo.get("stocks", [])
                if not stocks:
                    break

                for s in stocks:
                    symbol_raw = s.get("symbolCode", "")
                    symbol = symbol_raw.split(".")[0]  # 네이버 표기(.O 등) 접미사 제거
                    name = s.get("stockNameEng") or s.get("stockName") or symbol

                    # 시가총액 관련 필드명이 API 문서화가 안 되어 있어 방어적으로 탐색
                    marcap = None
                    for key in s.keys():
                        kl = key.lower()
                        if "marketvalue" in kl or ("market" in kl and "cap" in kl):
                            try:
                                marcap = float(s[key])
                            except (TypeError, ValueError):
                                pass
                            break

                    rows.append({"ticker": symbol, "name": name, "market_cap": marcap})
                    fetched += 1

                page += 1

        if not rows:
            raise ValueError("미국 종목 리스트를 가져오지 못했습니다 (네이버 해외증시 API 응답 없음).")

        df = pd.DataFrame(rows).drop_duplicates(subset=["ticker"])

        if df["market_cap"].notna().any():
            df = df.sort_values("market_cap", ascending=False)
        # market_cap 필드를 못 찾은 경우, API 자체가 이미 시가총액순으로 반환한다고 가정하고 원래 순서 유지

        return df.head(n).reset_index(drop=True)

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
# 4. 간단 백테스트 (상위 K개 종목 동일가중 Buy & Hold + 종목별 개별 성과)
# ------------------------------------------------------------------
def _compute_metrics_from_series(series: pd.Series) -> dict:
    """가격(또는 자산가치) 시계열 하나에 대해 CAGR/MDD/총수익률/기간을 계산한다."""
    series = series.dropna()
    if len(series) < 2:
        raise ValueError("데이터가 2개 미만이라 성과를 계산할 수 없습니다.")

    start_date, end_date = series.index[0], series.index[-1]
    n_days = (end_date - start_date).days
    n_years = n_days / 365.25

    start_val, end_val = series.iloc[0], series.iloc[-1]
    total_return = end_val / start_val - 1
    cagr = (end_val / start_val) ** (1 / n_years) - 1 if n_years >= 30 / 365.25 else float("nan")

    cum_max = series.cummax()
    drawdown = series / cum_max - 1
    mdd = drawdown.min()

    return {
        "start_date": start_date, "end_date": end_date, "n_days": n_days,
        "start_value": start_val, "end_value": end_val,
        "total_return": total_return, "cagr": cagr, "mdd": mdd,
    }


def run_simple_backtest(price_matrix: pd.DataFrame, tickers: list, initial_capital: float = 10_000_000,
                         start_date=None, end_date=None) -> dict:
    """
    선정된 종목들을 지정 기간 동안 각각 보유했을 때의 개별 성과와,
    동일가중으로 묶었을 때의 포트폴리오 성과를 함께 계산한다.
    (리밸런싱 없음, 거래비용 미반영 - 참고용 approximation)

    start_date/end_date를 지정하면 해당 구간만 사용하고, 미지정 시 각 종목이 보유한
    전체 데이터 범위를 사용한다 (종목별로 시작일이 다를 수 있음에 유의).

    반환: {
        'per_ticker': {ticker: {'series': pd.Series(정규화 자산가치), 'metrics': dict}},
        'portfolio': {'series': pd.Series, 'metrics': dict},
    }
    """
    sub = price_matrix[tickers].copy()
    if start_date is not None:
        sub = sub[sub.index >= pd.Timestamp(start_date)]
    if end_date is not None:
        sub = sub[sub.index <= pd.Timestamp(end_date)]

    if sub.empty:
        raise ValueError("지정한 기간에 해당하는 가격 데이터가 없습니다.")

    per_ticker = {}
    for t in tickers:
        s = sub[t].dropna()
        if len(s) < 2:
            continue
        equity = (s / s.iloc[0]) * initial_capital
        m = _compute_metrics_from_series(equity)
        per_ticker[t] = {"series": equity, "metrics": m}

    if not per_ticker:
        raise ValueError("백테스트 가능한 종목이 없습니다 (해당 기간 데이터 부족).")

    # 포트폴리오: 공통으로 데이터가 있는 구간만 사용해 동일가중 평균
    common = sub[list(per_ticker.keys())].dropna(how="any")
    if len(common) < 2:
        raise ValueError("종목들의 공통 거래일이 부족해 포트폴리오 성과를 계산할 수 없습니다.")
    normalized = common / common.iloc[0]
    portfolio_equity = normalized.mean(axis=1) * initial_capital
    portfolio_metrics = _compute_metrics_from_series(portfolio_equity)

    return {
        "per_ticker": per_ticker,
        "portfolio": {"series": portfolio_equity, "metrics": portfolio_metrics},
    }


# ------------------------------------------------------------------
# 5. 전체 파이프라인
# ------------------------------------------------------------------
def run_screener(country: str, n: int, k: int, lookback_days: int = 420):
    universe = get_universe(country, n)
    print(f"[1/3] 유니버스 구성 완료: {country} 시가총액 상위 {len(universe)}개")

    price_matrix = fetch_price_matrix(universe["ticker"].tolist(), lookback_days=lookback_days)
    print(f"[2/3] 가격 데이터 다운로드 완료: {price_matrix.shape[1]}개 종목, {price_matrix.shape[0]}거래일")

    scored = compute_momentum_scores(price_matrix)
    scored = scored.merge(universe[["ticker", "name", "market_cap"]], on="ticker", how="left")
    scored = scored.sort_values("score", ascending=False).reset_index(drop=True)
    print(f"[3/3] 모멘텀 점수 계산 완료: {len(scored)}개 종목 (데이터 부족 종목 제외)")

    top_k = scored.head(k).copy()
    return top_k, scored, price_matrix


# ------------------------------------------------------------------
# 5. CLI
# ------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Relative Momentum Screener")
    parser.add_argument("--country", required=True, choices=["KR", "US"], help="KR 또는 US")
    parser.add_argument("--n", type=int, default=50, help="시가총액 상위 몇 개를 후보군으로 볼지 (기본 50)")
    parser.add_argument("--k", type=int, default=3, help="최종 몇 개 종목을 선별할지 (기본 3)")
    parser.add_argument("--out", default="screener_result.csv", help="전체 결과 저장 경로")
    parser.add_argument("--capital", type=float, default=10_000_000, help="백테스트 초기 투자금 (기본 1000만)")
    parser.add_argument("--lookback-days", type=int, default=420,
                         help="가격 데이터 조회 기간(일). 백테스트를 더 긴 과거로 하려면 늘리세요 (기본 420일=약 13개월)")
    args = parser.parse_args()

    top_k, full, price_matrix = run_screener(args.country, args.n, args.k, lookback_days=args.lookback_days)

    print("\n" + "=" * 70)
    print(f"모멘텀 점수 상위 {args.k}개 종목 ({args.country}, 후보군 {args.n}개 중)")
    print("=" * 70)
    for _, row in top_k.iterrows():
        print(f"{row['ticker']:<10} {row['name']:<25} 점수 {row['score']:.2%}  "
              f"(1m {row['R_1m']:.1%} / 3m {row['R_3m']:.1%} / 6m {row['R_6m']:.1%} / 12m {row['R_12m']:.1%})")

    full.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(f"\n[저장 완료] 전체 결과 -> {args.out}")

    # --- 간단 백테스트: 선정된 K개 종목의 개별 성과 + 동일가중 포트폴리오 성과 ---
    try:
        bt = run_simple_backtest(price_matrix, top_k["ticker"].tolist(), args.capital)

        print("\n" + "=" * 70)
        print("간단 백테스트 (종목별 개별 성과, Buy&Hold)")
        print("=" * 70)
        for t, info in bt["per_ticker"].items():
            m = info["metrics"]
            print(f"{t:<10} {m['start_date'].date()} ~ {m['end_date'].date()}  "
                  f"총수익률 {m['total_return']:.2%}  CAGR {m['cagr']:.2%}  MDD {m['mdd']:.2%}")

        pm = bt["portfolio"]["metrics"]
        print("\n" + "-" * 70)
        print(f"포트폴리오(동일가중) {pm['start_date'].date()} ~ {pm['end_date'].date()}")
        print(f"{'최종 평가액':<15}: {pm['end_value']:,.0f}")
        print(f"{'총 수익률':<15}: {pm['total_return']:.2%}")
        print(f"{'CAGR':<15}: {pm['cagr']:.2%}")
        print(f"{'MDD':<15}: {pm['mdd']:.2%}")
    except Exception as e:
        print(f"\n[백테스트 생략] {e}")


if __name__ == "__main__":
    main()
