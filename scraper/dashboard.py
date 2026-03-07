"""
San Diego City Council — Community Voice Dashboard
Run: streamlit run dashboard.py -- --db ../data/council.db
"""

import streamlit as st
import sqlite3
import pandas as pd
import json
import plotly.express as px
import plotly.graph_objects as go
import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="SD Council · Community Voice",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
body { font-family: 'Inter', sans-serif; }
.hero {
    background: linear-gradient(120deg, #003087 0%, #0057B8 100%);
    color: white; padding: 1.4rem 2rem; border-radius: 12px; margin-bottom: 1.2rem;
}
.hero h1 { margin: 0; font-size: 1.7rem; }
.hero p  { margin: 4px 0 0; opacity: .85; font-size: .95rem; }
.comment-card {
    background: #f8f9fa; border-left: 4px solid #0057B8;
    padding: .7rem 1rem; border-radius: 0 8px 8px 0; margin-bottom: .6rem;
    font-size: .88rem;
}
.tag {
    display: inline-block; padding: 2px 9px; border-radius: 20px;
    font-size: .75rem; color: white; margin: 2px;
}
.match-badge {
    background: #52B788; color: white; font-size: .7rem;
    padding: 2px 8px; border-radius: 10px; margin-left: 6px;
}
.no-match-badge {
    background: #ADB5BD; color: white; font-size: .7rem;
    padding: 2px 8px; border-radius: 10px; margin-left: 6px;
}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

THEME_COLORS = {
    "Housing & Development":           "#4A90D9",
    "Homelessness & Social Services":  "#E8A838",
    "Public Safety":                   "#D94F4F",
    "Environment & Climate":           "#52B788",
    "Transportation & Infrastructure": "#9B5DE5",
    "Budget & Finance":                "#F15BB5",
    "Community & Neighborhoods":       "#00B4D8",
    "Land Use & Planning":             "#FB8500",
    "Business & Economy":              "#8338EC",
    "Equity & Social Justice":         "#06D6A0",
    "Education & Youth":               "#FFD166",
    "Other / General":                 "#ADB5BD",
}

def tc(name): return THEME_COLORS.get(name, "#888")


@st.cache_resource
def get_conn(path):
    return sqlite3.connect(path, check_same_thread=False)


@st.cache_data(ttl=120)
def q(_conn, sql, params=()):
    return pd.read_sql_query(sql, _conn, params=params)


def get_db_path():
    try:
        i = sys.argv.index("--db")
        return sys.argv[i+1]
    except (ValueError, IndexError):
        pass
    d = Path(__file__).parent.parent / "data" / "council.db"
    return str(d) if d.exists() else None


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────────

def sidebar(conn):
    st.sidebar.title("🔍 Filters")

    dates_df = q(conn, "SELECT MIN(date) as mn, MAX(date) as mx FROM meetings WHERE date != ''")
    mn = pd.to_datetime(dates_df["mn"].iloc[0]) if not dates_df.empty else datetime(2024,1,1)
    mx = pd.to_datetime(dates_df["mx"].iloc[0]) if not dates_df.empty else datetime.today()

    default_start = max(mn.date(), (mx - timedelta(days=365)).date())
    dr = st.sidebar.date_input("Date range",
        value=(default_start, mx.date()),
        min_value=mn.date(), max_value=mx.date())

    themes_df = q(conn, "SELECT name FROM themes ORDER BY name")
    all_themes = themes_df["name"].tolist()
    sel_themes = st.sidebar.multiselect("Theme", all_themes, placeholder="All")

    sel_sent = st.sidebar.multiselect("Sentiment", ["support","oppose","neutral","mixed"],
                                       placeholder="All")

    search = st.sidebar.text_input("🔎 Search text", placeholder="housing, traffic, parks…")

    st.sidebar.markdown("---")
    st.sidebar.caption("Data: City of San Diego City Council\nPublic Comment Records")

    d_from = str(dr[0]) if len(dr) > 0 else None
    d_to   = str(dr[1]) if len(dr) > 1 else None
    return d_from, d_to, sel_themes, sel_sent, search


# ─────────────────────────────────────────────────────────────────────────────
# Data loaders
# ─────────────────────────────────────────────────────────────────────────────

def load_comments(conn, d_from, d_to, sel_themes, sel_sent):
    sql = """
        SELECT pc.id, pc.meeting_id, pc.meeting_date, pc.commenter_name,
               pc.comment_text, pc.raw_item_ref, pc.position, pc.sentiment,
               pc.themes, pc.keywords, pc.ai_summary, pc.district,
               pc.agenda_item_id,
               ai.item_number, ai.title AS item_title, ai.category
        FROM public_comments pc
        LEFT JOIN agenda_items ai ON pc.agenda_item_id = ai.id
        WHERE pc.themes IS NOT NULL
    """
    params = []
    if d_from: sql += " AND pc.meeting_date >= ?"; params.append(d_from)
    if d_to:   sql += " AND pc.meeting_date <= ?"; params.append(d_to)

    df = pd.read_sql_query(sql, conn, params=params)
    df["themes_list"]   = df["themes"].apply(lambda x: json.loads(x) if x else [])
    df["keywords_list"] = df["keywords"].apply(lambda x: json.loads(x) if x else [])
    df["primary_theme"] = df["themes_list"].apply(lambda x: x[0] if x else "Other / General")
    df["item_matched"]  = df["agenda_item_id"].notna()

    if sel_themes:
        df = df[df["themes_list"].apply(lambda tl: any(t in tl for t in sel_themes))]
    if sel_sent:
        df = df[df["sentiment"].isin(sel_sent)]
    return df


def load_theme_counts(conn):
    return q(conn, """
        SELECT t.name, t.color, COUNT(ct.comment_id) as count
        FROM themes t LEFT JOIN comment_themes ct ON t.id=ct.theme_id
        GROUP BY t.id ORDER BY count DESC
    """)


def load_item_comment_counts(conn):
    return q(conn, """
        SELECT ai.meeting_id, ai.item_number, ai.title,
               m.date, m.title as meeting_title,
               COUNT(pc.id) as total_comments,
               SUM(CASE WHEN pc.sentiment='support'  THEN 1 ELSE 0 END) as support_count,
               SUM(CASE WHEN pc.sentiment='oppose'   THEN 1 ELSE 0 END) as oppose_count,
               SUM(CASE WHEN pc.sentiment='neutral'  THEN 1 ELSE 0 END) as neutral_count,
               SUM(CASE WHEN pc.sentiment='mixed'    THEN 1 ELSE 0 END) as mixed_count
        FROM agenda_items ai
        JOIN meetings m ON ai.meeting_id = m.meeting_id
        LEFT JOIN public_comments pc ON pc.agenda_item_id = ai.id
        GROUP BY ai.id
        HAVING total_comments > 0
        ORDER BY total_comments DESC
    """)


def load_sentiment_by_theme(conn):
    return q(conn, """
        SELECT t.name as theme, pc.sentiment, COUNT(*) as count
        FROM public_comments pc
        JOIN comment_themes ct ON pc.id=ct.comment_id
        JOIN themes t ON ct.theme_id=t.id
        WHERE pc.sentiment IS NOT NULL
        GROUP BY t.name, pc.sentiment
    """)


def load_timeline(conn):
    return q(conn, """
        SELECT pc.meeting_date,
               t.name as theme,
               COUNT(*) as count
        FROM public_comments pc
        JOIN comment_themes ct ON pc.id=ct.comment_id
        JOIN themes t ON ct.theme_id=t.id
        WHERE pc.meeting_date IS NOT NULL AND pc.meeting_date != ''
        GROUP BY pc.meeting_date, t.name
    """)


# ─────────────────────────────────────────────────────────────────────────────
# Chart builders
# ─────────────────────────────────────────────────────────────────────────────

def chart_theme_bar(theme_df):
    d = theme_df[theme_df["count"] > 0].sort_values("count", ascending=True)
    fig = go.Figure(go.Bar(
        x=d["count"], y=d["name"], orientation="h",
        marker_color=d["color"] if "color" in d.columns else "#0057B8",
        text=d["count"], textposition="outside",
    ))
    fig.update_layout(title="Community Concerns by Theme", height=460,
                      xaxis_title="Comments", yaxis_title="",
                      plot_bgcolor="white", margin=dict(l=10,r=50,t=40,b=10))
    return fig


def chart_sentiment_stack(sent_df):
    pivot = sent_df.pivot_table(index="theme", columns="sentiment",
                                 values="count", fill_value=0).reset_index()
    colors = {"support":"#52B788","oppose":"#D94F4F","neutral":"#ADB5BD","mixed":"#E8A838"}
    fig = go.Figure()
    for s in ["support","oppose","mixed","neutral"]:
        if s in pivot.columns:
            fig.add_trace(go.Bar(name=s.capitalize(), x=pivot["theme"],
                                  y=pivot[s], marker_color=colors[s]))
    fig.update_layout(barmode="stack", title="Sentiment by Theme", height=400,
                      xaxis_tickangle=-35, plot_bgcolor="white",
                      margin=dict(l=10,r=10,t=40,b=120))
    return fig


def chart_timeline(tl_df, top_n=5):
    if tl_df.empty: return go.Figure()
    tl_df = tl_df.copy()
    tl_df["month"] = pd.to_datetime(tl_df["meeting_date"], errors="coerce").dt.to_period("M").astype(str)
    monthly = tl_df.groupby(["month","theme"])["count"].sum().reset_index()
    top = monthly.groupby("theme")["count"].sum().nlargest(top_n).index.tolist()
    monthly = monthly[monthly["theme"].isin(top)]
    cmap = {t: tc(t) for t in top}
    fig = px.line(monthly, x="month", y="count", color="theme",
                  color_discrete_map=cmap, markers=True,
                  title=f"Top {top_n} Concerns Over Time",
                  labels={"month":"Month","count":"Comments","theme":"Theme"})
    fig.update_layout(height=380, plot_bgcolor="white",
                      xaxis=dict(showgrid=True, gridcolor="#f0f0f0"),
                      yaxis=dict(showgrid=True, gridcolor="#f0f0f0"))
    return fig


def chart_match_rate(df):
    matched   = df["item_matched"].sum()
    unmatched = (~df["item_matched"]).sum()
    fig = go.Figure(go.Pie(
        labels=["Mapped to agenda item","General / unmatched"],
        values=[matched, unmatched],
        marker_colors=["#0057B8","#e0e0e0"],
        hole=0.5
    ))
    fig.update_layout(title="Comment → Agenda Item Match Rate",
                      height=300, margin=dict(t=40,b=10))
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# Tab: Agenda Item Drill-down
# ─────────────────────────────────────────────────────────────────────────────

def tab_agenda_items(conn, df):
    st.subheader("Comments mapped to agenda items")

    item_df = load_item_comment_counts(conn)
    if item_df.empty:
        st.info("No agenda items with comments yet. Run the scraper to populate.")
        return

    # Top items table
    display_df = item_df[[
        "date","meeting_title","item_number","title",
        "total_comments","support_count","oppose_count","neutral_count"
    ]].rename(columns={
        "date":"Date","meeting_title":"Meeting","item_number":"Item #",
        "title":"Title","total_comments":"Total","support_count":"✅ Support",
        "oppose_count":"❌ Oppose","neutral_count":"➖ Neutral"
    })
    st.dataframe(display_df.head(50), use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("Drill into a specific agenda item")

    # Build selection options
    options = [
        f"{row['date']} — Item {row['item_number']}: {(row['title'] or '')[:60]}"
        for _, row in item_df.iterrows()
    ]
    if not options:
        return

    chosen_idx = st.selectbox("Select item", range(len(options)),
                               format_func=lambda i: options[i])
    chosen = item_df.iloc[chosen_idx]
    chosen_id_val = chosen["item_number"]
    chosen_meeting = chosen["meeting_id"]

    # Pull comments for this item
    item_comments = df[
        (df["item_number"] == chosen_id_val) &
        (df["meeting_id"] == chosen_meeting)
    ]

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Comments", len(item_comments))
    c2.metric("In Support",     int(chosen["support_count"]))
    c3.metric("In Opposition",  int(chosen["oppose_count"]))

    if not item_comments.empty:
        # Sentiment donut
        sent_counts = item_comments["sentiment"].value_counts().reset_index()
        sent_counts.columns = ["sentiment","count"]
        colors_map = {"support":"#52B788","oppose":"#D94F4F","neutral":"#ADB5BD","mixed":"#E8A838"}
        fig = px.pie(sent_counts, values="count", names="sentiment",
                     color="sentiment", color_discrete_map=colors_map,
                     hole=0.5, title="Sentiment breakdown")
        fig.update_layout(height=280, margin=dict(t=40,b=10))
        st.plotly_chart(fig, use_container_width=True)

        # Comment list
        for _, row in item_comments.iterrows():
            themes_html = "".join(
                f'<span class="tag" style="background:{tc(t)}">{t}</span>'
                for t in row["themes_list"]
            )
            sent_icon = {"support":"✅","oppose":"❌","neutral":"➖","mixed":"🔀"}.get(row["sentiment"],"")
            ai = f'<br><em style="color:#555">{row["ai_summary"]}</em>' if row.get("ai_summary") else ""
            st.markdown(f"""
<div class="comment-card">
  <div style="display:flex;justify-content:space-between;margin-bottom:4px">
    <b>{row.get("commenter_name","Anonymous")}</b>
    <span>{sent_icon} {(row.get("sentiment") or "").capitalize()}</span>
  </div>
  <div style="margin-bottom:6px">{str(row["comment_text"])[:500]}{"…" if len(str(row["comment_text"]))>500 else ""}</div>
  {ai}
  <div style="margin-top:6px">{themes_html}</div>
</div>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Tab: Comment browser
# ─────────────────────────────────────────────────────────────────────────────

def tab_comment_browser(df, search):
    if search:
        df = df[df["comment_text"].str.contains(search, case=False, na=False)]

    st.markdown(f"**{len(df):,} comments** match your filters")

    col1, col2 = st.columns([2,1])
    with col1:
        sort_by = st.selectbox("Sort by", ["meeting_date","sentiment","primary_theme"])
    with col2:
        show_matched_only = st.checkbox("Mapped to agenda item only", value=False)

    if show_matched_only:
        df = df[df["item_matched"]]

    df = df.sort_values(sort_by, ascending=False)

    page_size = 20
    total_pages = max(1, (len(df)-1)//page_size + 1)
    page = st.number_input("Page", 1, total_pages, 1) - 1
    page_df = df.iloc[page*page_size:(page+1)*page_size]

    for _, row in page_df.iterrows():
        themes_html = "".join(
            f'<span class="tag" style="background:{tc(t)}">{t}</span>'
            for t in row["themes_list"]
        )
        sent_icon = {"support":"✅","oppose":"❌","neutral":"➖","mixed":"🔀"}.get(row["sentiment"],"")
        ai = f'<br><em style="color:#555">{row["ai_summary"]}</em>' if row.get("ai_summary") else ""

        # Show item link badge
        if row["item_matched"] and row["item_number"]:
            item_badge = f'<span class="match-badge">Item {row["item_number"]}: {(row["item_title"] or "")[:40]}</span>'
        elif row["raw_item_ref"]:
            item_badge = f'<span class="no-match-badge">"{row["raw_item_ref"]}" (unmatched)</span>'
        else:
            item_badge = '<span class="no-match-badge">General comment</span>'

        st.markdown(f"""
<div class="comment-card">
  <div style="display:flex;justify-content:space-between;margin-bottom:3px">
    <small><b>{row.get("commenter_name","Anonymous")}</b> · {row.get("meeting_date","")} {item_badge}</small>
    <small>{sent_icon} {(row.get("sentiment") or "").capitalize()}</small>
  </div>
  <div style="margin:5px 0">{str(row["comment_text"])[:450]}{"…" if len(str(row["comment_text"]))>450 else ""}</div>
  {ai}
  <div style="margin-top:5px">{themes_html}</div>
</div>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Main app
# ─────────────────────────────────────────────────────────────────────────────

def main():
    db_path = get_db_path()
    if not db_path or not Path(db_path).exists():
        st.error(f"Database not found. Run the scraper first:\n```\npython scraper/scraper.py --output ./data --limit 10\n```")
        return

    conn = get_conn(db_path)
    d_from, d_to, sel_themes, sel_sent, search = sidebar(conn)
    df = load_comments(conn, d_from, d_to, sel_themes, sel_sent)

    # ── Hero header ──────────────────────────────────────────────────────────
    st.markdown("""
<div class="hero">
  <h1>🏛️ San Diego City Council · Community Voice</h1>
  <p>Public comment analysis — what residents are saying and which agenda items they care about</p>
</div>
""", unsafe_allow_html=True)

    # ── Metrics row ──────────────────────────────────────────────────────────
    mtg_count = q(conn, "SELECT COUNT(*) as n FROM meetings WHERE date != ''")["n"].iloc[0]
    item_count = q(conn, "SELECT COUNT(*) as n FROM agenda_items")["n"].iloc[0]
    matched = int(df["item_matched"].sum()) if not df.empty else 0
    match_pct = f"{100*matched//len(df)}%" if len(df) > 0 else "N/A"

    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Comments (filtered)", f"{len(df):,}")
    c2.metric("Meetings", mtg_count)
    c3.metric("Agenda Items", item_count)
    c4.metric("Mapped to Item", match_pct)

    st.markdown("---")

    # ── Tabs ─────────────────────────────────────────────────────────────────
    t1,t2,t3,t4,t5 = st.tabs([
        "📊 Themes",
        "📋 By Agenda Item",
        "📈 Trends",
        "💬 Comments",
        "🔗 Mapping Quality"
    ])

    with t1:
        theme_df = load_theme_counts(conn)
        sent_df  = load_sentiment_by_theme(conn)
        c1,c2 = st.columns(2)
        with c1:
            st.plotly_chart(chart_theme_bar(theme_df), use_container_width=True)
        with c2:
            if not sent_df.empty:
                st.plotly_chart(chart_sentiment_stack(sent_df), use_container_width=True)

        if not df.empty:
            top_themes = df["primary_theme"].value_counts().head(8).reset_index()
            top_themes.columns = ["theme","count"]
            fig_d = px.pie(top_themes, values="count", names="theme",
                           color="theme",
                           color_discrete_map={r["theme"]:tc(r["theme"]) for _,r in top_themes.iterrows()},
                           hole=0.45, title="Share of Community Concerns")
            fig_d.update_layout(height=360, margin=dict(t=40,b=10))
            st.plotly_chart(fig_d, use_container_width=True)

    with t2:
        tab_agenda_items(conn, df)

    with t3:
        tl_df = load_timeline(conn)
        if not tl_df.empty:
            st.plotly_chart(chart_timeline(tl_df), use_container_width=True)
            monthly = pd.to_datetime(df["meeting_date"], errors="coerce").dt.to_period("M")\
                        .value_counts().sort_index().reset_index()
            monthly.columns = ["month","count"]
            monthly["month"] = monthly["month"].astype(str)
            fig_v = px.bar(monthly, x="month", y="count",
                           title="Monthly Comment Volume", color_discrete_sequence=["#0057B8"])
            fig_v.update_layout(height=300, plot_bgcolor="white")
            st.plotly_chart(fig_v, use_container_width=True)
        else:
            st.info("No timeline data yet.")

    with t4:
        tab_comment_browser(df, search)

    with t5:
        st.subheader("Comment → Agenda Item Mapping Quality")
        c1,c2 = st.columns(2)
        with c1:
            st.plotly_chart(chart_match_rate(df), use_container_width=True)
        with c2:
            has_ref = df[df["raw_item_ref"].notna() & (df["raw_item_ref"] != "")]
            no_match = has_ref[~has_ref["item_matched"]]
            if not no_match.empty:
                st.markdown("**Unmatched item references** (check these against the agenda):")
                uc = no_match["raw_item_ref"].value_counts().head(20).reset_index()
                uc.columns = ["Reference in comment","Count"]
                st.dataframe(uc, use_container_width=True, hide_index=True)
            else:
                st.success("All item references matched!")

        st.markdown("""
**How mapping works:**

Each public comment's *Agenda Item* field is normalised (stripped of punctuation,
lowercased) and looked up against the scraped agenda items for that same meeting date.

| Comment says | Matches agenda item |
|---|---|
| `"100"` | Item **100** |
| `"Item 200A"` | Item **200A** |
| `"S-1"` | Item **S-1** |
| `"100, 200"` | Items **100** and **200** (first used for primary link) |
| `"Non-Agenda"` | *(left unmapped — general comment)* |

If the match rate is low, the most likely cause is that agenda items haven't been
scraped yet, or item numbers in the Excel differ from the agenda format.
Run `scraper.py --no-headless` to inspect the agenda pages manually.
        """)

    st.caption(f"Data: City of San Diego · Dashboard generated {datetime.now().strftime('%Y-%m-%d')}")


if __name__ == "__main__":
    main()
