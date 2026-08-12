import asyncio
import csv
import orjson
from pathlib import Path
from typing import List, Dict, Any, Union, Optional
from dataclasses import asdict
from datetime import datetime
import pandas as pd

from ..models import Freelancer, ProfileDetails

class StorageService:
    """Handles data persistence, checkpointing, and format conversion."""

    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}

    def _lock_for(self, path: Union[str, Path]) -> asyncio.Lock:
        """Return the write lock guarding a single file path.

        Concurrent pipeline stages may append to different checkpoints at
        the same time; one lock per path keeps their lines from interleaving
        without serialising unrelated files.
        """
        key = str(Path(path))
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def asave_json(self, data: Any, path: Union[str, Path]) -> None:
        """Concurrency-safe variant of :meth:`save_json`."""
        async with self._lock_for(path):
            self.save_json(data, path)

    async def asave_jsonl(self, records: List[Dict[str, Any]], path: Union[str, Path], append: bool = True) -> None:
        """Concurrency-safe variant of :meth:`save_jsonl`."""
        async with self._lock_for(path):
            self.save_jsonl(records, path, append=append)

    def save_json(self, data: Any, path: Union[str, Path]) -> None:
        """Save data to a JSON file using orjson for performance."""
        path = Path(path)
        # orjson.dumps returns bytes. we use OPT_INDENT_2 to match previous behavior
        # but OPT_NON_STR_KEYS is also useful if we ever have non-string keys
        # ensure_ascii=False is default in orjson
        content = orjson.dumps(data, option=orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS)
        path.write_bytes(content)

    def load_json(self, path: Union[str, Path]) -> Any:
        """Load data from a JSON file using orjson."""
        path = Path(path)
        if not path.exists():
            return None
        return orjson.loads(path.read_bytes())

    def save_jsonl(self, records: List[Dict[str, Any]], path: Union[str, Path], append: bool = True) -> None:
        """Save/Append records to a JSONL file (good for checkpoints)."""
        path = Path(path)
        mode = "ab" if append else "wb"
        with path.open(mode) as f:
            for rec in records:
                f.write(orjson.dumps(rec) + b"\n")

    def load_jsonl(self, path: Union[str, Path]) -> List[Dict[str, Any]]:
        """Load records from a JSONL file."""
        path = Path(path)
        if not path.exists():
            return []
        records = []
        with path.open("rb") as f:
            for line in f:
                if line.strip():
                    records.append(orjson.loads(line))
        return records

    def save_csv(self, records: List[Dict[str, Any]], path: Union[str, Path]) -> None:
        """Save records to a CSV file."""
        if not records:
            return
        path = Path(path)
        
        # Flatten nested dicts/lists for CSV
        flattened = []
        for rec in records:
            row = dict(rec)
            for k, v in row.items():
                if isinstance(v, (list, dict)):
                    row[k] = orjson.dumps(v).decode("utf-8")
            flattened.append(row)

        df = pd.DataFrame(flattened)
        df.to_csv(path, index=False, encoding="utf-8-sig")
