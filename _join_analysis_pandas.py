"""Part 5 — 채널 × AppsFlyer 5-key JOIN (pandas 버전, 하루치)."""
from pathlib import Path

import pandas as pd

BASE = Path(__file__).parent
OUT = BASE / "outputs"
OUT.mkdir(exist_ok=True)

ch = pd.read_csv(BASE / "raw/channel/2025-01-01.csv", parse_dates=["일"])
af = pd.read_csv(BASE / "raw/appsflyer/2025-01-01.csv", parse_dates=["일"])

CHANNEL_MAP = {"구글": "googleadwords_int", "메타": "Facebook Ads", "네이버": "naver_search"}
af["채널"] = af["미디어소스"].map({v: k for k, v in CHANNEL_MAP.items()})
af_renamed = af.drop(columns=["미디어소스"]).rename(
    columns={c: f"af_{c}" for c in ["클릭", "회원가입", "구매", "구매매출"]}
)

JOIN_KEYS = ["일", "채널", "캠페인", "그룹", "소재"]
merged = ch.merge(af_renamed, on=JOIN_KEYS, how="left", indicator=True)

matched = (merged["_merge"] == "both").sum()
print(f"조인 성공 {matched}행 / 매칭 실패 {len(merged) - matched}건")
merged = merged.drop(columns="_merge")

summary = merged.groupby("채널", as_index=False)[["비용", "af_회원가입", "af_구매매출"]].sum()
summary["ROAS"] = (summary["af_구매매출"] / summary["비용"]).round(2)
summary["CAC"] = (summary["비용"] / summary["af_회원가입"]).round(0)
summary["판정"] = pd.cut(summary["ROAS"], [-1, 2, 4, 1e9], labels=["개선 필요", "관찰", "양호"], right=False)
summary = summary.sort_values("ROAS", ascending=False)
print(summary.to_string(index=False))

merged.to_csv(OUT / "joined_pandas_2025-01-01.csv", index=False, encoding="utf-8-sig")
summary.to_csv(OUT / "summary_by_channel_pandas.csv", index=False, encoding="utf-8-sig")
