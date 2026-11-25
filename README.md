# 네이버 플러스 스토어 마케팅 분석 (실습)

채널 광고 + AppsFlyer 5-key 조인 → DuckDB → Streamlit 대시보드, Braze RFM → 카피 실험 → 세그먼트별 카피 생성.
회사 룰·지표 정의는 [CLAUDE.md](CLAUDE.md) 참고. 데이터는 전부 실습용 샘플입니다.

## 실행

```bash
pip install -r requirements.txt
python _join_analysis_duckdb.py          # 조인 → outputs/joined_result.parquet / .csv
python _rfm.py                           # RFM → outputs/segmented_users.csv
python _copy_experiments.py              # 카피 3라운드 분석
python -m streamlit run dashboard_app.py --server.port 8502
```

## 매일 운영

어제 데이터를 `raw/channel/YYYY-MM-DD.csv`, `raw/appsflyer/YYYY-MM-DD.csv` 로 저장 → 대시보드 새로고침.
`*.csv` glob 으로 자동 병합됩니다.

## 대시보드 탭

Overview(자동 인사이트) · 전체 추세 · 목적별 · 소재 속성 · A/B 테스트 · 타겟그룹 · 캠페인 랭킹 · RFM 세그먼트(CSV 다운로드) · Raw 데이터
