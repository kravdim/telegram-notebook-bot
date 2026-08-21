#!/usr/bin/env python3
"""Download the configured local Whisper model during installation."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config import settings


def main() -> None:
    stt_cfg = settings.yaml_config.get("stt", {})
    if stt_cfg.get("provider", "local_whisper") != "local_whisper":
        print("Local Whisper is disabled; model prefetch skipped")
        return

    from faster_whisper import WhisperModel

    model_size = stt_cfg.get("model", "medium")
    download_root = stt_cfg.get("download_root") or os.environ.get(
        "DAILYPLANNER_STT_CACHE"
    )
    WhisperModel(
        model_size,
        device="cpu",
        compute_type="int8",
        local_files_only=False,
        download_root=download_root,
    )
    print(f"Whisper model is cached: {model_size}")


if __name__ == "__main__":
    main()
