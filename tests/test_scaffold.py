#!/usr/bin/env python3
"""Папка проекта рождается настроенной, а не настраивается потом.

Человек без опыта не знает, что у проекта бывает конституция, что у неё есть
потолок и что «проверить» — это команда, а не взгляд. Он и не должен. Он должен
получить папку, где всё это уже стоит, выведенное из того, что он сам рассказал.

Здесь заперты четыре отказа, и каждый — способ навредить, выглядя услужливым:

  · генератор дописывает недостающий раздел «разумным» текстом — то есть
    создаёт решение, которого человек не принимал, и подписывает его именем;
  · перезаписывает то, что человек уже написал;
  · порождает конституцию сверх потолка, который сам же и проверяет;
  · кладёт проект без черты готовности.
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

TOOL = at("tools", "scaffold.py")

СПЕКА = """# Спека

## Что должно получиться

Форма записи на стрижку: клиент выбирает мастера, время и оставляет телефон.

## Как проверить

`npm test` зелёный; на /booking выбор мастера и времени создаёт запись в базе.

## Чего НЕ делаем

Онлайн-оплату в этой версии не делаем.
"""


class Проект(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "запись-на-стрижку"
        (self.root / ".superstack").mkdir(parents=True)

    def _спека(self, текст: str = СПЕКА) -> None:
        (self.root / ".superstack" / "spec.md").write_text(текст, encoding="utf-8")

    def _прогон(self, *флаги: str) -> tuple:
        p = subprocess.run([sys.executable, str(TOOL), str(self.root), *флаги],
                           capture_output=True, text=True, timeout=60)
        return p.returncode, json.loads(p.stdout), p.stderr


class НетВхода(Проект):

    def test_без_спеки_не_генерирует(self):
        код, v, _ = self._прогон()
        self.assertEqual(код, 2)
        self.assertIn("не из чего", v["detail"])

    def test_спека_без_раздела_как_проверить_отвергается(self):
        """Недостающий раздел не дописывается: это было бы решение, которого
        человек не принимал, подписанное его именем."""
        self._спека("# Спека\n\n## Что должно получиться\n\nФорма записи.\n")
        код, v, _ = self._прогон()
        self.assertEqual(код, 2)
        self.assertIn("Как проверить", v["detail"])


class Генерация(Проект):

    def test_кладёт_конституцию_шаблон_и_планку(self):
        self._спека()
        код, v, _ = self._прогон()
        self.assertEqual(код, 0, v)
        self.assertEqual(set(v["written"]),
                         {"CLAUDE.md", "SPEC_TEMPLATE.md", ".superstack/bar.json"})
        for имя in v["written"]:
            self.assertTrue((self.root / имя).is_file())

    def test_конституция_состоит_из_слов_человека(self):
        """Каждая строка выведена из спеки, а не сочинена."""
        self._спека()
        self._прогон()
        текст = (self.root / "CLAUDE.md").read_text("utf-8")
        self.assertIn("Форма записи на стрижку", текст)
        self.assertIn("npm test", текст)
        self.assertIn("Онлайн-оплату в этой версии не делаем", текст)
        self.assertIn("запись-на-стрижку", текст)

    def test_решения_из_интервью_попадают_в_конституцию(self):
        self._спека()
        (self.root / ".superstack" / "interview.json").write_text(json.dumps(
            {"nodes": [{"id": "мастера", "question": "Сколько мастеров?",
                        "answer": "трое", "needs": []},
                       {"id": "оплата", "question": "Оплата?", "answer": "",
                        "needs": []}]}, ensure_ascii=False), encoding="utf-8")
        self._прогон()
        текст = (self.root / "CLAUDE.md").read_text("utf-8")
        self.assertIn("трое", текст)
        self.assertNotIn("Оплата?", текст, "неулаженный узел попал в конституцию")

    def test_планка_проекта_кладётся_сразу(self):
        """Продукт без черты готовности нечем объявить готовым."""
        self._спека()
        self._прогон()
        планка = json.loads((self.root / ".superstack" / "bar.json").read_text("utf-8"))
        self.assertEqual(планка["schema"], "superstack.bar.v1")
        self.assertTrue(планка["gates"])
        for в in планка["gates"]:
            self.assertTrue(в.get("why"), "ворота без причины")


class ЧужоеНеТрогаем(Проект):

    def test_существующий_файл_останавливает(self):
        self._спека()
        (self.root / "CLAUDE.md").write_text("моё, писал руками\n", encoding="utf-8")
        код, v, _ = self._прогон()
        self.assertEqual(код, 1)
        self.assertIn("CLAUDE.md", v["occupied"])
        self.assertEqual((self.root / "CLAUDE.md").read_text("utf-8"),
                         "моё, писал руками\n")

    def test_force_разрешает_перезапись(self):
        """Обратный контроль: запрет без обхода превращается в тупик."""
        self._спека()
        (self.root / "CLAUDE.md").write_text("старое\n", encoding="utf-8")
        код, _, _ = self._прогон("--force")
        self.assertEqual(код, 0)
        self.assertIn("Форма записи", (self.root / "CLAUDE.md").read_text("utf-8"))


class Потолок(Проект):

    def test_конституция_сверх_потолка_не_кладётся(self):
        """Генератор, порождающий находку, спорит сам с собой."""
        длинно = "\n".join(f"строка про требование {i}" for i in range(400))
        self._спека(f"# Спека\n\n## Что должно получиться\n\n{длинно}\n\n"
                    "## Как проверить\n\n`npm test` зелёный.\n")
        код, v, _ = self._прогон()
        self.assertEqual(код, 1)
        self.assertGreater(v["lines"], 200)
        self.assertFalse((self.root / "CLAUDE.md").exists())

    def test_обычная_спека_в_потолок_помещается(self):
        self._спека()
        _, v, _ = self._прогон()
        self.assertLessEqual(v["lines"], 200)


if __name__ == "__main__":
    unittest.main()
