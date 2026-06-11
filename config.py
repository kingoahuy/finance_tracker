import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_env_file(path=None, override=False):
    env_path = Path(path or PROJECT_ROOT / ".env")
    if not env_path.exists():
        return

    for key, value in read_env_file(env_path).items():
        if override or key not in os.environ:
            os.environ[key] = value


def read_env_file(path=None):
    env_path = Path(path or PROJECT_ROOT / ".env")
    values = {}
    if not env_path.exists():
        return values

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
    return values


def save_env_values(values, path=None):
    env_path = Path(path or PROJECT_ROOT / ".env")
    current = read_env_file(env_path)
    current.update({key: str(value) for key, value in values.items() if value is not None})

    lines = [f"{key}={value}" for key, value in current.items()]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    for key, value in current.items():
        os.environ[key] = value
