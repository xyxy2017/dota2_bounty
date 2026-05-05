from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m scripts.replay_payload <payload.json>")
    payload_path = sys.argv[1]
    payload = json.loads(open(payload_path, "r", encoding="utf-8").read())
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        "http://127.0.0.1:8000/gsi",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=10) as response:
        print(response.status)
        print(response.read().decode("utf-8"))


if __name__ == "__main__":
    main()
