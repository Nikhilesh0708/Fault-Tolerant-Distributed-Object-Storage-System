"""Small durable event history; not authoritative object metadata."""
import json
import logging
import sqlite3
from collections import Counter, deque
from .models import now

log = logging.getLogger("vault.recovery")


class Metrics:
    def __init__(self, path: str, limit: int = 500):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, timestamp TEXT, kind TEXT, details TEXT)")
        self.limit = limit
        self.counts = Counter()
        self.repair_times = deque(maxlen=limit)
        self.detection_times = deque(maxlen=limit)

    def event(self, kind: str, **details):
        self.counts[kind] += 1
        self.db.execute("INSERT INTO events(timestamp, kind, details) VALUES(?,?,?)",
                        (now(), kind, json.dumps(details)))
        self.db.execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT ?)", (self.limit,))
        self.db.commit()
        level = logging.ERROR if "failed" in kind else logging.WARNING if "offline" in kind or "corrupt" in kind else logging.INFO
        log.log(level, "%s %s", kind, details)

    def events(self, limit=100):
        return [{"timestamp": t, "kind": k, "details": json.loads(d)} for t, k, d in
                self.db.execute("SELECT timestamp,kind,details FROM events ORDER BY id DESC LIMIT ?", (min(limit, self.limit),))]

    @staticmethod
    def average(samples):
        return sum(samples) / len(samples) if samples else None

    def close(self):
        self.db.close()
