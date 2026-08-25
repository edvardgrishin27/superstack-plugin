#!/usr/bin/env python3
"""Отчёт догоняет человека там, где его нет за компьютером.

Автономия без исходящего канала ненастоящая: система может работать час, но
если итог лежит в терминале, человек всё равно привязан к столу — он либо
ждёт, либо возвращается проверять.

Здесь заперты четыре отказа:

  · канал не объявлен, а доставка «работает» — молчание неотличимо от успеха
    ровно до того дня, когда оно понадобится;
  · секрет уезжает наружу: исходящий канал — самый дешёвый способ вынести
    ключ с машины;
  · «отправлено» без ответа сервера — это «вызвали функцию»;
  · сбой доставки отменяет сделанное.

Сеть здесь не трогается: отправитель подаётся снаружи. Проверка, стучащаяся
в интернет, измеряет чужой сервер, а не наш код.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import at  # noqa: E402

_s = importlib.util.spec_from_file_location("ss_notify", at("tools", "notify.py"))
nt = importlib.util.module_from_spec(_s)
_s.loader.exec_module(nt)


class Канал(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".superstack").mkdir()
        self.ушло = []

    def _спека(self, d: dict) -> None:
        (self.root / ".superstack" / "notify.json").write_text(
            json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def _отправитель(self, код: int = 200, ошибка: str = ""):
        def шлём(url, тело):
            self.ушло.append((url, тело))
            return (код, ошибка)
        return шлём


class НетКанала(Канал):

    def test_не_объявлен_это_не_тихо_ок(self):
        v = nt.send(self.root, "итог", self._отправитель())
        self.assertEqual(v["status"], "unknown")
        self.assertEqual(self.ушло, [])

    def test_http_не_принимается(self):
        """По http итог уедет открытым текстом."""
        self._спека({"webhook": "http://пример.рф/хук"})
        v = nt.send(self.root, "итог", self._отправитель())
        self.assertEqual(v["status"], "unknown")
        self.assertIn("https", v["detail"])

    def test_выключен_намеренно_это_успех(self):
        """Обратный контроль: человек имеет право не хотеть уведомлений."""
        self._спека({"quiet": True})
        v = nt.send(self.root, "итог", self._отправитель())
        self.assertEqual(v["status"], "pass")
        self.assertFalse(v["delivered"])
        self.assertEqual(self.ушло, [])


class СекретыНеУезжают(Канал):

    def test_ключ_в_тексте_вырезается(self):
        self._спека({"webhook": "https://пример.рф/хук"})
        секрет = "ghp_" + "a" * 36
        v = nt.send(self.root, f"готово, токен {секрет} записан",
                    self._отправитель())
        self.assertEqual(v["status"], "pass")
        ушедший = json.dumps(self.ушло[0][1], ensure_ascii=False)
        self.assertNotIn(секрет, ушедший)
        self.assertIn("вырезано", ушедший)
        self.assertEqual(v["redacted"], 1)

    def test_пароль_в_команде_вырезается(self):
        self._спека({"webhook": "https://пример.рф/хук"})
        v = nt.send(self.root, "запускал sshpass -p 'тайна' ssh root@х",
                    self._отправитель())
        self.assertNotIn("тайна", json.dumps(self.ушло[0][1], ensure_ascii=False))
        self.assertGreaterEqual(v["redacted"], 1)

    def test_обычный_текст_не_портится(self):
        """Редактура, режущая всё подряд, делает отчёт нечитаемым."""
        self._спека({"webhook": "https://пример.рф/хук"})
        v = nt.send(self.root, "собрал форму записи, тесты зелёные",
                    self._отправитель())
        self.assertEqual(v["redacted"], 0)
        self.assertIn("тесты зелёные", json.dumps(self.ушло[0][1],
                                                  ensure_ascii=False))


class ДоставкаПодтверждается(Канал):

    def test_ошибка_канала_это_не_отправлено(self):
        """«Отправлено» без ответа сервера — это «вызвали функцию»."""
        self._спека({"webhook": "https://пример.рф/хук"})
        v = nt.send(self.root, "итог", self._отправитель(500, "канал ответил 500"))
        self.assertEqual(v["status"], "fail")

    def test_недоступный_канал_не_отменяет_работу(self):
        self._спека({"webhook": "https://пример.рф/хук"})
        v = nt.send(self.root, "итог", lambda u, t: (None, "канал недоступен"))
        self.assertEqual(v["status"], "fail")
        self.assertIn("не отменяется", v["next"])

    def test_успех_несёт_код_ответа(self):
        self._спека({"webhook": "https://пример.рф/хук"})
        v = nt.send(self.root, "итог", self._отправитель(204))
        self.assertEqual(v["status"], "pass")
        self.assertEqual(v["code"], 204)


if __name__ == "__main__":
    unittest.main()
