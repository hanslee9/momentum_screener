"""
Relative Momentum Screener - Streamlit 웹앱

개별종목과 ETF는 변동성 구조 자체가 달라(특히 브로드 지수 ETF는 원래 변동성이 낮음)
같은 기준으로 순위를 매기면 ETF가 구조적으로 밀리기 때문에, 두 유니버스를
완전히 분리된 탭(입력·테이블·백테스트 그래프 모두 독립)으로 운영한다.

주의: Streamlit은 위젯 조작(날짜 변경 등) 시 스크립트 전체가 재실행되므로,
각 섹션의 결과를 st.session_state에 저장해 위젯 조작으로 결과가 사라지지 않게 한다.
"""

import streamlit as st
import pandas as pd
import altair as alt

from screener import (
    get_universe, get_etf_universe, fetch_price_matrix,
    compute_momentum_scores, run_simple_backtest,
)

st.set_page_config(page_title="상대모멘텀 스크리너", layout="wide")

st.markdown(
    "<h5 style='margin-bottom:0;'>📊 상대모멘텀 스크리너</h5>",
    unsafe_allow_html=True,
)
st.caption("1/3/6/12개월 가중평균 수익률(10/20/30/40%, 장기 비중 강화)로 순위를 매깁니다. "
           "개별종목과 ETF는 변동성 구조가 달라 별도 탭에서 각각 스크리닝합니다.")

country = st.selectbox("국가", options=["KR", "US"], format_func=lambda x: "한국" if x == "KR" else "미국")

LOOKBACK_OPTIONS = {
    "13개월 (기본, 모멘텀 점수 계산에 필요한 최소 기간)": 420,
    "3년": 1095,
    "5년": 1825,
    "10년": 3650,
}


# ------------------------------------------------------------------
# 공통 렌더링 함수 (개별종목 탭 / ETF 탭에서 각각 호출)
# ------------------------------------------------------------------
def render_section(section_key: str, universe_label: str, is_etf: bool):
    result_key = f"{section_key}_result"
    if result_key not in st.session_state:
        st.session_state[result_key] = None

    col1, col2, col3 = st.columns(3)
    with col1:
        if is_etf:
            count = st.number_input(f"{universe_label} 수 (AUM 상위)", min_value=1, max_value=50, value=10, step=1,
                                     key=f"{section_key}_count",
                                     help="지수/섹터/테마 대표 ETF 후보군 중 순자산총액(AUM) 상위 개수")
        else:
            count = st.number_input(f"{universe_label} 수 (시가총액 상위)", min_value=5, max_value=300, value=50, step=5,
                                     key=f"{section_key}_count")
    with col2:
        k = st.number_input("최종 선별 수 (K)", min_value=1, max_value=30, value=3, step=1, key=f"{section_key}_k")
    with col3:
        lookback_label = st.selectbox("가격 데이터 조회 기간", options=list(LOOKBACK_OPTIONS.keys()),
                                       key=f"{section_key}_lookback",
                                       help="모멘텀 점수는 항상 최근 13개월 데이터로 계산됩니다. 기간을 늘리면 백테스트에서 더 과거 날짜까지 선택 가능합니다.")
    lookback_days = LOOKBACK_OPTIONS[lookback_label]

    run = st.button("스크리닝 실행", type="primary", key=f"{section_key}_run")

    if run:
        try:
            with st.spinner(f"{country} {universe_label} {count}개 유니버스 구성 중..."):
                if is_etf:
                    universe = get_etf_universe(country, int(count))
                else:
                    universe = get_universe(country, int(count))
                    universe["asset_type"] = "종목"

            with st.spinner(f"가격 데이터 다운로드 중... ({lookback_label} 기준)"):
                price_matrix = fetch_price_matrix(universe["ticker"].tolist(), lookback_days=lookback_days)

            with st.spinner("모멘텀 점수 계산 중..."):
                scored = compute_momentum_scores(price_matrix)
                merge_cols = ["ticker", "name", "market_cap"]
                if "size_source" in universe.columns:
                    merge_cols.append("size_source")
                scored = scored.merge(universe[merge_cols], on="ticker", how="left")
                scored = scored.sort_values("score", ascending=False).reset_index(drop=True)

            top_k = scored.head(int(k))

            st.session_state[result_key] = {
                "universe": universe, "price_matrix": price_matrix, "scored": scored,
                "top_k": top_k, "n_universe": len(universe), "lookback_label": lookback_label,
            }
        except Exception as e:
            st.session_state[result_key] = None
            st.error(f"오류 발생: {e}")
            st.info("네트워크 상태나 개수를 줄여서 다시 시도해보세요.")

    result = st.session_state[result_key]
    if result is None:
        st.info("옵션을 설정하고 '스크리닝 실행' 버튼을 눌러주세요.")
        return

    universe = result["universe"]
    price_matrix = result["price_matrix"]
    scored = result["scored"]
    top_k = result["top_k"]

    st.success(f"유니버스 구성 완료: {result['n_universe']}개")

    cap_label = "AUM" if is_etf else "시가총액"
    top_cols = ["ticker", "name", "score", "R_1m", "R_3m", "R_6m", "R_12m", "market_cap"]
    top_col_names = ["티커", "이름", "점수(%)", "1개월(%)", "3개월(%)", "6개월(%)", "12개월(%)", cap_label]
    if is_etf and "size_source" in top_k.columns:
        top_cols.append("size_source")
        top_col_names.append("규모 산정방식")

    st.subheader(f"🏆 모멘텀 점수 상위 {len(top_k)}개")
    display_top = top_k[top_cols].copy()
    for c in ["score", "R_1m", "R_3m", "R_6m", "R_12m"]:
        display_top[c] = (display_top[c] * 100).round(2)
    display_top.columns = top_col_names
    st.dataframe(display_top, use_container_width=True, hide_index=True)
    if is_etf and "size_source" in top_k.columns and (top_k["size_source"] == "거래대금(추정)").any():
        st.caption("⚠️ 일부 ETF는 AUM 데이터가 없어 최근 1개월 평균 거래대금으로 규모를 추정했습니다.")

    st.subheader("📋 전체 후보군 결과")
    st.caption(f"유효 {len(scored)}개 (데이터 부족 제외됨)")
    display_full = scored[["ticker", "name", "score", "R_1m", "R_3m", "R_6m", "R_12m", "market_cap"]].copy()
    for c in ["score", "R_1m", "R_3m", "R_6m", "R_12m"]:
        display_full[c] = (display_full[c] * 100).round(2)
    display_full.columns = ["티커", "이름", "점수(%)", "1개월(%)", "3개월(%)", "6개월(%)", "12개월(%)", cap_label]
    st.dataframe(display_full, use_container_width=True, hide_index=True)

    csv = scored.to_csv(index=False).encode("utf-8-sig")
    st.download_button("결과 CSV 다운로드", csv, f"{section_key}_screener_result.csv", "text/csv",
                        key=f"{section_key}_csv")

    # --- 간단 백테스트 ---
    st.subheader("📈 간단 백테스트 (Buy & Hold)")
    st.caption("선정된 종목을 지정 기간 동안 각각 매수해 그대로 보유했다고 가정한 결과입니다. 리밸런싱·거래비용은 반영되지 않습니다.")

    data_min = price_matrix.index.min().date()
    data_max = price_matrix.index.max().date()

    bt_col1, bt_col2, bt_col3 = st.columns(3)
    with bt_col1:
        bt_start = st.date_input("백테스트 시작일", value=data_min, min_value=data_min, max_value=data_max,
                                  key=f"{section_key}_bt_start")
    with bt_col2:
        bt_end = st.date_input("백테스트 종료일", value=data_max, min_value=data_min, max_value=data_max,
                                key=f"{section_key}_bt_end")
    with bt_col3:
        capital = st.number_input("초기 투자금", min_value=100_000, value=10_000_000, step=100_000,
                                   key=f"{section_key}_bt_capital")

    extra_input = st.text_input(
        "비교용 티커 추가 (선택, 쉼표로 구분)",
        placeholder="예: 005930.KS (삼성전자)" if not is_etf else "예: SPY, QQQ",
        key=f"{section_key}_bt_extra",
    )
    extra_tickers = [t.strip().upper() for t in extra_input.split(",") if t.strip()]

    if bt_start >= bt_end:
        st.warning("시작일은 종료일보다 이전이어야 합니다.")
        return

    try:
        bt_tickers = list(top_k["ticker"])
        bt_price_matrix = price_matrix

        missing = [t for t in extra_tickers if t not in bt_price_matrix.columns]
        if missing:
            with st.spinner(f"비교 티커 가격 데이터 다운로드 중... ({', '.join(missing)})"):
                try:
                    extra_prices = fetch_price_matrix(missing, lookback_days=lookback_days)
                    bt_price_matrix = bt_price_matrix.join(extra_prices, how="outer")
                except Exception as fetch_err:
                    st.warning(f"비교 티커 데이터를 가져오지 못했습니다: {fetch_err}")

        still_missing = [t for t in extra_tickers if t not in bt_price_matrix.columns]
        if still_missing:
            st.warning(f"다음 티커는 데이터를 찾을 수 없어 제외됩니다: {', '.join(still_missing)}")

        for t in extra_tickers:
            if t not in bt_tickers and t in bt_price_matrix.columns:
                bt_tickers.append(t)

        bt = run_simple_backtest(bt_price_matrix, bt_tickers, capital, start_date=bt_start, end_date=bt_end)

        name_map = dict(zip(universe["ticker"], universe["name"]))
        table_rows = []
        for t, info in bt["per_ticker"].items():
            m = info["metrics"]
            table_rows.append({
                "이름": name_map.get(t, t), "티커": t,
                "시작일": m["start_date"].date(), "종료일": m["end_date"].date(),
                "총수익률(%)": round(m["total_return"] * 100, 2),
                "CAGR(%)": round(m["cagr"] * 100, 2) if pd.notna(m["cagr"]) else None,
                "MDD(%)": round(m["mdd"] * 100, 2),
            })
        pm = bt["portfolio"]["metrics"]
        table_rows.append({
            "이름": "📊 포트폴리오(동일가중)", "티커": "-",
            "시작일": pm["start_date"].date(), "종료일": pm["end_date"].date(),
            "총수익률(%)": round(pm["total_return"] * 100, 2),
            "CAGR(%)": round(pm["cagr"] * 100, 2) if pd.notna(pm["cagr"]) else None,
            "MDD(%)": round(pm["mdd"] * 100, 2),
        })
        st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

        if (bt_end - bt_start).days < 30:
            st.caption("⚠️ 기간이 30일 미만이라 CAGR은 연환산 왜곡이 커서 표시하지 않았습니다.")

        chart_df = pd.DataFrame({name_map.get(t, t): info["series"] for t, info in bt["per_ticker"].items()})
        chart_df["포트폴리오(동일가중)"] = bt["portfolio"]["series"]

        log_scale = st.checkbox("Y축 로그 스케일", value=True, key=f"{section_key}_log_scale",
                                 help="기간이 길거나 등락폭 차이가 크면 로그 스케일이 비교하기 쉽습니다.")
        st.caption("Y축 단위: 각 종목에 초기 투자금을 단독 투자했다고 가정했을 때의 평가금액 (모든 선이 같은 시작값에서 출발)")

        chart_long = chart_df.reset_index().melt(id_vars=chart_df.index.name or "index",
                                                   var_name="이름", value_name="평가금액")
        chart_long = chart_long.rename(columns={chart_df.index.name or "index": "날짜"})

        y_scale = alt.Scale(type="log") if log_scale else alt.Scale(type="linear")
        legend_selection = alt.selection_point(fields=["이름"], bind="legend")
        line_chart = (
            alt.Chart(chart_long)
            .mark_line()
            .encode(
                x=alt.X("날짜:T", title="날짜"),
                y=alt.Y("평가금액:Q", scale=y_scale, title="평가금액"),
                color=alt.Color("이름:N", title="이름"),
                opacity=alt.condition(legend_selection, alt.value(1), alt.value(0.08)),
                tooltip=["날짜:T", "이름:N", alt.Tooltip("평가금액:Q", format=",.0f")],
            )
            .add_params(legend_selection)
            .interactive()
        )
        st.caption("💡 범례를 클릭하면 해당 선만 강조됩니다 (Shift+클릭으로 다중 선택)")
        st.altair_chart(line_chart, use_container_width=True)

    except Exception as e:
        st.warning(f"백테스트를 계산할 수 없습니다: {e}")


# ------------------------------------------------------------------
# 탭 구성: 개별종목 / ETF 완전 분리
# ------------------------------------------------------------------
tab_stock, tab_etf = st.tabs(["📈 개별종목", "📊 ETF"])

with tab_stock:
    render_section("stock", "개별종목", is_etf=False)

with tab_etf:
    render_section("etf", "ETF", is_etf=True)
