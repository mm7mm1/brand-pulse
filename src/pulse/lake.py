"""Parquet lake on a local path or gs:// - one file per (dataset, brand, month) slice."""

from __future__ import annotations

import os
import uuid
from datetime import date

import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq

PAGEVIEWS_SCHEMA = pa.schema(
    [
        ("brand_id", pa.string()),
        ("wiki_project", pa.string()),
        ("articles", pa.string()),
        ("date", pa.date32()),
        ("views", pa.int64()),
        ("_loaded_at", pa.timestamp("us", tz="UTC")),
        ("_run_id", pa.string()),
    ]
)


class Lake:
    def __init__(self, uri: str):
        self.fs, self.root = pafs.FileSystem.from_uri(uri if "://" in uri else os.path.abspath(uri))
        self.is_local = isinstance(self.fs, pafs.LocalFileSystem)

    def slice_path(self, dataset: str, month: date, brand_id: str) -> str:
        # Hive-style month partition: BigQuery external tables and DuckDB both prune on it
        return f"{self.root}/{dataset}/month={month:%Y-%m}/{brand_id}.parquet"

    def replace_slice(self, path: str, table: pa.Table) -> None:
        """Replace a slice so that a reader sees the old file or the new one, never half.

        GCS publishes an object only once its upload completes, so it is written in place.
        A local file is written next to the live one and renamed over it; a crash mid-write
        leaves only a dot-file that the `*.parquet` glob does not match.
        """
        if not self.is_local:
            pq.write_table(table, path, filesystem=self.fs)
            return
        parent, name = path.rsplit("/", 1)
        self.fs.create_dir(parent, recursive=True)
        tmp = f"{parent}/.{name}.{uuid.uuid4().hex}.tmp"
        pq.write_table(table, tmp, filesystem=self.fs)
        self.fs.move(tmp, path)

    def read_slice(self, path: str) -> pa.Table:
        return pq.read_table(path, filesystem=self.fs)

    def exists(self, path: str) -> bool:
        return self.fs.get_file_info(path).type == pafs.FileType.File
