"""Format-aware exporting of domain objects, decoupled from raw file I/O."""

import logging
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable, List, Optional, Sequence, Union
from pathlib import Path

from .storage import StorageService

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

    def _to_records(self, items: Sequence[Any]) -> List[dict]:
        return [asdict(item) if is_dataclass(item) else dict(item) for item in items]

    def export(
        self,
        items: Sequence[Any],
        *,
        json_path: Union[str, Path, None] = None,
        csv_path: Union[str, Path, None] = None,
    ) -> None:
        """Export records to JSON and/or CSV, whichever path is provided."""
        records = self._to_records(items)

        if json_path:
            self.storage.save_json(records, json_path)
            log.info(f"Exported {len(records)} records -> {json_path}")

        if csv_path:
            self.storage.save_csv(records, csv_path)
            log.info(f"Exported {len(records)} records -> {csv_path}")

    def export_json(self, items: Sequence[Any], json_path: Union[str, Path]) -> None:
        self.export(items, json_path=json_path)

    def export_csv(self, items: Sequence[Any], csv_path: Union[str, Path]) -> None:
        self.export(items, csv_path=csv_path)
