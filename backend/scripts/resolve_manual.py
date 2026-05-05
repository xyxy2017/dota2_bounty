from __future__ import annotations

import json
import sys
from urllib import request


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python scripts/resolve_manual.py <resolved-match.json>")
    payload = json.loads(open(sys.argv[1], "r", encoding="utf-8").read())
    body = json.dumps({"manual_result": payload}).encode("utf-8")
    req = request.Request(
        "http://127.0.0.1:8000/resolve/now",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=10) as response:
        print(response.status)
        print(response.read().decode("utf-8"))


if __name__ == "__main__":
    main()
