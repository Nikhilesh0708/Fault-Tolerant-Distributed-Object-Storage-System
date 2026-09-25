"""Clearly labeled, read-only sample data. Never used as fallback for failed APIs."""
def sample_data():
    return {
        "health": {"status": "running", "mode": "preview"},
        "nodes": [
            {"node_id": "node-1", "status": "ONLINE", "used_bytes": 42 * 1024**3, "capacity_bytes": 100 * 1024**3, "response_ms": 8.2},
            {"node_id": "node-2", "status": "ONLINE", "used_bytes": 38 * 1024**3, "capacity_bytes": 100 * 1024**3, "response_ms": 9.1},
            {"node_id": "node-3", "status": "OFFLINE", "used_bytes": 40 * 1024**3, "capacity_bytes": 100 * 1024**3, "response_ms": None},
            {"node_id": "node-4", "status": "ONLINE", "used_bytes": 34 * 1024**3, "capacity_bytes": 100 * 1024**3, "response_ms": 7.6},
        ],
        "repairs": {"system_status": "HEALTHY", "active_repairs": 0, "completed_repairs": 12, "failed_repairs": 0,
                    "last_completed_scan": "Sample scan", "repairs": [
                        {"task_id": "preview-1", "object_id": "design-brief", "status": "COMPLETED", "source_node": "node-1", "destination_node": "node-4", "progress": 100, "message": "Required replicas verified", "updated_at": "Sample event"}]},
        "metrics": {"total_objects": 128, "healthy_objects": 128, "degraded_objects": 0, "average_repair_time_seconds": 4.8, "storage_overhead": 3.0},
        "files": {"files": [
            {"file_id": "design-brief", "filename": "Design brief.pdf", "size": 2400000, "replicas": 3, "status": "HEALTHY", "version": "v2"},
            {"file_id": "circuit", "filename": "Circuit simulation.zip", "size": 8400000, "replicas": 3, "status": "HEALTHY", "version": "v1"},
            {"file_id": "research", "filename": "Research notes.md", "size": 18200, "replicas": 3, "status": "HEALTHY", "version": "v3"},
            {"file_id": "poster", "filename": "Project poster.png", "size": 4100000, "replicas": 3, "status": "HEALTHY", "version": "v1"},
        ], "next_cursor": None, "truncated": False},
        "events": [
            {"timestamp": "Sample event", "kind": "repair_completed", "details": {"object_id": "design-brief", "attempts": 1}},
            {"timestamp": "Sample event", "kind": "node_offline", "details": {"node_id": "node-3"}},
            {"timestamp": "Sample event", "kind": "scrub_completed", "details": {"objects_checked": 128}},
        ],
    }
