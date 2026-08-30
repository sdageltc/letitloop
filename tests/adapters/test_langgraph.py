"""Unit tests for LangGraph native LetItLoopCheckpointSaver."""

import os

import pytest
from letitloop.adapters.langgraph import LetItLoopCheckpointSaver


def test_langgraph_saver_init(tmp_path):
    wal_dir = tmp_path / "lg_checkpoints"
    saver = LetItLoopCheckpointSaver(wal_dir=str(wal_dir))
    assert os.path.isdir(str(wal_dir))
    assert os.path.isfile(saver.db_path)


def test_langgraph_put_get_tuple(tmp_path):
    wal_dir = tmp_path / "lg_checkpoints"
    saver = LetItLoopCheckpointSaver(wal_dir=str(wal_dir))

    config = {"configurable": {"thread_id": "thread_1", "checkpoint_ns": ""}}
    checkpoint = {
        "v": 1,
        "ts": "2026-08-30T10:00:00Z",
        "id": "1ef6-0001",
        "channel_values": {"messages": ["hello"], "count": 1},
    }
    metadata = {"source": "input", "step": 1}

    # Put checkpoint
    res_config = saver.put(config, checkpoint, metadata)
    assert res_config["configurable"]["thread_id"] == "thread_1"
    assert res_config["configurable"]["checkpoint_id"] == "1ef6-0001"

    # Get tuple
    chk_tuple = saver.get_tuple(config)
    assert chk_tuple is not None

    if hasattr(chk_tuple, "checkpoint"):
        assert chk_tuple.checkpoint["channel_values"]["count"] == 1
        assert chk_tuple.metadata["step"] == 1
    else:
        assert chk_tuple["checkpoint"]["channel_values"]["count"] == 1
        assert chk_tuple["metadata"]["step"] == 1


def test_langgraph_writes_and_list(tmp_path):
    wal_dir = tmp_path / "lg_checkpoints"
    saver = LetItLoopCheckpointSaver(wal_dir=str(wal_dir))

    config1 = {"configurable": {"thread_id": "thread_2", "checkpoint_ns": "", "checkpoint_id": "c1"}}
    saver.put(config1, {"id": "c1", "state": "init"}, {"step": 1})

    config2 = {"configurable": {"thread_id": "thread_2", "checkpoint_ns": "", "checkpoint_id": "c2"}}
    saver.put(config2, {"id": "c2", "state": "step2"}, {"step": 2})

    # Record write
    saver.put_writes(config2, [("messages", "write_val_1")], task_id="node_a")

    # List checkpoints
    items = list(saver.list({"configurable": {"thread_id": "thread_2"}}))
    assert len(items) == 2


@pytest.mark.asyncio
async def test_langgraph_async_methods(tmp_path):
    wal_dir = tmp_path / "lg_async"
    saver = LetItLoopCheckpointSaver(wal_dir=str(wal_dir))

    config = {"configurable": {"thread_id": "async_thread", "checkpoint_ns": "", "checkpoint_id": "a1"}}
    await saver.aput(config, {"id": "a1", "val": 42}, {"meta": "test"})

    chk = await saver.aget_tuple(config)
    assert chk is not None
