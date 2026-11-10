"""VisionTrace Databricks App: restricted-zone security event search.

Reads workspace.visiontrace Delta tables through the shared Serverless
Starter Warehouse. Auth is the app service principal (WorkspaceClient
default). Pages: video list, per-video event timeline, filter search.
"""
from __future__ import annotations

import os

import streamlit as st

SCHEMA = os.environ.get("VISIONTRACE_SCHEMA", "workspace.visiontrace")
WH = (
    os.environ.get("DATABRICKS_WAREHOUSE_ID")
    or os.environ.get("VISIONTRACE_WAREHOUSE_ID")
    or "69f9e6c699ad2f29"
)

st.set_page_config(page_title="VisionTrace", page_icon="📹", layout="wide")


@st.cache_resource
def _ws():
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()


def query(statement: str, params: dict | None = None) -> list[dict]:
    from databricks.sdk.service.sql import StatementParameterListItem

    items = [StatementParameterListItem(name=k, value=str(v)) for k, v in (params or {}).items()]
    result = _ws().statement_execution.execute_statement(
        statement=statement,
        warehouse_id=WH,
        parameters=items or None,
        wait_timeout="50s",
    )
    state = result.status.state
    state_val = getattr(state, "value", str(state))
    if state_val != "SUCCEEDED":
        err = ""
        if result.status and result.status.error:
            err = result.status.error.message or ""
        raise RuntimeError(f"{state_val}: {err}")
    if not result.result or not result.manifest:
        return []
    cols = [c.name for c in result.manifest.schema.columns]
    return [dict(zip(cols, row)) for row in (result.result.data_array or [])]


@st.cache_data(ttl=60)
def load_videos() -> list[dict]:
    return query(
        f"""
        SELECT v.video_id, v.camera_id, CAST(v.recorded_at AS STRING) AS recorded_at,
               v.duration_s, v.fps, v.width, v.height, v.zone_json,
               COUNT(e.event_id) AS event_count
        FROM {SCHEMA}.visiontrace_videos v
        LEFT JOIN {SCHEMA}.visiontrace_events e ON v.video_id = e.video_id
        GROUP BY v.video_id, v.camera_id, recorded_at, v.duration_s, v.fps, v.width, v.height, v.zone_json
        ORDER BY v.video_id
        """
    )


@st.cache_data(ttl=60)
def load_events(video_id: str | None = None, event_type: str | None = None, text: str = "") -> list[dict]:
    clauses = ["1=1"]
    params: dict = {}
    if video_id:
        clauses.append("video_id = :vid")
        params["vid"] = video_id
    if event_type:
        clauses.append("event_type = :et")
        params["et"] = event_type
    if text:
        clauses.append("lower(description) LIKE concat('%', lower(:q), '%')")
        params["q"] = text
    where = " AND ".join(clauses)
    return query(
        f"""
        SELECT event_id, video_id, event_type, track_id, start_s, end_s,
               description, confidence, backend
        FROM {SCHEMA}.visiontrace_events
        WHERE {where}
        ORDER BY video_id, start_s
        LIMIT 500
        """,
        params,
    )


def _counts() -> tuple[int, int]:
    try:
        n_v = int(query(f"SELECT count(*) AS n FROM {SCHEMA}.visiontrace_videos")[0]["n"])
        n_e = int(query(f"SELECT count(*) AS n FROM {SCHEMA}.visiontrace_events")[0]["n"])
        return n_v, n_e
    except Exception as exc:  # noqa: BLE001
        st.session_state["_vt_error"] = str(exc)[:400]
        return -1, -1


st.title("VisionTrace")
st.caption("Structured security event search for restricted-zone cameras.")
n_v, n_e = _counts()
if n_v < 0:
    st.error(f"Could not read {SCHEMA} via warehouse {WH}: {st.session_state.get('_vt_error', '')}")
    st.stop()
st.success(f"Loaded {n_v} videos and {n_e} events from `{SCHEMA}`.")

page = st.sidebar.radio("Pages", ["Videos", "Event timeline", "Search"], index=0)
videos = load_videos()
video_ids = [str(v["video_id"]) for v in videos]
types = sorted({str(e["event_type"]) for e in load_events()})

if page == "Videos":
    st.header("Videos")
    st.dataframe(videos, use_container_width=True, hide_index=True)
    if video_ids:
        pick = st.selectbox("Open event timeline for", video_ids)
        if st.button("Open timeline"):
            st.session_state["timeline_video"] = pick
            st.sidebar.radio("Pages", ["Videos", "Event timeline", "Search"], index=1, key="_nav_jump")
            st.info(f"Use the sidebar: open **Event timeline** and select `{pick}`.")

elif page == "Event timeline":
    st.header("Event timeline")
    default = st.session_state.get("timeline_video")
    idx = video_ids.index(default) if default in video_ids else 0
    vid = st.selectbox("Video", video_ids, index=idx if video_ids else 0)
    if not vid:
        st.warning("No videos in the table yet.")
        st.stop()
    meta = next((v for v in videos if str(v["video_id"]) == vid), {})
    c1, c2, c3 = st.columns(3)
    c1.metric("Camera", str(meta.get("camera_id") or "—"))
    c2.metric("Recorded", str(meta.get("recorded_at") or "—"))
    c3.metric("Events", str(meta.get("event_count") or 0))
    st.caption(f"Zone: {meta.get('zone_json') or '—'}  ·  {meta.get('width')}×{meta.get('height')} @ {meta.get('fps')} fps")
    evs = load_events(video_id=vid)
    if not evs:
        st.info("No events for this video.")
    else:
        for ev in evs:
            start, end = float(ev["start_s"]), float(ev["end_s"])
            label = f"**{start:.1f}–{end:.1f}s** · `{ev['event_type']}` · track {ev['track_id']}"
            st.markdown(label)
            st.write(str(ev["description"]))
        st.subheader("Table")
        st.dataframe(evs, use_container_width=True, hide_index=True)

else:
    st.header("Search events")
    q = st.text_input("Description contains", "")
    col_a, col_b = st.columns(2)
    et = col_a.selectbox("Event type", [""] + types)
    vid = col_b.selectbox("Video", [""] + video_ids)
    evs = load_events(video_id=vid or None, event_type=et or None, text=q.strip())
    st.write(f"{len(evs)} matching events")
    st.dataframe(evs, use_container_width=True, hide_index=True)
