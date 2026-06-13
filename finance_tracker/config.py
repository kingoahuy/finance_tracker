import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class EnvFileValidationError(ValueError):
    def __init__(self, message, duplicate_keys=None):
        super().__init__(message)
        self.duplicate_keys = tuple(duplicate_keys or ())


def load_env_file(path=None, override=False):
    env_path = Path(path or PROJECT_ROOT / ".env")
    if not env_path.exists():
        return {}

    values = read_env_file(env_path)
    for key, value in values.items():
        if override or not os.environ.get(key):
            os.environ[key] = value
    return values


def read_env_file(path=None):
    env_path = Path(path or PROJECT_ROOT / ".env")
    values = {}
    key_lines = {}
    if not env_path.exists():
        return values

    for line_number, line in enumerate(
        env_path.read_text(encoding="utf-8-sig").splitlines(),
        start=1,
    ):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        key_lines.setdefault(key, []).append(line_number)
        value = value.strip().strip('"').strip("'")
        values[key] = value

    duplicates = {
        key: line_numbers
        for key, line_numbers in key_lines.items()
        if len(line_numbers) > 1
    }
    if duplicates:
        details = ", ".join(
            f"{key} (lines {','.join(map(str, line_numbers))})"
            for key, line_numbers in sorted(duplicates.items())
        )
        raise EnvFileValidationError(
            f"Duplicate environment variables in {env_path.name}: {details}",
            duplicate_keys=duplicates,
        )
    return values


def save_env_values(values, path=None):
    env_path = Path(path or PROJECT_ROOT / ".env")
    current = read_env_file(env_path)
    current.update({key: str(value) for key, value in values.items() if value is not None})

    lines = [f"{key}={value}" for key, value in current.items()]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    for key, value in current.items():
        os.environ[key] = value
