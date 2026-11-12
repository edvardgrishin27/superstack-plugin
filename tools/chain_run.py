#!/usr/bin/env python3
"""Сквозной прогон: настоящие инструменты плагина подряд, без модели.

Тесты шагов проверяют каждый инструмент своими фикстурами. Разрыв между
инструментами они не видят: один пишет файл под одним именем, другой читает
под другим, и оба теста зелёные. Здесь инструменты зовутся процессами, во
временной папке, в том порядке, в каком их зовёт человек. Стадии каждой
волны лежат в своём файле `chain_stages/waveN.py`.

  python3 tools/chain_run.py [--plugin <папка плагина>] [--keep]

  код 0 — все стадии дали ожидаемый итог, 1 — сорвалась стадия (названа
  первой фразой вывода), 3 — ошибка вызова.
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WAVES = ("wave1",)
STAGE_TIMEOUT = 300


class Ctx:
    """Временный «компьютер человека»: домашняя папка, состояние, проект."""

    def __init__(self, plugin: Path, base: Path):
        self.plugin = plugin
        self.tools = plugin / "tools"
        self.hooks = plugin / "hooks"
        self.home = base / "home"
        self.state = base / "state"
        self.proj = self.home / "proj"
        (self.proj / "tests").mkdir(parents=True)
        self.state.mkdir()
        (self.proj / "tests" / "conftest.py").write_text("", "utf-8")
        (self.proj / "tests" / "test_ok.py").write_text(
            "def test_ok():\n    assert 1 == 1\n", "utf-8")
        (self.proj / "app.py").write_text("x = 1\n", "utf-8")

    def env(self) -> dict:
        e = {k: v for k, v in os.environ.items()
             if not k.startswith(("SUPERSTACK_", "CLAUDE_"))}
        e.update(HOME=str(self.home), SUPERSTACK_STATE_DIR=str(self.state),
                 PYTHONDONTWRITEBYTECODE="1")
        return e

    def run(self, script: Path, *args: str, stdin: str = "",
            cwd: "Path | None" = None, extra_env: "dict | None" = None):
        env = {**self.env(), **(extra_env or {})}
        return subprocess.run([sys.executable, str(script), *args],
                              input=stdin, capture_output=True, text=True,
                              cwd=str(cwd or self.proj), env=env,
                              timeout=STAGE_TIMEOUT)

    def tool(self, name: str, *args: str, **kw):
        return self.run(self.tools / f"{name}.py", *args, **kw)


def main(argv: list) -> int:
    plugin, keep, i = ROOT / "plugins" / "superstack", False, 0
    while i < len(argv):
        if argv[i] == "--keep":
            keep = True
        elif argv[i] == "--plugin" and i + 1 < len(argv):
            i += 1
            plugin = Path(argv[i])
        else:
            print("вызов: chain_run.py [--plugin <папка плагина>] [--keep]",
                  file=sys.stderr)
            return 3
        i += 1
    if not (plugin / "tools").is_dir():
        print(f"НЕ УДАЛОСЬ: нет папки плагина — {plugin}", file=sys.stderr)
        return 3
    sys.path.insert(0, str(ROOT / "tools"))
    stages = []
    for w in WAVES:
        stages += importlib.import_module(f"chain_stages.{w}").STAGES
    base = Path(tempfile.mkdtemp(prefix="chain-")).resolve()
    try:
        ctx = Ctx(plugin.resolve(), base)
        for name, fn in stages:
            try:
                why = fn(ctx)
            except Exception as e:  # noqa: BLE001 — сбой стадии есть её итог
                why = f"{type(e).__name__}: {e}"
            if why:
                print(f"сорвалась стадия «{name}»: {why}")
                return 1
            print(f"стадия «{name}»: ок")
        print(f"цепочка прошла: стадий {len(stages)}")
        return 0
    finally:
        if keep:
            print(f"папка прогона оставлена: {base}")
        else:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
