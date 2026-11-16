"""Append-only JSONL experiment log for local runs."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .contracts import MiningTask, Trial


class JsonlRecorder:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def write_trial(self, run_id: str, task: MiningTask, trial: Trial) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "schema_version": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "task": asdict(task),
            "trial": asdict(trial),
            "fingerprint": trial.candidate.fingerprint,
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
