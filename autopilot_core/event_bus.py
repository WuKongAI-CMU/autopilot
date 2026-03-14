"""File-based NDJSON event bus for cross-session coordination.

Extracted and generalized from agent-ops discovery-bus.py.
Zero external dependencies — stdlib only.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Tail-read tuning constants
_TAIL_READ_DEFAULT = 256 * 1024
_TAIL_READ_MIN = 8 * 1024
_TAIL_READ_MAX = 2 * 1024 * 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_json_line(raw: str) -> dict[str, Any] | None:
    line = str(raw).strip()
    if not line:
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _resolve_tail_bytes(value: Any, default: int = _TAIL_READ_DEFAULT) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return 0
    return max(_TAIL_READ_MIN, min(_TAIL_READ_MAX, parsed))


class EventBus:
    """Append-only NDJSON event bus with file locking for concurrent safety.

    Supports two modes:
    - Unidirectional: append events, read recent events
    - Bidirectional: publish/subscribe to channels with TTL support

    Args:
        path: Path to the NDJSON file. Created automatically on first write.
        tail_bytes: Bytes to read from end of file for tail optimization.
            Set to 0 to disable tail mode and always read full file.
    """

    def __init__(self, path: str | Path, *, tail_bytes: int | None = None):
        self.path = Path(path).expanduser().resolve()
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._tail_bytes = _resolve_tail_bytes(
            tail_bytes if tail_bytes is not None else os.environ.get("AUTOPILOT_TAIL_BYTES"),
        )

    def _acquire_lock(self, *, exclusive: bool) -> Any:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._lock_path.open("a+", encoding="utf-8")
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(handle, mode)
        return handle

    def _release_lock(self, handle: Any) -> None:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()

    def _load_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                data = _safe_json_line(line)
                if data is not None:
                    rows.append(data)
        return rows

    def _load_tail(self) -> tuple[list[dict[str, Any]], bool]:
        if not self.path.exists():
            return [], False
        with self.path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size <= 0:
                return [], False
            read_bytes = min(size, self._tail_bytes)
            f.seek(size - read_bytes)
            chunk = f.read(read_bytes)

        text = chunk.decode("utf-8", errors="replace")
        lines = text.splitlines()
        truncated = read_bytes < size
        if truncated and lines:
            lines = lines[1:]  # First line may be partial

        rows: list[dict[str, Any]] = []
        for line in lines:
            data = _safe_json_line(line)
            if data is not None:
                rows.append(data)
        return rows, truncated

    def _atomic_rewrite(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_raw = tempfile.mkstemp(
            prefix=".autopilot-bus-", suffix=".tmp", dir=str(self.path.parent)
        )
        tmp_path = Path(tmp_raw)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=True))
                    f.write("\n")
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _make_event_id(self, event_type: str, timestamp: str) -> str:
        seed = f"{timestamp}|{event_type}|{id(self)}|{os.getpid()}"
        short = hashlib.sha256(seed.encode()).hexdigest()[:10]
        ts_compact = timestamp.replace(":", "").replace("-", "").replace("T", "").replace("Z", "")
        return f"evt-{ts_compact}-{short}"

    # --- Core API ---

    def append(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        source: str | None = None,
        trace_id: str | None = None,
    ) -> str:
        """Append an event to the bus. Returns the event ID."""
        timestamp = _now_iso()
        event_id = self._make_event_id(event_type, timestamp)
        record: dict[str, Any] = {
            "id": event_id,
            "event_type": event_type,
            "timestamp": timestamp,
            "source": source or "autopilot",
            "trace_id": trace_id,
            "payload": payload or {},
        }

        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self._acquire_lock(exclusive=True)
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=True))
                f.write("\n")
                f.flush()
        finally:
            self._release_lock(lock)
        return event_id

    def recent(
        self,
        n: int = 50,
        *,
        event_type: str | None = None,
        max_age_hours: float | None = None,
    ) -> list[dict[str, Any]]:
        """Read the most recent N events, optionally filtered by type and age."""
        if n <= 0:
            return []

        lock = self._acquire_lock(exclusive=False)
        try:
            if self._tail_bytes > 0:
                rows, truncated = self._load_tail()
                if truncated and len(rows) < n:
                    rows = self._load_all()
            else:
                rows = self._load_all()
        finally:
            self._release_lock(lock)

        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(hours=max_age_hours)) if max_age_hours else None

        filtered: deque[dict[str, Any]] = deque(maxlen=n)
        for row in rows:
            if event_type and row.get("event_type") != event_type:
                continue
            if cutoff:
                ts = _parse_iso(row.get("timestamp", ""))
                if ts is None or ts < cutoff:
                    continue
            filtered.append(row)
        return list(filtered)

    def read_recent(self, n: int = 50) -> list[dict[str, Any]]:
        """Alias for recent() with no filters."""
        return self.recent(n)

    # --- Pub/Sub API ---

    def publish(
        self,
        channel: str,
        message: str | dict[str, Any],
        *,
        source: str | None = None,
        ttl_hours: int = 24,
    ) -> str:
        """Publish a message to a named channel."""
        payload = {
            "channel": channel,
            "message": message if isinstance(message, dict) else {"text": message},
            "ttl_hours": ttl_hours,
        }
        return self.append(f"channel.{channel}", payload, source=source)

    def subscribe(
        self,
        channel: str,
        *,
        since_hours: float = 24.0,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Read messages from a named channel within the time window."""
        events = self.recent(
            limit * 5,  # Over-fetch then filter
            event_type=f"channel.{channel}",
            max_age_hours=since_hours,
        )

        now = datetime.now(timezone.utc)
        result: list[dict[str, Any]] = []
        for event in events:
            # Check TTL
            ttl_h = (event.get("payload") or {}).get("ttl_hours")
            if ttl_h and isinstance(ttl_h, (int, float)):
                ts = _parse_iso(event.get("timestamp", ""))
                if ts and (now - ts).total_seconds() > ttl_h * 3600:
                    continue
            result.append(event)
            if len(result) >= limit:
                break
        return result

    # --- Maintenance ---

    def gc(self, max_age_hours: float = 48.0) -> dict[str, int]:
        """Garbage-collect expired events. Returns {removed, kept}."""
        now = datetime.now(timezone.utc)
        age_cutoff = now - timedelta(hours=max_age_hours)

        lock = self._acquire_lock(exclusive=True)
        try:
            rows = self._load_all()
            kept: list[dict[str, Any]] = []
            removed = 0
            for row in rows:
                ts = _parse_iso(row.get("timestamp", ""))
                ttl_h = (row.get("payload") or {}).get("ttl_hours")

                expired_by_ttl = False
                if ts and ttl_h and isinstance(ttl_h, (int, float)):
                    expired_by_ttl = (now - ts).total_seconds() > ttl_h * 3600

                expired_by_age = bool(ts and ts < age_cutoff)

                if expired_by_ttl or expired_by_age:
                    removed += 1
                else:
                    kept.append(row)

            if removed > 0:
                self._atomic_rewrite(kept)
        finally:
            self._release_lock(lock)

        return {"removed": removed, "kept": len(kept)}

    def prune(self, max_entries: int) -> dict[str, int]:
        """Keep only the most recent max_entries events."""
        lock = self._acquire_lock(exclusive=True)
        try:
            rows = self._load_all()
            total = len(rows)
            if total <= max_entries:
                return {"total": total, "kept": total, "pruned": 0}

            kept = rows[-max_entries:]
            self._atomic_rewrite(kept)
        finally:
            self._release_lock(lock)

        return {"total": total, "kept": len(kept), "pruned": total - len(kept)}
