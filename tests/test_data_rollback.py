#!/usr/bin/env python3
"""Откат данных: у кода есть git, у данных его нет.

Во всех 24 разобранных репозиториях бэкапов и отката баз — ноль механизмов.
Причина общая: это инструменты работы с КОДОМ, а модель отката кода — git.
Агент, снёсший файл, ловится `git checkout`; агент, снёсший строки в таблице,
не ловится ничем.

Здесь заперты три отказа, и каждый — способ пройти проверку, ничего не защитив:

  · намерение выдаётся за откат («сделаем бэкап перед миграцией» исполняется
    словами);
  · снимок объявлен без восстановления — файл неизвестной годности;
  · «не смог определить» читается как «правок данных нет».
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

TOOL = at("tools", "data_rollback.py")

МИГРАЦИЯ_С_ОБРАТНОЙ = (
    "def upgrade():\n    op.add_column('users', 'age')\n\n"
    "def downgrade():\n    op.drop_column('users', 'age')\n")
МИГРАЦИЯ_БЕЗ_ОБРАТНОЙ = "def upgrade():\n    op.drop_column('users', 'legacy')\n"
ПУТЬ_ОТКАТА = {"snapshot": "pg_dump -Fc $DATABASE_URL > snap.dump",
               "restore": "pg_restore -c -d $DATABASE_URL snap.dump"}


class Проект(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._git("init", "-q")
        self._git("config", "user.email", "т@т")
        self._git("config", "user.name", "тест")
        (self.root / "README.md").write_text("проект\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-qm", "первый")

    def _git(self, *a: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *a], cwd=str(self.root),
                              capture_output=True, text=True, timeout=60)

    def _файл(self, путь: str, текст: str) -> None:
        p = self.root / путь
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(текст, encoding="utf-8")

    def _спека(self, d: dict) -> None:
        p = self.root / ".superstack"
        p.mkdir(parents=True, exist_ok=True)
        (p / "data-rollback.json").write_text(json.dumps(d, ensure_ascii=False),
                                              encoding="utf-8")

    def _прогон(self, *флаги: str) -> tuple:
        p = subprocess.run([sys.executable, str(TOOL), str(self.root), *флаги],
                           capture_output=True, text=True, timeout=120)
        return p.returncode, json.loads(p.stdout), p.stderr


class БезПравокДанных(Проект):

    def test_правки_только_кода_проходят(self):
        """Проверка, срабатывающая на любой правке, будет отключена в первый день."""
        self._файл("src/main.py", "print('привет')\n")
        код, v, _ = self._прогон()
        self.assertEqual(код, 0)
        self.assertIn("правок данных нет", v["detail"])


class ДанныеБезОтката(Проект):

    def test_миграция_без_объявленного_пути_отката_роняет(self):
        self._файл("migrations/0001_add_age.py", МИГРАЦИЯ_С_ОБРАТНОЙ)
        код, v, _ = self._прогон()
        self.assertEqual(код, 1)
        self.assertIn("не объявлен", v["detail"])

    def test_снимок_без_восстановления_не_считается(self):
        """Снимок без обратной команды — файл неизвестной годности."""
        self._файл("migrations/0001_add_age.py", МИГРАЦИЯ_С_ОБРАТНОЙ)
        self._спека({"snapshot": "pg_dump ..."})
        код, v, _ = self._прогон()
        self.assertEqual(код, 1)
        self.assertIn("наполовину", v["detail"])

    def test_путь_отката_делает_вердикт_зелёным(self):
        self._файл("migrations/0001_add_age.py", МИГРАЦИЯ_С_ОБРАТНОЙ)
        self._спека(ПУТЬ_ОТКАТА)
        код, v, _ = self._прогон()
        self.assertEqual(код, 0, v)


class Необратимое(Проект):

    def test_правка_без_обратной_роняет(self):
        self._файл("migrations/0002_drop_legacy.py", МИГРАЦИЯ_БЕЗ_ОБРАТНОЙ)
        self._спека(ПУТЬ_ОТКАТА)
        код, v, _ = self._прогон()
        self.assertEqual(код, 1)
        self.assertIn("migrations/0002_drop_legacy.py",
                      v["irreversible_undeclared"])

    def test_объявленная_необратимой_проходит(self):
        """Необратимое бывает. Молча — нельзя, с причиной — можно."""
        self._файл("migrations/0002_drop_legacy.py", МИГРАЦИЯ_БЕЗ_ОБРАТНОЙ)
        self._спека({**ПУТЬ_ОТКАТА,
                     "irreversible": [{"file": "migrations/0002_drop_legacy.py",
                                       "why": "колонка удаляется по требованию юриста"}]})
        код, _, _ = self._прогон()
        self.assertEqual(код, 0)

    def test_парный_down_sql_считается_обратной(self):
        self._файл("db/migrate/003_add_index.sql", "CREATE INDEX i ON t (c);\n")
        self._файл("db/migrate/003_add_index.down.sql", "DROP INDEX i;\n")
        self._спека(ПУТЬ_ОТКАТА)
        код, _, _ = self._прогон()
        self.assertEqual(код, 0)


class НеСмогОпределить(Проект):

    def test_нет_git_это_не_зелёное(self):
        """«Определить не смог» и «правок данных нет» — разные утверждения."""
        # Каталог обязан лежать ВНЕ репозитория: подкаталог внутри git-дерева
        # прекрасно отвечает на `git diff`, и проверка измеряла бы не то.
        with tempfile.TemporaryDirectory() as d:
            p = subprocess.run([sys.executable, str(TOOL), d],
                               capture_output=True, text=True, timeout=120,
                               env={**__import__("os").environ,
                                    "GIT_CEILING_DIRECTORIES": str(Path(d).parent)})
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)

    def test_незнакомый_формат_даёт_серое(self):
        self._файл("migrations/0003_странное.rb", "class Странное; end\n")
        self._спека(ПУТЬ_ОТКАТА)
        код, v, _ = self._прогон()
        self.assertEqual(код, 2)
        self.assertIn("migrations/0003_странное.rb", v["unclear"])


if __name__ == "__main__":
    unittest.main()
