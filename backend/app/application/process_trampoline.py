"""Wait for explicit parent approval, then run a command in this process tree."""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    gate = sys.stdin.buffer.readline(16)
    if gate != b"GO" + bytes([10]):
        return 125
    command = sys.argv[1:]
    if not command:
        return 126
    try:
        # Windows' os.execv does not preserve argv quoting for embedded Python
        # scripts. Popen/subprocess uses the platform argv rules and keeps all
        # arguments, including spaces and non-ASCII paths, verbatim.
        result = subprocess.run(command, stdin=subprocess.DEVNULL, check=False)
    except OSError:
        return 127
    return 0 if result.returncode is None else result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
