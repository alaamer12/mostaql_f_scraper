import json
import csv
from pathlib import Path
from typing import List, Dict, Any, Union, Optional
from dataclasses import asdict
from datetime import datetime
import pandas as pd

from ..models import Freelancer, ProfileDetails

class StorageService:
    """Handles data persistence, checkpointing, and format conversion."""

    def __init__(self):
        pass

    def save_json(self, data: Any, path: Union[str, Path]) -> None:
        """Save data to a JSON file."""
        path = Path(path)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_json(self, path: Union[str, Path]) -> Any:
        """Load data from a JSON file."""
        path = Path(path)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def save_jsonl(self, records: List[Dict[str, Any]], path: Union[str, Path], append: bool = True) -> None:
        """Save/Append records to a JSONL file (good for checkpoints)."""
        path = Path(path)
        mode = "a" if append else "w"
        with path.open(mode, encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def load_jsonl(self, path: Union[str, Path]) -> List[Dict[str, Any]]:
        """Load records from a JSONL file."""
        path = Path(path)
        if not path.exists():
            return []
        records = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line))
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
                    row[k] = json.dumps(v, ensure_ascii=False)
            flattened.append(row)

        df = pd.DataFrame(flattened)
        df.to_csv(path, index=False, encoding="utf-8-sig")
