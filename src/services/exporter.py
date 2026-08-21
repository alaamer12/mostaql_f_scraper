"""Format-aware exporting of domain objects, decoupled from raw file I/O."""

import logging
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable, List, Optional, Sequence, Union
from pathlib import Path

from .storage import StorageService
from ..utils.validators import StrictZeroNullValidator

log = logging.getLogger(__name__)


class ExporterService:
    """Exports lists of dataclass records (or dicts) to JSON and/or CSV.

    Every phase (Discovery, Extraction, Fetch, Parse) can independently
    export its own results without depending on the others, keeping the
    export format concern (JSON/CSV) separate from raw file persistence
    (handled by StorageService) and from data acquisition (network/parser).
    """

    def __init__(self, storage: Optional[StorageService] = None) -> None:
        self.storage = storage or StorageService()

    def _to_records(self, items: Sequence[Any], flat: bool = False) -> List[dict]:
        records = []
        for item in items:
            if flat and hasattr(item, "to_flat_dict"):
                rec = item.to_flat_dict()
            elif hasattr(item, "to_dict"):
                rec = item.to_dict()
            elif hasattr(item, "model_dump"):
                rec = item.model_dump()
            elif is_dataclass(item):
                rec = asdict(item)
            else:
                rec = dict(item)
            StrictZeroNullValidator.validate_record_dict(rec)
            records.append(rec)
        return records

    def export(
        self,
        items: Sequence[Any],
        *,
        json_path: Union[str, Path, None] = None,
        csv_path: Union[str, Path, None] = None,
    ) -> None:
        """Export records to JSON and/or CSV, whichever path is provided."""
        if json_path:
            json_records = self._to_records(items, flat=False)
            self.storage.save_json(json_records, json_path)
            log.info(f"Exported {len(json_records)} records -> {json_path}")

        if csv_path:
            csv_records = self._to_records(items, flat=True)
            self.storage.save_csv(csv_records, csv_path)
            log.info(f"Exported {len(csv_records)} records -> {csv_path}")

    def export_json(self, items: Sequence[Any], json_path: Union[str, Path]) -> None:
        self.export(items, json_path=json_path)

    def export_csv(self, items: Sequence[Any], csv_path: Union[str, Path]) -> None:
        self.export(items, csv_path=csv_path)
