"""
server/recorder.py
==================
Production Match Trajectory Recorder for Blak Game Server.
Captures online and local matches, tagging whether opponents defeated
were humans, dumbots, or mixed, with source="production".
"""

from __future__ import annotations
import os
from typing import Optional

from sandbox.recorder import GameRecorder as BaseRecorder


class ProductionGameRecorder(BaseRecorder):
    """
    Subclasses GameRecorder, setting source="production" by default.
    """

    def __init__(
        self,
        match_id: Optional[str] = None,
        output_file: str = "data/training_data/matches.jsonl",
    ) -> None:
        super().__init__(
            match_id=match_id,
            output_file=output_file,
            source="production",
        )


def get_dataset_size(
    filepath: str = "data/training_data/matches.jsonl",
    target_training_samples: int = 50_000,
):
    from sandbox.dataset import get_dataset_size as _getter  # Deferred import: circular dependency
    return _getter(filepath, target_training_samples)
