"""
Flask API server — serves the high-fidelity frontend and JSON data from council.db.
Usage: python scraper/api.py --db ./data/council.db
"""
import argparse
import sqlite3
import os
from flask import Flask, jsonify, send_from_directory, request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

app = Flask(__name__, static_folder=FRONTEND_DIR)

DB_PATH = None

PALETTE = [
    "#F25C3B", "#1A90D9", "#8B5CF6", "#2EC4A0",
    "#F5A623", "#E84393", "#0B4F82", "#2EC4A0",
]

CATEGORY_ICON = {
    "CONSENT": "📋",
    "ITEMS": "📌",
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def meeting_filter(meeting_id):
    if meeting_id:
        return "WHERE pc.meeting_id = ?", [meeting_id]
    return "", []


@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/chatbot")
def chatbot():
    return send_from_directory(FRONTEND_DIR, "chatbot.html")


@app.route("/analytics")
def analytics():
    return send_from_directory(FRONTEND_DIR, "analytics_export.html")


@app.route("/api/meetings")
def api_meetings():
    conn = get_db()
    rows = conn.execute("""
        SELECT m.meeting_id, m.title, m.date, COUNT(pc.id) AS comment_count
        FROM meetings m
        LEFT JOIN public_comments pc USING(meeting_id)
        GROUP BY m.meeting_id
        ORDER BY m.date DESC
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/stats")
def api_stats():
    meeting_id = request.args.get("meeting_id")
    conn = get_db()

    if meeting_id:
        where = "WHERE pc.meeting_id = ?"
        params = [meeting_id]
        meeting_row = conn.execute(
            "SELECT title, date FROM meetings WHERE meeting_id = ?", [meeting_id]
        ).fetchone()
        meeting_title = meeting_row["title"] if meeting_row else None
        meeting_date = meeting_row["date"] if meeting_row else None
    else:
        where = ""
        params = []
        meeting_title = None
        meeting_date = None

    row = conn.execute(f"""
        SELECT
            COUNT(*) AS total_comments,
            COUNT(DISTINCT commenter_name) AS unique_speakers,
            SUM(CASE WHEN position LIKE '%Support%' THEN 1 ELSE 0 END) AS support_count
        FROM public_comments pc
        {where}
    """, params).fetchone()

    themes_count = conn.execute("SELECT COUNT(*) FROM themes").fetchone()[0]

    total = row["total_comments"] or 0
    support = row["support_count"] or 0
    support_pct = round(support / total * 100) if total else 0

    conn.close()
    return jsonify({
        "total_comments": total,
        "unique_speakers": row["unique_speakers"] or 0,
        "themes_identified": themes_count,
        "support_pct": support_pct,
        "meeting_title": meeting_title,
        "meeting_date": meeting_date,
    })


@app.route("/api/themes")
def api_themes():
    meeting_id = request.args.get("meeting_id")
    conn = get_db()

    # Check if NLP has run
    has_nlp = conn.execute("SELECT COUNT(*) FROM comment_themes").fetchone()[0] > 0

    if has_nlp:
        if meeting_id:
            where = "WHERE pc.meeting_id = ?"
            params = [meeting_id]
        else:
            where = ""
            params = []

        rows = conn.execute(f"""
            SELECT t.id, t.name, t.color, COUNT(ct.comment_id) AS cnt,
                   GROUP_CONCAT(pc.ai_summary, '|||') AS summaries,
                   GROUP_CONCAT(pc.commenter_name, '|||') AS names,
                   GROUP_CONCAT(pc.position, '|||') AS positions
            FROM themes t
            JOIN comment_themes ct ON ct.theme_id = t.id
            JOIN public_comments pc ON pc.id = ct.comment_id
            {where}
            GROUP BY t.id
            ORDER BY cnt DESC
            LIMIT 8
        """, params).fetchall()

        result = []
        for i, r in enumerate(rows):
            color = r["color"] or PALETTE[i % len(PALETTE)]
            summaries = (r["summaries"] or "").split("|||")[:3]
            names = (r["names"] or "").split("|||")
            positions = (r["positions"] or "").split("|||")

            # Determine sentiment from positions
            sup = sum(1 for p in positions if "Support" in (p or ""))
            opp = sum(1 for p in positions if "Opposition" in (p or ""))
            sentiment = "support" if sup > opp else ("oppose" if opp > sup else "mixed")

            comments = [
                {"name": names[j] or "Anon", "affil": positions[j] or "", "text": summaries[j]}
                for j in range(min(3, len(summaries))) if summaries[j]
            ]

            result.append({
                "id": r["id"],
                "name": r["name"],
                "sub": "",
                "icon": "📋",
                "color": color,
                "bgColor": color + "1f",
                "count": r["cnt"],
                "sentiment": sentiment,
                "comments": comments,
            })
        conn.close()
        return jsonify(result)

    # NLP fallback: group by agenda item
    if meeting_id:
        where = "WHERE pc.meeting_id = ?"
        params = [meeting_id]
    else:
        where = ""
        params = []

    rows = conn.execute(f"""
        SELECT
            pc.agenda_item_id,
            ai.item_number,
            ai.title AS item_title,
            ai.category,
            ai.district,
            COUNT(*) AS cnt,
            SUM(CASE WHEN pc.position LIKE '%Support%' THEN 1 ELSE 0 END) AS sup,
            SUM(CASE WHEN pc.position LIKE '%Opposition%' THEN 1 ELSE 0 END) AS opp,
            GROUP_CONCAT(pc.commenter_name || '|||' || COALESCE(pc.position,'') || '|||' || SUBSTR(pc.comment_text,1,200), '~^~') AS raw_comments
        FROM public_comments pc
        LEFT JOIN agenda_items ai ON ai.id = pc.agenda_item_id
        {where}
        GROUP BY pc.agenda_item_id
        ORDER BY cnt DESC
        LIMIT 9
    """, params).fetchall()

    result = []
    for i, r in enumerate(rows):
        color = PALETTE[i % len(PALETTE)]
        cat = r["category"] or ""
        icon = CATEGORY_ICON.get(cat.upper(), "📄")

        if r["agenda_item_id"] is None:
            name = "General / Unassigned"
            sub = "No matched agenda item"
        else:
            num = r["item_number"] or ""
            title = r["item_title"] or ""
            name = f"Item {num} — {title}"[:60]
            sub_parts = [p for p in [cat, f"District {r['district']}" if r["district"] else ""] if p]
            sub = " · ".join(sub_parts)

        sup = r["sup"] or 0
        opp = r["opp"] or 0
        total = r["cnt"] or 1
        sentiment = "support" if sup > opp else ("oppose" if opp > sup else "mixed")

        comments = []
        for entry in (r["raw_comments"] or "").split("~^~")[:3]:
            parts = entry.split("|||")
            if len(parts) >= 3:
                comments.append({
                    "name": parts[0] or "Anon",
                    "affil": parts[1] or "",
                    "text": parts[2],
                })

        result.append({
            "id": r["agenda_item_id"] or 0,
            "name": name,
            "sub": sub,
            "icon": icon,
            "color": color,
            "bgColor": color + "1f",
            "count": r["cnt"],
            "sentiment": sentiment,
            "comments": comments,
        })

    conn.close()
    return jsonify(result)


@app.route("/api/recent")
def api_recent():
    meeting_id = request.args.get("meeting_id")
    conn = get_db()

    if meeting_id:
        where = "WHERE pc.meeting_id = ?"
        params = [meeting_id]
    else:
        where = ""
        params = []

    rows = conn.execute(f"""
        SELECT pc.commenter_name, pc.raw_item_ref, pc.submitted_at,
               pc.comment_text, pc.agenda_item_id
        FROM public_comments pc
        {where}
        ORDER BY pc.submitted_at DESC
        LIMIT 5
    """, params).fetchall()

    result = []
    for i, r in enumerate(rows):
        name = r["commenter_name"] or "Anonymous"
        ref = r["raw_item_ref"] or "General Comment"
        time_part = ""
        if r["submitted_at"]:
            parts = str(r["submitted_at"]).split("T")
            time_part = parts[1][:5] if len(parts) > 1 else str(r["submitted_at"])[-5:]
        color = PALETTE[i % len(PALETTE)]
        result.append({
            "text": f"{name} commented on {ref}.",
            "tooltip": (r["comment_text"] or "")[:80],
            "time": f"{ref} — {time_part}" if time_part else ref,
            "color": color,
        })

    conn.close()
    return jsonify(result)


@app.route("/api/comments")
def api_comments():
    agenda_item_id = request.args.get("agenda_item_id")
    meeting_id = request.args.get("meeting_id")
    conn = get_db()

    conditions = []
    params = []

    if agenda_item_id and agenda_item_id != "0":
        conditions.append("pc.agenda_item_id = ?")
        params.append(agenda_item_id)
    elif agenda_item_id == "0":
        # unassigned
        conditions.append("pc.agenda_item_id IS NULL")
    if meeting_id:
        conditions.append("pc.meeting_id = ?")
        params.append(meeting_id)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    rows = conn.execute(f"""
        SELECT pc.id, pc.commenter_name, pc.position, pc.comment_text,
               pc.raw_item_ref, pc.submitted_at, pc.district,
               ai.item_number, ai.title AS item_title, ai.category
        FROM public_comments pc
        LEFT JOIN agenda_items ai ON ai.id = pc.agenda_item_id
        {where}
        ORDER BY pc.submitted_at ASC
    """, params).fetchall()

    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/speakers")
def api_speakers():
    meeting_id = request.args.get("meeting_id")
    conn = get_db()

    if meeting_id:
        where = "WHERE meeting_id = ?"
        params = [meeting_id]
    else:
        where = ""
        params = []

    rows = conn.execute(f"""
        SELECT commenter_name, COUNT(*) AS cnt
        FROM public_comments
        {where}
        GROUP BY commenter_name
        ORDER BY cnt DESC
        LIMIT 5
    """, params).fetchall()

    result = [
        {"name": r["commenter_name"] or "Anonymous", "comments": r["cnt"], "color": PALETTE[i % len(PALETTE)]}
        for i, r in enumerate(rows)
    ]
    conn.close()
    return jsonify(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="./data/council.db")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    DB_PATH = os.path.abspath(args.db)
    print(f"Using DB: {DB_PATH}")
    print(f"Serving frontend from: {FRONTEND_DIR}")
    app.run(host=args.host, port=args.port, debug=True)
