from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any


class VideoSegmentIndex:
    """Disk-backed knowledge-point lookup for published video segments."""

    SCHEMA_VERSION = "1"

    def __init__(self, source_root: Path, index_path: Path) -> None:
        self.source_root = source_root
        self.index_path = index_path
        self._lock = threading.RLock()
        # A release directory is immutable after deployment. Validate its disk
        # index once when this process first uses it, not once per matched KP.
        self._ready = False

    def videos_for_kp(self, kp_id: str) -> list[dict[str, Any]]:
        normalized = str(kp_id or "").strip()
        if not normalized:
            return []
        self._ensure_current()
        with sqlite3.connect(self.index_path) as connection:
            rows = connection.execute(
                """
                SELECT s.bvid, s.aid, s.cid, s.page, s.video_title,
                       s.part_title, s.start_seconds, s.end_seconds,
                       s.topic, s.transcript, k.match_json
                FROM segment_kps AS k
                JOIN segments AS s ON s.segment_id = k.segment_id
                WHERE k.kp_id = ?
                ORDER BY s.bvid, s.page, s.start_seconds, s.end_seconds
                """,
                (normalized,),
            ).fetchall()
        return [
            {
                "bvid": row[0],
                "aid": row[1],
                "cid": row[2],
                "page": row[3],
                "video_title": row[4],
                "part_title": row[5],
                "start_seconds": row[6],
                "end_seconds": row[7],
                "topic": row[8],
                "transcript": row[9],
                "match": json.loads(row[10]),
            }
            for row in rows
        ]

    def _result_paths(self) -> list[Path]:
        return sorted(self.source_root.glob("BV*/classification_result.json"))

    @staticmethod
    def _source_signature(paths: list[Path]) -> str:
        digest = hashlib.sha256()
        for path in paths:
            stat = path.stat()
            digest.update(path.parent.name.encode("utf-8"))
            digest.update(str(stat.st_size).encode("ascii"))
            digest.update(str(stat.st_mtime_ns).encode("ascii"))
        return digest.hexdigest()

    def _ensure_current(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            paths = self._result_paths()
            signature = self._source_signature(paths)
            if self._matches(signature):
                self._ready = True
                return
            self._build(paths, signature)
            self._ready = True

    def _matches(self, signature: str) -> bool:
        if not self.index_path.is_file():
            return False
        try:
            with sqlite3.connect(self.index_path) as connection:
                values = dict(connection.execute("SELECT key, value FROM metadata"))
        except (sqlite3.Error, OSError):
            return False
        return (
            values.get("schema_version") == self.SCHEMA_VERSION
            and values.get("source_signature") == signature
        )

    def _build(self, paths: list[Path], signature: str) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.index_path.with_suffix(self.index_path.suffix + ".tmp")
        temporary.unlink(missing_ok=True)
        try:
            with sqlite3.connect(temporary) as connection:
                connection.executescript(
                    """
                    PRAGMA journal_mode=OFF;
                    PRAGMA synchronous=OFF;
                    CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE segments (
                        segment_id INTEGER PRIMARY KEY,
                        bvid TEXT NOT NULL,
                        aid INTEGER,
                        cid INTEGER,
                        page INTEGER NOT NULL,
                        video_title TEXT NOT NULL,
                        part_title TEXT NOT NULL,
                        start_seconds REAL NOT NULL,
                        end_seconds REAL NOT NULL,
                        topic TEXT NOT NULL,
                        transcript TEXT NOT NULL,
                        UNIQUE (bvid, page, start_seconds, end_seconds)
                    );
                    CREATE TABLE segment_kps (
                        kp_id TEXT NOT NULL,
                        segment_id INTEGER NOT NULL,
                        match_json TEXT NOT NULL,
                        PRIMARY KEY (kp_id, segment_id),
                        FOREIGN KEY (segment_id) REFERENCES segments(segment_id)
                    );
                    CREATE INDEX idx_segment_kps_kp ON segment_kps(kp_id);
                    """
                )
                for path in paths:
                    self._index_result(connection, path)
                connection.executemany(
                    "INSERT INTO metadata(key, value) VALUES (?, ?)",
                    (
                        ("schema_version", self.SCHEMA_VERSION),
                        ("source_signature", signature),
                    ),
                )
                connection.commit()
            os.replace(temporary, self.index_path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _index_result(connection: sqlite3.Connection, path: Path) -> None:
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(result, dict):
            return
        bvid = str(result.get("bvid") or path.parent.name)
        for page in result.get("pages") or []:
            if not isinstance(page, dict):
                continue
            for segment in page.get("segments") or []:
                if not isinstance(segment, dict):
                    continue
                matches = [
                    match
                    for match in segment.get("kp_matches") or []
                    if isinstance(match, dict) and str(match.get("kp_id") or "").strip()
                ]
                if not matches:
                    continue
                values = (
                    bvid,
                    result.get("aid"),
                    page.get("cid"),
                    int(page.get("page") or 0),
                    str(result.get("video_title") or ""),
                    str(page.get("original_part_title") or ""),
                    float(segment.get("start_seconds") or 0),
                    float(segment.get("end_seconds") or 0),
                    str(segment.get("topic") or "知识讲解"),
                    str(segment.get("transcript") or ""),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO segments(
                        bvid, aid, cid, page, video_title, part_title,
                        start_seconds, end_seconds, topic, transcript
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                segment_id = connection.execute(
                    """
                    SELECT segment_id FROM segments
                    WHERE bvid=? AND page=? AND start_seconds=? AND end_seconds=?
                    """,
                    (values[0], values[3], values[6], values[7]),
                ).fetchone()[0]
                connection.executemany(
                    "INSERT OR REPLACE INTO segment_kps(kp_id, segment_id, match_json) VALUES (?, ?, ?)",
                    (
                        (
                            str(match["kp_id"]),
                            segment_id,
                            json.dumps(match, ensure_ascii=False, separators=(",", ":")),
                        )
                        for match in matches
                    ),
                )
