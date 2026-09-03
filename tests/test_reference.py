#!/usr/bin/env python3
"""Доводка не запускается без образца, и круги считает код.

Дефект, ради которого механизм существует. После приёмки человек говорит
«доведи» — и цикл сравнения начинается с пустой рукой: ориентир он называл
словами в начале разговора, а к этому моменту слова остались в переписке.
Ведущий сравнивает работу сам с собой, различий не находит (их и не с чем
находить), объявляет «стало лучше» и делает круг за кругом, пока не кончится
терпение. Это не доводка, а блуждание за счёт человека.

Заперты два свойства, и оба — кодом возврата, а не пожеланием:

  · нет образца — `check` отказывает кодом 1, цикл не начинается;
  · кругов не больше трёх — потолок объявлен числом и достигается счётом.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG  # noqa: E402

TOOL = PKG / "tools" / "reference.py"
ФАЗА = PKG / "skills" / "go" / "phases" / "polish.md"


def зови(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True, timeout=60)


class БезОбразцаЦиклаНет(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_проверка_без_образца_отказывает_кодом(self):
        r = зови(str(self.d), "check")
        self.assertEqual(r.returncode, 1,
                         "образца нет, а доводка разрешена: цикл сравнения "
                         "пойдёт сверять работу с вкусом модели\n" + r.stdout)

    def test_с_образцом_круг_разрешён(self):
        зови(str(self.d), "add", "--kind", "ссылка",
             "--what", "https://linear.app · «чтобы было как здесь»")
        r = зови(str(self.d), "check")
        self.assertEqual(r.returncode, 0,
                         "образец записан, а круг всё равно запрещён\n" + r.stdout)

    def test_потолок_кругов_останавливает_счётом(self):
        зови(str(self.d), "add", "--kind", "ссылка",
             "--what", "https://linear.app · «чтобы было как здесь»")
        for i in (1, 2, 3):
            зови(str(self.d), "add", "--kind", "круг", "--what", f"круг {i}")
        r = зови(str(self.d), "check")
        self.assertEqual(r.returncode, 1,
                         "три круга сделано, а четвёртый разрешён: потолок, "
                         "который можно перешагнуть, потолком не является\n"
                         + r.stdout)


class ФазаДоводкиНазываетПотолокИОткат(unittest.TestCase):
    """Правила круга живут в файле фазы, а не в памяти ведущего.

    Оба — отказы, срабатывающие один раз за прогон. По памяти такие
    выполняются через раз, а «через раз» здесь означает бесконечный цикл
    правок и починку сломанного круга вместо отката.
    """

    def test_потолок_кругов_назван_в_фазе(self):
        self.assertIn("потолок — три круга", ФАЗА.read_text("utf-8").lower(),
                      "в файле фазы нет потолка кругов — доводка кончится не "
                      "результатом, а усталостью человека")

    def test_сломавший_круг_откатывается_целиком(self):
        текст = ФАЗА.read_text("utf-8")
        self.assertIn("ОТКАТЫВАЕТСЯ ЦЕЛИКОМ", текст,
                      "нет правила отката круга: сломанную косметическую "
                      "правку начнут чинить, и доводка станет стройкой")
        self.assertIn("checkpoint.py\" . back", текст,
                      "откат назван словами, но не командой — возвращать "
                      "будет нечем")


if __name__ == "__main__":
    unittest.main()
