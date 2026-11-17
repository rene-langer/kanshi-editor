"""Tiny persistent preferences in $XDG_STATE_HOME/kanshi-editor/settings.json."""

from __future__ import annotations

import json
import os

from .monitors import state_path

DEFAULTS = {"show_thumbnails": True}


def _path():
    return state_path().with_name("settings.json")


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        stored = json.loads(_path().read_text())
        if isinstance(stored, dict):
            data.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return data


def save(data: dict) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)
    except OSError:
        pass
