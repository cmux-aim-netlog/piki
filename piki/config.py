import json
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".config" / "piki"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS: dict[str, Any] = {}


def load() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return dict(DEFAULTS)
    with CONFIG_FILE.open() as f:
        return json.load(f)


def save(data: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_FILE.open("w") as f:
        json.dump(data, f, indent=2)


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)


def set_(key: str, value: Any) -> None:
    data = load()
    data[key] = value
    save(data)


def delete(key: str) -> bool:
    data = load()
    if key not in data:
        return False
    del data[key]
    save(data)
    return True


def reset() -> None:
    save(dict(DEFAULTS))
