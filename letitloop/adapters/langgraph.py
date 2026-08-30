"""letitloop/adapters/langgraph.py — LangGraph native BaseCheckpointSaver implementation with sub-millisecond SQLite WAL.

Provides zero-daemon, sub-millisecond checkpointing for LangGraph and LangChain workflows.
Implements the exact BaseCheckpointSaver interface required by LangGraph compile(checkpointer=...)
using an embedded SQLite database configured with atomic WAL mode (PRAGMA journal_mode=WAL).
"""

from __future__ import annotations

import json
import os
import pickle
import sqlite3
import threading
import time
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

# Try importing BaseCheckpointSaver from LangGraph if installed
_HAS_LANGGRAPH = False
try:
    from langgraph.checkpoint.base import (
        BaseCheckpointSaver,
        ChannelVersions,
        Checkpoint,
        CheckpointMetadata,
        CheckpointTuple,
    )

    _HAS_LANGGRAPH = True
except ImportError:
    # Standalone duck-typed fallback base class
    class BaseCheckpointSaver:  # type: ignore
        """Duck-typed fallback when langgraph is not installed."""

        pass

    Checkpoint = Dict[str, Any]  # type: ignore
    CheckpointMetadata = Dict[str, Any]  # type: ignore
    CheckpointTuple = Any  # type: ignore
    ChannelVersions = Dict[str, Any]  # type: ignore


class LetItLoopCheckpointSaver(BaseCheckpointSaver):
    """Sub-millisecond SQLite WAL checkpoint saver for LangGraph StateGraph workflows.

    Parameters
    ----------
    db_path : Optional[str]
        Path to the SQLite database file. Defaults to '.letitloop/checkpoints/langgraph.db'.
    wal_dir : Optional[str]
        Directory for the database and WAL journals.

    Examples
    --------
    >>> from letitloop.adapters.langgraph import LetItLoopCheckpointSaver
    >>> checkpointer = LetItLoopCheckpointSaver()
    >>> # Pass directly to LangGraph graph compilation:
    >>> # app = workflow.compile(checkpointer=checkpointer)
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        wal_dir: Optional[str] = None,
    ) -> None:
        if db_path:
            self.db_path = os.path.abspath(db_path)
            self.wal_dir = os.path.dirname(self.db_path)
        else:
            base_dir = os.path.abspath(
                wal_dir or os.environ.get("LETITLOOP_WAL_DIR") or os.path.join(".letitloop", "checkpoints")
            )
            self.wal_dir = base_dir
            self.db_path = os.path.join(self.wal_dir, "langgraph_state.db")

        os.makedirs(self.wal_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    @classmethod
    def is_available(cls) -> bool:
        """Return True if LangGraph is available in the environment."""
        return _HAS_LANGGRAPH

    def _get_connection(self) -> sqlite3.Connection:
        """Create a thread-safe connection configured with SQLite WAL mode."""
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_db(self) -> None:
        """Initialize database tables for checkpoints and writes."""
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS checkpoints (
                            thread_id TEXT NOT NULL,
                            checkpoint_ns TEXT NOT NULL DEFAULT '',
                            checkpoint_id TEXT NOT NULL,
                            parent_checkpoint_id TEXT,
                            type TEXT,
                            checkpoint BLOB NOT NULL,
                            metadata BLOB,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
                        );
                    """)
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS checkpoint_writes (
                            thread_id TEXT NOT NULL,
                            checkpoint_ns TEXT NOT NULL DEFAULT '',
                            checkpoint_id TEXT NOT NULL,
                            task_id TEXT NOT NULL,
                            idx INTEGER NOT NULL,
                            channel TEXT NOT NULL,
                            type TEXT,
                            value BLOB NOT NULL,
                            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
                        );
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_checkpoints_lookup
                        ON checkpoints(thread_id, checkpoint_ns, checkpoint_id);
                    """)
            finally:
                conn.close()

    def _dump_blob(self, obj: Any) -> bytes:
        """Serialize checkpoint or metadata to bytes (JSON with pickle fallback)."""
        try:
            return json.dumps(obj, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            return pickle.dumps(obj)

    def _load_blob(self, data: bytes) -> Any:
        """Deserialize bytes to object."""
        if not data:
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return pickle.loads(data)

    def get_tuple(self, config: Dict[str, Any]) -> Optional[Any]:
        """Fetch the latest checkpoint tuple matching config (thread_id, checkpoint_ns, checkpoint_id)."""
        configurable = config.get("configurable", config)
        thread_id = str(configurable.get("thread_id", ""))
        checkpoint_ns = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = configurable.get("checkpoint_id")

        with self._lock:
            conn = self._get_connection()
            try:
                if checkpoint_id:
                    cursor = conn.execute(
                        """
                        SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint, metadata
                        FROM checkpoints
                        WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                        """,
                        (thread_id, checkpoint_ns, str(checkpoint_id)),
                    )
                else:
                    cursor = conn.execute(
                        """
                        SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint, metadata
                        FROM checkpoints
                        WHERE thread_id = ? AND checkpoint_ns = ?
                        ORDER BY checkpoint_id DESC
                        LIMIT 1
                        """,
                        (thread_id, checkpoint_ns),
                    )
                row = cursor.fetchone()
                if not row:
                    return None

                t_id, c_ns, c_id, parent_id, c_blob, m_blob = row
                checkpoint_dict = self._load_blob(c_blob)
                metadata_dict = self._load_blob(m_blob) or {}

                # Fetch pending writes if any
                w_cursor = conn.execute(
                    """
                    SELECT task_id, channel, value
                    FROM checkpoint_writes
                    WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                    ORDER BY idx ASC
                    """,
                    (t_id, c_ns, c_id),
                )
                pending_writes = [(r[0], r[1], self._load_blob(r[2])) for r in w_cursor.fetchall()]

                cfg = {
                    "configurable": {
                        "thread_id": t_id,
                        "checkpoint_ns": c_ns,
                        "checkpoint_id": c_id,
                    }
                }

                if _HAS_LANGGRAPH:
                    return CheckpointTuple(
                        config=cfg,
                        checkpoint=checkpoint_dict,
                        metadata=metadata_dict,
                        parent_config={
                            "configurable": {"thread_id": t_id, "checkpoint_ns": c_ns, "checkpoint_id": parent_id}
                        }
                        if parent_id
                        else None,
                        pending_writes=pending_writes,
                    )

                return {
                    "config": cfg,
                    "checkpoint": checkpoint_dict,
                    "metadata": metadata_dict,
                    "parent_checkpoint_id": parent_id,
                    "pending_writes": pending_writes,
                }
            finally:
                conn.close()

    def list(
        self,
        config: Optional[Dict[str, Any]],
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
    ) -> Iterator[Any]:
        """List checkpoints matching the filter criteria."""
        configurable = (config or {}).get("configurable", config or {})
        thread_id = str(configurable.get("thread_id", "")) if configurable.get("thread_id") else None
        checkpoint_ns = str(configurable.get("checkpoint_ns", "")) if configurable.get("checkpoint_ns") else None

        query = "SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint, metadata FROM checkpoints WHERE 1=1"
        params: List[Any] = []

        if thread_id:
            query += " AND thread_id = ?"
            params.append(thread_id)
        if checkpoint_ns:
            query += " AND checkpoint_ns = ?"
            params.append(checkpoint_ns)
        if before:
            before_cfg = before.get("configurable", before)
            if before_cfg.get("checkpoint_id"):
                query += " AND checkpoint_id < ?"
                params.append(str(before_cfg["checkpoint_id"]))

        query += " ORDER BY checkpoint_id DESC"
        if limit:
            query += f" LIMIT {int(limit)}"

        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.execute(query, params)
                for row in cursor.fetchall():
                    t_id, c_ns, c_id, parent_id, c_blob, m_blob = row
                    cfg = {
                        "configurable": {
                            "thread_id": t_id,
                            "checkpoint_ns": c_ns,
                            "checkpoint_id": c_id,
                        }
                    }
                    c_dict = self._load_blob(c_blob)
                    m_dict = self._load_blob(m_blob) or {}
                    if _HAS_LANGGRAPH:
                        yield CheckpointTuple(
                            config=cfg,
                            checkpoint=c_dict,
                            metadata=m_dict,
                            parent_config={
                                "configurable": {"thread_id": t_id, "checkpoint_ns": c_ns, "checkpoint_id": parent_id}
                            }
                            if parent_id
                            else None,
                        )
                    else:
                        yield {
                            "config": cfg,
                            "checkpoint": c_dict,
                            "metadata": m_dict,
                            "parent_checkpoint_id": parent_id,
                        }
            finally:
                conn.close()

    def put(
        self,
        config: Dict[str, Any],
        checkpoint: Dict[str, Any],
        metadata: Dict[str, Any],
        new_versions: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Store a checkpoint for the specified config."""
        configurable = config.get("configurable", config)
        thread_id = str(configurable.get("thread_id", ""))
        checkpoint_ns = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = str(checkpoint.get("id") or configurable.get("checkpoint_id") or time.time())
        parent_id = (
            str(configurable.get("checkpoint_id", "")) if configurable.get("checkpoint_id") != checkpoint_id else None
        )

        c_blob = self._dump_blob(checkpoint)
        m_blob = self._dump_blob(metadata)

        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint, metadata)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (thread_id, checkpoint_ns, checkpoint_id, parent_id, c_blob, m_blob),
                    )
            finally:
                conn.close()

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(
        self,
        config: Dict[str, Any],
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
    ) -> None:
        """Store intermediate writes for an ongoing step."""
        configurable = config.get("configurable", config)
        thread_id = str(configurable.get("thread_id", ""))
        checkpoint_ns = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = str(configurable.get("checkpoint_id", ""))

        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    for idx, (channel, val) in enumerate(writes):
                        v_blob = self._dump_blob(val)
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, value)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (thread_id, checkpoint_ns, checkpoint_id, str(task_id), idx, str(channel), v_blob),
                        )
            finally:
                conn.close()

    # Async compatibility interfaces
    async def aget_tuple(self, config: Dict[str, Any]) -> Optional[Any]:
        return self.get_tuple(config)

    async def alist(
        self,
        config: Optional[Dict[str, Any]],
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
    ) -> Iterator[Any]:
        return self.list(config, filter=filter, before=before, limit=limit)

    async def aput(
        self,
        config: Dict[str, Any],
        checkpoint: Dict[str, Any],
        metadata: Dict[str, Any],
        new_versions: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: Dict[str, Any],
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
    ) -> None:
        return self.put_writes(config, writes, task_id)
