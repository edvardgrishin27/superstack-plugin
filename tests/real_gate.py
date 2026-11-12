#!/usr/bin/env python3
"""Настоящий проект и настоящий прогон гейта — для тестов, которым нужно «доказано».

Зачем это отдельный модуль. Слово «доказано» теперь ставит только запись,
которую пишет сам гейт после прогона. Подложить её руками значило бы
проверять заготовку, а не цепочку: ровно так «проверено машиной» и держалось
месяцами на одной подкладке, хотя писать файл было некому. Поэтому здесь
строится маленький git-проект с одним тестом, и гейт гоняется по-настоящему.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG  # noqa: E402

VERIFY = PKG / "tools" / "verify.py"

GREEN = "def test_ok():\n    assert True\n"
RED = "def test_broken():\n    assert False\n"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), check=True,
                   capture_output=True, timeout=60)


def project(root: Path, body: str = GREEN) -> Path:
    """Git-проект с одним тестом и папкой `.superstack`; первый снимок сделан.

    `.superstack` заводится ПОСЛЕ снимка и остаётся неотслеживаемой — так она
    и лежит у человека в свежем проекте. `tests/conftest.py` нужен ступени
    автономии: по нему она узнаёт, что команда тестов в проекте есть.
    """
    root = Path(root).resolve()
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "conftest.py").write_text("", encoding="utf-8")
    (root / "tests" / "test_one.py").write_text(body, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "первый снимок")
    (root / ".superstack").mkdir(exist_ok=True)
    return root


def gate_env(state: Path) -> dict:
    """Окружение прогона: счётчик попыток — во временном файле теста."""
    return {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1",
            "SUPERSTACK_VERIFY_STATE": str(state)}


def run_gate(root: Path, state: Path) -> subprocess.CompletedProcess:
    """Прогнать гейт по-настоящему и вернуть его ответ."""
    return subprocess.run([sys.executable, str(VERIFY), "--json", str(root)],
                          capture_output=True, text=True, timeout=300,
                          env=gate_env(state))


def record(root: Path) -> "dict | None":
    """Что гейт записал о последнем прогоне. None — записи нет."""
    try:
        return json.loads((Path(root) / ".superstack" / "verify-last.json")
                          .read_text("utf-8"))
    except (OSError, ValueError):
        return None
