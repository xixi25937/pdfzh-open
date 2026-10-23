"""Configure only BabelDOC's cache, without replacing the user's home/profile."""
import os
from pathlib import Path


def configure_layout_cache():
    import babeldoc.const as const
    base=Path(os.environ.get('LOCALAPPDATA') or Path.home()/'.cache')/'PDFZH'/'models'
    legacy=Path(__file__).parent/'.home-next'/'.cache'/'babeldoc'
    # Developer installation can reuse existing local assets. This directory is
    # not included in source releases. User HOME and USERPROFILE stay unchanged.
    const.CACHE_FOLDER=legacy if legacy.is_dir() else base
    const.CACHE_FOLDER.mkdir(parents=True,exist_ok=True)
