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
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG, skill_text  # noqa: E402

TOOL = PKG / "tools" / "stall.py"
_s = importlib.util.spec_from_file_location("ss_stall", TOOL)
st = importlib.util.module_from_spec(_s)
_s.loader.exec_module(st)

TOOL_WATCHDOG = PKG / "tools" / "watchdog.py"
_w = importlib.util.spec_from_file_location("ss_watchdog", TOOL_WATCHDOG)
wd = importlib.util.module_from_spec(_w)
_w.loader.exec_module(wd)


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

    def test_скилл_зовёт_сторожа(self):
        self.assertIn("watchdog.py", skill_text("go"))


# ---------------------------------------------------------------------------
# Волна: проверка ВСЕХ журналов, а не одного случайного (второй дефект).
# ---------------------------------------------------------------------------
def подставной_дом(дом: Path, проект: Path) -> Path:
    """Тот же путь, что вычислит инструмент, но от ПОДСТАВНОГО дома.

    Тот же приём, что у `СудитьНечемНеЗначитИдёт.каталог`: `Path.home()` в
    этом процессе настоящий, и без подмены журнал лёг бы в дом человека, а
    подпроцесс искал бы его во временном.
    """
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(проект.resolve()))
    d = дом / ".claude" / "projects" / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def запустить_stall(дом: Path, проект: Path, *флаги: str) -> tuple:
    окружение = dict(os.environ, HOME=str(дом))
    p = subprocess.run([sys.executable, str(TOOL), str(проект), *флаги],
                       capture_output=True, text=True, timeout=120,
                       env=окружение)
    return p.returncode, p.stdout + p.stderr


def волна(дом: Path, проект: Path, участники: dict) -> Path:
    """Записать `subagents/agent-<имя>.jsonl` для каждого участника волны в
    ОДНОЙ сессии, с управляемым mtime.

    `участники`: имя -> (шаги, сдвиг_mtime_в_секундах_от_сейчас). Отрицательный
    сдвиг делает файл СТАРШЕ остальных. Именно этим и проверяется второй
    дефект: `max(mtime)` выбрал бы случайный файл волны, а не застрявший, —
    здесь застрявшему намеренно даётся САМЫЙ старый штамп, а идущим — более
    свежие, вплоть до нулевого сдвига («самый свежий», и он смирно идёт).
    """
    корень = подставной_дом(дом, проект)
    subagents = корень / "сессия-1" / "subagents"
    subagents.mkdir(parents=True, exist_ok=True)
    сейчас = time.time()
    for имя, (шаги_, сдвиг) in участники.items():
        f = журнал(subagents / f"agent-{имя}.jsonl", шаги_)
        t = сейчас + сдвиг
        os.utime(f, (t, t))
    return корень


class ВолнаНесколькихЖурналов(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.дом = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_застрявший_ловится_даже_не_самым_свежим(self):
        """Ровно второй дефект. На старом коде `журнал()` берёт
        `max(mtime)` — здесь это agent-c, живой, — и проверка ни словом не
        упомянула бы застрявший agent-a. Он здесь самый СТАРЫЙ из троих."""
        проект = self.дом / "проект"
        проект.mkdir()
        застрявший = [bash("pytest tests/", True)] * 3 + [bash("ls")] * 4
        живой1 = [bash("pytest tests/")] * 8
        живой2 = [bash("pytest tests/")] * 8
        волна(self.дом, проект, {
            "a": (застрявший, -300),   # старше остальных на 5 минут
            "b": (живой1, -60),
            "c": (живой2, 0),          # САМЫЙ СВЕЖИЙ — и он не застрял
        })
        код, текст = запустить_stall(self.дом, проект)
        self.assertEqual(код, 1, текст)
        self.assertIn("ЗАСТРЯЛ", текст)
        self.assertIn("agent-a", текст)

    def test_все_идут_код_ноль_и_каждый_перечислен(self):
        проект = self.дом / "проект"
        проект.mkdir()
        участники = {c: ([bash("pytest tests/")] * 8, -i * 10)
                    for i, c in enumerate("abc")}
        волна(self.дом, проект, участники)
        код, текст = запустить_stall(self.дом, проект)
        self.assertEqual(код, 0, текст)
        for c in "abc":
            self.assertIn(f"agent-{c}", текст)

    def test_застрявший_перебивает_идущих_в_общем_коде(self):
        """Приоритет агрегата: «застрял хотя бы один» обязан быть виден,
        даже когда остальные в порядке — не средним, не большинством."""
        проект = self.дом / "проект"
        проект.mkdir()
        волна(self.дом, проект, {
            "a": ([bash("pytest tests/")] * 8, -20),
            "b": ([bash("pytest tests/")] * 8, -10),
            "c": ([bash("pytest tests/", True)] * 3 + [bash("ls")] * 4, 0),
        })
        код, текст = запустить_stall(self.дом, проект)
        self.assertEqual(код, 1, текст)

    def test_рано_не_маскирует_застрявшего(self):
        проект = self.дом / "проект"
        проект.mkdir()
        волна(self.дом, проект, {
            "a": ([bash("ls")] * 2, 0),
            "b": ([bash("pytest tests/", True)] * 3 + [bash("ls")] * 4, -5),
        })
        код, текст = запустить_stall(self.дом, проект)
        self.assertEqual(код, 1, текст)

    def test_рано_и_идёт_без_застрявших_даёт_ноль_а_не_два(self):
        """Один агент ещё не набрал шагов — это не повод объявить волну
        целиком «судить нечем», пока другой явно идёт нормально."""
        проект = self.дом / "проект"
        проект.mkdir()
        волна(self.дом, проект, {
            "a": ([bash("ls")] * 2, 0),
            "b": ([bash("pytest tests/")] * 8, -5),
        })
        код, текст = запустить_stall(self.дом, проект)
        self.assertEqual(код, 0, текст)

    def test_все_рано_код_два(self):
        проект = self.дом / "проект"
        проект.mkdir()
        волна(self.дом, проект, {"a": ([bash("ls")], 0),
                                 "b": ([bash("ls")] * 2, -5)})
        код, текст = запустить_stall(self.дом, проект)
        self.assertEqual(код, 2, текст)

    def test_agent_остаётся_одиночной_проверкой(self):
        """`--agent <id>` обязан остаться прежним однозначным путём, а не
        волновым выводом — таково явное требование не ломать его."""
        проект = self.дом / "проект"
        проект.mkdir()
        волна(self.дом, проект, {
            "a": ([bash("pytest tests/", True)] * 3 + [bash("ls")] * 4, -300),
            "b": ([bash("pytest tests/")] * 8, 0),
        })
        код, текст = запустить_stall(self.дом, проект, "--agent", "agent-b")
        self.assertEqual(код, 0, текст)
        self.assertNotIn("волна:", текст)


class Агрегировать(unittest.TestCase):

    def test_застрял_побеждает_идущих(self):
        self.assertEqual(st.агрегировать([{"код": 0}, {"код": 1}, {"код": 0}]), 1)

    def test_идёт_побеждает_рано(self):
        self.assertEqual(st.агрегировать([{"код": 2}, {"код": 0}]), 0)

    def test_пусто_или_всё_рано_даёт_нечем(self):
        self.assertEqual(st.агрегировать([]), 2)
        self.assertEqual(st.агрегировать([{"код": 2}, {"код": 2}]), 2)


# ---------------------------------------------------------------------------
# Сторож: фоновый опрос волны, пока оркестратор занят.
# ---------------------------------------------------------------------------
def _агент(имя: str, код: int, причины=()) -> dict:
    return {"агент": имя, "код": код, "причины": list(причины), "шагов": 10,
            "проверок": 1, "журнал": f"{имя}.jsonl", "почему": ""}


class ИтоговыйКодСторожа(unittest.TestCase):

    def test_пустая_история_нечем(self):
        self.assertEqual(wd._итоговый_код({}), 2)

    def test_все_идут_ноль(self):
        история = {"a": {"код": 0, "когда_либо_1": False},
                   "b": {"код": 0, "когда_либо_1": False}}
        self.assertEqual(wd._итоговый_код(история), 0)

    def test_когда_либо_застревал_держит_код_даже_после_починки(self):
        """Форензика: последний срез агента — код 0 (починился), но общий
        код волны обязан помнить, что застревание БЫЛО — иначе починка
        секундой раньше `--stop` стирает из отчёта само происшествие."""
        история = {"a": {"код": 0, "когда_либо_1": True}}
        self.assertEqual(wd._итоговый_код(история), 1)


class ИсторияПереходов(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.лог = Path(self.tmp.name) / "лог.log"

    def tearDown(self):
        self.tmp.cleanup()

    def _строки(self) -> list:
        return (self.лог.read_text("utf-8").splitlines()
               if self.лог.is_file() else [])

    def test_первое_появление_не_логируется(self):
        """Начало наблюдения — не переход: волна из пяти агентов не обязана
        оставить пять строк в логе в первую же секунду."""
        wd._обновить_историю({}, [_агент("agent-a", 0)], self.лог)
        self.assertEqual(self._строки(), [])

    def test_переход_в_застревание_логируется(self):
        история = wd._обновить_историю({}, [_агент("agent-a", 0)], self.лог)
        wd._обновить_историю(история, [_агент("agent-a", 1, ["петля: x"])], self.лог)
        строки = self._строки()
        self.assertEqual(len(строки), 1, строки)
        self.assertIn("ЗАСТРЯЛ agent-a", строки[0])

    def test_переход_из_застревания_логируется_а_повтор_кода_не_дублирует(self):
        история = wd._обновить_историю({}, [_агент("agent-a", 1, ["петля: x"])], self.лог)
        история = wd._обновить_историю(история, [_агент("agent-a", 1, ["петля: x"])],
                                      self.лог)
        wd._обновить_историю(история, [_агент("agent-a", 0)], self.лог)
        строки = self._строки()
        self.assertEqual(len(строки), 1, строки)  # повтор кода 1 не продублировал
        self.assertIn("БОЛЬШЕ НЕ ЗАСТРЯЛ agent-a", строки[0])

    def test_история_не_мутирует_переданный_словарь(self):
        """Правило проекта об иммутабельности: новая история — новый
        объект, старый остаётся нетронутым у вызывающего."""
        старая = {"agent-a": {"код": 0, "когда_либо_1": False}}
        копия = dict(старая)
        wd._обновить_историю(старая, [_агент("agent-a", 1, ["петля: x"])], self.лог)
        self.assertEqual(старая, копия)


class СрезИсключаетДоВолны(unittest.TestCase):
    """`_срез()` — то, чем `--serve` меряет каждый опрос: судит только то,
    чего не было в слепке, снятом ДО запуска сторожа."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.дом = Path(self.tmp.name)
        self._старый_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.дом)

    def tearDown(self):
        if self._старый_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._старый_home
        self.tmp.cleanup()

    def test_баз_слепок_исключает_файлы_из_прошлой_волны(self):
        проект = self.дом / "проект"
        проект.mkdir()
        волна(self.дом, проект, {"old": ([bash("ls")] * 8, 0)})
        вне_волны = set(wd._текущие(проект))  # слепок «до» — приём из --serve
        волна(self.дом, проект, {
            "old": ([bash("ls")] * 8, 0),
            "new": ([bash("pytest tests/", True)] * 3 + [bash("ls")] * 4, 0),
        })
        срез = wd._срез(проект, [], вне_волны)
        имена = {a["агент"] for a in срез["агенты"]}
        self.assertEqual(имена, {"agent-new"})
        self.assertEqual(срез["код"], 1)


class СторожЖивойЦикл(unittest.TestCase):
    """Сквозная проверка настоящим подпроцессом: `--serve`, реальный опрос
    на таймере, `--stop` через настоящий SIGTERM. Интервал — 1 секунда, а
    не боевые 90: тест обязан пройти за секунды, а не минуты."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.дом = Path(self.tmp.name)
        self.проект = self.дом / "проект"
        self.проект.mkdir()
        self._proc = None
        self._старый_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.дом)

    def tearDown(self):
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self._старый_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._старый_home
        self.tmp.cleanup()

    def _watchdog(self, *флаги: str) -> tuple:
        окружение = dict(os.environ, HOME=str(self.дом))
        p = subprocess.run([sys.executable, str(TOOL_WATCHDOG), str(self.проект),
                            *флаги], capture_output=True, text=True, timeout=30,
                           env=окружение)
        return p.returncode, p.stdout + p.stderr

    def _дождаться_живого(self, попыток: int = 20) -> tuple:
        код, текст = 4, ""
        for _ in range(попыток):
            код, текст = self._watchdog("--check")
            if код == 0:
                return код, текст
            time.sleep(0.25)
        return код, текст

    def test_ловит_застрявшего_не_самого_свежего_и_помнит_после_починки(self):
        окружение = dict(os.environ, HOME=str(self.дом))
        self._proc = subprocess.Popen(
            [sys.executable, str(TOOL_WATCHDOG), str(self.проект),
             "--serve", "--interval", "1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=окружение)

        код, текст = self._дождаться_живого()
        self.assertEqual(код, 0, f"сторож не поднялся: {текст}")

        # agent-a СТАРШЕ agent-b и застрял; agent-b — самый свежий и идёт.
        волна(self.дом, self.проект, {
            "a": ([bash("pytest tests/", True)] * 3 + [bash("ls")] * 4, -60),
            "b": ([bash("pytest tests/")] * 8, 0),
        })
        time.sleep(2.2)  # не меньше двух опросов при интервале в 1с

        self.assertIsNone(self._proc.poll(), "сторож упал во время опроса волны")

        снимок = json.loads((wd._состояние(self.проект) / "stall-watch.json")
                            .read_text("utf-8"))
        self.assertEqual(снимок["код"], 1, снимок)
        self.assertIn("agent-a", снимок["когда_либо_застревали"])

        # agent-a «чинится» — переписываем его журнал идущим.
        волна(self.дом, self.проект, {
            "a": ([bash("pytest tests/")] * 8, -60),
            "b": ([bash("pytest tests/")] * 8, 0),
        })
        time.sleep(1.5)

        код, текст = self._watchdog("--stop")
        self._proc = None  # --stop уже остановил процесс — tearDown его не трогает
        self.assertEqual(код, 1, текст)  # форензика: помнит, хоть сейчас и чисто
        self.assertIn("agent-a", текст)


class ЖивЛиСторожНаWindows(unittest.TestCase):
    """Вопрос «жив ли» не имеет права убивать того, о ком спрашивают.

    Сигнал 0 есть только у POSIX. На Windows `os.kill` не спрашивает, а
    завершает процесс: та же строка кода там превращает проверку живости
    чужого сторожа в его убийство. Охрана была в двух копиях этого кода и
    отсутствовала в единственной, которая едет к людям.

    Проверяется подстановкой: настоящей Windows тут нет и не было.
    """

    class _ОС:
        """Отвечает «Windows» на имя; `kill` звать здесь запрещено."""

        name = "nt"

        def kill(self, *a):
            raise AssertionError("os.kill позван на Windows — это убийство, "
                                 "а не вопрос")

        def __getattr__(self, имя):
            return getattr(os, имя)

    def test_на_windows_живость_не_зовёт_os_kill(self):
        было = wd.os
        wd.os = self._ОС()
        try:
            self.assertTrue(wd._alive(999_999),
                            "на Windows «не знаю» обязано читаться как «жив»")
        finally:
            wd.os = было


class СессияЭтажомВыше(unittest.TestCase):
    """Журналы сессии, открытой в РОДИТЕЛЬСКОЙ папке, принадлежат проекту.

    Ровно ночная находка. Сессия строила `…/Super стэк/superstack`, а открыта
    была в `…/Super стэк`: журналы легли в каталог по имени родительской
    папки, поиск шёл по имени самой папки проекта — и находил ноль при 68
    лежащих рядом. Сторож всю ночь показывал «агентов волны: 0».
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.дом = Path(self.tmp.name)
        self._старый_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.дом)

    def tearDown(self):
        if self._старый_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._старый_home
        self.tmp.cleanup()

    def test_журналы_родительской_сессии_видны_проекту(self):
        родитель = self.дом / "рабочее"
        проект = родитель / "проект"
        проект.mkdir(parents=True)
        волна(self.дом, родитель, {"a": ([bash("pytest tests/")] * 8, 0)})

        найдено = st.подагенты(проект)
        self.assertTrue(найдено, "журналы сессии, открытой этажом выше, "
                                 "не найдены — сторож снова слеп")
        self.assertEqual([f.name for f in найдено], ["agent-a.jsonl"])
        self.assertEqual(st.проверить_все(проект)["код"], 0)


class СторожНеТрогаетДеревоПроекта(unittest.TestCase):
    """Файлы сторожа живут вне проекта, а «ноль журналов» называет место.

    Снимок и pid писались в `.superstack/` проекта каждые 90 секунд — под
    прогоном планки это меняло проверяемое дерево. И отдельно: «агентов
    волны: 0» ничего не говорило о том, ГДЕ искали, а искали не там.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.дом = Path(self.tmp.name)
        self.проект = self.дом / "проект"
        self.проект.mkdir()
        self._proc = None

    def tearDown(self):
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self.tmp.cleanup()

    def _watchdog(self, *флаги: str) -> tuple:
        окружение = dict(os.environ, HOME=str(self.дом))
        p = subprocess.run([sys.executable, str(TOOL_WATCHDOG), str(self.проект),
                            *флаги], capture_output=True, text=True, timeout=30,
                           env=окружение)
        return p.returncode, p.stdout + p.stderr

    def test_состояние_вне_дерева_и_check_называет_место_поиска(self):
        окружение = dict(os.environ, HOME=str(self.дом))
        self._proc = subprocess.Popen(
            [sys.executable, str(TOOL_WATCHDOG), str(self.проект),
             "--serve", "--interval", "1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=окружение)

        код, текст = 4, ""
        for _ in range(20):
            код, текст = self._watchdog("--check")
            if код == 0:
                break
            time.sleep(0.25)
        self.assertEqual(код, 0, f"сторож не поднялся: {текст}")

        следы = sorted(str(f) for f in self.проект.rglob("stall-watch.*"))
        self.assertEqual(следы, [], "сторож пишет в дерево проекта под прогоном")

        self.assertIn("журналов субагентов найдено: 0", текст)
        self.assertIn(str(self.дом / ".claude" / "projects"), текст)

        код, текст = self._watchdog("--stop")
        self._proc = None
        self.assertEqual(код, 2, текст)
        self.assertIn("журналов субагентов видно 0", текст)
        self.assertEqual(sorted(self.проект.rglob("stall-watch.*")), [])


if __name__ == "__main__":
    unittest.main()
