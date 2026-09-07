from pathlib import Path


def update_env_file(path: Path, values: dict[str, str]) -> bool:
    """Точечно обновляет (или добавляет) переменные `KEY=value` в существующем .env-файле,
    не трогая остальные строки и их порядок. Возвращает False, если файла нет (ничего не
    создаёт — .env должен быть заведён заранее из .env.example)."""

    if not path.exists():
        return False

    lines = path.read_text(encoding="utf-8").splitlines()
    remaining = dict(values)
    new_lines = []

    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else None
        if key in remaining:
            new_lines.append(f"{key}={remaining.pop(key)}")
        else:
            new_lines.append(line)

    for key, value in remaining.items():
        new_lines.append(f"{key}={value}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return True
