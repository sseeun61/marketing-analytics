"""Part 15 — Braze users + purchases → RFM 점수 + 7 세그먼트 (CLAUDE.md §12·§13).

사용:  python _rfm.py [스냅샷기준일 YYYY-MM-DD]   (기본 2025-03-31)
결과:  outputs/segmented_users.csv
"""
import sys
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

BASE = Path(__file__).parent
OUT = BASE / "outputs"
# 주간 export 가 쌓여도 최신 users 파일 1개 + purchases 전체를 읽는다
USERS_CSV = max((BASE / "raw/braze").glob("users_*.csv")).as_posix()
PURCHASES_CSV = (BASE / "raw/braze/purchases_*.csv").as_posix()

DEFAULT_SNAPSHOT = date(2025, 3, 31)
SEGMENTS = ["Champion", "Loyal", "At-Risk", "Hibernating", "Lost", "New", "Potential"]


def compute_rfm(snapshot: date = DEFAULT_SNAPSHOT) -> pd.DataFrame:
    """CLAUDE.md §12 기준으로 R/F/M 점수와 §13 세그먼트를 계산한다."""
    con = duckdb.connect()
    snap = snapshot.isoformat()
    return con.execute(f"""
        WITH users AS (
            SELECT * FROM read_csv_auto('{USERS_CSV}')
        ),
        p AS (  -- 환불/0원 제외 (§7-1), 스냅샷 이전 구매만
            SELECT DISTINCT * FROM read_csv_auto('{PURCHASES_CSV}', union_by_name=true)
            WHERE amount > 0 AND purchase_at <= DATE '{snap}'
        ),
        agg AS (
            SELECT user_id,
                   MAX(purchase_at) AS last_purchase_q1,
                   COUNT(*) FILTER (WHERE purchase_at > DATE '{snap}' - INTERVAL 90 DAY) AS f_90d,
                   COALESCE(SUM(amount) FILTER (WHERE purchase_at > DATE '{snap}' - INTERVAL 90 DAY), 0) AS m_90d
            FROM p GROUP BY user_id
        ),
        base AS (
            SELECT u.external_id, u.email, u.push_enabled, u.marketing_opt_in,
                   u.signup_date, u.attribution_source, u._segment_truth,
                   -- R: purchases 로그(Q1) + users 프로필의 last_purchase_at 중 최근값 (Q1 이전 구매 반영)
                   GREATEST(a.last_purchase_q1,
                            CASE WHEN u.last_purchase_at <= DATE '{snap}' THEN u.last_purchase_at END) AS last_purchase,
                   COALESCE(a.f_90d, 0) AS f_90d,
                   COALESCE(a.m_90d, 0) AS m_90d
            FROM users u LEFT JOIN agg a ON a.user_id = u.external_id
            WHERE u.signup_date <= DATE '{snap}'
        ),
        scored AS (
            SELECT *,
                   DATE '{snap}' - last_purchase AS recency_days,
                   CASE
                       WHEN last_purchase IS NULL THEN 1
                       WHEN DATE '{snap}' - last_purchase <= 30 THEN 5
                       WHEN DATE '{snap}' - last_purchase <= 60 THEN 4
                       WHEN DATE '{snap}' - last_purchase <= 90 THEN 3
                       WHEN DATE '{snap}' - last_purchase <= 180 THEN 2
                       ELSE 1 END AS R,
                   CASE
                       WHEN f_90d >= 10 THEN 5
                       WHEN f_90d >= 4 THEN 4
                       WHEN f_90d >= 2 THEN 3
                       WHEN f_90d = 1 THEN 2
                       ELSE 1 END AS F,
                   CASE WHEN m_90d > 0
                        THEN NTILE(5) OVER (PARTITION BY m_90d > 0 ORDER BY m_90d)
                        ELSE 1 END AS M,
                   DATE '{snap}' - signup_date AS days_since_signup
            FROM base
        )
        SELECT external_id, email, push_enabled, marketing_opt_in, signup_date, attribution_source,
               last_purchase, recency_days, f_90d AS purchase_count_90d, m_90d AS purchase_amount_90d,
               R, F, M, CAST(R AS VARCHAR) || F || M AS RFM,
               CASE  -- §13 매트릭스, 위에서부터 우선 적용
                   WHEN R >= 4 AND F >= 4 AND M >= 4 THEN 'Champion'
                   WHEN R >= 3 AND F >= 4 THEN 'Loyal'
                   WHEN days_since_signup <= 30 AND f_90d <= 1 THEN 'New'
                   -- §12 데이터 보정: F/M 이 90일 창이라 R≤2(91일+) 유저는 항상 F=1·M=1.
                   -- 그래서 At-Risk / Hibernating 은 R=2 구간을 경과일로 나눈다.
                   WHEN R = 2 AND recency_days <= 135 THEN 'At-Risk'
                   WHEN R = 2 THEN 'Hibernating'
                   WHEN R = 1 AND F <= 2 THEN 'Lost'
                   ELSE 'Potential' END AS segment,
               _segment_truth
        FROM scored
        ORDER BY external_id
    """).df()


def segment_distribution(df: pd.DataFrame) -> pd.DataFrame:
    dist = (
        df.groupby("segment")
        .agg(인원=("external_id", "count"),
             평균_R=("R", "mean"), 평균_F=("F", "mean"), 평균_M=("M", "mean"),
             평균_90일매출=("purchase_amount_90d", "mean"),
             마케팅수신동의=("marketing_opt_in", "mean"))
        .reindex(SEGMENTS).dropna(how="all")
    )
    dist.insert(1, "비율(%)", (dist["인원"] / dist["인원"].sum() * 100).round(1))
    return dist.round(2)


def main() -> None:
    snapshot = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SNAPSHOT
    df = compute_rfm(snapshot)
    OUT.mkdir(exist_ok=True)
    df.drop(columns="_segment_truth").to_csv(OUT / "segmented_users.csv", index=False, encoding="utf-8-sig")

    print(f"스냅샷 {snapshot} | 유저 {len(df):,}명 → outputs/segmented_users.csv\n")
    print(segment_distribution(df).to_string())
    agree = (df["segment"] == df["_segment_truth"]).mean() * 100
    print(f"\n샘플 정답(_segment_truth) 대비 일치율: {agree:.1f}%")
    print(pd.crosstab(df["_segment_truth"], df["segment"]).to_string())


if __name__ == "__main__":
    main()
