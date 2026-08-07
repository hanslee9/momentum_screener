"""
Relative Momentum Screener - Streamlit 웹앱

screener.py의 로직을 그대로 사용하되, 웹 UI로 입력값을 받고 결과를 표/차트로 보여준다.
"""

import streamlit as st
import pandas as pd

from screener import get_universe, fetch_price_matrix, compute_momentum_scores, run_simple_backtest

st.set_page_config(page_title="상대모멘텀 스크리너", layout="wide")

st.markdown(
    "<h3 style='margin-bottom:0;'>📊 상대모멘텀 스크리너</h3>",
    unsafe_allow_html=True,
)
st.caption("시가총액 상위 N개 종목 중, 1/3/6/12개월 가중평균 수익률(40/30/20/10%) 상위 K개를 선별합니다.")

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

manual_tickers = None
if source_mode == "직접 입력":
    ticker_hint = "예: 005930.KS, 000660.KS, 035420.KS" if country == "KR" else "예: AAPL, MSFT, NVDA"
    manual_input = st.text_area("티커 목록 (쉼표로 구분)", placeholder=ticker_hint, height=80)
    manual_tickers = [t.strip() for t in manual_input.split(",") if t.strip()]

run = st.button("스크리닝 실행", type="primary")

# ------------------------------------------------------------------
# 실행
# ------------------------------------------------------------------
if run:
    try:
        if source_mode == "직접 입력":
            if not manual_tickers:
                st.warning("티커를 하나 이상 입력해주세요.")
                st.stop()
            universe = pd.DataFrame({"ticker": manual_tickers, "name": manual_tickers, "market_cap": None})
            st.success(f"직접 입력한 {len(universe)}개 종목으로 진행합니다.")
        else:
            with st.spinner(f"{country} 시가총액 상위 {n}개 유니버스 구성 중..."):
                universe = get_universe(country, int(n))
            st.success(f"유니버스 구성 완료: {len(universe)}개 종목")

        with st.spinner("가격 데이터 다운로드 중... (종목 수에 따라 1~3분 소요될 수 있습니다)"):
            price_matrix = fetch_price_matrix(universe["ticker"].tolist())

        with st.spinner("모멘텀 점수 계산 중..."):
            scored = compute_momentum_scores(price_matrix)
            scored = scored.merge(universe[["ticker", "name", "market_cap"]], on="ticker", how="left")
            scored = scored.sort_values("score", ascending=False).reset_index(drop=True)

        top_k = scored.head(int(k))

        st.subheader(f"🏆 모멘텀 점수 상위 {k}개 종목")
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
            bt_start = st.date_input("백테스트 시작일", value=data_min, min_value=data_min, max_value=data_max)
        with bt_col2:
            bt_end = st.date_input("백테스트 종료일", value=data_max, min_value=data_min, max_value=data_max)
        with bt_col3:
            capital = st.number_input("초기 투자금", min_value=100_000, value=10_000_000, step=100_000)

        if bt_start >= bt_end:
            st.warning("시작일은 종료일보다 이전이어야 합니다.")
        else:
            try:
                bt = run_simple_backtest(
                    price_matrix, top_k["ticker"].tolist(), capital,
                    start_date=bt_start, end_date=bt_end,
                )

                # --- 종목별 + 포트폴리오 성과 테이블 ---
                name_map = dict(zip(universe["ticker"], universe["name"])) if source_mode != "직접 입력" else {}
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
                st.line_chart(chart_df)

            except Exception as e:
                st.warning(f"백테스트를 계산할 수 없습니다: {e}")

    except Exception as e:
        st.error(f"오류 발생: {e}")
        st.info("네트워크 상태나 종목 수(N)를 줄여서 다시 시도해보세요.")
else:
    st.info("왼쪽 옵션을 설정하고 '스크리닝 실행' 버튼을 눌러주세요.")
