#!/usr/bin/env python3
"""Download the minimal verified BabelDOC assets with resumable curl calls."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
LOCAL_HOME = HERE / ".home-next"
MINIMAL_FONTS = [
    "LXGWWenKaiGB-Regular.1.520.ttf",
    "SourceHanSerifCN-Bold.ttf",
    "SourceHanSerifCN-Regular.ttf",
    "SourceHanSansCN-Bold.ttf",
    "SourceHanSansCN-Regular.ttf",
    "GoNotoKurrent-Regular.ttf",
    "GoNotoKurrent-Bold.ttf",
]


def configure_environment() -> None:
    os.environ["HOME"] = str(LOCAL_HOME)
    os.environ["USERPROFILE"] = str(LOCAL_HOME)
    os.environ["HF_HOME"] = str(LOCAL_HOME / "huggingface")
    os.environ["TIKTOKEN_CACHE_DIR"] = str(
        LOCAL_HOME / ".cache" / "babeldoc" / "tiktoken"
    )
    os.environ["PYTHONIOENCODING"] = "utf-8"
    for path in (
        LOCAL_HOME / ".cache" / "babeldoc" / "fonts",
        LOCAL_HOME / ".cache" / "babeldoc" / "models",
        Path(os.environ["TIKTOKEN_CACHE_DIR"]),
    ):
        path.mkdir(parents=True, exist_ok=True)


def download_verified(url: str, destination: Path, sha3_256: str) -> None:
    from babeldoc.assets.assets import verify_file

    if verify_file(destination, sha3_256):
        print(f"[OK] {destination.name}")
        return

    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl was not found; Windows 10/11 normally includes curl.exe")

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    print(f"[GET] {destination.name}")
    command = [
        curl,
        "-L",
        "--fail",
        "--retry",
        "8",
        "--retry-all-errors",
        "--connect-timeout",
        "20",
        "--continue-at",
        "-",
        "--output",
        str(partial),
        url,
    ]
    completed = subprocess.run(command)
    if completed.returncode != 0:
        raise RuntimeError(f"download failed ({completed.returncode}): {destination.name}")
    if not verify_file(partial, sha3_256):
        raise RuntimeError(f"checksum failed: {destination.name}")
    partial.replace(destination)
    print(f"[OK] {destination.name}")


def main() -> int:
    configure_environment()
    from babeldoc.assets.embedding_assets_metadata import (
        DOC_LAYOUT_ONNX_MODEL_URL,
        DOCLAYOUT_YOLO_DOCSTRUCTBENCH_IMGSZ1024ONNX_SHA3_256,
        EMBEDDING_FONT_METADATA,
        FONT_URL_BY_UPSTREAM,
    )
    from babeldoc.const import get_cache_file_path

    model_name = "doclayout_yolo_docstructbench_imgsz1024.onnx"
    download_verified(
        DOC_LAYOUT_ONNX_MODEL_URL["hf-mirror"],
        get_cache_file_path(model_name, "models"),
        DOCLAYOUT_YOLO_DOCSTRUCTBENCH_IMGSZ1024ONNX_SHA3_256,
    )
    for name in MINIMAL_FONTS:
        download_verified(
            FONT_URL_BY_UPSTREAM["hf-mirror"](name),
            get_cache_file_path(name, "fonts"),
            EMBEDDING_FONT_METADATA[name]["sha3_256"],
        )

    # BabelDOC uses this tokenizer only for local request sizing and caching.
    from tiktoken import encoding_for_model

    encoding_for_model("gpt-4o")
    print("[OK] minimal BabelDOC assets are ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
