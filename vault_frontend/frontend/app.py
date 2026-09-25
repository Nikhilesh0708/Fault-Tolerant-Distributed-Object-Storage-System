"""Python-only Streamlit UI. Network calls are delegated to api_client.py."""
import sys
from pathlib import Path
from dataclasses import replace
from datetime import datetime
import html
import time

# Allows both 'streamlit run frontend/app.py' and AppTest to use package imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import streamlit as st
from frontend.api_client import APIClient, APIError, safe_filename
from frontend.config import Config, validate_url
from frontend.preview import sample_data
from frontend.styles import CSS


st.set_page_config(page_title="Vault · Storage console", page_icon="◈", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)


def esc(value):
    return html.escape(str(value))


def format_bytes(value):
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024 or unit == "TiB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024


def invalidate():
    st.session_state["metadata_cache"] = {}


def read_data(name, config, preview=False, cursor=None):
    if preview:
        return sample_data()[name], None
    cache = st.session_state.setdefault("metadata_cache", {})
    key = (name, cursor)
    cached = cache.get(key)
    if cached and time.monotonic() - cached[0] < config.metadata_ttl:
        return cached[1], cached[2]
    client = APIClient(config)
    try:
        if name == "files":
            result = client.get_files(cursor)
        else:
            method = {"health": client.get_health, "nodes": client.get_nodes,
                      "repairs": client.get_repair_status, "metrics": client.get_metrics,
                      "events": client.get_events}[name]
            result = method()
        error = None
    except APIError as exc:
        result, error = None, str(exc)
    finally:
        client.close()
    if len(cache) > 20:
        cache.clear()
    cache[key] = (time.monotonic(), result, error)
    return result, error


def badge(status):
    status = str(status).upper()
    css = "off" if status in {"OFFLINE", "FAILED", "UNAVAILABLE", "CORRUPTED"} else "wait" if status in {"DEGRADED", "REPAIRING", "SUSPECTED", "RECOVERING", "UNKNOWN", "BLOCKED", "QUEUED"} else ""
    return f'<span class="pill {css}">{esc(status)}</span>'


def status_band(status, note):
    css = "" if status == "HEALTHY" else "neutral" if status == "UNKNOWN" else "warn"
    title = {"HEALTHY": "Your replicas are healthy", "DEGRADED": "Some replicas need attention",
             "REPAIRING": "Recovery is in progress", "UNAVAILABLE": "Some objects are currently unavailable",
             "UNKNOWN": "Waiting for verified system status"}.get(status, str(status).capitalize())
    st.markdown(f'<div class="status-band {css}"><div class="status-dot"></div><div><div class="status-title">{esc(title)}</div><div class="status-note">{esc(note)}</div></div></div>', unsafe_allow_html=True)


def node_grid(nodes):
    if not nodes:
        st.info("No storage nodes have been reported yet.")
        return
    for offset in range(0, len(nodes), 4):
        columns = st.columns(min(4, len(nodes) - offset))
        for col, node in zip(columns, nodes[offset:offset + 4]):
            with col, st.container(border=True):
                st.markdown(badge(node.get("status", "UNKNOWN")), unsafe_allow_html=True)
                st.markdown(f'<div class="node-title">{esc(node["node_id"])}</div>', unsafe_allow_html=True)
                used, capacity = node.get("used_bytes", 0), node.get("capacity_bytes", 0)
                st.progress(min(1.0, used / capacity) if capacity else 0.0)
                st.caption(f"{format_bytes(used)} / {format_bytes(capacity)}")
                latency = node.get("response_ms")
                online = node.get("status") in {"ONLINE", "FULL", "RECOVERING"}
                note = f"Last response · {latency:.1f} ms" if online and isinstance(latency, (int, float)) else "Awaiting a successful health check"
                st.markdown(f'<div class="node-note">{esc(note)}</div>', unsafe_allow_html=True)


def file_table(files):
    if not files:
        st.info("No files on this page. Upload your first object to get started.")
        return
    rows = [{"File": f["filename"], "Size": format_bytes(f["size"]),
             "Replicas": f["replicas"] if f["replicas"] is not None else "—",
             "Status": f["status"], "Version": f["version"] or "—"} for f in files]
    st.dataframe(rows, hide_index=True, width="stretch")


def header(title, subtitle):
    st.markdown('<div class="eyebrow">VAULT / DISTRIBUTED OBJECT STORAGE</div>', unsafe_allow_html=True)
    st.title(title)
    st.caption(subtitle)


def recovery_tasks(repairs):
    tasks = repairs.get("repairs", [])
    if not tasks:
        st.info("No repair tasks recorded in this service session.")
        return
    for task in list(reversed(tasks))[:8]:
        with st.container(border=True):
            left, right = st.columns([4, 1])
            left.markdown(f"**{esc(task.get('object_id', 'Object'))}**")
            right.markdown(badge(task.get("status", "UNKNOWN")), unsafe_allow_html=True)
            st.caption(f"{task.get('source_node') or 'Source pending'} → {task.get('destination_node') or 'Destination pending'}")
            progress = task.get("progress", 0)
            progress = progress if isinstance(progress, (int, float)) else 0
            st.progress(min(1.0, max(0.0, progress / 100)))
            st.caption(str(task.get("message", "")))


def overview(config, preview):
    health, health_error = read_data("health", config, preview)
    repairs, repairs_error = read_data("repairs", config, preview)
    nodes, nodes_error = read_data("nodes", config, preview)
    metrics, metrics_error = read_data("metrics", config, preview)
    header("Storage, under control.", "One place to watch your objects, replicas, and recovery.")
    if preview:
        st.info("Design preview · sample data only. Switch to Live services for your actual system.")
    elif health and health.get("mode") == "demo":
        st.info("Recovery demo connected · nodes and recovery events are simulated by your Member 3 service.")
    if health_error or repairs_error:
        st.warning(health_error or repairs_error)
    status = repairs.get("system_status", "UNKNOWN") if repairs else "UNKNOWN"
    status_band(status, "Snapshot from the latest integrity scan. A healthy replica set can include an offline node." if repairs else "Start the recovery service or check its address in Connections.")
    cols = st.columns(4)
    cols[0].metric("Stored objects", metrics.get("total_objects", "—") if metrics else "—")
    cols[1].metric("Nodes online", f"{sum(n.get('status') in {'ONLINE', 'FULL'} for n in nodes)} / {len(nodes)}" if nodes is not None else "—")
    cols[2].metric("Active repairs", repairs.get("active_repairs", "—") if repairs else "—")
    cols[3].metric("Healthy objects", metrics.get("healthy_objects", "—") if metrics else "—")
    st.write("")
    st.subheader("Storage nodes")
    if nodes_error:
        st.warning(nodes_error)
    else:
        node_grid(nodes)
    st.write("")
    main, side = st.columns([1.6, 1], gap="large")
    with main:
        st.subheader("Your files")
        listing, files_error = read_data("files", config, preview)
        if files_error:
            st.info("File storage is not connected yet. Recovery monitoring is available independently. Set the storage API address in Connections.")
            st.caption(files_error)
        else:
            file_table(listing["files"][:5])
            st.caption("Open Files to upload, download, delete, or browse more objects.")
    with side:
        st.subheader("Recent activity")
        events, error = read_data("events", config, preview)
        if error:
            st.caption(error)
        elif not events:
            st.caption("Events will appear as the recovery service works.")
        else:
            for event in events[:4]:
                details = event.get("details", {})
                if not isinstance(details, dict):
                    details = {}
                label = str(event.get("kind", "event")).replace("_", " ").capitalize()
                with st.container(border=True):
                    st.write(label)
                    st.caption(str(details.get("object_id") or details.get("node_id") or event.get("timestamp", "")))
    if metrics_error:
        st.caption("Metrics unavailable: " + metrics_error)
    st.markdown(f'<div class="footer-note">{ "Sample data" if preview else "Last dashboard refresh · " + datetime.now().strftime("%H:%M:%S") } · Vault recovery console</div>', unsafe_allow_html=True)


def files_page(config, preview):
    header("Your files", "Upload objects and manage the copies protected by your storage backend.")
    if preview:
        st.info("Read-only design preview. File actions are disabled.")
    if message := st.session_state.pop("file_notice", None):
        st.success(message)
    cursor = st.session_state.get("file_cursor")
    listing, error = read_data("files", config, preview, cursor)
    connected = listing is not None and not preview
    if error:
        st.warning(error)
        st.info("Your recovery service on port 8003 does not provide file upload or listing. Connect the main storage backend in Connections.")
    with st.container(border=True):
        st.subheader("Add an object")
        uploaded = st.file_uploader("Choose a file", accept_multiple_files=False,
                                   max_upload_size=config.upload_limit_mb, disabled=not connected,
                                   key="uploader_" + str(st.session_state.get("upload_generation", 0)))
        st.caption(f"Up to {config.upload_limit_mb} MiB per file. The browser file picker uses memory; files are never cached by this app.")
        if uploaded:
            st.caption(f"{uploaded.name} · {format_bytes(uploaded.size)}")
        if st.button("Upload to Vault", type="primary", disabled=not connected or uploaded is None):
            client = APIClient(config)
            progress = st.progress(0, text="Sending file to the storage backend…")
            last_percent = -1
            def update(sent, total):
                nonlocal last_percent
                percent = int(95 * sent / max(total, 1))
                if percent != last_percent:
                    progress.progress(percent, text=f"Sending file · {percent}% (waiting for backend confirmation)")
                    last_percent = percent
            try:
                result = client.upload_file(uploaded, uploaded.name, uploaded.size, update)
                progress.progress(100, text="Backend received the file")
                st.session_state["file_notice"] = (f"Upload accepted for {uploaded.name}; backend processing is still pending."
                    if result.get("accepted_only") else f"Uploaded {uploaded.name}.")
                st.session_state["upload_generation"] = st.session_state.get("upload_generation", 0) + 1
                st.session_state["file_cursor"] = None
                invalidate()
                st.rerun()
            except APIError as exc:
                st.error(str(exc))
            finally:
                client.close()
    st.subheader("Stored objects")
    search = st.text_input("Search files on this page", placeholder="Search by filename or object ID")
    records = listing["files"] if listing else []
    query = search.casefold()
    shown = [f for f in records if query in f["filename"].casefold() or query in f["file_id"].casefold()]
    if listing is not None:
        file_table(shown)
        if listing.get("truncated"):
            st.warning("Only the first 2,000 returned records are shown. Enable backend pagination for the rest.")
        first, next_col, count = st.columns([1, 1, 3])
        if first.button("First page", disabled=cursor is None):
            st.session_state["file_cursor"] = None
            st.rerun()
        if next_col.button("Next page", disabled=not listing.get("next_cursor") or preview):
            st.session_state["file_cursor"] = listing["next_cursor"]
            st.rerun()
        count.caption(f"{len(shown)} matching objects on this page")
    if not shown:
        return
    by_id = {f["file_id"]: f for f in shown}
    selected_id = st.selectbox("Select an object for actions", list(by_id),
                              format_func=lambda key: f"{by_id[key]['filename']} · {key}", key="selected_file")
    chosen = by_id[selected_id]
    left, right = st.columns(2, gap="large")
    with left, st.container(border=True):
        st.subheader("Download")
        if preview:
            st.button("Download file", disabled=True)
        elif not config.storage_token:
            client = APIClient(config)
            st.link_button("Download file", client.download_url(selected_id), type="primary")
            client.close()
            st.caption("The browser downloads directly from your storage service.")
        else:
            st.caption("Authenticated storage: request a temporary link, or prepare a file up to 8 MiB.")
            if st.button("Get secure download link"):
                client = APIClient(config)
                try:
                    url = client.get_download_link(selected_id)
                    st.link_button("Download file", url)
                except APIError as exc:
                    st.error(str(exc))
                    st.caption("Large protected downloads require the optional download-ticket endpoint.")
                finally:
                    client.close()
            if st.button("Prepare small download", disabled=chosen["size"] > config.small_download_limit_mb * 1024**2):
                client = APIClient(config)
                try:
                    with st.spinner("Retrieving file…"):
                        content = client.download_file(selected_id)
                    st.download_button("Save file", content, file_name=safe_filename(chosen["filename"]), mime="application/octet-stream")
                except APIError as exc:
                    st.error(str(exc))
                finally:
                    client.close()
    with right, st.container(border=True):
        st.subheader("Delete")
        st.caption("Deletion is sent to the main storage backend. Confirm the selected object before continuing.")
        with st.form("delete_" + selected_id):
            confirmed = st.checkbox("I confirm deletion of this object", disabled=preview)
            submitted = st.form_submit_button("Delete selected object", disabled=preview)
            if submitted:
                if not confirmed:
                    st.warning("Select the confirmation checkbox first. Nothing was deleted.")
                else:
                    client = APIClient(config)
                    try:
                        status = client.delete_file(selected_id)
                        st.session_state["file_notice"] = (f"Deletion requested for {chosen['filename']}; waiting for the backend."
                            if status == 202 else f"Deleted {chosen['filename']}.")
                        invalidate()
                        st.rerun()
                    except APIError as exc:
                        st.error(str(exc))
                    finally:
                        client.close()


def recovery_page(config, preview):
    header("Recovery center", "Track the work keeping your replica sets healthy.")
    repairs, error = read_data("repairs", config, preview)
    if preview:
        st.info("Read-only design preview · sample repair history.")
    if error:
        st.warning(error)
        return
    columns = st.columns(3)
    columns[0].metric("Active repairs", repairs.get("active_repairs", 0))
    columns[1].metric("Completed this session", repairs.get("completed_repairs", 0))
    columns[2].metric("Failed attempts", repairs.get("failed_repairs", 0))
    st.write("")
    st.subheader("Repair activity")
    recovery_tasks(repairs)
    st.caption("A failed or blocked repair does not prove that an object is permanently lost.")
    with st.expander("Recovery controls"):
        st.caption("Actions run in your existing recovery backend. Large integrity scans may take time.")
        action = st.selectbox("Action", ["Refresh node health", "Run integrity scan", "Pause rebalancing", "Resume rebalancing", "Run one rebalance"])
        if st.button("Run selected action", disabled=preview):
            code = {"Refresh node health": "check", "Run integrity scan": "scan", "Pause rebalancing": "pause", "Resume rebalancing": "resume", "Run one rebalance": "rebalance"}[action]
            client = APIClient(config)
            try:
                with st.spinner("Waiting for the recovery service…"):
                    result = client.recovery_action(code)
                invalidate()
                st.success("The recovery service responded.")
                st.json(result, expanded=False)
            except APIError as exc:
                st.error(str(exc))
            finally:
                client.close()


def connections_page(config, preview):
    header("Connections", "Keep the storage backend and recovery service independently configurable.")
    st.info("Recovery monitoring works with your Member 3 service on port 8003. Uploads, downloads, and deletion need the storage backend, normally on port 8000.")
    with st.form("connections"):
        storage = st.text_input("Storage API address", config.storage_url)
        recovery = st.text_input("Recovery API address", config.recovery_url)
        public = st.text_input("Browser download address", config.public_storage_url,
                               help="Address reachable from your browser. On your own laptop this usually matches the storage API address.")
        storage_token = st.text_input("Storage token (optional)", config.storage_token, type="password")
        recovery_token = st.text_input("Recovery admin token (optional in demo)", config.recovery_token, type="password")
        if st.form_submit_button("Save connections", type="primary"):
            try:
                st.session_state["connection_config"] = replace(config, storage_url=validate_url(storage),
                    recovery_url=validate_url(recovery), public_storage_url=validate_url(public),
                    storage_token=storage_token.strip(), recovery_token=recovery_token.strip())
                invalidate()
                st.session_state["file_cursor"] = None
                st.success("Connections saved for this browser session.")
            except ValueError as exc:
                st.error(str(exc))
    st.caption("Tokens remain in this session and are sent only to their configured service. They are not written to a configuration file.")
    if st.button("Test connections", disabled=preview):
        current = st.session_state["connection_config"]
        invalidate()
        for label, name in [("Recovery health", "health"), ("Storage file list", "files")]:
            value, error = read_data(name, current)
            if error:
                st.warning(label + ": " + error)
            else:
                st.success(label + " connected.")
    st.subheader("Local service addresses")
    st.table([{"Service": "Streamlit frontend", "Default port": "8501"},
              {"Service": "Storage backend", "Default port": "8000"},
              {"Service": "Recovery / Member 3", "Default port": "8003"}])


def main():
    try:
        if "connection_config" not in st.session_state:
            st.session_state["connection_config"] = Config.from_env()
    except ValueError as exc:
        st.error("Configuration error: " + str(exc))
        st.stop()
    config = st.session_state["connection_config"]
    with st.sidebar:
        st.markdown('<div class="brand"><span class="brand-mark">◈</span> vault</div><div class="brand-subtitle">DISTRIBUTED STORAGE CONSOLE</div>', unsafe_allow_html=True)
        page = st.radio("Workspace", ["Overview", "Files", "Recovery", "Connections"], label_visibility="collapsed")
        st.divider()
        mode = st.radio("Data source", ["Live services", "Design preview"], key="source_mode")
        preview = mode == "Design preview"
        auto = st.toggle("Auto-refresh monitoring", value=True, disabled=preview)
        if st.button("Refresh now", width="stretch"):
            invalidate()
        st.caption("Monitoring refreshes every 5 seconds while this tab is active. File forms refresh only when you interact.")
        st.divider()
        st.caption("VAULT / PROMPT-A-THON")
        st.caption("Store. Verify. Recover.")
    if page in {"Overview", "Recovery"}:
        @st.fragment(run_every="5s" if auto and not preview else None)
        def monitor_view():
            if page == "Overview":
                overview(config, preview)
            else:
                recovery_page(config, preview)
        monitor_view()
    elif page == "Files":
        files_page(config, preview)
    else:
        connections_page(config, preview)


main()
