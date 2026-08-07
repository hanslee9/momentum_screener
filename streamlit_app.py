"""
Relative Momentum Screener - Streamlit 웹앱

screener.py의 로직을 그대로 사용하되, 웹 UI로 입력값을 받고 결과를 표/차트로 보여준다.

주의: Streamlit은 위젯 조작(날짜 변경 등) 시 스크립트 전체가 재실행되므로,
스크리닝 결과(가격 데이터 등 무거운 데이터 포함)를 st.session_state에 저장해
백테스트 기간을 바꿔도 결과가 사라지지 않고, 데이터 재다운로드도 발생하지 않도록 한다.
"""

import streamlit as st
import pandas as pd
import altair as alt

from screener import get_universe, fetch_price_matrix, compute_momentum_scores, run_simple_backtest

st.set_page_config(page_title="상대모멘텀 스크리너", layout="wide")

st.markdown(
    "<h5 style='margin-bottom:0;'>📊 상대모멘텀 스크리너</h5>",
    unsafe_allow_html=True,
)
st.caption("시가총액 상위 N개 종목 중, 1/3/6/12개월 가중평균 수익률(10/20/30/40%, 장기 비중 강화) 상위 K개를 선별합니다.")

if "result" not in st.session_state:
    st.session_state.result = None  # 스크리닝 결과 전체를 여기에 저장

# ------------------------------------------------------------------
# 입력값
# ------------------------------------------------------------------
source_mode = st.radio(
    "종목군 소스",
    options=["자동 (시가총액 상위)", "직접 입력"],
    horizontal=True,
    help="자동 조회가 서버 차단 등으로 실패할 경우 '직접 입력'으로 전환해 사용하세요.",
)

col1, col2, col3 = st.columns(3)
with col1:
    country = st.selectbox("국가", options=["KR", "US"], format_func=lambda x: "한국" if x == "KR" else "미국")
with col2:
    n = st.number_input("후보군 크기 (시가총액 상위 N개)", min_value=5, max_value=300, value=50, step=5,
                         disabled=(source_mode == "직접 입력"))
with col3:
    k = st.number_input("최종 선별 종목 수 (K)", min_value=1, max_value=50, value=3, step=1)

lookback_options = {
    "13개월 (기본, 모멘텀 점수 계산에 필요한 최소 기간)": 420,
    "3년": 1095,
    "5년": 1825,
    "10년": 3650,
}
lookback_label = st.selectbox(
    "가격 데이터 조회 기간",
    options=list(lookback_options.keys()),
    help="모멘텀 점수는 항상 최근 13개월 데이터로 계산됩니다. 기간을 늘리면 백테스트에서 더 과거 날짜까지 선택할 수 있지만, 데이터 다운로드 시간이 길어집니다.",
)
lookback_days = lookback_options[lookback_label]

manual_tickers = None
if source_mode == "직접 입력":
    ticker_hint = "예: 005930.KS, 000660.KS, 035420.KS" if country == "KR" else "예: AAPL, MSFT, NVDA"
    manual_input = st.text_area("티커 목록 (쉼표로 구분)", placeholder=ticker_hint, height=80)
    manual_tickers = [t.strip() for t in manual_input.split(",") if t.strip()]

run = st.button("스크리닝 실행", type="primary")

# ------------------------------------------------------------------
# 실행: 버튼을 눌렀을 때만 데이터를 새로 가져와 session_state에 저장
# ------------------------------------------------------------------
if run:
    try:
        if source_mode == "직접 입력":
            if not manual_tickers:
                st.warning("티커를 하나 이상 입력해주세요.")
                st.stop()
            universe = pd.DataFrame({"ticker": manual_tickers, "name": manual_tickers, "market_cap": None})
        else:
            with st.spinner(f"{country} 시가총액 상위 {n}개 유니버스 구성 중..."):
                universe = get_universe(country, int(n))

        with st.spinner(f"가격 데이터 다운로드 중... ({lookback_label} 기준, 종목 수에 따라 시간이 걸릴 수 있습니다)"):
            price_matrix = fetch_price_matrix(universe["ticker"].tolist(), lookback_days=lookback_days)

        with st.spinner("모멘텀 점수 계산 중..."):
            scored = compute_momentum_scores(price_matrix)
            scored = scored.merge(universe[["ticker", "name", "market_cap"]], on="ticker", how="left")
            scored = scored.sort_values("score", ascending=False).reset_index(drop=True)

        top_k = scored.head(int(k))

        # 다음 재실행(rerun)에서도 유지되도록 session_state에 저장
        st.session_state.result = {
            "universe": universe,
            "price_matrix": price_matrix,
            "scored": scored,
            "top_k": top_k,
            "source_mode": source_mode,
            "n_universe": len(universe),
        }

    except Exception as e:
        st.session_state.result = None
        st.error(f"오류 발생: {e}")
        st.info("네트워크 상태나 종목 수(N)를 줄여서 다시 시도해보세요.")

# ------------------------------------------------------------------
# 결과 표시: session_state에 저장된 결과가 있으면 항상 표시
# (백테스트 날짜를 바꾸는 등 위젯 조작으로 재실행되어도 여기는 계속 실행됨)
# ------------------------------------------------------------------
result = st.session_state.result

if result is None:
    st.info("옵션을 설정하고 '스크리닝 실행' 버튼을 눌러주세요.")
else:
    universe = result["universe"]
    price_matrix = result["price_matrix"]
    scored = result["scored"]
    top_k = result["top_k"]
    res_source_mode = result["source_mode"]

    st.success(f"유니버스 구성 완료: {result['n_universe']}개 종목")

    st.subheader(f"🏆 모멘텀 점수 상위 {len(top_k)}개 종목")
    display_top = top_k[["ticker", "name", "score", "R_1m", "R_3m", "R_6m", "R_12m", "market_cap"]].copy()
    for c in ["score", "R_1m", "R_3m", "R_6m", "R_12m"]:
        display_top[c] = (display_top[c] * 100).round(2)
    display_top.columns = ["티커", "종목명", "점수(%)", "1개월(%)", "3개월(%)", "6개월(%)", "12개월(%)", "시가총액"]
    st.dataframe(display_top, use_container_width=True, hide_index=True)

    st.subheader("📋 전체 후보군 결과")
    st.caption(f"유효 종목 {len(scored)}개 (데이터 부족 종목 자동 제외됨)")
    display_full = scored[["ticker", "name", "score", "R_1m", "R_3m", "R_6m", "R_12m", "market_cap"]].copy()
    for c in ["score", "R_1m", "R_3m", "R_6m", "R_12m"]:
        display_full[c] = (display_full[c] * 100).round(2)
    display_full.columns = ["티커", "종목명", "점수(%)", "1개월(%)", "3개월(%)", "6개월(%)", "12개월(%)", "시가총액"]
    st.dataframe(display_full, use_container_width=True, hide_index=True)

    csv = scored.to_csv(index=False).encode("utf-8-sig")
    st.download_button("결과 CSV 다운로드", csv, "screener_result.csv", "text/csv")

    # --- 간단 백테스트: 선정된 K개 종목의 개별 성과 + 동일가중 포트폴리오 성과 ---
    st.subheader("📈 간단 백테스트 (Buy & Hold)")
    st.caption("선정된 종목을 지정 기간 동안 각각 매수해 그대로 보유했다고 가정한 결과입니다. 리밸런싱·거래비용은 반영되지 않습니다.")

    data_min = price_matrix.index.min().date()
    data_max = price_matrix.index.max().date()

    bt_col1, bt_col2, bt_col3 = st.columns(3)
    with bt_col1:
        bt_start = st.date_input("백테스트 시작일", value=data_min, min_value=data_min, max_value=data_max, key="bt_start")
    with bt_col2:
        bt_end = st.date_input("백테스트 종료일", value=data_max, min_value=data_min, max_value=data_max, key="bt_end")
    with bt_col3:
        capital = st.number_input("초기 투자금", min_value=100_000, value=10_000_000, step=100_000, key="bt_capital")

    extra_input = st.text_input(
        "비교용 종목 추가 (선택, 쉼표로 구분)",
        placeholder="예: 005930.KS (삼성전자)",
        help="선정된 종목 외에 비교하고 싶은 종목을 티커로 입력하세요. 예: 005930.KS, AAPL",
        key="bt_extra_tickers",
    )
    # yfinance 티커는 대문자가 표준이므로 자동 변환 (005930.ks -> 005930.KS)
    extra_tickers = [t.strip().upper() for t in extra_input.split(",") if t.strip()]

    if bt_start >= bt_end:
        st.warning("시작일은 종료일보다 이전이어야 합니다.")
    else:
        try:
            bt_tickers = list(top_k["ticker"])
            bt_price_matrix = price_matrix

            # 비교용으로 추가한 종목 중, 기존 가격 데이터에 없는 것만 추가로 다운로드
            missing = [t for t in extra_tickers if t not in bt_price_matrix.columns]
            if missing:
                with st.spinner(f"비교 종목 가격 데이터 다운로드 중... ({', '.join(missing)})"):
                    from screener import fetch_price_matrix as _fetch
                    try:
                        extra_prices = _fetch(missing, lookback_days=lookback_days)
                        bt_price_matrix = bt_price_matrix.join(extra_prices, how="outer")
                    except Exception as fetch_err:
                        st.warning(f"비교 종목 데이터를 가져오지 못했습니다: {fetch_err}")

            # 다운로드 후에도 여전히 없는 티커는 제외하고, 어떤 티커가 빠졌는지 안내
            still_missing = [t for t in extra_tickers if t not in bt_price_matrix.columns]
            if still_missing:
                st.warning(f"다음 티커는 데이터를 찾을 수 없어 백테스트에서 제외됩니다: {', '.join(still_missing)} "
                            f"(티커 형식을 확인해주세요. 한국: 005930.KS, 미국: AAPL)")

            for t in extra_tickers:
                if t not in bt_tickers and t in bt_price_matrix.columns:
                    bt_tickers.append(t)

            bt = run_simple_backtest(
                bt_price_matrix, bt_tickers, capital,
                start_date=bt_start, end_date=bt_end,
            )

            # --- 종목별 + 포트폴리오 성과 테이블 ---
            # name_map에 없는 티커(직접 추가한 비교 종목 등)는 티커 자체를 이름으로 표시
            name_map = dict(zip(universe["ticker"], universe["name"])) if res_source_mode != "직접 입력" else {}
            table_rows = []
            for t, info in bt["per_ticker"].items():
                m = info["metrics"]
                table_rows.append({
                    "종목": name_map.get(t, t), "티커": t,
                    "시작일": m["start_date"].date(), "종료일": m["end_date"].date(),
                    "총수익률(%)": round(m["total_return"] * 100, 2),
                    "CAGR(%)": round(m["cagr"] * 100, 2) if pd.notna(m["cagr"]) else None,
                    "MDD(%)": round(m["mdd"] * 100, 2),
                })
            pm = bt["portfolio"]["metrics"]
            table_rows.append({
                "종목": "📊 포트폴리오(동일가중)", "티커": "-",
                "시작일": pm["start_date"].date(), "종료일": pm["end_date"].date(),
                "총수익률(%)": round(pm["total_return"] * 100, 2),
                "CAGR(%)": round(pm["cagr"] * 100, 2) if pd.notna(pm["cagr"]) else None,
                "MDD(%)": round(pm["mdd"] * 100, 2),
            })
            st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

            if (bt_end - bt_start).days < 30:
                st.caption("⚠️ 기간이 30일 미만이라 CAGR은 연환산 왜곡이 커서 표시하지 않았습니다.")

            # --- 통합 자산곡선 차트 (종목별 + 포트폴리오) ---
            chart_df = pd.DataFrame({name_map.get(t, t): info["series"] for t, info in bt["per_ticker"].items()})
            chart_df["포트폴리오(동일가중)"] = bt["portfolio"]["series"]

            log_scale = st.checkbox(
                "Y축 로그 스케일", value=True,
                help="기간이 길거나 종목 간 등락폭 차이가 크면 로그 스케일이 비교하기 쉽습니다. 초반 구간이 0에 붙어 보이면 켜보세요.",
            )
            st.caption("Y축 단위: 각 종목에 초기 투자금을 단독 투자했다고 가정했을 때의 평가금액 (모든 선이 같은 시작값에서 출발)")

            chart_long = chart_df.reset_index().melt(id_vars=chart_df.index.name or "index",
                                                       var_name="종목", value_name="평가금액")
            chart_long = chart_long.rename(columns={chart_df.index.name or "index": "날짜"})

            y_scale = alt.Scale(type="log") if log_scale else alt.Scale(type="linear")
            legend_selection = alt.selection_point(fields=["종목"], bind="legend")
            line_chart = (
                alt.Chart(chart_long)
                .mark_line()
                .encode(
                    x=alt.X("날짜:T", title="날짜"),
                    y=alt.Y("평가금액:Q", scale=y_scale, title="평가금액"),
                    color=alt.Color("종목:N", title="종목"),
                    opacity=alt.condition(legend_selection, alt.value(1), alt.value(0.08)),
                    tooltip=["날짜:T", "종목:N", alt.Tooltip("평가금액:Q", format=",.0f")],
                )
                .add_params(legend_selection)
                .interactive()
            )
            st.caption("💡 범례의 종목명을 클릭하면 해당 선만 강조됩니다 (Shift+클릭으로 여러 개 선택, 빈 공간 클릭 시 초기화)")
            st.altair_chart(line_chart, use_container_width=True)

        except Exception as e:
            st.warning(f"백테스트를 계산할 수 없습니다: {e}")
