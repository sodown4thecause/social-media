from __future__ import annotations
import json
import time
import sqlite3
from datetime import datetime, timezone

import streamlit as st

from . import get_con, db_path

st.set_page_config(page_title="LGI Pipeline", layout="wide")

PAGE = st.sidebar.radio("Navigate", [
    "Dashboard", "Review Queue", "Posts Browser", "Leads Pipeline", "Posting Queue",
])

if PAGE == "Dashboard":
    st.title("Pipeline Dashboard")

    con = get_con()

    # --- Top stats row ---
    total_posts = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    total_candidates = con.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    total_intents = con.execute("SELECT COUNT(*) FROM intents").fetchone()[0]
    total_approved = con.execute("SELECT COUNT(*) FROM approvals WHERE decision='approved'").fetchone()[0]
    total_rejected = con.execute("SELECT COUNT(*) FROM approvals WHERE decision='rejected'").fetchone()[0]
    total_edited = con.execute("SELECT COUNT(*) FROM approvals WHERE decision='edited'").fetchone()[0]
    total_enriched = con.execute("SELECT COUNT(*) FROM enrichments").fetchone()[0]
    total_cost = con.execute("SELECT COALESCE(SUM(cost_cents),0) FROM enrichments").fetchone()[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Posts Ingested", total_posts)
    c2.metric("Intent Classified", total_intents)
    c3.metric("Candidates", total_candidates)
    c4.metric("Enrichments", f"{total_enriched} (${total_cost/100:.2f})")

    c1, c2, c3 = st.columns(3)
    c1.metric("Approved", total_approved, delta_color="normal")
    c2.metric("Rejected", total_rejected, delta_color="inverse")
    c3.metric("Edited", total_edited)

    st.divider()

    # --- Source breakdown ---
    st.subheader("Posts by Source")
    rows = con.execute(
        "SELECT source, COUNT(*) as cnt FROM posts GROUP BY source ORDER BY cnt DESC"
    ).fetchall()
    if rows:
        cols = st.columns(len(rows))
        for i, r in enumerate(rows):
            cols[i].metric(r["source"], r["cnt"])
    else:
        st.info("No posts yet. Run `python -m ingestion.ingest`")

    st.divider()

    # --- Intent distribution ---
    st.subheader("Intent Distribution")
    intents = con.execute(
        "SELECT cluster, COUNT(*) as cnt, AVG(confidence) as avg_conf FROM intents GROUP BY cluster ORDER BY cnt DESC"
    ).fetchall()
    if intents:
        chart_data = {r["cluster"]: r["cnt"] for r in intents}
        st.bar_chart(chart_data)
    else:
        st.info("No intents classified yet.")

    st.divider()

    # --- Recent candidates ---
    st.subheader("Top Scored Candidates (pending)")
    recent = con.execute("""
        SELECT c.id, p.source, substr(p.text,1,100) as snippet, i.cluster, c.angle, c.total_score
        FROM candidates c
        JOIN posts p ON p.id = c.post_id
        JOIN intents i ON i.post_id = p.id
        LEFT JOIN approvals a ON a.candidate_id = c.id
        WHERE a.candidate_id IS NULL
        ORDER BY c.total_score DESC
        LIMIT 10
    """).fetchall()
    if recent:
        st.dataframe([dict(r) for r in recent], use_container_width=True)
    else:
        st.info("No pending candidates.")

    con.close()


elif PAGE == "Review Queue":
    st.title("Review Queue")

    con = get_con()

    # Actions bar
    ac1, ac2, ac3 = st.columns([2, 1, 1])
    with ac1:
        sort_by = st.selectbox("Sort by", ["total_score DESC", "confidence DESC", "created_at DESC"])
    with ac2:
        filter_source = st.selectbox("Source", ["All"] + [
            r[0] for r in con.execute("SELECT DISTINCT source FROM posts").fetchall()
        ])
    with ac3:
        limit = st.number_input("Show", 5, 100, 30)

    st.divider()

    source_clause = "AND p.source = ?" if filter_source != "All" else ""
    query_params = [filter_source] if filter_source != "All" else []
    query_params.append(limit)

    pending = con.execute(f"""
        SELECT c.id, p.source, p.url, substr(p.text,1,300) as snippet, i.cluster,
               i.confidence, c.tone, c.angle, c.text, c.total_score, c.score_breakdown
        FROM candidates c
        JOIN posts p ON p.id = c.post_id
        JOIN intents i ON i.post_id = p.id
        LEFT JOIN approvals a ON a.candidate_id = c.id
        WHERE a.candidate_id IS NULL {source_clause}
        ORDER BY {sort_by}
        LIMIT ?
    """, tuple(query_params)).fetchall()

    if not pending:
        st.info("No pending candidates.")
        con.close()
        st.stop()

    # Batch approve controls
    if "batch_ids" not in st.session_state:
        st.session_state.batch_ids = set()

    b1, b2 = st.columns(2)
    with b1:
        if st.button(f"Batch Approve ({len(st.session_state.batch_ids)} selected)", type="primary"):
            if st.session_state.batch_ids:
                ts = int(time.time())
                for cid in st.session_state.batch_ids:
                    con.execute(
                        "INSERT OR IGNORE INTO approvals(candidate_id, decision, decided_at, channel) VALUES (?, 'approved', ?, 'streamlit')",
                        (cid, ts),
                    )
                con.commit()
                count = len(st.session_state.batch_ids)
                st.session_state.batch_ids = set()
                st.toast(f"Approved {count} candidates")
                st.rerun()
    with b2:
        if st.button("Clear Selection"):
            st.session_state.batch_ids = set()
            st.rerun()

    st.divider()

    for row in pending:
        cid = row["id"]
        checked = cid in st.session_state.batch_ids

        expander_label = f"{'[✓] ' if checked else ''}#{cid} | {row['source']} | {row['cluster']} | {row['angle']} | score: {row['total_score']:.2f}"
        with st.expander(expander_label):
            col1, col2, col3 = st.columns([1, 1, 7])
            with col1:
                if st.checkbox("Select", value=checked, key=f"chk_{cid}"):
                    st.session_state.batch_ids.add(cid)
                else:
                    st.session_state.batch_ids.discard(cid)
            with col2:
                st.metric("Score", f"{row['total_score']:.2f}")
            with col3:
                st.caption(f"Confidence: {row['confidence']:.2f} | Tone: {row['tone']} | Angle: {row['angle']}")

            st.text(row["url"])
            st.markdown(f"**Post:** {row['snippet']}")
            st.markdown(f"**Candidate:** {row['text'][:800]}")

            decision = st.radio(
                f"Decision for #{cid}",
                ["Skip", "Approve", "Edit", "Reject"],
                horizontal=True,
                key=f"dec_{cid}",
            )

            if decision == "Approve":
                note = st.text_input("Note (optional)", key=f"note_{cid}")
                if st.button(f"Confirm Approve #{cid}", key=f"approve_{cid}"):
                    ts = int(time.time())
                    con.execute(
                        "INSERT OR IGNORE INTO approvals(candidate_id, decision, decided_at, channel, reviewer_note) VALUES (?, 'approved', ?, 'streamlit', ?)",
                        (cid, ts, note or None),
                    )
                    con.commit()
                    st.toast(f"Approved #{cid}")
                    st.rerun()

            elif decision == "Edit":
                edited = st.text_area("Edit text", value=row["text"], key=f"edit_{cid}", height=120)
                note = st.text_input("Note (optional)", key=f"enote_{cid}")
                if st.button(f"Save Edit #{cid}", key=f"save_{cid}"):
                    if edited.strip():
                        ts = int(time.time())
                        con.execute(
                            "INSERT OR IGNORE INTO approvals(candidate_id, decision, edited_text, decided_at, channel, reviewer_note) VALUES (?, 'edited', ?, ?, 'streamlit', ?)",
                            (cid, edited.strip(), ts, note or None),
                        )
                        con.commit()
                        st.toast(f"Saved edit #{cid}")
                        st.rerun()

            elif decision == "Reject":
                if st.button(f"Confirm Reject #{cid}", key=f"reject_{cid}"):
                    ts = int(time.time())
                    con.execute(
                        "INSERT OR IGNORE INTO approvals(candidate_id, decision, decided_at, channel) VALUES (?, 'rejected', ?, 'streamlit')",
                        (cid, ts),
                    )
                    con.commit()
                    st.toast(f"Rejected #{cid}")
                    st.rerun()

    con.close()


elif PAGE == "Posts Browser":
    st.title("Posts Browser")

    con = get_con()

    col1, col2, col3 = st.columns(3)
    with col1:
        source_filter = st.selectbox("Source", ["All"] + [
            r[0] for r in con.execute("SELECT DISTINCT source FROM posts").fetchall()
        ])
    with col2:
        clusters = ["All"] + [
            r[0] for r in con.execute("SELECT DISTINCT cluster FROM intents WHERE cluster IS NOT NULL").fetchall()
        ]
        cluster_filter = st.selectbox("Intent Cluster", clusters)
    with col3:
        limit_posts = st.number_input("Show", 10, 200, 40)

    st.divider()

    where = ["1=1"]
    params: list = []
    if source_filter != "All":
        where.append("p.source = ?")
        params.append(source_filter)
    if cluster_filter != "All":
        where.append("i.cluster = ?")
        params.append(cluster_filter)

    posts = con.execute(f"""
        SELECT p.id, p.source, p.url, p.author, p.text, p.created_at, p.prefilter_score, p.engagement,
               i.cluster, i.confidence, i.top_alt_clusters
        FROM posts p
        LEFT JOIN intents i ON i.post_id = p.id
        WHERE {' AND '.join(where)}
        ORDER BY p.prefilter_score DESC, p.created_at DESC
        LIMIT ?
    """, (*params, limit_posts)).fetchall()

    if not posts:
        st.info("No posts match filters.")
        con.close()
        st.stop()

    for post in posts:
        dt = datetime.fromtimestamp(post["created_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        label = f"[{post['source']}] {dt} | {post['cluster'] or 'unclassified'} | {post['prefilter_score']:.2f}"
        with st.expander(label):
            st.caption(f"URL: {post['url']}")
            if post["author"]:
                st.caption(f"Author: {post['author']}")
            st.markdown(post["text"])

            c1, c2, c3 = st.columns(3)
            c1.metric("Prefilter Score", f"{post['prefilter_score']:.2f}")
            c2.metric("Engagement", f"{post['engagement']:.2f}")
            if post["confidence"]:
                c3.metric("Intent Confidence", f"{post['confidence']:.2f}")

            # Show alternatives
            if post["top_alt_clusters"]:
                try:
                    alts = json.loads(post["top_alt_clusters"])
                    if alts:
                        st.caption("Top alt clusters: " + ", ".join(
                            f"{a['cluster']}({a['score']:.2f})" for a in alts[:3]
                        ))
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass

            # Show candidates for this post
            cand_rows = con.execute(
                "SELECT id, tone, angle, text, total_score, score_breakdown FROM candidates WHERE post_id = ? ORDER BY total_score DESC",
                (post["id"],),
            ).fetchall()
            if cand_rows:
                st.divider()
                st.caption(f"Candidates ({len(cand_rows)}):")
                for cr in cand_rows:
                    app = con.execute(
                        "SELECT decision, edited_text FROM approvals WHERE candidate_id = ?",
                        (cr["id"],),
                    ).fetchone()
                    status_icon = ""
                    if app:
                        if app["decision"] == "approved":
                            status_icon = " (approved)"
                        elif app["decision"] == "edited":
                            status_icon = " (edited)"
                        elif app["decision"] == "rejected":
                            status_icon = " (rejected)"

                    st.caption(
                        f"  [{cr['tone']}/{cr['angle']}] score:{cr['total_score']:.2f}{status_icon}"
                    )

    con.close()


# ── Leads Pipeline ────────────────────────────────────────────────────────────

elif PAGE == "Leads Pipeline":
    st.title("Leads Pipeline")

    con = get_con()

    from ingestion.db import lead_count_by_status
    counts = lead_count_by_status(con)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("New", counts.get("new", 0))
    c2.metric("Contacted", counts.get("contacted", 0))
    c3.metric("Converted", counts.get("converted", 0))
    c4.metric("Lost", counts.get("lost", 0))
    c5.metric("Total", sum(counts.values()))

    st.divider()

    col1, col2, col3 = st.columns(3)
    with col1:
        status_filter = st.selectbox("Status", ["All", "new", "contacted", "converted", "lost", "ignored"])
    with col2:
        min_score_filter = st.slider("Min Lead Score", 0, 100, 0)
    with col3:
        show_limit = st.number_input("Show", 5, 200, 50)

    status_clause = None if status_filter == "All" else status_filter
    from ingestion.db import load_leads
    leads = load_leads(con, status=status_clause, min_score=min_score_filter, limit=show_limit)

    if not leads:
        st.info("No leads match filters.")
        con.close()
        st.stop()

    export_cols = st.columns([1, 4])
    with export_cols[0]:
        if st.button("Export CSV"):
            import csv
            import io
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["id", "source", "author", "url", "intent_cluster", "confidence", "lead_score", "status", "created_at"])
            for l in leads:
                writer.writerow([l["id"], l["source"], l["author"], l["url"], l["intent_cluster"],
                                 l["confidence"], l["lead_score"], l["status"], l["created_at"]])
            st.download_button("Download", buf.getvalue(), "leads.csv", "text/csv")

    st.divider()

    for lead in leads:
        score = lead["lead_score"]
        label = f"{'🔥' if score >= 50 else ''} #{lead['id']} | {lead['source']} | {lead['intent_cluster']} | score: {score:.0f} | {lead['status']}"
        with st.expander(label):
            st.caption(f"URL: {lead['url']}")
            if lead["author"]:
                st.caption(f"Author: {lead['author']}")
            st.caption(f"Confidence: {lead['confidence']:.2f} | Created: {datetime.fromtimestamp(lead['created_at'], tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}")

            new_status = st.selectbox(
                "Status", ["new", "contacted", "converted", "lost", "ignored"],
                index=["new", "contacted", "converted", "lost", "ignored"].index(lead["status"]),
                key=f"lead_status_{lead['id']}",
            )
            notes = st.text_area("Notes", value=lead["notes"] or "", key=f"lead_notes_{lead['id']}", height=80)
            if st.button("Update", key=f"lead_update_{lead['id']}"):
                from ingestion.db import update_lead_status
                update_lead_status(con, lead["id"], new_status, notes)
                st.toast(f"Updated lead #{lead['id']}")
                st.rerun()

    con.close()


# ── Posting Queue ─────────────────────────────────────────────────────────────

elif PAGE == "Posting Queue":
    st.title("Posting Queue")

    con = get_con()

    st.caption("Approved candidates awaiting post. Post via Reddit PRAW (free) or X GetXAPI ($0.001/call).")

    pending = con.execute("""
        SELECT c.id, p.source, p.url, substr(p.text,1,120) as snippet, i.cluster,
               c.text as reply_text, c.total_score
        FROM candidates c
        JOIN posts p ON p.id = c.post_id
        JOIN intents i ON i.post_id = p.id
        JOIN approvals a ON a.candidate_id = c.id
        LEFT JOIN post_performance pp ON pp.candidate_id = c.id
        WHERE a.decision IN ('approved', 'edited')
          AND pp.id IS NULL
        ORDER BY c.total_score DESC
        LIMIT 50
    """).fetchall()

    if not pending:
        st.info("No pending posts. Approve some candidates first.")
        con.close()
        st.stop()

    st.metric("Awaiting Post", len(pending))
    st.divider()

    for row in pending:
        with st.expander(f"#{row['id']} | {row['source']} | {row['cluster']} | score: {row['total_score']:.2f}"):
            st.caption(f"URL: {row['url']}")
            st.markdown(f"**Post:** {row['snippet']}")
            st.markdown(f"**Reply:** {row['reply_text'][:400]}")

            c1, c2 = st.columns(2)
            with c1:
                st.code(f"python -m ingestion.reddit_poster {row['id']} --no-dry-run" if row['source'] == 'reddit'
                        else f"python -m ingestion.x_poster {row['id']} --no-dry-run", language="bash")
            with c2:
                st.caption("Copy and run in terminal to post")

    con.close()
