"""Part 5·6·9 — 채널 × AppsFlyer 5-key JOIN (DuckDB 버전).

raw/channel/*.csv, raw/appsflyer/*.csv 를 glob 으로 통째로 읽기 때문에
매일 CSV 한 장씩 폴더에 떨어뜨리면 자동 반영된다.
결과: 내부용 Parquet + 공유용 CSV(utf-8-sig) 동시 저장.
"""
from pathlib import Path

import duckdb

BASE = Path(__file__).parent
OUT = BASE / "outputs"
OUT.mkdir(exist_ok=True)

CH_GLOB = (BASE / "raw" / "channel" / "*.csv").as_posix()
AF_GLOB = (BASE / "raw" / "appsflyer" / "*.csv").as_posix()

# CLAUDE.md §3-1 — 신규 채널은 여기와 §3-1을 같이 업데이트
CHANNEL_MAP = [("구글", "googleadwords_int"), ("메타", "Facebook Ads"), ("네이버", "naver_search"),
               ("카카오", "kakao_moment"), ("틱톡", "TikTok_Ads")]
BRAND_KW = "NVR_CMP_01_브랜드KW"  # §6-1 ROAS 산정 제외


def build_joined(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(f"""
        CREATE OR REPLACE VIEW channel AS
        SELECT * EXCLUDE (filename)
        FROM read_csv_auto('{CH_GLOB}', union_by_name=true, filename=true)
        -- 5-key 중복 시 최신 파일(수정본) 1행만 유지 (§7-3)
        QUALIFY ROW_NUMBER() OVER (PARTITION BY 일, 채널, 캠페인, 그룹, 소재 ORDER BY filename DESC) = 1
    """)
    con.execute(f"""
        CREATE OR REPLACE VIEW appsflyer AS
        SELECT * EXCLUDE (filename)
        FROM read_csv_auto('{AF_GLOB}', union_by_name=true, filename=true)
        QUALIFY ROW_NUMBER() OVER (PARTITION BY 일, 미디어소스, 캠페인, 그룹, 소재 ORDER BY filename DESC) = 1
    """)
    con.execute("CREATE OR REPLACE TABLE channel_map (채널 VARCHAR, 미디어소스 VARCHAR)")
    con.executemany("INSERT INTO channel_map VALUES (?, ?)", CHANNEL_MAP)
    # 5-key JOIN (CLAUDE.md §4-1)
    con.execute("""
        CREATE OR REPLACE TABLE joined AS
        SELECT
            c.*,
            a.클릭     AS af_클릭,
            a.회원가입 AS af_회원가입,
            a.구매     AS af_구매,
            a.구매매출 AS af_구매매출
        FROM channel c
        LEFT JOIN channel_map m ON c.채널 = m.채널
        LEFT JOIN appsflyer a
            ON a.일 = c.일 AND a.미디어소스 = m.미디어소스
           AND a.캠페인 = c.캠페인 AND a.그룹 = c.그룹 AND a.소재 = c.소재
    """)


def main() -> None:
    con = duckdb.connect()
    build_joined(con)

    n_total, n_miss, d_min, d_max = con.execute(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE af_회원가입 IS NULL), MIN(일), MAX(일) FROM joined"
    ).fetchone()
    print(f"기간 {d_min} ~ {d_max} | 조인 {n_total:,}행 / 매칭 실패 {n_miss}건")
    if n_miss:
        print("⚠️ 매칭 실패 행이 있습니다 — CHANNEL_MAP(§3-1) 또는 AF 파일 누락 확인")
    # 이하 요약은 브랜드KW 제외 (§6-1). joined 원본 저장은 전체 유지.
    con.execute(f"CREATE OR REPLACE VIEW joined_ex AS SELECT * FROM joined WHERE 캠페인 <> '{BRAND_KW}'")

    print("\n[채널별 요약 — AF 매출 기준, 브랜드KW 제외]")
    summary = con.execute("""
        SELECT 채널,
               SUM(비용) AS 비용,
               SUM(af_회원가입) AS af_가입,
               SUM(af_구매매출) AS af_매출,
               ROUND(SUM(af_구매매출)::DOUBLE / NULLIF(SUM(비용), 0), 2) AS ROAS,
               ROUND(SUM(비용)::DOUBLE / NULLIF(SUM(af_회원가입), 0), 0) AS CAC,
               ROUND(SUM(af_회원가입)::DOUBLE / NULLIF(SUM(회원가입), 0) * 100, 1) AS AF커버리지
        FROM joined_ex GROUP BY 채널 ORDER BY ROAS DESC
    """).df()
    print(summary.to_string(index=False))

    print("\n[채널 내 소재 랭킹 Top 1 — 윈도우 함수]")
    ranking = con.execute("""
        SELECT * FROM (
            SELECT 채널, 소재,
                   SUM(비용) AS 비용,
                   SUM(af_구매매출) AS 매출_AF,
                   ROUND(SUM(af_구매매출)::DOUBLE / NULLIF(SUM(비용), 0), 2) AS ROAS_AF,
                   RANK() OVER (PARTITION BY 채널
                                ORDER BY SUM(af_구매매출)::DOUBLE / NULLIF(SUM(비용), 0) DESC) AS 순위
            FROM joined_ex GROUP BY 채널, 소재
        ) WHERE 순위 = 1 ORDER BY 채널
    """).df()
    print(ranking.to_string(index=False))

    campaign = con.execute("""
        SELECT 채널, 캠페인, 캠페인목적,
               SUM(비용) AS 비용, SUM(af_구매매출) AS af_매출,
               ROUND(SUM(af_구매매출)::DOUBLE / NULLIF(SUM(비용), 0), 2) AS ROAS
        FROM joined_ex GROUP BY ALL ORDER BY ROAS DESC
    """).df()

    # Part 6 — 내부용 Parquet + 공유용 CSV
    out_parquet = (OUT / "joined_result.parquet").as_posix()
    con.execute(f"COPY joined TO '{out_parquet}' (FORMAT PARQUET)")
    con.execute("SELECT * FROM joined").df().to_csv(
        OUT / "joined_result.csv", index=False, encoding="utf-8-sig"
    )
    summary.to_csv(OUT / "summary_by_channel.csv", index=False, encoding="utf-8-sig")
    campaign.to_csv(OUT / "summary_by_campaign.csv", index=False, encoding="utf-8-sig")

    pq = (OUT / "joined_result.parquet").stat().st_size
    cs = (OUT / "joined_result.csv").stat().st_size
    print(f"\n저장: Parquet {pq/1024:.0f} KB / CSV {cs/1024:.0f} KB (압축 {cs/pq:.1f}배)")


if __name__ == "__main__":
    main()
