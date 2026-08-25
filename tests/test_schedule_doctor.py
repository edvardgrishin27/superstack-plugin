#!/usr/bin/env python3
"""Доктор ходит сам, а не когда человек о нём вспомнит.

Ответ на вопрос «что протухло» меняется не от работы человека, а от чужих
релизов: репозиторий заархивировали, возможность стала нативной, плагин
перестали трогать. Событие происходит снаружи и в тишине — и потому «позвать
доктора» никогда не оказывается сегодняшней задачей.

Здесь заперты три отказа:

  · расписание ставится молча — правка чужой машины без спроса;
  · «вроде настраивали» засчитывается за состояние системы;
  · задача зовёт доктора, но итог никуда не уходит: находка, которую никто
    не прочитал, ничем не отличается от ненайденной.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import at  # noqa: E402

TOOL = at("tools", "schedule_doctor.py")
_s = importlib.util.spec_from_file_location("ss_sched", TOOL)
sd = importlib.util.module_from_spec(_s)
_s.loader.exec_module(sd)


class Проверка(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.корень = Path(self.tmp.name)

    def _задача(self, текст: str) -> None:
        d = self.корень / sd.ИМЯ
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(текст, encoding="utf-8")

    def test_нет_задачи_значит_нет(self):
        self.assertFalse(sd.installed(self.корень)["present"])

    def test_чужая_задача_с_тем_же_именем_не_считается(self):
        """«Вроде настраивали» — не состояние системы."""
        self._задача("---\nname: superstack-doctor\n---\n\nделай что-нибудь\n")
        v = sd.installed(self.корень)
        self.assertFalse(v["present"])
        self.assertIn("доктора в ней нет", v["why"])

    def test_задача_без_доставки_не_считается(self):
        """Находка, которую никто не прочитал, равна ненайденной."""
        self._задача("зови doctor.py и всё\n")
        v = sd.installed(self.корень)
        self.assertFalse(v["present"])
        self.assertIn("никуда не уходит", v["why"])

    def test_полная_задача_засчитывается(self):
        """Обратный контроль: проверка, никогда не зеленеющая, бесполезна."""
        self._задача(sd.task_body("/куда-то/superstack"))
        self.assertTrue(sd.installed(self.корень)["present"])


class Установка(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.корень = Path(self.tmp.name)

    def test_без_явного_согласия_не_ставит(self):
        p = subprocess.run([sys.executable, str(TOOL), "--install"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 3)
        self.assertIn("--yes", p.stderr)

    def test_поставленная_задача_зовёт_и_доктора_и_доставку(self):
        v = sd.install("/куда-то/superstack", self.корень)
        self.assertEqual(v["status"], "pass")
        текст = (self.корень / sd.ИМЯ / "SKILL.md").read_text("utf-8")
        self.assertIn(sd.ЗОВЁМ, текст)
        self.assertIn(sd.ДОСТАВКА, текст)

    def test_задача_велит_молчать_когда_нечего_сказать(self):
        """Пустой еженедельный отчёт учит пропускать все следующие."""
        текст = sd.task_body("/куда-то/superstack")
        self.assertIn("Нечего сказать — молчи", текст)

    def test_повторная_установка_не_ломает(self):
        sd.install("/куда-то/superstack", self.корень)
        v = sd.install("/куда-то/superstack", self.корень)
        self.assertEqual(v["status"], "pass")
        self.assertTrue(sd.installed(self.корень)["present"])


if __name__ == "__main__":
    unittest.main()
