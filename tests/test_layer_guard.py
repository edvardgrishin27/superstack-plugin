#!/usr/bin/env python3
"""Границы слоёв держит машина, а не внимательность ревьюера.

Когда код пишет модель, объём правок растёт быстрее, чем внимание того, кто их
читает. Никто не ломает границу нарочно — её ломают по дороге к работающей
фиче, и на ревью это выглядит обычной строкой `import`.

Здесь заперты два отказа:

  · инструмент угадывает слои сам и выдаёт своё мнение за архитектуру проекта;
  · «границы не объявлены» читается как «нарушений нет».
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import at  # noqa: E402

TOOL = at("tools", "layer_guard.py")

СЛОИ = {"layers": [
    {"name": "домен", "paths": ["src/domain/"], "may_import": []},
    {"name": "приложение", "paths": ["src/app/"], "may_import": ["домен"]},
    {"name": "инфра", "paths": ["src/infra/"], "may_import": ["домен", "приложение"]},
]}


class Проект(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _файл(self, путь: str, текст: str) -> None:
        p = self.root / путь
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(текст, encoding="utf-8")

    def _слои(self, d: dict = None) -> None:
        p = self.root / ".superstack"
        p.mkdir(parents=True, exist_ok=True)
        (p / "layers.json").write_text(json.dumps(d or СЛОИ, ensure_ascii=False),
                                       encoding="utf-8")

    def _прогон(self) -> tuple:
        p = subprocess.run([sys.executable, str(TOOL), str(self.root)],
                           capture_output=True, text=True, timeout=120)
        return p.returncode, json.loads(p.stdout), p.stderr


class ГраницыНеОбъявлены(Проект):

    def test_нет_спеки_это_не_чисто(self):
        """«Не объявлено» и «нарушений нет» — разные утверждения."""
        self._файл("src/domain/цена.py", "from src.infra.база import query\n")
        код, v, _ = self._прогон()
        self.assertEqual(код, 2)
        self.assertIn("не объявлены", v["detail"])

    def test_пустой_список_слоёв_тоже_не_чисто(self):
        self._слои({"layers": []})
        self.assertEqual(self._прогон()[0], 2)


class Нарушения(Проект):

    def test_домен_смотрящий_в_инфру_ловится(self):
        self._слои()
        self._файл("src/domain/цена.py", "from src.infra.база import query\n")
        код, v, _ = self._прогон()
        self.assertEqual(код, 1)
        н = v["violations"][0]
        self.assertEqual((н["from_layer"], н["to_layer"]), ("домен", "инфра"))
        self.assertEqual(н["line"], 1)

    def test_разрешённое_направление_молчит(self):
        """Обратный контроль: гард, запрещающий всё, снимут в первый день."""
        self._слои()
        self._файл("src/app/сценарий.py", "from src.domain.цена import Цена\n")
        код, v, _ = self._прогон()
        self.assertEqual(код, 0, v)

    def test_относительный_импорт_тоже_считается(self):
        """Граница, которую обходит `../`, не граница."""
        self._слои()
        self._файл("src/domain/цена.ts", "import { q } from '../infra/база';\n")
        код, v, _ = self._прогон()
        self.assertEqual(код, 1, v)
        self.assertEqual(v["violations"][0]["to_layer"], "инфра")

    def test_нарушение_называет_адрес_и_обе_стороны(self):
        """Находка без адреса — мнение, а мнение здесь запрещено."""
        self._слои()
        self._файл("src/domain/склад.js", "\n\nconst db = require('../infra/db');\n")
        _, v, _ = self._прогон()
        н = v["violations"][0]
        for поле in ("file", "line", "from_layer", "to_layer", "import"):
            with self.subTest(поле):
                self.assertTrue(н.get(поле) is not None and н[поле] != "")
        self.assertEqual(н["line"], 3)

    def test_внешние_пакеты_не_считаются_слоями(self):
        self._слои()
        self._файл("src/domain/цена.py", "import json\nfrom pathlib import Path\n")
        self.assertEqual(self._прогон()[0], 0)

    def test_чужие_каталоги_не_читаются(self):
        """node_modules — не архитектура проекта, и его размер убил бы прогон."""
        self._слои()
        self._файл("src/domain/цена.py", "from src.domain.скидка import x\n")
        self._файл("node_modules/пакет/src/domain/чужое.py",
                   "from src.infra.база import query\n")
        self.assertEqual(self._прогон()[0], 0)


if __name__ == "__main__":
    unittest.main()
