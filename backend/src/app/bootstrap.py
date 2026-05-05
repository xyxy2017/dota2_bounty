from __future__ import annotations

from app.supervisor import Supervisor
from config.settings import Settings


def build_supervisor() -> Supervisor:
    settings = Settings()
    return Supervisor(settings=settings)
