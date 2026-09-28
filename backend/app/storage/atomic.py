import json, os
from pathlib import Path

def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
            f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)
    except Exception:
        if temp.exists(): temp.unlink()
        raise
