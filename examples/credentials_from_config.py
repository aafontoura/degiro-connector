"""Example-only credential loader.

The hardened fork removed `build_credentials` from the package so no supported
code path reads credentials from the environment or a file. The examples still
need a way to load a local config, so the original helper lives here.
"""

import os
from pathlib import Path

from orjson import loads

from degiro_connector.trading.models.credentials import Credentials


def build_credentials(location: str = "", override: dict | None = None) -> Credentials:
    if not location and not override:
        raise AttributeError("You need to provide at least on argument.")

    if location:
        location_path = Path(location)
    else:
        location_path = None

    env_content = os.environ.get("DEGIRO_ACCOUNT")

    if env_content:
        config = loads(env_content)
    elif location_path and location_path.exists():
        config = loads(location_path.read_text())
    else:
        config = {}

    if override:
        config.update(override)

    credentials = Credentials.model_validate(obj=config)

    return credentials
