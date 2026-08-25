#!/usr/bin/env python3
"""Итог сверяется у ВНЕШНЕЙ стороны, а не у себя.

Всё остальное доказательство система производит сама: сама пишет код, сама
гоняет тесты, сама читает свой вывод. Это честно ровно до тех пор, пока
инструменты исправны, — а сломанный прибор рисует зелёное с той же
уверенностью, что и рабочий. Нужен хотя бы один шаг, результат которого
система не может себе нарисовать.

Здесь заперты три отказа:

  · «похоже, что создан» засчитывается за доказательство: PR без единой
    добавленной строки открывается так же легко, как и с ними;
  · молчание чужой стороны толкуется в свою пользу;
  · проверка ходит в сеть и потому не может жить в наборе.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import at  # noqa: E402

_s = importlib.util.spec_from_file_location("ss_external",
                                            at("tools", "external_check.py"))
ec = importlib.util.module_from_spec(_s)
_s.loader.exec_module(ec)


def ответ(состояние="open", добавлено=42):
    return lambda slug, номер: ({"state": состояние, "additions": добавлено}, "")


class Условия(unittest.TestCase):

    def test_открытый_с_добавлениями_подтверждается(self):
        """Обратный контроль: проверка, никогда не подтверждающая, бесполезна."""
        v = ec.check("кто-то/репо", 7, ответ())
        self.assertEqual(v["status"], "pass")
        self.assertEqual(v["additions"], 42)

    def test_закрытый_не_подтверждается(self):
        v = ec.check("кто-то/репо", 7, ответ(состояние="closed"))
        self.assertEqual(v["status"], "fail")
        self.assertIn("closed", v["detail"])

    def test_ноль_добавленных_строк_не_доказательство(self):
        """PR без единой добавленной строки открывается так же легко."""
        v = ec.check("кто-то/репо", 7, ответ(добавлено=0))
        self.assertEqual(v["status"], "fail")
        self.assertIn("ноль", v["detail"])

    def test_отсутствие_поля_не_считается_нулём_молча(self):
        v = ec.check("кто-то/репо", 7, lambda s, н: ({"state": "open"}, ""))
        self.assertEqual(v["status"], "fail")
        self.assertIn("не назвал", v["detail"])


class МолчаниеНеСогласие(unittest.TestCase):

    def test_нет_ответа_это_не_подтверждено(self):
        v = ec.check("кто-то/репо", 7, lambda s, н: (None, "хостинг ответил 404"))
        self.assertEqual(v["status"], "unknown")

    def test_кривой_слаг_не_уходит_в_сеть(self):
        звали = []
        v = ec.check("../../чужое", 7,
                     lambda s, н: (звали.append(s), ({}, ""))[1])
        self.assertEqual(v["status"], "unknown")
        self.assertEqual(звали, [])


if __name__ == "__main__":
    unittest.main()
