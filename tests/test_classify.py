#!/usr/bin/env python3
"""Вид продукта выводится из файлов, а не спрашивается у человека.

Набор проверок безопасности зависит от вида: магазину нужны деньги и возвраты,
ИИ-продавцу — граница между данными и инструкциями. Спросить «это SaaS или
магазин?» можно, но неразработчик ответит наугад — он не знает, чем они
различаются ЗДЕСЬ, — и его догадка станет основанием для того, что проверят,
а что нет.

Здесь заперты три отказа:

  · вывод без улики — гадание с уверенным лицом;
  · один намёк засчитывается за вид, и половину проверок молча не проведут;
  · найдено два вида, а выбран «главный» — второй набор проверок пропадает.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import at, PKG  # noqa: E402

_s = importlib.util.spec_from_file_location("ss_classify", at("tools", "classify.py"))
cl = importlib.util.module_from_spec(_s)
_s.loader.exec_module(cl)


class Проект(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".superstack").mkdir()

    def _идея(self, текст: str) -> None:
        (self.root / ".superstack" / "idea.md").write_text(текст, encoding="utf-8")


class Виды(Проект):

    def test_магазин_узнаётся_по_словам_человека(self):
        self._идея("Клиент кладёт товар в корзину, оформляет заказ и платит.")
        v = cl.run(self.root)
        self.assertEqual(v["kinds"], ["магазин"])

    def test_каждый_вывод_несёт_улику(self):
        """Не «похоже на магазин», а конкретное слово в конкретном файле."""
        self._идея("Клиент кладёт товар в корзину, оформляет заказ и платит.")
        v = cl.run(self.root)
        улики = v["evidence"]["магазин"]
        self.assertGreaterEqual(len(улики), 2)
        for u in улики:
            self.assertTrue(u["word"] and u["file"])

    def test_два_вида_остаются_двумя(self):
        """Магазин с ИИ-помощником — и то и другое; проверки складываются."""
        self._идея("Клиент кладёт товар в корзину и платит. Бот-ассистент "
                   "подсказывает, отвечает на вопросы про доставку.")
        v = cl.run(self.root)
        self.assertIn("магазин", v["kinds"])
        self.assertIn("ии-продавец", v["kinds"])

    def test_зависимости_тоже_улика(self):
        (self.root / "package.json").write_text(
            json.dumps({"dependencies": {"stripe": "^14", "next": "^15"}}),
            encoding="utf-8")
        v = cl.run(self.root)
        self.assertIn("магазин", v.get("kinds", []))


class НеЗнаю(Проект):

    def test_один_намёк_видом_не_делает(self):
        """Слово «заказ» встречается и у сайта с формой заявки."""
        self._идея("Человек оставляет заказ звонка.")
        v = cl.run(self.root)
        self.assertEqual(v["status"], "unknown")

    def test_пустой_проект_честно_не_знает(self):
        v = cl.run(self.root)
        self.assertEqual(v["status"], "unknown")
        self.assertIn("документа идеи", v["next"])

    def test_слабые_сигналы_ведут_к_вопросу_человеку(self):
        """Единственный случай, когда вопрос честнее догадки."""
        self._идея("Хочу сделать что-нибудь полезное.")
        v = cl.run(self.root)
        self.assertEqual(v["status"], "unknown")
        self.assertIn("спросить человека", v["next"])


class Согласованность(unittest.TestCase):

    def test_виды_совпадают_с_набором_проверок(self):
        """Классификатор, называющий вид, которого нет в наборе проверок,
        отправляет в пустоту: подобрать по нему нечего."""
        d = json.loads((PKG / "data" / "security-checks.json").read_text("utf-8"))
        известные = set(d["kinds"]["map"])
        for вид in cl.ПРИЗНАКИ:
            with self.subTest(вид):
                self.assertIn(вид, известные)


if __name__ == "__main__":
    unittest.main()
