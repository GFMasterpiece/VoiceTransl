import json, os
from uuid import uuid4
from pathlib import Path

def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A process can save the same state file from a runner and a cancel
    # request at nearly the same time.  A fixed temporary filename would let
    # those writers clobber each other's partial JSON before os.replace().
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
            f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)
    except Exception:
        if temp.exists(): temp.unlink()
        raise
