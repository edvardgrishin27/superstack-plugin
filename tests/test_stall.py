#!/usr/bin/env python3
"""Обречённый прогон рвётся рано, а не доигрывается до конца.

Дефект, ради которого инструмент существует: исполнитель доходил до конца,
гейт говорил «нет», круг повторялся целиком. Прогон, обречённый на третьем
шаге, оплачивался полностью — вместе с разбором результата и следующим заходом.

Отдельно заперт ЛОЖНЫЙ признак. Первая редакция считала разговором «нет правок
файлов» и валила живой прогон, который час гонял проверки и ничего не правил:
read-only работа работой быть не перестаёт. Признак переформулирован в «ходы
без единого вызова инструмента», и тест ниже держит именно это.

Проверено на настоящем материале: девять журналов этого проекта и сессия на
996 шагов — ни одного ложного срабатывания. Сами отказы в том материале не
случались (самая длинная петля за 996 шагов — одно падение), поэтому
ОБНАРУЖЕНИЕ доказывается здесь построенными журналами, а не реальными.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG, skill_text  # noqa: E402

TOOL = PKG / "tools" / "stall.py"
_s = importlib.util.spec_from_file_location("ss_stall", TOOL)
st = importlib.util.module_from_spec(_s)
_s.loader.exec_module(st)


def ход(вызовы: list) -> str:
    """Ход помощника: ноль или больше вызовов инструментов."""
    блоки = [{"type": "tool_use", "id": i, "name": n, "input": v}
             for i, n, v in вызовы]
    return json.dumps({"type": "assistant", "message": {"content": блоки}},
                      ensure_ascii=False)


def итог(идентификатор: str, упал: bool) -> str:
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": идентификатор,
         "is_error": упал}]}}, ensure_ascii=False)


def журнал(путь: Path, шаги: list, хвост_молчания: int = 0) -> Path:
    """Журнал в той же форме, в какой его пишет Claude Code."""
    строки = []
    for n, (инструмент, вход, упал) in enumerate(шаги):
        i = f"t{n}"
        строки.append(ход([(i, инструмент, вход)]))
        строки.append(итог(i, упал))
    строки += [ход([]) for _ in range(хвост_молчания)]
    путь.write_text("\n".join(строки) + "\n", encoding="utf-8")
    return путь


def bash(команда: str, упал: bool = False) -> tuple:
    return ("Bash", {"command": команда}, упал)


class Основа(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def вердикт(self, шаги: list, зоны=(), хвост_молчания: int = 0):
        f = журнал(self.d / "run.jsonl", шаги, хвост_молчания)
        ш = st.шаги(f)
        return st.разобрать(ш, list(зоны), st.холостые_ходы(f))


class Петля(Основа):

    def test_три_одинаковых_падения_это_петля(self):
        в = self.вердикт([bash("pytest tests/", True)] * 3 + [bash("ls")])
        self.assertTrue(any("петля" in п for п in в["причины"]), в)

    def test_два_падения_подряд_ещё_не_петля(self):
        """Вторая попытка — норма отладки. Валить на ней значит мешать работе."""
        в = self.вердикт([bash("pytest tests/", True)] * 2 + [bash("pytest tests/")]
                         + [bash(f"echo {i}") for i in range(6)])
        self.assertEqual([п for п in в["причины"] if "петля" in п], [])

    def test_разные_падения_не_петля(self):
        в = self.вердикт([bash("а", True), bash("б", True), bash("в", True),
                          bash("pytest")] + [bash(f"x{i}") for i in range(5)])
        self.assertEqual([п for п in в["причины"] if "петля" in п], [])


class Вслепую(Основа):

    def test_много_шагов_без_проверок(self):
        в = self.вердикт([bash(f"cat файл{i}.py") for i in range(14)])
        self.assertTrue(any("вслепую" in п for п in в["причины"]), в)

    def test_запуск_проверки_снимает_признак(self):
        шаги = [bash(f"cat файл{i}.py") for i in range(13)] + [bash("pytest tests/")]
        в = self.вердикт(шаги)
        self.assertEqual([п for п in в["причины"] if "вслепую" in п], [])


class НезакрытаяОшибка(Основа):

    def test_хвост_в_ошибке(self):
        в = self.вердикт([bash("ok")] * 6 + [bash("сборка", True)])
        self.assertTrue(any("незакрытая ошибка" in п for п in в["причины"]), в)

    def test_успех_после_ошибки_закрывает(self):
        в = self.вердикт([bash("ok")] * 5 + [bash("сборка", True), bash("сборка")])
        self.assertEqual([п for п in в["причины"] if "незакрытая" in п], [])


class РазговорВместоРаботы(Основа):

    def test_ходы_без_вызовов_это_разговор(self):
        в = self.вердикт([bash("pytest") for _ in range(7)], хвост_молчания=4)
        self.assertTrue(any("разговор" in п for п in в["причины"]), в)

    def test_работа_без_правок_разговором_не_является(self):
        """Ложный признак, поймавший живой прогон: он час гонял проверки и
        ничего не правил — и первая редакция объявила это разговором."""
        в = self.вердикт([bash("pytest tests/")] * 8 + [bash("cat отчёт.json")] * 6)
        self.assertEqual([п for п in в["причины"] if "разговор" in п], [], в)


class ЧужаяЗона(Основа):

    def test_правка_за_границей(self):
        шаги = [("Edit", {"file_path": "src/other/main.py"}, False)] + \
               [bash("pytest")] * 6
        в = self.вердикт(шаги, зоны=["src/bot/"])
        self.assertTrue(any("чужая зона" in п for п in в["причины"]), в)

    def test_правка_в_своей_зоне(self):
        шаги = [("Edit", {"file_path": "src/bot/main.py"}, False)] + \
               [bash("pytest")] * 6
        в = self.вердикт(шаги, зоны=["src/bot/"])
        self.assertEqual([п for п in в["причины"] if "зона" in п], [])

    def test_без_объявленной_зоны_не_судим(self):
        шаги = [("Edit", {"file_path": "где угодно.py"}, False)] + [bash("pytest")] * 6
        self.assertEqual([п for п in self.вердикт(шаги)["причины"] if "зона" in п], [])


class СудитьНечемНеЗначитИдёт(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.дом = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def каталог(self, проект: Path) -> Path:
        """Тот же путь, что вычислит инструмент, но от ПОДСТАВНОГО дома.

        `каталог_журналов` берёт `Path.home()`, а он в этом процессе настоящий:
        журнал лёг бы в домашний каталог человека, а подпроцесс искал бы его
        во временном — и тест краснел бы на верном коде.
        """
        slug = re.sub(r"[^A-Za-z0-9]", "-", str(проект.resolve()))
        d = self.дом / ".claude" / "projects" / slug
        d.mkdir(parents=True, exist_ok=True)
        return d

    def запуск(self, проект: Path, *флаги):
        окружение = dict(os.environ, HOME=str(self.дом))
        p = subprocess.run([sys.executable, str(TOOL), str(проект), *флаги],
                           capture_output=True, text=True, timeout=120,
                           env=окружение)
        return p.returncode, p.stdout + p.stderr

    def test_нет_журналов_код_два(self):
        код, текст = self.запуск(self.дом / "проект")
        self.assertEqual(код, 2, "отсутствие журнала принято за «идёт»")
        self.assertIn("СУДИТЬ НЕЧЕМ", текст)

    def test_мало_шагов_код_два(self):
        проект = self.дом / "проект"
        проект.mkdir()
        каталог = self.каталог(проект)
        журнал(каталог / "сессия.jsonl", [bash("ls")] * 2)
        код, текст = self.запуск(проект)
        self.assertEqual(код, 2)
        self.assertIn("судить рано", текст)

    def test_застрявший_прогон_код_один(self):
        проект = self.дом / "проект"
        проект.mkdir()
        каталог = self.каталог(проект)
        журнал(каталог / "сессия.jsonl", [bash("pytest", True)] * 3 + [bash("ls")] * 4)
        код, текст = self.запуск(проект)
        self.assertEqual(код, 1)
        self.assertIn("ЗАСТРЯЛ", текст)

    def test_идущий_прогон_код_ноль_и_называет_непроверенное(self):
        проект = self.дом / "проект"
        проект.mkdir()
        каталог = self.каталог(проект)
        журнал(каталог / "сессия.jsonl", [bash("pytest tests/")] * 8)
        код, текст = self.запуск(проект)
        self.assertEqual(код, 0, текст)
        self.assertIn("ЧЕГО НЕ ЗНАЕМ", текст)


class ФазаЗовётПроверку(unittest.TestCase):

    def test_скилл_зовёт_инструмент(self):
        self.assertIn("stall.py", skill_text("go"))


if __name__ == "__main__":
    unittest.main()
