"""Минимальный ручной стенд для Huawei VRP parser.

Запуск из корня проекта без установки пакета:

    python3 manual_test.py

Для эксперимента измените PATTERN_DOCUMENT и строку внутри main(). Полный
рабочий набор проекта хранится только в data/commands.json.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Позволяет запускать файл напрямую без `pip install -e .`.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from vrp_parser import CommandLineParser, ConfigurationParser  # noqa: E402

PATTERN_DOCUMENT = {
    "commands": [
        "#",
        "TEXT<1-4096>",
        "description TEXT<1-4096>",
        "interface STRING<1-63>",
        "mode ENUM{fast,safe} [ optional ]",
        "interface { STRING<1-63> | ENUM{Vbdif,Vlanif} STRING<1-63> }",
        "preference INTEGER<1-15>",
        "show { up | down }",
        "show up",
        "value INTEGER<1-10>",
        "value HEX<1-A>",
        "features { alpha | beta | gamma } *",
        "access-operation { { create | read } * | * }",
    ]
}

config = """
interface Eth-Trunk1
    description eth-trunk descritpion
    show up
"""


def main() -> None:
    parser = CommandLineParser(PATTERN_DOCUMENT)
    configuration_parser = ConfigurationParser(parser)
    result = configuration_parser.parse("!interface Vlanif100")
    print(result)


if __name__ == "__main__":
    main()
