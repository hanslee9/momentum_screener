# Relative Momentum Screener

한국(KOSPI/KOSDAQ)·미국(NASDAQ/NYSE/AMEX) 시가총액 상위 N개 종목 중, 상대모멘텀 점수가 가장 높은
상위 K개 종목을 현재 시점 기준으로 선별하는 1회성 스크리너입니다.

## 로직

1. **유니버스 구성**: 국가별 시가총액 상위 N개 종목 추출 (FinanceDataReader)
2. **가격 데이터**: 약 13개월치 일별 종가를 배치 다운로드 (yfinance)
3. **구간 수익률**: 각 종목의 1/3/6/12개월 수익률 계산 (최근 1개월 포함, 거래일 기준 근사: 21/63/126/252일)
4. **모멘텀 점수**: `0.40*R_1m + 0.30*R_3m + 0.20*R_6m + 0.10*R_12m` (단기 비중 가중평균)
5. **선별**: 점수 내림차순 정렬 후 상위 K개 출력

상장 이력이 13개월 미만이거나 가격 데이터가 결측인 종목은 자동 제외됩니다.

## 실행 방법

### 방법 A. Streamlit 웹앱 (권장)

1. 이 저장소를 GitHub에 push
2. [share.streamlit.io](https://share.streamlit.io) 접속 → GitHub 계정으로 로그인
3. "New app" → 이 저장소 선택 → Main file path에 `streamlit_app.py` 입력 → Deploy
4. 배포된 웹 페이지에서 국가/N/K 입력 후 "스크리닝 실행" 버튼 클릭

### 방법 B. 로컬 CLI 실행

```bash
pip install -r requirements.txt
python screener.py --country KR --n 50 --k 3
```


### 옵션

| 옵션 | 설명 | 기본값 |
|---|---|---|
| `--country` | `KR` 또는 `US` | 필수 |
| `--n` | 시가총액 상위 몇 개를 후보군으로 볼지 | 50 |
| `--k` | 최종 몇 개 종목을 선별할지 | 3 |
| `--out` | 전체 스크리닝 결과 저장 경로 (CSV) | screener_result.csv |

## 출력

- 콘솔: 상위 K개 종목의 티커·종목명·모멘텀 점수·구간별 수익률
- `screener_result.csv`: 후보군 전체(N개 중 유효 종목)의 점수·순위 전체 결과

## 한계 / 참고사항

- 시가총액·가격 데이터는 FinanceDataReader·yfinance 서비스 상태에 의존합니다.
- 거래비용·슬리피지는 반영되지 않습니다 (순수 랭킹 로직).
- 과거 수익률 기반 스크리닝이며, 미래 수익률을 보장하지 않습니다.
- 다음 단계로 이 로직을 매월 반복 실행하는 백테스트 엔진으로 확장 가능합니다 (추후 작업).
