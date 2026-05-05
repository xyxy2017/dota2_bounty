from __future__ import annotations

from app.bootstrap import build_supervisor
from infra.http_server import run_server


def main() -> None:
    supervisor = build_supervisor()
    run_server(
        supervisor=supervisor,
        host=supervisor.settings.server_host,
        port=supervisor.settings.server_port,
    )


if __name__ == "__main__":
    main()
