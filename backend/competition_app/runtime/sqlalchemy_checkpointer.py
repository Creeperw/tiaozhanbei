from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    ChannelVersions,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from sqlalchemy import Engine, text


class SqlAlchemyCheckpointSaver(BaseCheckpointSaver[str]):
    """Portable LangGraph saver backed by the application's SQLAlchemy engine.

    The official SQLite/Postgres savers are optional packages.  This adapter
    keeps the project deployable on both its supported SQLite development
    database and MySQL production database without introducing a second store.
    """

    persistent = True

    def __init__(self, engine: Engine, *, serde=None) -> None:
        super().__init__(serde=serde)
        self.engine = engine

    @staticmethod
    def _config(thread_id: str, namespace: str, checkpoint_id: str) -> RunnableConfig:
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": namespace,
                "checkpoint_id": checkpoint_id,
            }
        }

    def _load_blobs(
        self,
        connection,
        thread_id: str,
        namespace: str,
        versions: ChannelVersions,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for channel, version in versions.items():
            row = connection.execute(
                text(
                    "SELECT value_type, value_blob FROM langgraph_checkpoint_blobs "
                    "WHERE thread_id=:thread_id AND checkpoint_ns=:namespace "
                    "AND channel_name=:channel AND channel_version=:version"
                ),
                {
                    "thread_id": thread_id,
                    "namespace": namespace,
                    "channel": channel,
                    "version": str(version),
                },
            ).first()
            if row is not None and row.value_type != "empty":
                values[channel] = self.serde.loads_typed((row.value_type, bytes(row.value_blob)))
        return values

    def _pending_writes(
        self,
        connection,
        thread_id: str,
        namespace: str,
        checkpoint_id: str,
    ) -> list[tuple[str, str, Any]]:
        rows = connection.execute(
            text(
                "SELECT task_id, channel_name, value_type, value_blob "
                "FROM langgraph_checkpoint_writes WHERE thread_id=:thread_id "
                "AND checkpoint_ns=:namespace AND checkpoint_id=:checkpoint_id "
                "ORDER BY task_id, write_index"
            ),
            {
                "thread_id": thread_id,
                "namespace": namespace,
                "checkpoint_id": checkpoint_id,
            },
        ).all()
        return [
            (
                row.task_id,
                row.channel_name,
                self.serde.loads_typed((row.value_type, bytes(row.value_blob))),
            )
            for row in rows
        ]

    def _tuple_from_row(self, connection, row, *, requested_config=None) -> CheckpointTuple:
        checkpoint = self.serde.loads_typed(
            (row.checkpoint_type, bytes(row.checkpoint_blob))
        )
        checkpoint = {
            **checkpoint,
            "channel_values": self._load_blobs(
                connection,
                row.thread_id,
                row.checkpoint_ns,
                checkpoint["channel_versions"],
            ),
        }
        config = requested_config or self._config(
            row.thread_id, row.checkpoint_ns, row.checkpoint_id
        )
        parent_config = (
            self._config(row.thread_id, row.checkpoint_ns, row.parent_checkpoint_id)
            if row.parent_checkpoint_id else None
        )
        return CheckpointTuple(
            config=config,
            checkpoint=checkpoint,
            metadata=self.serde.loads_typed(
                (row.metadata_type, bytes(row.metadata_blob))
            ),
            parent_config=parent_config,
            pending_writes=self._pending_writes(
                connection, row.thread_id, row.checkpoint_ns, row.checkpoint_id
            ),
        )

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        namespace = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = get_checkpoint_id(config)
        query = (
            "SELECT * FROM langgraph_checkpoints WHERE thread_id=:thread_id "
            "AND checkpoint_ns=:namespace"
        )
        params = {"thread_id": thread_id, "namespace": namespace}
        if checkpoint_id:
            query += " AND checkpoint_id=:checkpoint_id"
            params["checkpoint_id"] = checkpoint_id
        query += " ORDER BY checkpoint_id DESC LIMIT 1"
        with self.engine.connect() as connection:
            row = connection.execute(text(query), params).first()
            return self._tuple_from_row(
                connection,
                row,
                requested_config=config if checkpoint_id else None,
            ) if row is not None else None

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        clauses = []
        params: dict[str, Any] = {}
        if config:
            configurable = config["configurable"]
            clauses.append("thread_id=:thread_id")
            params["thread_id"] = str(configurable["thread_id"])
            if "checkpoint_ns" in configurable:
                clauses.append("checkpoint_ns=:namespace")
                params["namespace"] = str(configurable.get("checkpoint_ns", ""))
            if checkpoint_id := get_checkpoint_id(config):
                clauses.append("checkpoint_id=:checkpoint_id")
                params["checkpoint_id"] = checkpoint_id
        if before and (before_id := get_checkpoint_id(before)):
            clauses.append("checkpoint_id<:before_id")
            params["before_id"] = before_id
        query = "SELECT * FROM langgraph_checkpoints"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY checkpoint_id DESC"
        if limit is not None:
            query += " LIMIT :limit"
            params["limit"] = max(0, limit)
        with self.engine.connect() as connection:
            rows = connection.execute(text(query), params).all()
            for row in rows:
                item = self._tuple_from_row(connection, row)
                if filter and not all(item.metadata.get(key) == value for key, value in filter.items()):
                    continue
                yield item

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        namespace = str(configurable.get("checkpoint_ns", ""))
        parent_id = configurable.get("checkpoint_id")
        checkpoint_copy = checkpoint.copy()
        channel_values: dict[str, Any] = checkpoint_copy.pop("channel_values")
        checkpoint_type, checkpoint_blob = self.serde.dumps_typed(checkpoint_copy)
        metadata_type, metadata_blob = self.serde.dumps_typed(
            get_checkpoint_metadata(config, metadata)
        )
        with self.engine.begin() as connection:
            for channel, version in new_versions.items():
                value_type, value_blob = (
                    self.serde.dumps_typed(channel_values[channel])
                    if channel in channel_values else ("empty", b"")
                )
                exists = connection.execute(
                    text(
                        "SELECT 1 FROM langgraph_checkpoint_blobs WHERE thread_id=:thread_id "
                        "AND checkpoint_ns=:namespace AND channel_name=:channel "
                        "AND channel_version=:version"
                    ),
                    {"thread_id": thread_id, "namespace": namespace, "channel": channel, "version": str(version)},
                ).first()
                if exists is None:
                    connection.execute(
                        text(
                            "INSERT INTO langgraph_checkpoint_blobs "
                            "(thread_id, checkpoint_ns, channel_name, channel_version, value_type, value_blob) "
                            "VALUES (:thread_id, :namespace, :channel, :version, :value_type, :value_blob)"
                        ),
                        {"thread_id": thread_id, "namespace": namespace, "channel": channel, "version": str(version), "value_type": value_type, "value_blob": value_blob},
                    )
            exists = connection.execute(
                text(
                    "SELECT 1 FROM langgraph_checkpoints WHERE thread_id=:thread_id "
                    "AND checkpoint_ns=:namespace AND checkpoint_id=:checkpoint_id"
                ),
                {"thread_id": thread_id, "namespace": namespace, "checkpoint_id": checkpoint["id"]},
            ).first()
            values = {
                "thread_id": thread_id,
                "namespace": namespace,
                "checkpoint_id": checkpoint["id"],
                "parent_id": parent_id,
                "checkpoint_type": checkpoint_type,
                "checkpoint_blob": checkpoint_blob,
                "metadata_type": metadata_type,
                "metadata_blob": metadata_blob,
            }
            if exists is None:
                connection.execute(
                    text(
                        "INSERT INTO langgraph_checkpoints "
                        "(thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint_type, checkpoint_blob, metadata_type, metadata_blob) "
                        "VALUES (:thread_id, :namespace, :checkpoint_id, :parent_id, :checkpoint_type, :checkpoint_blob, :metadata_type, :metadata_blob)"
                    ), values,
                )
            else:
                connection.execute(
                    text(
                        "UPDATE langgraph_checkpoints SET parent_checkpoint_id=:parent_id, "
                        "checkpoint_type=:checkpoint_type, checkpoint_blob=:checkpoint_blob, "
                        "metadata_type=:metadata_type, metadata_blob=:metadata_blob "
                        "WHERE thread_id=:thread_id AND checkpoint_ns=:namespace "
                        "AND checkpoint_id=:checkpoint_id"
                    ), values,
                )
        return self._config(thread_id, namespace, checkpoint["id"])

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        namespace = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = str(configurable["checkpoint_id"])
        with self.engine.begin() as connection:
            for sequence_index, (channel, value) in enumerate(writes):
                write_index = WRITES_IDX_MAP.get(channel, sequence_index)
                exists = connection.execute(
                    text(
                        "SELECT 1 FROM langgraph_checkpoint_writes WHERE thread_id=:thread_id "
                        "AND checkpoint_ns=:namespace AND checkpoint_id=:checkpoint_id "
                        "AND task_id=:task_id AND write_index=:write_index"
                    ),
                    {"thread_id": thread_id, "namespace": namespace, "checkpoint_id": checkpoint_id, "task_id": task_id, "write_index": write_index},
                ).first()
                if exists is not None and write_index >= 0:
                    continue
                value_type, value_blob = self.serde.dumps_typed(value)
                params = {
                    "thread_id": thread_id, "namespace": namespace,
                    "checkpoint_id": checkpoint_id, "task_id": task_id,
                    "write_index": write_index, "channel": channel,
                    "value_type": value_type, "value_blob": value_blob,
                    "task_path": task_path,
                }
                if exists is None:
                    connection.execute(
                        text(
                            "INSERT INTO langgraph_checkpoint_writes "
                            "(thread_id, checkpoint_ns, checkpoint_id, task_id, write_index, channel_name, value_type, value_blob, task_path) "
                            "VALUES (:thread_id, :namespace, :checkpoint_id, :task_id, :write_index, :channel, :value_type, :value_blob, :task_path)"
                        ), params,
                    )
                else:
                    connection.execute(
                        text(
                            "UPDATE langgraph_checkpoint_writes SET channel_name=:channel, "
                            "value_type=:value_type, value_blob=:value_blob, task_path=:task_path "
                            "WHERE thread_id=:thread_id AND checkpoint_ns=:namespace "
                            "AND checkpoint_id=:checkpoint_id AND task_id=:task_id "
                            "AND write_index=:write_index"
                        ), params,
                    )

    def delete_thread(self, thread_id: str) -> None:
        with self.engine.begin() as connection:
            for table in (
                "langgraph_checkpoint_writes",
                "langgraph_checkpoint_blobs",
                "langgraph_checkpoints",
            ):
                connection.execute(
                    text(f"DELETE FROM {table} WHERE thread_id=:thread_id"),
                    {"thread_id": thread_id},
                )

    def delete_for_runs(self, run_ids: Sequence[str]) -> None:
        # Run ids are stored in checkpoint metadata rather than indexed columns.
        # LangGraph does not use this method in the application execution path.
        return None

    def copy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        raise NotImplementedError("thread copying is not part of the application contract")

    def prune(self, thread_ids: Sequence[str], *, strategy: str = "keep_latest") -> None:
        if strategy == "delete":
            for thread_id in thread_ids:
                self.delete_thread(thread_id)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        items = await asyncio.to_thread(
            lambda: list(self.list(config, filter=filter, before=before, limit=limit))
        )
        for item in items:
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self.delete_thread, thread_id)

    async def adelete_for_runs(self, run_ids: Sequence[str]) -> None:
        await asyncio.to_thread(self.delete_for_runs, run_ids)

    async def acopy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        await asyncio.to_thread(self.copy_thread, source_thread_id, target_thread_id)

    async def aprune(self, thread_ids: Sequence[str], *, strategy: str = "keep_latest") -> None:
        await asyncio.to_thread(self.prune, thread_ids, strategy=strategy)

    def get_next_version(self, current: str | None, channel: None) -> str:
        if current is None:
            current_value = 0
        elif isinstance(current, int):
            current_value = current
        else:
            current_value = int(str(current).split(".", 1)[0])
        return f"{current_value + 1:032}.0000000000000000"
