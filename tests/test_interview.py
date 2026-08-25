#!/usr/bin/env python3
"""Дерево интервью: три состояния и право передумать.

Ценность дерева не в ветвях, а в трёх ответах одним взглядом: что улажено, что
можно решать сейчас, что стоит и чем именно держится. И в возможности вернуться
к улаженному: человек передумывает — это часть работы, а не сбой.

Здесь заперты три отказа:

  · состояние проставляется словом и расходится с делом;
  · возврат к узлу молчит о том, что он снял, — человек уверен, что прежние
    решения в силе, а они уже нет;
  · пустой фронтир при открытых узлах читается как «всё улажено».
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

TOOL = at("tools", "interview.py")


class Дерево(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _вызов(self, *a: str) -> tuple:
        p = subprocess.run([sys.executable, str(TOOL), str(self.root), *a],
                           capture_output=True, text=True, timeout=60)
        v = json.loads(p.stdout) if p.stdout.strip() else None
        return p.returncode, v, p.stderr

    def _узел(self, id_: str, вопрос: str = "вопрос", нужды: str = "") -> None:
        args = [str(self.root), "add", id_, "--question", вопрос]
        if нужды:
            args += ["--needs", нужды]
        subprocess.run([sys.executable, str(TOOL), *args],
                       capture_output=True, text=True, timeout=60)

    def _ответ(self, id_: str, текст: str = "решили так") -> tuple:
        return self._вызов("answer", id_, "--with", текст)


class ТриСостояния(Дерево):

    def test_новый_узел_на_фронтире(self):
        self._узел("аудитория")
        _, v, _ = self._вызов("show")
        self.assertEqual([у["id"] for у in v["фронтир"]], ["аудитория"])

    def test_узел_с_неулаженной_предпосылкой_заблокирован(self):
        self._узел("аудитория")
        self._узел("оплата", нужды="аудитория")
        _, v, _ = self._вызов("show")
        self.assertEqual([у["id"] for у in v["заблокировано"]], ["оплата"])
        self.assertEqual(v["заблокировано"][0]["blocked_by"], ["аудитория"])

    def test_ответ_переводит_и_разблокирует(self):
        self._узел("аудитория")
        self._узел("оплата", нужды="аудитория")
        self._ответ("аудитория", "малый бизнес")
        _, v, _ = self._вызов("show")
        self.assertEqual([у["id"] for у in v["улажено"]], ["аудитория"])
        self.assertEqual([у["id"] for у in v["фронтир"]], ["оплата"])

    def test_состояние_не_читается_из_поля(self):
        """Ярлык, проставленный руками, однажды разойдётся с делом — молча."""
        self._узел("аудитория")
        p = self.root / ".superstack" / "interview.json"
        d = json.loads(p.read_text("utf-8"))
        d["nodes"][0]["state"] = "улажено"          # подделка
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        _, v, _ = self._вызов("show")
        self.assertEqual([у["id"] for у in v["фронтир"]], ["аудитория"])
        self.assertEqual(v["улажено"], [])

    def test_пустой_ответ_не_улаживает(self):
        self._узел("аудитория")
        код, _, _ = self._ответ("аудитория", "   ")
        self.assertEqual(код, 3)


class ПравоПередумать(Дерево):

    def test_возврат_снимает_ответы_зависимых(self):
        """Передумал в одном месте — тронуто всё, что на этом стояло."""
        self._узел("аудитория")
        self._узел("оплата", нужды="аудитория")
        self._узел("чек", нужды="оплата")
        for i in ("аудитория", "оплата", "чек"):
            self._ответ(i)
        код, v, текст = self._вызов("reopen", "аудитория", "--why", "клиент передумал")
        self.assertEqual(v["улажено"], [])
        self.assertIn("снят ответ: оплата", текст)
        self.assertIn("снят ответ: чек", текст)

    def test_возврат_называет_причину_в_дереве(self):
        self._узел("аудитория")
        self._ответ("аудитория")
        self._вызов("reopen", "аудитория", "--why", "клиент передумал")
        _, v, _ = self._вызов("show")
        self.assertEqual(v["фронтир"][0]["id"], "аудитория")
        d = json.loads((self.root / ".superstack" / "interview.json").read_text("utf-8"))
        self.assertEqual(d["nodes"][0]["reopened_why"], "клиент передумал")
        self.assertEqual(d["nodes"][0]["previous_answer"], "решили так")

    def test_возврат_без_причины_отвергается(self):
        """Через неделю возврат без причины не отличить от ошибки."""
        self._узел("аудитория")
        self._ответ("аудитория")
        код, _, _ = self._вызов("reopen", "аудитория", "--why", " ")
        self.assertEqual(код, 3)

    def test_возврат_к_несуществующему_узлу_отвергается(self):
        self._узел("аудитория")
        код, _, _ = self._вызов("reopen", "нету", "--why", "почему бы и нет")
        self.assertEqual(код, 3)


class Тупик(Дерево):

    def test_кольцо_зависимостей_это_тупик_а_не_тишина(self):
        """Спрашивать нечего, работа не готова — тишина читалась бы как «всё улажено»."""
        self._узел("а", нужды="б")
        self._узел("б", нужды="а")
        код, v, текст = self._вызов("show")
        self.assertEqual(код, 1)
        self.assertEqual(v["фронтир"], [])
        self.assertIn("ТУПИК", текст)

    def test_всё_улажено_это_не_тупик(self):
        """Обратный контроль: пустой фронтир при закрытых узлах — норма."""
        self._узел("аудитория")
        self._ответ("аудитория")
        код, v, _ = self._вызов("show")
        self.assertEqual(код, 0)
        self.assertEqual(v["фронтир"], [])

    def test_нет_дерева_это_код_два(self):
        код, _, _ = self._вызов("show")
        self.assertEqual(код, 2)


if __name__ == "__main__":
    unittest.main()
