"""Configuration helpers for the Wodify Hermes integration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel


CONFIG_PATH = Path("~/.hermes/wodify/config.json")


class WodifyConfig(BaseModel):
    """Typed configuration values loaded from disk and environment."""

    base_url: str | None = None
    email: str | None = None
    password: str | None = None
    token: str | None = None
    tenant_id: str | None = None
    program_id: int | None = None

    class Config:
        extra = "allow"


ENVIRONMENT_KEYS = {
    "base_url": ("WODIFY_BASE_URL", "HERMES_WODIFY_BASE_URL"),
    "email": ("WODIFY_EMAIL", "HERMES_WODIFY_EMAIL"),
    "password": ("WODIFY_PASSWORD", "HERMES_WODIFY_PASSWORD"),
    "token": ("WODIFY_TOKEN", "HERMES_WODIFY_TOKEN"),
    "tenant_id": ("WODIFY_TENANT_ID", "HERMES_WODIFY_TENANT_ID"),
    "program_id": ("WODIFY_PROGRAM_ID", "HERMES_WODIFY_PROGRAM_ID"),
}


def _config_path() -> Path:
    return CONFIG_PATH.expanduser()


def _ensure_config_file() -> Path:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("{}\n", encoding="utf-8")
    return path


def _read_config_file() -> dict[str, Any]:
    path = _ensure_config_file()
    with path.open("r", encoding="utf-8") as config_file:
        data = json.load(config_file)
    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a JSON object: {path}")
    return data


def _write_config_file(data: dict[str, Any]) -> None:
    path = _ensure_config_file()
    with path.open("w", encoding="utf-8") as config_file:
        json.dump(data, config_file, indent=2, sort_keys=True)
        config_file.write("\n")


def _merge_environment(data: dict[str, Any]) -> dict[str, Any]:
    merged = dict(data)
    for key, environment_names in ENVIRONMENT_KEYS.items():
        if key in merged and merged[key] is not None:
            continue
        for environment_name in environment_names:
            value = os.environ.get(environment_name)
            if value is not None:
                merged[key] = value
                break
    return merged


def load_config() -> WodifyConfig:
    """Load config from disk and fill missing keys from environment variables."""

    return WodifyConfig(**_merge_environment(_read_config_file()))


def save_config(updates: dict[str, Any]) -> WodifyConfig:
    """Merge updates into the config file while preserving existing keys."""

    if not isinstance(updates, dict):
        raise TypeError("updates must be a dict")

    data = _read_config_file()
    data.update(updates)
    WodifyConfig(**data)
    _write_config_file(data)
    return load_config()
