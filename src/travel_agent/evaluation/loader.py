from __future__ import annotations

import json
from pathlib import Path

from travel_agent.evaluation.models import ReleaseManifest


def load_manifest(path: Path) -> ReleaseManifest:
    return ReleaseManifest.model_validate_json(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
