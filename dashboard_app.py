"""네이버 플러스 스토어 마케팅 대시보드 (Streamlit).

실행:  python -m streamlit run dashboard_app.py --server.port 8502
데이터: raw/channel/*.csv + raw/appsflyer/*.csv (glob 자동 병합), raw/braze/* (RFM)
규칙: CLAUDE.md (채널 컬러 §3-1, 지표 §5, 분리 원칙 §6-1, RFM §12·§13)
"""
import re
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from _join_analysis_duckdb import build_joined
from _rfm import DEFAULT_SNAPSHOT, SEGMENTS, compute_rfm

BASE = Path(__file__).parent

# ── CLAUDE.md §3-1 브랜드 컬러 ─────────────────────────────────────────────
CHANNEL_COLORS = {"구글": "#4285F4", "메타": "#1877F2", "네이버": "#03C75A",
                  "카카오": "#FEE500", "틱톡": "#000000"}
SEGMENT_COLORS = {"Champion": "#03C75A", "Loyal": "#4285F4", "New": "#8E7CF0",
                  "Potential": "#9AA4B2", "At-Risk": "#F5A623", "Hibernating": "#E8743B",
                  "Lost": "#D64545"}
ACCENT = "#03C75A"
BRAND_KW = "NVR_CMP_01_브랜드KW"
SUM_COLS = ["비용", "노출", "클릭", "클릭_기준", "가입", "매출", "구매_기준", "채널_가입", "af_가입"]
COV_LOW, COV_HIGH = 70, 90  # AF 커버리지 정상 범위 (CLAUDE.md §5-1)

st.set_page_config(page_title="네이버 플러스 스토어 마케팅 대시보드", page_icon="📊", layout="wide")

# ── v2 UI: 미니멀 Nordic + Notion 카드 + Pretendard ──────────────────────
st.markdown("""
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css');
html, body, [class*="css"], .stMarkdown, .stText, .stMetric, button, input, textarea, select {
    font-family: 'Pretendard Variable', Pretendard, -apple-system, sans-serif;
}
/* Material 아이콘은 원래 폰트 유지 (keyboard_double_arrow_down 글자 노출 버그 방지) */
[data-testid="stIconMaterial"], .material-symbols-rounded, span[class*="material"] {
    font-family: 'Material Symbols Rounded' !important;
}
.block-container { padding-top: 2rem; max-width: 1400px; }
h1, h2, h3 { letter-spacing: -0.02em; font-weight: 700; }
[data-testid="stMetric"] {
    background: var(--secondary-background-color);
    border: 1px solid rgba(128,128,128,0.18); border-radius: 14px;
    padding: 14px 16px; transition: all .15s ease;
}
[data-testid="stMetric"]:hover {
    border-color: #03C75A; box-shadow: 0 4px 14px rgba(3,199,90,0.08); transform: translateY(-1px);
}
[data-testid="stMetricValue"] { font-size: 1.55rem; }
.insight-card {
    border: 1px solid rgba(128,128,128,0.18); border-radius: 14px; padding: 12px 16px;
    margin-bottom: 10px; background: var(--secondary-background-color);
}
.stTabs [data-baseweb="tab-list"] { gap: 4px; }
.stTabs [data-baseweb="tab"] { border-radius: 10px 10px 0 0; padding: 8px 14px; }
</style>
""", unsafe_allow_html=True)


# ── 데이터 로드 ──────────────────────────────────────────────────────────
def _raw_signature() -> tuple:
    """raw 폴더 파일 목록·수정시각 — 새 CSV가 떨어지면 캐시 무효화."""
    files = sorted((BASE / "raw").rglob("*.csv"))
    return tuple((f.name, f.stat().st_mtime) for f in files)


@st.cache_data(show_spinner="데이터 병합 중…", max_entries=1)
def load_joined(_sig: tuple) -> pd.DataFrame:
    con = duckdb.connect()
    build_joined(con)
    df = con.execute("SELECT * FROM joined").df()
    df["일"] = pd.to_datetime(df["일"])
    # 소재명 5속성 파싱 (CLAUDE.md §3-3) — AB 없는 4파트 소재 처리
    parts = df["소재"].str.split("_")
    df["소재타입"] = parts.str[0]
    df["카테고리"] = parts.str[1]
    df["시즌"] = parts.str[2]
    df["AB"] = parts.apply(lambda p: p[3] if len(p) == 5 else "-")
    df["버전"] = parts.str[-1]
    return df


@st.cache_data(show_spinner="RFM 계산 중…", max_entries=4)
def load_rfm(snapshot: date, _sig: tuple) -> pd.DataFrame:
    return compute_rfm(snapshot).drop(columns="_segment_truth")


# ── 지표 헬퍼 ────────────────────────────────────────────────────────────
def safe_div(a, b):
    """0 나누기 → NaN (Series / 스칼라 모두)."""
    if isinstance(b, pd.Series):
        return a / b.replace(0, pd.NA).astype("Float64")
    return a / b if b else float("nan")


def with_metrics(g: pd.DataFrame) -> pd.DataFrame:
    g = g.copy()
    g["ROAS"] = safe_div(g["매출"], g["비용"]).astype(float).round(2)
    g["CAC"] = safe_div(g["비용"], g["가입"]).astype(float).round(0)
    g["CTR"] = (safe_div(g["클릭"], g["노출"]) * 100).astype(float).round(2)
    g["CVR"] = (safe_div(g["가입"], g["클릭_기준"]) * 100).astype(float).round(2)
    g["AF커버리지"] = (safe_div(g["af_가입"], g["채널_가입"]) * 100).astype(float).round(1)
    return g


def agg(df: pd.DataFrame, by) -> pd.DataFrame:
    return with_metrics(df.groupby(by, as_index=False)[SUM_COLS].sum())


def totals(df: pd.DataFrame) -> dict:
    s = df[SUM_COLS].sum()
    return {
        "비용": s["비용"], "노출": s["노출"], "가입": s["가입"],
        "CTR": safe_div(s["클릭"], s["노출"]) * 100,
        "CAC": safe_div(s["비용"], s["가입"]),
        "ROAS": safe_div(s["매출"], s["비용"]),
        "AF커버리지": safe_div(s["af_가입"], s["채널_가입"]) * 100,
    }


def won(v: float) -> str:
    """한국식 금액 축약 (CLAUDE.md §8)."""
    if pd.isna(v):
        return "-"
    if abs(v) >= 1e8:
        return f"₩{v / 1e8:.1f}억"
    if abs(v) >= 1e4:
        return f"₩{v / 1e4:,.0f}만"
    return f"₩{v:,.0f}"


def roas_status(r: float) -> str:
    if pd.isna(r):
        return "⚪"
    return "🟢" if r >= 4 else ("🟡" if r >= 2 else "🔴")


def chart(fig, height=380):
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=40, b=10),
                      font=dict(family="Pretendard Variable, Pretendard, sans-serif"),
                      legend=dict(orientation="h", y=-0.15), hoverlabel=dict(font_size=12))
    st.plotly_chart(fig, width="stretch")


# ── 사이드바 필터 ────────────────────────────────────────────────────────
raw = load_joined(_raw_signature())
min_d, max_d = raw["일"].min().date(), raw["일"].max().date()

with st.sidebar:
    st.markdown("### 🔎 필터")
    period = st.date_input("기간", value=(min_d, max_d), min_value=min_d, max_value=max_d)
    if not isinstance(period, (tuple, list)) or len(period) != 2:
        st.info("기간의 시작일과 종료일을 모두 선택하세요.")
        st.stop()
    start, end = period

    def multi(label, col, src=raw):
        opts = sorted(src[col].dropna().unique())
        return st.multiselect(label, opts, default=opts)

    sel_cls = multi("채널분류", "채널분류")
    sel_ch = multi("채널", "채널")
    sel_obj = multi("캠페인목적", "캠페인목적")
    sel_grp = multi("타겟그룹", "그룹")
    with st.expander("🎨 소재 속성", expanded=False):
        sel_type = multi("소재타입", "소재타입")
        sel_cat = multi("카테고리", "카테고리")
        sel_season = multi("시즌", "시즌")
    excl_brand = st.toggle("브랜드KW 제외 (§6-1)", value=True)
    basis = st.radio("지표 기준", ["AF (MMP)", "채널 보고"], horizontal=True)
    st.divider()
    st.markdown("### 💎 RFM")
    snapshot = st.date_input("스냅샷 기준일", value=DEFAULT_SNAPSHOT,
                             min_value=date(2025, 1, 1), max_value=DEFAULT_SNAPSHOT)
    if snapshot < DEFAULT_SNAPSHOT:
        st.warning("구매 로그가 2025-01-01부터라 이 기준일에선 F·M의 90일 창이 잘립니다.")
    st.caption(f"데이터 {min_d} ~ {max_d} · raw CSV {len(_raw_signature())}개")

df = raw[
    raw["일"].dt.date.between(start, end)
    & raw["채널분류"].isin(sel_cls) & raw["채널"].isin(sel_ch)
    & raw["캠페인목적"].isin(sel_obj) & raw["그룹"].isin(sel_grp)
    & raw["소재타입"].isin(sel_type) & raw["카테고리"].isin(sel_cat) & raw["시즌"].isin(sel_season)
].copy()
if excl_brand:
    df = df[df["캠페인"] != BRAND_KW]

use_af = basis.startswith("AF")
df["가입"] = df["af_회원가입"] if use_af else df["회원가입"]
df["매출"] = df["af_구매매출"] if use_af else df["구매매출"]
df["구매_기준"] = df["af_구매"] if use_af else df["구매"]
df["클릭_기준"] = df["af_클릭"] if use_af else df["클릭"]
df["채널_가입"] = df["회원가입"]
df["af_가입"] = df["af_회원가입"]

# ── 헤더 + KPI Row ───────────────────────────────────────────────────────
st.markdown("## 📊 네이버 플러스 스토어 마케팅 대시보드")
st.caption(f"{start} ~ {end} · 기준: {basis} · ROAS = 매출 / 비용 (D7 어트리뷰션, CLAUDE.md §5-1)"
           + (" · 브랜드KW 제외" if excl_brand else ""))
if not use_af:
    st.caption("⚠️ 채널 보고 모드: CAC·ROAS가 플랫폼 보고값으로 계산됩니다. 공식 지표(§5-1)는 AF 기준입니다.")
n_unmatched = int(raw["af_회원가입"].isna().sum())
if n_unmatched:
    st.warning(f"AppsFlyer와 매칭되지 않은 채널 행이 {n_unmatched:,}개 있습니다 (채널 매핑 §3-1 확인).")

if df.empty:
    st.warning("선택한 필터에 해당하는 데이터가 없습니다.")
    st.stop()

cur_t = totals(df)
d_max = df["일"].max()
has_wow = (df["일"].max() - df["일"].min()).days + 1 >= 14  # 실제 데이터 범위 기준 (인사이트와 동일)
prev_t = None
if has_wow:
    last7 = df[df["일"] > d_max - timedelta(days=7)]
    prev7 = df[(df["일"] <= d_max - timedelta(days=7)) & (df["일"] > d_max - timedelta(days=14))]
    if not last7.empty and not prev7.empty:
        cur_w, prev_t = totals(last7), totals(prev7)


def delta(key, pct=True):
    if prev_t is None:
        return None
    a, b = cur_w[key], prev_t[key]
    if pd.isna(a) or pd.isna(b) or b == 0:
        return None
    return f"{(a / b - 1) * 100:+.1f}% WoW" if pct else f"{a - b:+.1f}p WoW"


HELP = "값: 선택 기간 전체 · 증감: 최근 7일 vs 직전 7일 (WoW)"
k = st.columns(4) + st.columns(3)
k[0].metric("총 광고비", won(cur_t["비용"]), delta("비용"), delta_color="off", help=HELP)
k[1].metric("노출", f"{cur_t['노출'] / 1e6:,.1f}M", delta("노출"), help=HELP)
k[2].metric("CTR", f"{cur_t['CTR']:.2f}%", delta("CTR", pct=False), help=HELP)
k[3].metric("신규 가입", f"{cur_t['가입']:,.0f}", delta("가입"), help=HELP)
k[4].metric("CAC", won(cur_t["CAC"]), delta("CAC"), delta_color="inverse", help=HELP)
k[5].metric("ROAS", f"{cur_t['ROAS']:.2f}", delta("ROAS"), help=HELP)
k[6].metric("AF 커버리지", f"{cur_t['AF커버리지']:.1f}%", delta("AF커버리지", pct=False), delta_color="off",
            help=f"정상 {COV_LOW}~{COV_HIGH}% · " + HELP)
if not has_wow:
    st.caption("ℹ️ 기간이 14일 미만이라 WoW 비교를 생략했습니다.")


# ── 자동 인사이트 ────────────────────────────────────────────────────────
def generate_insights(d: pd.DataFrame) -> list[str]:
    out = []
    mx = d["일"].max()
    if (d["일"].max() - d["일"].min()).days + 1 >= 14:
        w1 = d[d["일"] > mx - timedelta(days=7)]
        w0 = d[(d["일"] <= mx - timedelta(days=7)) & (d["일"] > mx - timedelta(days=14))]
        r1, r0 = totals(w1)["ROAS"], totals(w0)["ROAS"]
        if r0 and not pd.isna(r0) and abs(r1 / r0 - 1) >= 0.05:
            arrow = "📈 상승" if r1 > r0 else "📉 하락"
            out.append(f"{arrow} 최근 7일 ROAS **{r1:.2f}** (직전 7일 {r0:.2f}, {(r1 / r0 - 1) * 100:+.1f}%)")
    ch = agg(d, "채널").sort_values("ROAS", ascending=False)
    ext = ch[ch["채널"] != "네이버"]  # §6-1 자체 매체 분리
    if len(ext) >= 2:
        out.append(f"🏆 외부 채널 Top **{ext.iloc[0]['채널']}** ROAS {ext.iloc[0]['ROAS']:.2f} · "
                   f"Bottom **{ext.iloc[-1]['채널']}** ROAS {ext.iloc[-1]['ROAS']:.2f} (네이버는 자체 매체로 분리)")
    daily = d.groupby("일")["비용"].sum()
    if len(daily) >= 8:
        base = daily.iloc[:-1].tail(7).mean()
        if base and daily.iloc[-1] / base - 1 >= 0.30:
            out.append(f"💸 비용 스파이크: {daily.index[-1].date()} 비용이 직전 7일 평균 대비 "
                       f"{(daily.iloc[-1] / base - 1) * 100:+.0f}%")
    cov = agg(d, "캠페인")
    odd = cov[(cov["AF커버리지"] < COV_LOW) | (cov["AF커버리지"] > COV_HIGH)]
    if len(odd):
        names = ", ".join(f"{r.캠페인}({r.AF커버리지:.0f}%)" for r in odd.head(3).itertuples())
        out.append(f"🧭 AF 커버리지 이상치 {len(odd)}개 캠페인 (정상 {COV_LOW}~{COV_HIGH}%): {names}")
    else:
        out.append(f"✅ 모든 캠페인 AF 커버리지가 정상 범위({COV_LOW}~{COV_HIGH}%)입니다.")
    red = cov[cov["ROAS"] < 2]
    if len(red):
        out.append(f"🔴 ROAS 2.0 미만 캠페인 {len(red)}개: " + ", ".join(red["캠페인"].head(3)))
    return out


tabs = st.tabs(["🧭 Overview", "📈 전체 추세", "🎯 목적별", "🎨 소재 속성", "🅰️🅱️ A/B 테스트",
                "👥 타겟그룹", "🏆 캠페인 랭킹", "💎 RFM 세그먼트", "🗂️ Raw 데이터"])

# Overview
with tabs[0]:
    st.markdown("#### 자동 인사이트")
    for line in generate_insights(df):
        html = re.sub(r"\*\*(.+?)\*\*", r"<b>\g<1></b>", line)
        st.markdown(f"<div class='insight-card'>{html}</div>", unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        ch = agg(df, "채널")
        fig = px.pie(ch, names="채널", values="비용", hole=0.55, color="채널",
                     color_discrete_map=CHANNEL_COLORS, title="채널별 광고비 비중")
        chart(fig, 320)
    with c2:
        ch_lbl = ch.merge(raw[["채널", "채널분류"]].drop_duplicates(), on="채널")
        ch_lbl["라벨"] = ch_lbl["채널"] + " (" + ch_lbl["채널분류"] + ")"
        fig = px.bar(ch_lbl.sort_values("ROAS"), x="ROAS", y="라벨", orientation="h", color="채널",
                     color_discrete_map=CHANNEL_COLORS, text="ROAS", facet_row="채널분류",
                     title="채널별 ROAS — 외부 vs 자체 매체 분리 (§6-1)")
        fig.update_yaxes(matches=None, title=None)
        fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
        fig.add_vline(x=4, line_dash="dot", line_color="gray", annotation_text="양호 4.0")
        chart(fig, 320)

# 전체 추세
with tabs[1]:
    daily = agg(df, "일")
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(x=daily["일"], y=daily["비용"], name="광고비", marker_color="#C9D1D9")
    fig.add_scatter(x=daily["일"], y=daily["매출"], name="매출", line=dict(color=ACCENT, width=2.5),
                    secondary_y=True)
    fig.update_layout(title="일별 광고비 vs 매출")
    fig.update_yaxes(title_text="광고비", secondary_y=False)
    fig.update_yaxes(title_text="매출", secondary_y=True)
    chart(fig)
    dc = agg(df, ["일", "채널"])
    fig = px.line(dc, x="일", y="ROAS", color="채널", color_discrete_map=CHANNEL_COLORS,
                  title="채널별 일 ROAS")
    chart(fig)

# 목적별
with tabs[2]:
    ob = agg(df, "캠페인목적").sort_values("ROAS", ascending=False)
    c1, c2 = st.columns(2)
    with c1:
        ob["상태"] = ob["ROAS"].map(roas_status)
        fig = px.bar(ob, x="캠페인목적", y="ROAS", text="ROAS", color="ROAS",
                     color_continuous_scale=["#D64545", "#F5A623", ACCENT], title="캠페인 목적별 ROAS")
        fig.add_hline(y=4, line_dash="dot", line_color="gray")
        chart(fig)
    with c2:
        share = ob.melt(id_vars="캠페인목적", value_vars=["비용", "매출"], var_name="지표", value_name="값")
        share["비중(%)"] = share.groupby("지표")["값"].transform(lambda s: s / s.sum() * 100).round(1)
        fig = px.bar(share, x="비중(%)", y="지표", color="캠페인목적", orientation="h",
                     title="비용 vs 매출 비중", text="비중(%)")
        chart(fig)
    st.dataframe(ob[["상태", "캠페인목적", "비용", "가입", "매출", "ROAS", "CAC", "CTR", "CVR"]],
                 hide_index=True, width="stretch")
    st.caption("플러스가입 캠페인은 초기 ROAS가 낮아도 LTV로 회수 가능 → LTV:CAC 3:1 우선 검토 (§5-4).")

# 소재 속성
with tabs[3]:
    c1, c2 = st.columns(2)
    with c1:
        ty = agg(df, ["채널", "소재타입"])
        fig = px.bar(ty, x="소재타입", y="ROAS", color="채널", barmode="group",
                     color_discrete_map=CHANNEL_COLORS, title="채널 × 소재타입 ROAS")
        chart(fig)
    with c2:
        se = agg(df, "시즌").sort_values("ROAS", ascending=False)
        fig = px.bar(se, x="시즌", y="ROAS", text="ROAS", title="시즌별 ROAS",
                     color_discrete_sequence=[ACCENT])
        chart(fig)
    pv = agg(df, ["소재타입", "카테고리"]).pivot(index="카테고리", columns="소재타입", values="ROAS")
    fig = px.imshow(pv, text_auto=".2f", color_continuous_scale="Greens", aspect="auto",
                    title="소재타입 × 카테고리 ROAS 히트맵")
    chart(fig, 420)

# A/B 테스트
with tabs[4]:
    ab = df[df["AB"].isin(["A", "B"])]
    if ab.empty:
        st.info("필터 범위에 A/B 소재가 없습니다.")
    else:
        keys = ["채널", "캠페인목적", "소재타입", "카테고리", "시즌"]  # 소재타입 고정: 교란 변수 제거
        g = agg(ab, keys + ["AB"])
        wide = g.pivot_table(index=keys, columns="AB", values=["CTR", "CVR", "ROAS", "노출"])
        wide.columns = [f"{m}_{v}" for m, v in wide.columns]
        wide = wide.reset_index()
        need = {"ROAS_A", "ROAS_B", "노출_A", "노출_B"}
        if not need.issubset(wide.columns):
            st.info("A와 B가 모두 있는 비교 조합이 없습니다.")
        else:
            wide = wide.dropna(subset=["ROAS_A", "ROAS_B"])
            wide["승자"] = wide.apply(
                lambda r: "A" if r["ROAS_A"] > r["ROAS_B"] else ("B" if r["ROAS_B"] > r["ROAS_A"] else "="), axis=1)
            wide["ROAS 차이(%)"] = (safe_div(wide["ROAS_B"] - wide["ROAS_A"], wide["ROAS_A"]) * 100).astype(float).round(1)
            wide["표본 충분"] = (wide[["노출_A", "노출_B"]].min(axis=1) >= 100_000).map({True: "✅", False: "⚠️"})
            c1, c2, c3 = st.columns(3)
            c1.metric("비교 조합", f"{len(wide)}개")
            c2.metric("A 승", f"{(wide['승자'] == 'A').sum()}개")
            c3.metric("B 승", f"{(wide['승자'] == 'B').sum()}개")
            fig = px.scatter(wide, x="ROAS_A", y="ROAS_B", color="채널", color_discrete_map=CHANNEL_COLORS,
                             hover_data=keys, title="A vs B ROAS (대각선 위 = B 우세)")
            lim = float(max(wide["ROAS_A"].max(), wide["ROAS_B"].max())) * 1.05
            fig.add_shape(type="line", x0=0, y0=0, x1=lim, y1=lim, line=dict(dash="dot", color="gray"))
            chart(fig)
            st.dataframe(wide, hide_index=True, width="stretch")
            st.caption("판정 기준 (§6-4): 그룹당 노출 10만 이상, 7일 이상 운영, p<0.05.")

# 타겟그룹
with tabs[5]:
    gp = agg(df, "그룹").sort_values("ROAS", ascending=False)
    c1, c2 = st.columns(2)
    with c1:
        fig = px.bar(gp, x="그룹", y="ROAS", text="ROAS", color_discrete_sequence=[ACCENT],
                     title="타겟그룹별 ROAS")
        chart(fig)
    with c2:
        fig = px.bar(gp, x="그룹", y="CAC", text="CAC", color_discrete_sequence=["#4285F4"],
                     title="타겟그룹별 CAC")
        chart(fig)
    gc = agg(df, ["그룹", "채널"])
    fig = px.bar(gc, x="그룹", y="ROAS", color="채널", barmode="group",
                 color_discrete_map=CHANNEL_COLORS, title="타겟그룹 × 채널 ROAS")
    chart(fig)

# 캠페인 랭킹
with tabs[6]:
    cp = agg(df, ["채널분류", "채널", "캠페인", "캠페인목적"]).sort_values(["채널분류", "ROAS"], ascending=[True, False])
    cp.insert(0, "상태", cp["ROAS"].map(roas_status))
    st.dataframe(
        cp[["상태", "채널분류", "채널", "캠페인", "캠페인목적", "비용", "가입", "매출", "ROAS", "CAC", "AF커버리지"]],
        hide_index=True, width="stretch",
        column_config={
            "ROAS": st.column_config.ProgressColumn("ROAS", format="%.2f", min_value=0,
                                                    max_value=float(cp["ROAS"].max() or 1)),
            "비용": st.column_config.NumberColumn(format="₩%d"),
            "매출": st.column_config.NumberColumn(format="₩%d"),
            "CAC": st.column_config.NumberColumn(format="₩%d"),
            "AF커버리지": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )
    st.caption("외부/자체 매체를 나눠 정렬합니다 (§6-1). 🟢 ROAS ≥ 4.0 예산 증액 검토 · 🟡 2.0~4.0 소재/타겟 튜닝 · 🔴 < 2.0 일시중단/소재 리프레시 (§5-4)")

# RFM 세그먼트 (Part 16)
with tabs[7]:
    seg = load_rfm(snapshot, _raw_signature())
    order = [s for s in SEGMENTS if s in set(seg["segment"])]
    st.caption(f"스냅샷 {snapshot} · 유저 {len(seg):,}명 · 기준 CLAUDE.md §12·§13")
    cnt = seg["segment"].value_counts().reindex(order).reset_index()
    cnt.columns = ["segment", "인원"]
    c1, c2 = st.columns([1, 2])
    with c1:
        fig = px.pie(cnt, names="segment", values="인원", hole=0.5, color="segment",
                     color_discrete_map=SEGMENT_COLORS, title="세그먼트 분포")
        chart(fig, 360)
        st.markdown("**세그먼트별 CSV 다운로드** (Braze User Import용)")
        opt_in_only = st.checkbox("마케팅 수신 동의자만 (§14)", value=True)
        for s in order:
            sub = seg[seg["segment"] == s]
            if opt_in_only:
                sub = sub[sub["marketing_opt_in"] == 1]
            st.download_button(
                f"⬇️ {s} ({len(sub):,}명) CSV",
                sub.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                file_name=f"segment_{s}_{snapshot}.csv", mime="text/csv", key=f"dl_{s}",
                width="stretch",
            )
    with c2:
        hm = seg.pivot_table(index="R", columns="F", values="M", aggfunc="mean").sort_index(ascending=False)
        fig = px.imshow(hm, text_auto=".1f", color_continuous_scale="Greens", aspect="auto",
                        title="R × F 히트맵 (셀 값 = 평균 M)", labels=dict(color="평균 M"))
        chart(fig, 360)
        sample = seg.sample(frac=1, random_state=0).groupby("segment").head(400)
        fig = px.scatter_3d(sample, x="R", y="F", z="M", color="segment",
                            color_discrete_map=SEGMENT_COLORS, opacity=0.65,
                            title="R·F·M 3D 스캐터 (세그먼트별 최대 400명 샘플)")
        fig.update_traces(marker=dict(size=3))
        chart(fig, 460)
    dist = seg.groupby("segment").agg(
        인원=("external_id", "count"), 평균_R=("R", "mean"), 평균_F=("F", "mean"), 평균_M=("M", "mean"),
        평균_90일매출=("purchase_amount_90d", "mean"), 수신동의율=("marketing_opt_in", "mean"),
    ).reindex(order)
    dist["비율(%)"] = (dist["인원"] / dist["인원"].sum() * 100).round(1)
    dist["수신동의율"] = (dist["수신동의율"] * 100).round(1)
    st.dataframe(dist.round(2), width="stretch")

# Raw 데이터
with tabs[8]:
    q = st.text_input("🔍 검색 (캠페인·소재·그룹 등 텍스트 컬럼 전체)", "")
    view = df.drop(columns=["가입", "매출", "구매_기준", "클릭_기준", "채널_가입", "af_가입"])
    if q:
        txt = view.select_dtypes(include=["object", "str"])
        mask = txt.apply(lambda c: c.astype(str).str.contains(q, case=False, regex=False)).any(axis=1)
        view = view[mask]
    c1, c2, c3 = st.columns(3)
    c1.metric("행 수", f"{len(view):,}")
    c2.metric("비용 합계", won(view["비용"].sum()))
    c3.metric("유니크 캠페인", f"{view['캠페인'].nunique()}")
    st.dataframe(view.sort_values("일", ascending=False), hide_index=True, width="stretch", height=480)
    st.download_button("⬇️ 필터 결과 CSV 다운로드",
                       view.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                       file_name=f"joined_{start}_{end}.csv", mime="text/csv")
