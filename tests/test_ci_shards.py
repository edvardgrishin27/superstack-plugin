#!/usr/bin/env python3
"""Дорогая проверка обязана ДОХОДИТЬ до конца, а «не смог» — не зеленеть.

Две болезни, одна дверь.

ПЕРВАЯ. Ночной прогон поломок не завершался НИ РАЗУ. Четыре запуска подряд на
одном коммите сняли по таймауту примерно на середине набора; в последнем
журнале была начата 383-я поломка из 577, и сам прогон оценивал остаток ещё в
час. Двухчасовая работа каждый раз пропадала целиком: ни числа проверенных, ни
имени поломки, на которой встали. Поломок с тех пор стало больше, и одним
заданием набор не закроется никогда — сколько предел ни поднимай.

Лечится не пределом, а нарезкой: доли идут независимыми заданиями, каждая
пишет проверенное по ходу, а вердикт выносит СВЕДЕНИЕ. «Пятнадцать долей из
шестнадцати зелёные» — не «набор проверен», и разница обязана быть красной.

ВТОРАЯ. «Не смог проверить» превращалось в предупреждение. Сборка честно
писала, что схему официальным инструментом хоста проверить не смогла и карту с
планом сверить не смогла, — и заканчивалась зелёной, потому что падала только
на `fail`. Честная оговорка, которую итог не читает, не стоит ничего.

Лечится списком: ворота, чьё «не смог» — провал, названы в файле, и ворота, не
названные в нём вовсе, роняют сборку сами по себе. Отдельно — снимок плана,
без которого сверка была невоспроизводима нигде, кроме машины автора.

Герметичность: настоящий CI, сеть и домашний каталог здесь не читаются. Доли,
отчёты и рабочий процесс собираются в temp-дереве или читаются как текст.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import REPO, at  # noqa: E402

GAUNTLET = at("tools", "gauntlet.py")
STAMP = at("tools", "plan_stamp.py")
WORKFLOW = REPO / ".github" / "workflows" / "gates.yml"
ENV = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gt = _load("ss_gauntlet_shards", GAUNTLET)
st = _load("ss_plan_stamp_shards", STAMP)


def _муты(n: int) -> list:
    return [{"id": f"m.{i:03d}", "file": "src.py", "find": f"A{i}",
             "replace": f"B{i}", "why": "поломка для проверки разбиения"}
            for i in range(n)]


class Разбиение(unittest.TestCase):
    """Доли не пересекаются, вместе дают весь набор и не зависят от порядка."""

    ЧИСЛА = ((626, 16), (10, 3), (7, 7), (5, 9), (1, 1))

    def test_доли_не_пересекаются(self):
        for всего, долей in self.ЧИСЛА:
            муты = _муты(всего)
            видели = []
            for i in range(1, долей + 1):
                видели += [m["id"] for m in gt.shard_of(муты, i, долей)]
            self.assertEqual(len(видели), len(set(видели)),
                             f"поломка попала в две доли при {всего}/{долей}")

    def test_доли_вместе_дают_весь_набор(self):
        for всего, долей in self.ЧИСЛА:
            муты = _муты(всего)
            собрано = set()
            for i in range(1, долей + 1):
                собрано |= {m["id"] for m in gt.shard_of(муты, i, долей)}
            self.assertEqual(собрано, {m["id"] for m in муты},
                             f"часть набора не досталась никому при {всего}/{долей}")

    def test_доли_примерно_равны(self):
        """Перекос в разы вернул бы ту же болезнь: одна доля не уложится в
        предел, пока остальные пятнадцать простаивают."""
        размеры = [len(gt.shard_of(_муты(626), i, 16)) for i in range(1, 17)]
        self.assertLessEqual(max(размеры) - min(размеры), 1, размеры)

    def test_состав_доли_не_зависит_от_порядка_записей(self):
        """Перестановка строк в наборе поломок иначе тихо перекраивает
        разбиение: доля 3 вчера и доля 3 сегодня — разные множества, и сумма
        по разным прогонам перестаёт что-либо значить."""
        муты = _муты(40)
        прямо = [m["id"] for m in gt.shard_of(муты, 2, 5)]
        наоборот = [m["id"] for m in gt.shard_of(list(reversed(муты)), 2, 5)]
        self.assertEqual(прямо, наоборот)

    def test_мусор_в_номере_доли_это_отказ(self):
        """`--shard 1-16` иначе даёт «1 из 1» — полный прогон под именем доли,
        который уходит в тот же таймаут и отчитывается как одна из
        шестнадцати."""
        for плохо in ("1-16", "", "доля", "1/", "/16", "1/2/3"):
            with self.assertRaises(ValueError, msg=плохо):
                gt.parse_shard(плохо)

    def test_доли_вне_диапазона_не_бывает(self):
        for плохо in ("0/16", "17/16", "1/0", "-1/4"):
            with self.assertRaises(ValueError, msg=плохо):
                gt.parse_shard(плохо)

    def test_годная_запись_разбирается(self):
        self.assertEqual(gt.parse_shard("3/16"), (3, 16))
        self.assertEqual(gt.parse_shard(" 1 / 2 "), (1, 2))


class ФлагиДолей(unittest.TestCase):
    """Незнакомый или противоречивый вызов обязан отказывать кодом 3."""

    def _запуск(self, *argv):
        return subprocess.run([sys.executable, str(GAUNTLET), *argv],
                              capture_output=True, text=True, timeout=120,
                              env=ENV)

    def test_мусорная_доля_возвращает_три(self):
        p = self._запуск("--gate", "мутации", "--shard", "1-16")
        self.assertEqual(p.returncode, 3, p.stderr[-300:])

    def test_доля_и_поимённый_список_вместе_отвергаются(self):
        """Две выборки сразу дают отчёт, который нельзя сложить ни с чем:
        «доля 1 из 16» с одной поломкой внутри выглядит как доля целиком."""
        p = self._запуск("--gate", "мутации", "--shard", "1/16",
                         "--mutation", "что.нибудь")
        self.assertEqual(p.returncode, 3, p.stderr[-300:])

    def test_доля_без_значения_возвращает_три(self):
        p = self._запуск("--gate", "мутации", "--shard")
        self.assertEqual(p.returncode, 3, p.stderr[-300:])


class ПодставноеДерево(unittest.TestCase):
    """Маленький плагин, где ответ известен заранее."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plug = Path(self.tmp.name)
        self.addCleanup(setattr, gt, "PLUG", gt.PLUG)
        self.addCleanup(setattr, gt, "SHARD", gt.SHARD)
        self.addCleanup(setattr, gt, "PROGRESS", gt.PROGRESS)
        gt.PLUG = self.plug
        self.write("src.py", "A = 1\nB = 2\nC = 3\nD = 4\n")
        self.write("tests/test_src.py",
                   "from pathlib import Path\n\n\n"
                   "def test_all():\n"
                   "    t = (Path(__file__).resolve().parent.parent"
                   " / 'src.py').read_text()\n"
                   "    assert t == 'A = 1\\nB = 2\\nC = 3\\nD = 4\\n'\n")
        self.write("tests/mutations.json", json.dumps({"mutations": [
            {"id": f"m.{n}", "file": "src.py", "find": f"{n} = {i + 1}",
             "replace": f"{n} = 9", "why": f"убран механизм {n}"}
            for i, n in enumerate("ABCD")]}, ensure_ascii=False))

    def tearDown(self):
        gt.SHARD = None
        gt.PROGRESS = None

    def write(self, rel: str, text: str) -> Path:
        f = self.plug / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        return f


class ВоротаНаДоле(ПодставноеДерево):

    def test_доля_проверяет_только_свои_поломки(self):
        gt.SHARD = (1, 4)
        r = gt.gate_mutations()
        self.assertEqual(r["registered"], 1, r)
        self.assertEqual(r["executed"], 1, r)

    def test_доля_это_выборка_а_не_взятая_планка(self):
        """Доказано ровно то, что проверено: одна поломка из четырёх."""
        gt.SHARD = (2, 4)
        r = gt.gate_mutations()
        self.assertEqual(r["status"], "partial", r)
        self.assertTrue(r.get("subset"), r)
        self.assertEqual(r["shard"], {"index": 2, "of": 4}, r)

    def test_доля_называет_число_всего_набора(self):
        """Без числа всего набора сумму выполненных не с чем сравнить, и
        пятнадцать долей из шестнадцати складываются в зелёный итог."""
        gt.SHARD = (3, 4)
        r = gt.gate_mutations()
        self.assertEqual(r["registry_total"], 4, r)

    def test_полный_прогон_остался_полным(self):
        """Обратный контроль: разбиение не имеет права превратить обычный
        прогон в вечную выборку."""
        r = gt.gate_mutations()
        self.assertEqual(r["status"], "pass", r)
        self.assertFalse(r.get("subset"), r)
        self.assertEqual(r["registry_total"], 4, r)

    def test_доля_дописывает_итог_в_промежуточный_файл(self):
        файл = self.plug / "доли" / "доля-4.json"
        gt.SHARD, gt.PROGRESS = (4, 4), файл
        r = gt.gate_mutations()
        сохранено = json.loads(файл.read_text("utf-8"))
        self.assertEqual(сохранено["status"], r["status"])
        self.assertEqual(сохранено["shard"], {"index": 4, "of": 4})
        self.assertEqual(сохранено["registry_total"], 4)


class ПромежуточноеПереживаетОтмену(ПодставноеДерево):
    """Отменённое задание обязано оставлять то, что успело проверить.

    Четыре ночных прогона подряд снимали по таймауту, и каждый оставлял ровно
    ничего: журнал сборки прокручен, артефактов нет, и даже имя поломки, на
    которой встали, приходилось искать глазами в двух тысячах строк вывода.
    """

    def _стаб(self, поведение):
        орig = gt._sh
        gt._sh = поведение
        self.addCleanup(setattr, gt, "_sh", орig)

    def test_отчёт_пишется_до_каждой_поломки(self):
        файл = self.plug / "прогресс.json"
        gt.PROGRESS = файл
        снимки = []
        муты = json.loads((self.plug / "tests" / "mutations.json")
                          .read_text("utf-8"))["mutations"]

        def прогон(*a, **k):
            снимки.append(json.loads(файл.read_text("utf-8")))
            return (1, "")

        self._стаб(прогон)
        with contextlib.redirect_stderr(io.StringIO()):
            gt._mutate_all(муты, реестр=len(муты))
        self.assertEqual([с["current"] for с in снимки],
                         [m["id"] for m in муты])
        self.assertEqual([с["executed"] for с in снимки], [0, 1, 2, 3])

    def test_обрыв_оставляет_имя_поломки_на_которой_встали(self):
        файл = self.plug / "прогресс.json"
        gt.PROGRESS = файл
        муты = json.loads((self.plug / "tests" / "mutations.json")
                          .read_text("utf-8"))["mutations"]
        звонки = []

        def прогон(*a, **k):
            звонки.append(1)
            if len(звонки) == 3:
                raise KeyboardInterrupt("задание снято по таймауту")
            return (1, "")

        self._стаб(прогон)
        with contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(KeyboardInterrupt):
            gt._mutate_all(муты, реестр=len(муты))
        осталось = json.loads(файл.read_text("utf-8"))
        self.assertEqual(осталось["status"], "running", осталось)
        self.assertEqual(осталось["current"], муты[2]["id"], осталось)
        self.assertEqual(осталось["executed"], 2, осталось)

    def test_отчёт_кладётся_подменой_целиком(self):
        """Запись поверх оставила бы обрезанный JSON ровно в тот момент, ради
        которого он и пишется."""
        файл = self.plug / "прогресс.json"
        файл.write_text("x" * 4000, encoding="utf-8")
        gt.PROGRESS = файл
        gt._write_progress({"status": "running", "current": "m.A"})
        self.assertEqual(json.loads(файл.read_text("utf-8"))["current"], "m.A")
        self.assertFalse(list(файл.parent.glob("*.tmp")),
                         "временный файл остался рядом с отчётом")

    def test_без_флага_ничего_не_пишется(self):
        gt.PROGRESS = None
        gt._write_progress({"status": "running"})
        self.assertEqual(list(self.plug.glob("*.json")), [])


class Сведение(unittest.TestCase):
    """Зелено ТОЛЬКО когда сумма выполненных равна числу зарегистрированных."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.дир = Path(self.tmp.name)

    def доля(self, i: int, of: int = 3, **поля) -> Path:
        d = {"gate": "мутации", "status": "partial",
             "shard": {"index": i, "of": of}, "registry_total": 9,
             "registered": 3, "executed": 3, "caught": 3,
             "survived": [], "inconclusive": []}
        d.update(поля)
        f = self.дир / f"shard-{i}.json"
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return f

    def свести(self) -> dict:
        return gt.merge_shards(sorted(self.дир.glob("*.json")))

    def test_все_доли_на_месте_и_сумма_сходится(self):
        for i in (1, 2, 3):
            self.доля(i)
        r = self.свести()
        self.assertEqual(r["status"], "pass", r)
        self.assertIn("выполнено 9", r["detail"])

    def test_объявленный_пропуск_не_роняет_сведение(self):
        """Запись «вне поставки» доля откладывает и называет: сведение это читает."""
        self.доля(1)
        self.доля(2)
        self.доля(3, executed=2, caught=2, **{"вне_дерева": ["x"]})
        r = self.свести()
        self.assertEqual(r["status"], "pass", r)
        self.assertIn("объявлено вне поставки 1", r["detail"])

    def test_необъявленный_пропуск_роняет_сведение(self):
        """Недосчёт без объявления остаётся красным: молчаливый пропуск не лечится."""
        self.доля(1)
        self.доля(2)
        self.доля(3, executed=2, caught=2)
        r = self.свести()
        self.assertEqual(r["status"], "fail", r)

    def test_нехватка_доли_роняет_и_называет_номер(self):
        """«Остальные прошли» — самая дорогая формулировка в этом файле."""
        self.доля(1)
        self.доля(3)
        r = self.свести()
        self.assertEqual(r["status"], "fail", r)
        self.assertEqual(r["missing_shards"], [2], r)

    def test_оборванная_доля_роняет_и_названа_вместе_с_поломкой(self):
        for i in (1, 2):
            self.доля(i)
        self.доля(3, status="running", current="crew.stamp", executed=1)
        r = self.свести()
        self.assertEqual(r["status"], "fail", r)
        self.assertIn("crew.stamp", r["detail"])

    def test_недосчитанное_роняет_даже_когда_все_доли_отчитались(self):
        """Доля может закончиться, не проверив своё: якорь протух, файла нет,
        прогон не состоялся. Числа обязаны сойтись, а не «все ответили»."""
        for i in (1, 2):
            self.доля(i)
        self.доля(3, executed=1, caught=1)
        r = self.свести()
        self.assertEqual(r["status"], "fail", r)
        self.assertIn("выполнено 7", r["detail"])

    def test_выжившая_в_любой_доле_роняет(self):
        for i in (1, 2):
            self.доля(i)
        self.доля(3, status="fail", executed=3, caught=2,
                  survived=[{"id": "bar.subset", "why": "выборка сошла за планку"}])
        r = self.свести()
        self.assertEqual(r["status"], "fail", r)
        self.assertIn("bar.subset", json.dumps(r, ensure_ascii=False))

    def test_несостоявшийся_прогон_не_зеленеет(self):
        for i in (1, 2):
            self.доля(i)
        self.доля(3, status="unknown", executed=3, caught=3,
                  inconclusive=[{"id": "x.timeout", "why": "не уложился в таймаут"}])
        r = self.свести()
        self.assertNotEqual(r["status"], "pass", r)
        self.assertIn("x.timeout", r["detail"])

    def test_доли_из_разных_наборов_роняют(self):
        """Доли, посчитанные на разных коммитах, складывать нельзя: сумма
        сойдётся, а проверен будет не тот набор."""
        self.доля(1)
        self.доля(2)
        self.доля(3, registry_total=12)
        r = self.свести()
        self.assertEqual(r["status"], "fail", r)

    def test_доля_без_номера_роняет(self):
        self.доля(1)
        self.доля(2)
        self.доля(3, shard=None)
        self.assertEqual(self.свести()["status"], "fail")

    def test_битое_число_долей_не_вешает_сведение(self):
        """Перебор от единицы до миллиарда — это не «медленно», это молчаливое
        зависание вместо отказа."""
        self.доля(1, shard={"index": 1, "of": 10 ** 9})
        self.assertEqual(self.свести()["status"], "fail")

    def test_пустой_каталог_это_не_успех(self):
        r = self.свести()
        self.assertEqual(r["status"], "unknown", r)

    def test_битая_доля_это_не_успех(self):
        self.доля(1)
        self.доля(2)
        (self.дир / "shard-3.json").write_text("{не json", encoding="utf-8")
        self.assertEqual(self.свести()["status"], "unknown")

    def test_код_возврата_ненулевой_когда_не_сведено(self):
        """Единственное, что читает сборка, — код возврата."""
        self.доля(1)
        p = subprocess.run([sys.executable, str(GAUNTLET), "--merge", str(self.дир)],
                           capture_output=True, text=True, timeout=120, env=ENV)
        self.assertNotEqual(p.returncode, 0, p.stdout[-400:])

    def test_код_возврата_ноль_когда_сведено(self):
        for i in (1, 2, 3):
            self.доля(i)
        p = subprocess.run([sys.executable, str(GAUNTLET), "--merge", str(self.дир)],
                           capture_output=True, text=True, timeout=120, env=ENV)
        self.assertEqual(p.returncode, 0, p.stdout[-400:] + p.stderr[-400:])


class ОбязательныеВорота(unittest.TestCase):
    """«Не смог проверить» у названных ворот — провал, а не предупреждение."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.список = Path(self.tmp.name) / "required-gates.json"
        self.список.write_text(json.dumps(
            {"required": [{"gate": "план", "why": "считается кодом всегда"}],
             "advisory": [{"gate": "манифест", "why": "нужен двоичный файл хоста"}]},
            ensure_ascii=False), encoding="utf-8")

    def отчёт(self, *ворота) -> dict:
        return {"bar": "superstack", "done": False,
                "gates": [{"gate": g, "status": s, "detail": "деталь"}
                          for g, s in ворота]}

    def test_неизвестное_в_обязательных_роняет(self):
        r = gt.gate_policy(self.отчёт(("план", "unknown"), ("манифест", "pass")),
                           self.список)
        self.assertEqual(r["status"], "fail", r)
        self.assertTrue(any("план" in e for e in r["errors"]), r)

    def test_неизвестное_в_справочных_остаётся_предупреждением(self):
        r = gt.gate_policy(self.отчёт(("план", "pass"), ("манифест", "unknown")),
                           self.список)
        self.assertEqual(r["status"], "pass", r)
        self.assertTrue(any("манифест" in w for w in r["warnings"]), r)

    def test_провал_роняет_независимо_от_списка(self):
        r = gt.gate_policy(self.отчёт(("манифест", "fail"), ("план", "pass")),
                           self.список)
        self.assertEqual(r["status"], "fail", r)

    def test_ворота_вне_списка_роняют(self):
        """Новые ворота иначе молча попадают в мягкую корзину, и список
        устаревает тише, чем портится."""
        r = gt.gate_policy(self.отчёт(("план", "pass"), ("новые", "unknown")),
                           self.список)
        self.assertEqual(r["status"], "fail", r)
        self.assertIn("новые", r["detail"])

    def test_битый_список_это_не_успех(self):
        self.список.write_text("{не json", encoding="utf-8")
        r = gt.gate_policy(self.отчёт(("план", "pass")), self.список)
        self.assertEqual(r["status"], "unknown", r)

    def test_зелёный_отчёт_проходит(self):
        r = gt.gate_policy(self.отчёт(("план", "pass"), ("манифест", "pass")),
                           self.список)
        self.assertEqual(r["status"], "pass", r)


class СписокВРепозитории(unittest.TestCase):
    """Список лежит в файле, и в нём названы ВСЕ ворота планки."""

    def setUp(self):
        self.свод = json.loads(
            (REPO / "data" / "required-gates.json").read_text("utf-8"))

    def test_каждое_ворота_планки_классифицировано(self):
        названы = ({g["gate"] for g in self.свод["required"]}
                   | {g["gate"] for g in self.свод["advisory"]})
        self.assertEqual(названы, {имя for имя, _ in gt.GATES})

    def test_у_каждой_записи_есть_причина(self):
        немые = [g["gate"] for g in self.свод["required"] + self.свод["advisory"]
                 if len(g.get("why", "")) < 20]
        self.assertEqual(немые, [], f"записи без причины: {немые}")

    def test_считающееся_кодом_названо_обязательным(self):
        """Ворота, которым для работы нужен только этот клон, не имеют права
        зеленеть предупреждением."""
        обязательные = {g["gate"] for g in self.свод["required"]}
        for имя in ("набор", "план", "сверка", "проводка", "правила", "вес"):
            self.assertIn(имя, обязательные)

    def test_справочное_объясняет_чего_не_хватает(self):
        for g in self.свод["advisory"]:
            self.assertIn("нет", g["why"], g)


class СнимокПлана(unittest.TestCase):
    """Сверка обязана быть воспроизводима в чужом клоне."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.дир = Path(self.tmp.name)
        self.план = self.дир / "план.md"
        self.план.write_text(
            "# план\n\n## первый\nтайное имя заказчика\n\n## второй\nещё текст\n",
            encoding="utf-8")

    def test_снимок_не_несёт_текста_плана(self):
        s = st.build_snapshot(self.план)
        сырое = json.dumps(s, ensure_ascii=False)
        self.assertNotIn("тайное имя заказчика", сырое)
        self.assertNotIn("первый", сырое)

    def test_снимок_несёт_отпечаток_плана_и_числа(self):
        s = st.build_snapshot(self.план)
        self.assertEqual(s["plan_digest"], st.digest(self.план))
        self.assertEqual(s["bytes"], self.план.stat().st_size)
        self.assertGreaterEqual(len(s["sections"]), 2)

    def test_правка_снимка_помимо_отпечатка_отвергается(self):
        путь = self.дир / "plan-snapshot.json"
        st.write_snapshot(self.план, путь)
        s = json.loads(путь.read_text("utf-8"))
        s["plan_digest"] = "sha256:" + "0" * 64
        путь.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
        r = st.read_snapshot(путь)
        self.assertFalse(r["ok"], r)

    def test_целый_снимок_читается(self):
        путь = self.дир / "plan-snapshot.json"
        st.write_snapshot(self.план, путь)
        self.assertTrue(st.read_snapshot(путь)["ok"])


class СверкаБезПлана(unittest.TestCase):
    """Плана нет в дереве — сверка идёт по снимку рядом с картой.

    Прежде здесь было честное «не смог», которое итог сборки читал как
    предупреждение. Ограничение назвали, зелёный статус остался, и читалось
    это как «все ворота прошли».
    """

    КАРТА = {"schema": "superstack.plan-coverage.v1", "source": "тест",
             "mechanisms": [{"id": "X", "layer": "тест", "mechanism": "проверяемое",
                             "evidence": {"file": "README.md",
                                          "contains": "SUPERSTACK"}}]}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.дир = Path(self.tmp.name)
        self.план = self.дир / "план.md"
        self.план.write_text("## слой\nвосемь слоёв агентной ОС\n", encoding="utf-8")
        self.карта = self.дир / "plan-coverage.json"
        self.карта.write_text(json.dumps(self.КАРТА, ensure_ascii=False),
                              encoding="utf-8")
        self._было = os.environ.get(st.PLAN_ENV)
        os.environ[st.PLAN_ENV] = str(self.план)
        self.addCleanup(self._вернуть)
        p = subprocess.run([sys.executable, str(STAMP), "--by", "проверяющая модель",
                            "--map", str(self.карта)],
                           capture_output=True, text=True, timeout=60,
                           env={**os.environ, st.PLAN_ENV: str(self.план)})
        self.assertEqual(p.returncode, 0, p.stderr)

    def _вернуть(self):
        if self._было is None:
            os.environ.pop(st.PLAN_ENV, None)
        else:
            os.environ[st.PLAN_ENV] = self._было

    def test_отметка_кладёт_снимок_рядом_с_картой(self):
        self.assertTrue((self.дир / st.SNAPSHOT_NAME).is_file(),
                        "снимок не положен рядом с картой — в клоне сверять нечем")

    def test_без_плана_сверка_идёт_по_снимку(self):
        self.план.unlink()
        v = gt.gate_reconciled(self.карта)
        self.assertEqual(v["status"], "pass", v)
        self.assertIn("снимк", v["detail"])

    def test_без_плана_и_без_снимка_это_не_успех(self):
        self.план.unlink()
        (self.дир / st.SNAPSHOT_NAME).unlink()
        self.assertEqual(gt.gate_reconciled(self.карта)["status"], "unknown")

    def test_снимок_от_другого_плана_не_проходит(self):
        """Снимок и отметка обязаны говорить об одном плане, иначе
        воспроизводимость сводится к «в файле написано, что сходится»."""
        чужой = self.дир / "чужой.md"
        чужой.write_text("## другое\nсовсем другой план\n", encoding="utf-8")
        st.write_snapshot(чужой, self.дир / st.SNAPSHOT_NAME)
        self.план.unlink()
        v = gt.gate_reconciled(self.карта)
        self.assertEqual(v["status"], "unknown", v)

    def test_план_на_месте_проверяется_строго(self):
        """Обратный контроль: снимок не имеет права заменить настоящий план
        там, где он есть."""
        self.план.write_text("## слой\nвосемь слоёв и девятый\n", encoding="utf-8")
        v = gt.gate_reconciled(self.карта)
        self.assertEqual(v["status"], "unknown", v)
        self.assertIn("правил", v["detail"])


class СнимокВРепозитории(unittest.TestCase):

    def setUp(self):
        self.путь = REPO / "data" / st.SNAPSHOT_NAME
        self.снимок = json.loads(self.путь.read_text("utf-8"))

    def test_снимок_сходится_с_отметкой_в_карте(self):
        карта = json.loads((REPO / "data" / "plan-coverage.json").read_text("utf-8"))
        self.assertEqual(self.снимок["plan_digest"],
                         карта["reconciled"]["digest"])

    def test_снимок_цел(self):
        self.assertTrue(st.read_snapshot(self.путь)["ok"])

    def test_в_снимке_нет_личных_путей(self):
        текст = self.путь.read_text("utf-8")
        self.assertIsNone(re.search(r"/Users/(?!me\b)", текст))
        self.assertNotIn(str(Path.home()), текст)


class РабочийПроцесс(unittest.TestCase):
    """Читается как текст: pyyaml в CI не ставится, и ставить его ради
    проверки — заводить зависимость там, где хватает строки."""

    УСЛОВИЕ = ("github.event_name == 'schedule' || "
               "github.event_name == 'workflow_dispatch' || "
               "startsWith(github.ref, 'refs/tags/')")

    def setUp(self):
        self.текст = WORKFLOW.read_text("utf-8")

    def test_знаменатель_доли_совпадает_с_матрицей(self):
        """Матрица из шестнадцати заданий с `--shard i/12` проверила бы три
        четверти набора и отчиталась бы как полная."""
        матрица = re.search(r"^\s*shard: \[(.+)\]$", self.текст, re.M)
        self.assertIsNotNone(матрица, "матрицы долей в рабочем процессе нет")
        номера = [int(x) for x in матрица.group(1).split(",")]
        знаменатели = set(re.findall(r"--shard \$\{\{ matrix\.shard \}\}/(\d+)",
                                     self.текст))
        self.assertEqual(len(знаменатели), 1, знаменатели)
        self.assertEqual(номера, list(range(1, int(знаменатели.pop()) + 1)))

    def test_рабочий_процесс_разбирается_как_yaml(self):
        """Файл, который хост не разберёт, падает за ноль секунд, не коснувшись
        кода, — и красный значок читается как поломка продукта."""
        try:
            import yaml
        except ImportError:
            self.skipTest("pyyaml не установлен — разбор проверяется там, где он есть")
        d = yaml.safe_load(self.текст)
        self.assertEqual(sorted(d["jobs"]),
                         ["floor", "gates", "merged", "mutations", "suite"])
        self.assertEqual(d["jobs"]["merged"]["needs"], "mutations")

    def test_у_доли_есть_свой_предел_времени(self):
        self.assertRegex(self.текст, r"(?m)^\s*timeout-minutes: \d+$")

    def test_доли_не_снимают_друг_друга(self):
        """Без этого первая же красная доля отменяет пятнадцать остальных, и
        сведение получает пятнадцать пропавших артефактов вместо результата."""
        self.assertIn("fail-fast: false", self.текст)

    def test_выгрузка_доли_переживает_отмену(self):
        self.assertIn("if: always()", self.текст)

    def test_переменные_оболочки_только_латиницей(self):
        """`код=0` для bash — не присваивание, а попытка запустить команду с
        таким именем: имена переменных допускаются только ASCII. Проверка
        `bash -n` этого НЕ ловит — строка синтаксически законна, — и шаг
        падал бы уже в CI, на прогоне, ради которого всё и затевалось."""
        for m in re.finditer(r"(?m)^\s*([^\s=]+)=", self.текст):
            self.assertRegex(m.group(1), r"^[A-Za-z_][A-Za-z0-9_]*$",
                             f"имя переменной оболочки не ASCII: {m.group(1)}")

    def test_имена_артефактов_ascii(self):
        """Идентификаторы заданий уже однажды отвергал хост за не-ASCII, и
        рабочий процесс падал за ноль секунд, не коснувшись кода."""
        for имя in re.findall(r"^\s*name: (\S*\$\{\{ matrix\.shard \}\}\S*)$",
                              self.текст, re.M):
            self.assertTrue(имя.isascii(), имя)

    def test_сведение_запускается_там_же_где_доли(self):
        """Иначе сведение либо не запустится ночью, либо запустится на каждый
        push и покраснеет на пустом каталоге."""
        self.assertEqual(self.текст.count(self.УСЛОВИЕ), 2,
                         "условие запуска долей и сведения разошлось")

    def test_сведение_зовёт_планку(self):
        """Вердикт по набору выносит планка, а не строка в рабочем процессе."""
        self.assertIn("--merge shards", self.текст)

    def test_сведение_идёт_даже_когда_доля_упала(self):
        self.assertIn("!cancelled()", self.текст)

    def test_доля_не_глушится_безусловным_true(self):
        """`|| true` вокруг прогона доли прячет и падение самой планки."""
        for строка in self.текст.splitlines():
            if "--shard" in строка:
                self.assertNotIn("|| true", строка)

    def test_итог_ворот_считает_планка_а_не_рабочий_процесс(self):
        self.assertIn("--ci ворота.json", self.текст)
        self.assertNotIn("::warning::", self.текст,
                         "разбор вернулся в YAML — там его не проверяет ни один тест")


if __name__ == "__main__":
    unittest.main()
