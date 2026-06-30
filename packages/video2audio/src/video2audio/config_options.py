from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


class ConfigOptionError(Exception):
    pass


def load_toml_config(config_path: Path) -> Mapping[str, object]:
    try:
        with config_path.open("rb") as config_file:
            return tomllib.load(config_file)
    except FileNotFoundError as exc:
        message = f"Config file does not exist: {config_path}"
        raise ConfigOptionError(message) from exc
    except tomllib.TOMLDecodeError as exc:
        message = f"Invalid TOML in config file {config_path}: {exc}"
        raise ConfigOptionError(message) from exc
