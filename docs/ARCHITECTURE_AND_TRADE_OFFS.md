# Architectural Design, Contract Boundaries & Trade-Offs (`v0.7.3`)

This document specifies what `letitloop` guarantees, where its single-process local filesystem model stops, and when you should use an established database-backed workflow engine instead.

---

## 1. Core Mechanism (`LILWAL02`)

`letitloop` persists step execution state in a local directory (`wal_dir`, defaulting to `.durable_wal`):
- **Write-Ahead Log (`state.wal.jsonl`)**: Every completed `step()` or `async_step()` appends an event record encoded in `LILWAL02` framing:
  ```text
  \nLILWAL02:<byte_len_hex>:<crc32_hex>:<canonical_json_payload>\n
  ```
  Each event includes a monotonic sequence number (`seq >= 1`, starting with `INIT`) and an unkeyed SHA-256 hash chain (`prev_hash` -> `event_hash`) followed by `f.flush()` and `os.fsync()`.
- **Periodic Atomic Snapshots (`state.json`)**: Every 50 steps and on clean context exit (`close()`), the projected state is written to a PID/timestamped temporary file in the same directory, flushed with `os.fsync()`, atomically swapped into `state.json` via `os.replace()` (with a 6-attempt exponential backoff loop on Windows NTFS to survive transient antivirus/indexer handles), and followed by a parent directory `fsync` on POSIX.
- **Crash Recovery**: On restart, `load_state()` reads `state.wal.jsonl`. If the final frame was torn mid-write by `SIGKILL` or power loss, the corrupt tail is truncated back to the last valid byte offset (`good_end`). Any corruption *prior* to the final frame raises `StateError` (fail-closed).

---

## 2. Explicit Contract Boundaries & Limitations

1. **Single-Writer Local Filesystem Contract**:
   - `letitloop` is designed for a **single active process per `wal_dir`** on a local POSIX or Windows NTFS volume.
   - Do not place `wal_dir` on network filesystems (NFS, SMB, cloud-synced FUSE mounts) where `O_CREAT | O_EXCL` or `os.replace()` atomicity is not guaranteed.
2. **Step Identity (`step_id`)**:
   - `step(step_id, fn, *args, **kwargs)` keys completed outputs strictly by `step_id` within `wal_dir`.
   - Inside loops, assign a distinct `step_id` per iteration (for example, `step(f"scrape_page_{i}", scrape, url)`).
   - To run a completed workflow again from scratch with new inputs, either pass a distinct `wal_dir` per run ID (`wal_dir=f".wal/{run_id}"`) or remove the prior `wal_dir`.
3. **Integrity vs. Cryptographic Authentication**:
   - `LILWAL02` uses **CRC32** frame checksums and an **unkeyed SHA-256 hash chain** to detect accidental disk corruption and torn writes. It does not perform keyed HMAC authentication on WAL frames; any local process with write permissions to `wal_dir` can rewrite the file.
4. **External Side Effects (`atomic_marker`)**:
   - `atomic_marker(marker_id)` serializes local execution via a two-tier lock (thread lock + `FileLock` with OS process start-time token verification) and records `.pending.<pid>` before yielding and `.committed` after the block succeeds.
   - If `SIGKILL` strikes *after* an outbound HTTP request leaves the machine but *before* `.committed` is written, the block will yield `True` again on restart. Always pair non-idempotent remote HTTP calls with an upstream `Idempotency-Key` header.

---

## 3. Why We Narrowed Scope (November 2026 Landscape Note)

`letitloop` began in 2024 as a 70-file autonomous coding-agent harness before being stripped down in October 2026 (`v0.7.0`) into a single-dependency (`psutil`) local durability kernel.

By late 2026, two major shifts occurred across the Python AI ecosystem:
1. **In-process SQLite workflow engines matured**: **DBOS (`dbos`)** runs zero-daemon on embedded SQLite by default with `@DBOS.workflow()` / `@DBOS.step()`, durable timers (`DBOS.sleep`), queues, a zero-code upgrade path to PostgreSQL, and official integrations with PydanticAI and the OpenAI Agents SDK. **Prefect** similarly offers an ephemeral local SQLite mode.
2. **Agent frameworks shipped native local checkpointers**: **LangGraph** (`SqliteSaver` + `@entrypoint` / `@task`), **CrewAI** (`@persist` Flows on SQLite), **Google ADK** (`DatabaseSessionService`), **OpenAI Agents SDK** (`SQLiteSession`), and **LlamaIndex** (`WorkflowCheckpointer`) all provide built-in state persistence.

Consequently, `letitloop` does not attempt to compete with DBOS, Temporal, or native framework savers. It remains a compact, auditable reference implementation (~2,000 lines of core Python in `orchestrator/`) for developers who want a zero-SQLite, human-readable JSONL Write-Ahead Log and a cross-platform CLI crash supervisor (`lil watch`) with one dependency.
