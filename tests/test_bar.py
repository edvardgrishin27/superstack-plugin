#!/usr/bin/env python3
"""Планка ПРОЕКТА: черта готовности, которую считает код, а не разговор.

Зачем это отдельно от гейта верификации. `verify.py` отвечает «зелено ли
сейчас», `prove_tests.py` — «держатся ли тесты». Ни один не отвечает на
вопрос, который человек задаёт последним и который решает всё: **готово ли**.
Пока на него отвечает переписка, ответ зависит от того, кто вчитывался: цель
«все ошибки исправлены» удовлетворяется тем, что агент СКАЗАЛ, будто исправил.

Отсюда три отказа, которые проверяются здесь и важнее самой проверки, потому
что каждый превращает планку в театр:

  · планки нет — и это молча засчитано за «взята»;
  · планка объявлена пустой — ноль ворот выглядит успехом;
  · объявленные ворота нечем запустить — они тихо выпадают из счёта.

Все три — одна и та же ошибка: «не проверяли» выдаётся за «проверено и
хорошо». Гаунтлет самого SUPERSTACK её уже держит; здесь то же свойство
достаётся любому проекту человека.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from paths import at

BAR = at("tools", "bar.py")
_s = importlib.util.spec_from_file_location("superstack_bar", BAR)
bar = importlib.util.module_from_spec(_s)
_s.loader.exec_module(bar)

ЗЕЛЁНОЕ = 'python3 -c pass'
КРАСНОЕ = 'python3 -c "raise SystemExit(1)"'
НЕЧЕМ = 'команды-такой-нет-9d3f1a'


def планка(*ворота: dict) -> dict:
    return {"schema": "superstack.bar.v1",
            "why": "черта готовности этого проекта",
            "gates": list(ворота)}


class Проект(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def положить(self, spec: dict, где: str = ".superstack") -> None:
        d = self.root / где
        d.mkdir(parents=True, exist_ok=True)
        (d / "bar.json").write_text(json.dumps(spec, ensure_ascii=False),
                                    encoding="utf-8")

    def прогон(self, *флаги: str) -> tuple:
        p = subprocess.run([sys.executable, str(BAR), str(self.root), *флаги],
                           capture_output=True, text=True, timeout=300)
        return p.returncode, json.loads(p.stdout), p.stderr


class НетПланки(Проект):
    """«Планки нет» и «планка взята» — разные утверждения."""

    def test_отсутствие_файла_не_успех(self):
        код, v, _ = self.прогон()
        self.assertEqual(код, 2)
        self.assertEqual(v["status"], "unknown")

    def test_причина_названа_человеку(self):
        _, _, текст = self.прогон()
        self.assertIn("планк", текст.lower())


class ПустаяПланка(Проект):
    """Ноль ворот — «не проверяли», а не «нечего предъявить, значит хорошо»."""

    def test_ноль_ворот_не_успех(self):
        self.положить(планка())
        код, v, _ = self.прогон()
        self.assertEqual(код, 2)
        self.assertEqual(v["status"], "unknown")


class Ворота(Проект):

    def test_зелёное_берёт_планку(self):
        self.положить(планка({"name": "набор", "why": "тесты", "run": ЗЕЛЁНОЕ}))
        код, v, _ = self.прогон()
        self.assertEqual(код, 0)
        self.assertEqual(v["status"], "pass")

    def test_красное_роняет(self):
        self.положить(планка({"name": "набор", "why": "тесты", "run": КРАСНОЕ}))
        код, v, _ = self.прогон()
        self.assertEqual(код, 1)
        self.assertEqual(v["status"], "fail")

    def test_нечем_запустить_это_не_провал_и_не_успех(self):
        self.положить(планка({"name": "линтер", "why": "стиль", "run": НЕЧЕМ}))
        код, v, _ = self.прогон()
        self.assertEqual(код, 2, "«нечем запустить» — не красное и не зелёное")
        self.assertEqual(v["status"], "unknown")

    def test_непроверенное_гасит_зелёное(self):
        """Одно зелёное рядом с непроверенным не даёт объявить планку взятой."""
        self.положить(планка({"name": "набор", "why": "тесты", "run": ЗЕЛЁНОЕ},
                             {"name": "линтер", "why": "стиль", "run": НЕЧЕМ}))
        код, v, _ = self.прогон()
        self.assertEqual(код, 2)

    def test_красное_сильнее_непроверенного(self):
        self.положить(планка({"name": "линтер", "why": "стиль", "run": НЕЧЕМ},
                             {"name": "набор", "why": "тесты", "run": КРАСНОЕ}))
        код, v, _ = self.прогон()
        self.assertEqual(код, 1, "красное обязано перебивать серое")

    def test_человек_видит_какие_ворота_упали(self):
        self.положить(планка({"name": "набор", "why": "тесты", "run": КРАСНОЕ}))
        _, _, текст = self.прогон()
        self.assertIn("набор", текст)

    def test_свой_код_не_смог_объявляется_проектом(self):
        """Команда, говорящая на языке кодов планки, называет их сама.

        Без этого «измерить не удалось» неотличимо от «упало», и планка
        обвинит продукт в поломке прибора.
        """
        СЕРОЕ = 'python3 -c "raise SystemExit(2)"'
        self.положить(планка({"name": "своя планка", "why": "чужой инструмент",
                              "run": СЕРОЕ, "unknown_on": [2]}))
        код, v, _ = self.прогон()
        self.assertEqual(код, 2)
        self.assertEqual(v["gates"][0]["status"], "unknown")

    def test_без_объявления_код_два_это_красное(self):
        СЕРОЕ = 'python3 -c "raise SystemExit(2)"'
        self.положить(планка({"name": "обычные", "why": "молчит о кодах",
                              "run": СЕРОЕ}))
        код, _, _ = self.прогон()
        self.assertEqual(код, 1)

    def test_флаг_json_гасит_человеческий_текст(self):
        """Машиночитаемый вывод — в stdout всегда, человеческий — по спросу."""
        self.положить(планка({"name": "набор", "why": "тесты", "run": ЗЕЛЁНОЕ}))
        _, v, текст = self.прогон("--json")
        self.assertEqual(v["status"], "pass")
        self.assertEqual(текст.strip(), "")

    def test_запасной_путь_claude(self):
        """Планка обещана в `.claude/bar.json` — обещание не отменяется."""
        self.положить(планка({"name": "набор", "why": "тесты", "run": ЗЕЛЁНОЕ}),
                      где=".claude")
        код, _, _ = self.прогон()
        self.assertEqual(код, 0)


class Встроенные(Проект):
    """Встроенные ворота обязаны ВЫЗЫВАТЬ соседний инструмент, а не называть его.

    Проверка живая: в пустом проекте `verify.py` отвечает «проверять нечем»
    кодом 2, и планка обязана унести этот ответ к себе, а не превратить его
    в зелёное. Инструмент, названный в словаре и не запущенный, прошёл бы
    любую проверку на подстроку.
    """

    def test_verify_делегируется_по_настоящему(self):
        self.положить(планка({"name": "верификация", "why": "зелено ли сейчас",
                              "builtin": "verify"}))
        код, v, _ = self.прогон()
        self.assertEqual(код, 2)
        self.assertEqual(v["gates"][0]["status"], "unknown")

    def test_неизвестное_встроенное_не_успех(self):
        self.положить(планка({"name": "выдумка", "why": "нет такого",
                              "builtin": "такого-нет"}))
        код, v, _ = self.прогон()
        self.assertEqual(код, 2)

    def test_словарь_встроенных_указывает_на_живые_файлы(self):
        for имя, (файл, _) in bar.BUILTINS.items():
            with self.subTest(имя):
                self.assertTrue((BAR.parent / файл).is_file(),
                                f"{имя} ссылается на несуществующий {файл}")


class Разбор(Проект):

    def test_битый_файл_это_ошибка_вызова(self):
        """Неразобранная планка — отказ вызова, а не вердикт о продукте.

        Машиночитаемого ответа здесь нет намеренно: печатать вердикт по
        файлу, который не прочитан, значит выдумывать его.
        """
        d = self.root / ".superstack"
        d.mkdir(parents=True)
        (d / "bar.json").write_text("{не json", encoding="utf-8")
        p = subprocess.run([sys.executable, str(BAR), str(self.root)],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 3)
        self.assertEqual(p.stdout.strip(), "")
        self.assertIn("планка не разобрана", p.stderr)

    def test_ворота_без_команды_не_молчат(self):
        """Запись без `run` и без `builtin` запускать нечем — это серое."""
        self.положить(планка({"name": "пусто", "why": "ничем не задано"}))
        код, v, _ = self.прогон()
        self.assertEqual(код, 2)


if __name__ == "__main__":
    unittest.main()
