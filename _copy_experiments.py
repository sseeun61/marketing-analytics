"""Part 17 — Braze 카피 실험 3라운드 분석.

1단계: Round 1 세그먼트 × 채널 open rate / CTR
2단계: 라운드 1/2/3 세그먼트별 open rate 비교 차트
3단계: (세그먼트, 톤) 피벗 + 평균 대비 +20% 승자 조합 ⭐

채널마다 base rate 가 완전히 달라서(push ≈4%, email ≈18%, in_app ≈32%, CLAUDE.md §14)
톤 비교는 '채널 평균 대비 배수(lift)'로 정규화한다.
톤은 메시지 문구로 직접 분류하고, 샘플 정답 라벨(_tone_truth)은 검증에만 쓴다.
"""
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px

BASE = Path(__file__).parent
OUT = BASE / "outputs"
OUT.mkdir(exist_ok=True)
CAMPAIGNS = (BASE / "raw/braze/campaigns/round*.csv").as_posix()
SEG_ORDER = ["Champion", "Loyal", "New", "Potential", "At-Risk", "Hibernating", "Lost"]

# 메시지 제목 → 톤 (CLAUDE.md §15 톤 4종). 문구를 읽고 분류.
TONE_BY_TITLE = {
    "고마워요, 플러스 VIP": "VIP",
    "VIP님만의 개인 큐레이션": "VIP",
    "[VIP 전용] 먼저 보시는 신상": "VIP",
    "오랜만이에요, 잘 지내셨어요?": "감성",
    "당신의 일상에 작은 쉼표": "감성",
    "기다리고 있었어요": "감성",
    "단 48시간, 마지막 기회": "긴급",
    "⏰ 오늘 자정 마감": "긴급",
    "무료배송 + 추가 적립 3%": "혜택",
    "지금 5,000원 적립금 받기": "혜택",
    "이번 주 플러스 할인 모아보기": "혜택",
}


def load() -> pd.DataFrame:
    con = duckdb.connect()
    df = con.execute(f"""
        SELECT *, CAST(regexp_extract(filename, 'round(\\d)', 1) AS INT) AS round
        FROM read_csv_auto('{CAMPAIGNS}', filename=true, union_by_name=true)
        WHERE delivered = 1                       -- 개봉률 = 개봉 / 도달 (§5-3)
    """).df()
    df["tone"] = df["message_title"].map(TONE_BY_TITLE)
    unmapped = df.loc[df["tone"].isna(), "message_title"].unique()
    if len(unmapped):
        raise ValueError(f"톤 미분류 메시지: {unmapped}")
    return df


def rates(g: pd.DataFrame) -> pd.Series:
    return pd.Series({
        "도달": len(g),
        "open_rate": g["opened"].mean() * 100,
        "CTR": g["clicked"].mean() * 100,
        "CVR": g["converted"].mean() * 100,
        "unsub": g["unsubscribed"].mean() * 100,
    })


def main() -> None:
    df = load()
    agree = (df["tone"] == df["_tone_truth"]).mean() * 100
    print(f"도달 {len(df):,}건 | 톤 분류 vs 샘플 정답 일치율 {agree:.1f}%\n")

    # 1단계 — Round 1 세그먼트 × 채널
    r1 = df[df["round"] == 1].groupby(["target_segment", "channel"]).apply(rates, include_groups=False)
    print("[1단계] Round 1 (혜택 톤 일괄) 세그먼트 × 채널")
    print(r1.round(2).to_string(), "\n")

    # 2단계 — 라운드별 세그먼트 open rate (채널 섞임 주의: lift 도 함께)
    ch_base = df.groupby("channel")["opened"].mean()
    df["lift"] = df["opened"] / df["channel"].map(ch_base)
    by_round = (df.groupby(["round", "target_segment"])
                  .agg(open_rate=("opened", "mean"), lift=("lift", "mean")).reset_index())
    by_round["open_rate"] *= 100
    print("[2단계] 라운드별 세그먼트 open rate (%) / 채널 보정 lift")
    print(by_round.pivot(index="target_segment", columns="round", values=["open_rate", "lift"])
          .reindex(SEG_ORDER).round(2).to_string(), "\n")
    fig = px.bar(by_round, x="target_segment", y="lift", color=by_round["round"].astype(str),
                 barmode="group", category_orders={"target_segment": SEG_ORDER},
                 labels={"lift": "채널 보정 open lift (1.0 = 채널 평균)", "color": "라운드",
                         "target_segment": "세그먼트"},
                 title="3라운드 Open Rate 비교 — 1월 → 3월 (채널 평균 대비 배수)")
    fig.write_html(OUT / "round_open_rate.html")

    # 3단계 — (세그먼트, 톤) 피벗
    combo = df.groupby(["target_segment", "tone"]).agg(
        도달=("opened", "size"), open_rate=("opened", "mean"), lift=("lift", "mean")).reset_index()
    combo["open_rate"] *= 100
    seg_avg = df.groupby("target_segment")["lift"].mean()
    combo["세그먼트평균대비"] = combo["lift"] / combo["target_segment"].map(seg_avg)
    combo["승자"] = combo["세그먼트평균대비"].ge(1.2).map({True: "⭐", False: ""})
    pv = combo.pivot(index="target_segment", columns="tone", values="세그먼트평균대비").reindex(SEG_ORDER)
    print("[3단계] 세그먼트 × 톤 — 세그먼트 평균 대비 배수 (⭐ ≥ 1.20)")
    print(pv.round(2).to_string(), "\n")
    winners = combo[combo["승자"] == "⭐"].sort_values("세그먼트평균대비", ascending=False)
    print("⭐ 승자 조합")
    for r in winners.itertuples():
        print(f"  {r.target_segment} × {r.tone}: {(r.세그먼트평균대비 - 1) * 100:+.0f}% "
              f"(lift {r.lift:.2f}, 도달 {r.도달:,})")
    urgent = combo[combo["tone"] == "긴급"][["target_segment", "세그먼트평균대비"]]
    print("\n긴급 톤 세그먼트별 배수:", dict(zip(urgent.target_segment, urgent.세그먼트평균대비.round(2))))

    # push 만 떼어 본 원 open rate (채널 혼합 없는 검증)
    push = df[df["channel"] == "push"].groupby(["target_segment", "tone"])["opened"].mean().unstack() * 100
    print("\n[검증] push 채널만 open rate (%)")
    print(push.reindex(SEG_ORDER).round(2).to_string())

    # A/B 맞대결 — 같은 캠페인·같은 채널 안에서 톤만 다른 비교 (가장 깨끗한 실험 비교)
    h2h = (df[df["round"] >= 2]
           .groupby(["round", "campaign_id", "target_segment", "channel", "variant", "tone"])
           .agg(도달=("opened", "size"), open_rate=("opened", "mean"))
           .reset_index())
    h2h["open_rate"] *= 100
    rows = []
    for (rnd, cid), g in h2h.groupby(["round", "campaign_id"]):
        if g["tone"].nunique() < 2:
            continue
        g = g.sort_values("open_rate", ascending=False)
        w, l = g.iloc[0], g.iloc[-1]
        rows.append({"round": rnd, "세그먼트": w.target_segment, "채널": w.channel,
                     "승자톤": w.tone, "승자open": round(w.open_rate, 2),
                     "패자톤": l.tone, "패자open": round(l.open_rate, 2),
                     "배수": round(w.open_rate / l.open_rate, 2) if l.open_rate else None})
    h2h_tbl = pd.DataFrame(rows)
    print("\n[A/B 맞대결] 같은 캠페인 내 톤 비교")
    print(h2h_tbl.to_string(index=False))
    h2h_tbl.to_csv(OUT / "ab_head_to_head.csv", index=False, encoding="utf-8-sig")

    fig = px.imshow(pv, text_auto=".2f", color_continuous_scale="Greens", aspect="auto",
                    labels=dict(color="세그먼트 평균 대비"),
                    title="톤 × 세그먼트 Open Rate 히트맵 (≥1.20 = ⭐ 승자)")
    fig.write_html(OUT / "tone_segment_heatmap.html")

    # 세그먼트별 best 톤 → Part 18 카피 생성 입력
    best = combo.loc[combo.groupby("target_segment")["세그먼트평균대비"].idxmax(),
                     ["target_segment", "tone", "세그먼트평균대비", "open_rate", "도달"]]
    best.to_csv(OUT / "best_tone_by_segment.csv", index=False, encoding="utf-8-sig")
    combo.to_csv(OUT / "tone_segment_pivot.csv", index=False, encoding="utf-8-sig")
    print("\n세그먼트별 best 톤:", dict(zip(best.target_segment, best.tone)))


if __name__ == "__main__":
    main()
