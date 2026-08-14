#!/usr/bin/env python3
"""Проводка: инструмент обязан быть достижим, а не просто существовать.

Единственные ворота, которые ловят болезнь этого проекта в его собственном
исходнике, и повод для них — не теория.

  · `hooks.json` объявлял 34 хука при девяти подключённых;
  · `learn.py` был написан целиком — планка из трёх условий, маршрутизация в
    три адресата, слияние похожего — и не вызывался НИКЕМ;
  · за один заход сюда добавилось шесть инструментов, до которых не дотягивался
    ни один скилл;
  · а два вызова, которые в сборочном скилле БЫЛИ, указывали в пустоту: скилл
    живёт в `superstack-build` и звал `$CLAUDE_PLUGIN_ROOT/tools/verify.py`,
    хотя `verify.py` лежит в `superstack-guard`.

Последнее — самый тихий из четырёх отказов. Он выглядит подключённым и падает
«нет такого файла», то есть как поломка окружения; человек идёт чинить
установку. Тесты его не видят: файл на месте, функции работают, набор зелёный.

Ворота отвечают на оба вопроса разом: дотянется ли кто-нибудь до инструмента и
существует ли путь, которым его зовут.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from paths import REPO

_g = importlib.util.spec_from_file_location("gauntlet_wiring",
                                            REPO / "tools" / "gauntlet.py")
gt = importlib.util.module_from_spec(_g)
_g.loader.exec_module(gt)

WHERE = REPO / "plugins" / "superstack-build" / "tools" / "where.py"
_w = importlib.util.spec_from_file_location("superstack_where", WHERE)
wh = importlib.util.module_from_spec(_w)
_w.loader.exec_module(wh)


class Tree(unittest.TestCase):
    """Поддельное дерево пакетов — ворота смотрят на него, а не на настоящее."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._orig = gt.PLUG
        gt.PLUG = self.root
        self.addCleanup(setattr, gt, "PLUG", self._orig)

    def tool(self, plug: str, name: str, body: str = "x = 1\n"):
        d = self.root / "plugins" / plug / "tools"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(body, encoding="utf-8")

    def skill(self, plug: str, body: str):
        d = self.root / "plugins" / plug / "skills" / "s"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(body, encoding="utf-8")

    def entrypoints(self, *pairs):
        d = self.root / "data"
        d.mkdir(parents=True, exist_ok=True)
        (d / "entrypoints.json").write_text(json.dumps(
            {"entrypoints": [{"tool": t, "why": w} for t, w in pairs]},
            ensure_ascii=False), encoding="utf-8")


class TestUnreachableIsRed(Tree):

    def test_tool_nobody_calls_is_red(self):
        self.tool("a", "alive.py")
        self.tool("a", "orphan.py")
        self.skill("a", "зовём alive.py и всё")
        v = gt.gate_wiring()
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("orphan.py" in p for p in v["dead"]), v)

    def test_everything_reachable_passes(self):
        self.tool("a", "alive.py")
        self.skill("a", "зовём alive.py")
        self.assertEqual(gt.gate_wiring()["status"], "pass")

    def test_reachability_is_transitive(self):
        """Скилл зовёт первый, первый зовёт второй — достижимы оба."""
        self.tool("a", "first.py", "import second  # second.py\n")
        self.tool("a", "second.py")
        self.skill("a", "зовём first.py")
        self.assertEqual(gt.gate_wiring()["status"], "pass")

    def test_a_closed_pair_is_still_dead(self):
        """`prove.py` и `state.py` ссылались друг на друга и ни на что больше.
        Взаимная ссылка выглядит связью и достижимости не даёт."""
        self.tool("a", "prove.py", "# зовёт state.py\n")
        self.tool("a", "state.py", "# зовёт prove.py\n")
        self.tool("a", "alive.py")
        self.skill("a", "зовём alive.py")
        v = gt.gate_wiring()
        self.assertEqual(v["status"], "fail")
        self.assertEqual(len(v["dead"]), 2)

    def test_hooks_and_agents_count_as_entry_points(self):
        for kind, fname, body in (("hooks", "h.sh", "python3 x/tools/byhook.py"),
                                  ("agents", "a.md", "зовёт byagent.py")):
            with self.subTest(kind=kind):
                r = Path(tempfile.mkdtemp(dir=self.root))
                gt.PLUG = r
                (r / "plugins" / "p" / "tools").mkdir(parents=True)
                name = "byhook.py" if kind == "hooks" else "byagent.py"
                (r / "plugins" / "p" / "tools" / name).write_text("x=1\n",
                                                                 encoding="utf-8")
                d = r / "plugins" / "p" / kind
                d.mkdir(parents=True)
                (d / fname).write_text(body, encoding="utf-8")
                self.assertEqual(gt.gate_wiring()["status"], "pass")


class TestDeclaredEntryPointsAreAllowedButNamed(Tree):
    """Список нужен не ради послаблений, а ради разницы между «решили так» и
    «забыли»: через месяц её иначе не отличить."""

    def test_declared_tool_is_not_dead(self):
        self.tool("a", "alive.py")
        self.tool("a", "byhand.py")
        self.skill("a", "зовём alive.py")
        self.entrypoints(("byhand.py", "запускает человек вокруг изменения"))
        self.assertEqual(gt.gate_wiring()["status"], "pass")

    def test_declaring_a_nonexistent_tool_is_unknown(self):
        """Список, разошедшийся с деревом, тихо разрешает то, чего нет, и
        перестаёт быть проверяемым."""
        self.tool("a", "alive.py")
        self.skill("a", "зовём alive.py")
        self.entrypoints(("исчезнувший.py", "когда-то был"))
        v = gt.gate_wiring()
        self.assertEqual(v["status"], "unknown")
        self.assertIn("несуществующие", v["detail"])

    def test_unparsable_list_is_unknown_not_pass(self):
        self.tool("a", "alive.py")
        self.skill("a", "зовём alive.py")
        (self.root / "data").mkdir(parents=True, exist_ok=True)
        (self.root / "data" / "entrypoints.json").write_text("{сломано",
                                                            encoding="utf-8")
        self.assertEqual(gt.gate_wiring()["status"], "unknown")


class TestAPathThatCannotExistIsRed(Tree):
    """Самый тихий отказ: выглядит подключённым, падает как поломка окружения."""

    def test_calling_a_sibling_tool_through_own_root_is_red(self):
        self.tool("build", "own.py")
        self.tool("guard", "verify.py")
        self.skill("build", 'python3 "$CLAUDE_PLUGIN_ROOT/tools/verify.py" .')
        self.skill("guard", "зовём verify.py")
        v = gt.gate_wiring()
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("verify.py" in w for w in v["wrong_paths"]), v)

    def test_calling_own_tool_through_own_root_is_fine(self):
        self.tool("build", "own.py")
        self.skill("build", 'python3 "$CLAUDE_PLUGIN_ROOT/tools/own.py" .')
        self.assertEqual(gt.gate_wiring()["status"], "pass")

    def test_braced_form_is_caught_too(self):
        self.tool("build", "own.py")
        self.tool("guard", "verify.py")
        self.skill("build", 'python3 "${CLAUDE_PLUGIN_ROOT}/tools/verify.py" .')
        self.skill("guard", "зовём verify.py")
        self.assertEqual(gt.gate_wiring()["status"], "fail")

    def test_wrong_path_outranks_the_dead_list(self):
        """Сначала сообщается о пути в пустоту: он объясняет, почему инструмент
        выглядит подключённым и всё равно не работает."""
        self.tool("build", "own.py")
        self.tool("build", "orphan.py")
        self.tool("guard", "verify.py")
        self.skill("build", 'python3 "$CLAUDE_PLUGIN_ROOT/tools/verify.py" .')
        self.skill("guard", "зовём verify.py")
        v = gt.gate_wiring()
        self.assertIn("wrong_paths", v)
        self.assertNotIn("dead", v)


class TestTheResolverFindsToolsAcrossPlugins(unittest.TestCase):
    """Предположение о раскладке пакетов живёт в ОДНОМ месте, а не в двадцати
    строках двадцати скиллов."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plugins = Path(self.tmp.name) / "plugins"
        for p, names in (("build", ["own.py", "where.py"]),
                         ("guard", ["verify.py"]),
                         ("core", ["render_html.py"])):
            d = self.plugins / f"superstack-{p}" / "tools"
            d.mkdir(parents=True)
            for n in names:
                (d / n).write_text("x = 1\n", encoding="utf-8")
        self.start = self.plugins / "superstack-build"

    def test_finds_a_tool_in_its_own_plugin(self):
        self.assertEqual(wh.find("own.py", self.start).name, "own.py")

    def test_finds_a_tool_in_a_sibling_plugin(self):
        hit = wh.find("verify.py", self.start)
        self.assertIsNotNone(hit)
        self.assertIn("superstack-guard", str(hit))

    def test_finds_a_tool_two_plugins_over(self):
        self.assertIsNotNone(wh.find("render_html.py", self.start))

    def test_missing_tool_returns_none_not_a_guess(self):
        """Молчаливая пустая строка дала бы `python3 ""` — ещё одну ошибку не
        по адресу, и человек пошёл бы искать её в третьем месте."""
        self.assertIsNone(wh.find("нетакого.py", self.start))

    def test_own_plugin_wins_over_a_sibling_with_the_same_name(self):
        (self.plugins / "superstack-guard" / "tools" / "own.py").write_text(
            "x = 2\n", encoding="utf-8")
        self.assertIn("superstack-build", str(wh.find("own.py", self.start)))

    def test_every_copy_of_the_resolver_is_identical(self):
        """Резолвер лежит по копии в каждом пакете со скиллами, и это не
        небрежность, а единственный доступный вариант.

        `${CLAUDE_PLUGIN_ROOT}` — единственный надёжный якорь, который есть у
        скилла; дотянуться до соседа можно либо зная раскладку пакетов, либо
        имея локальный файл, который её знает. Второе строго лучше: раскладка
        живёт в коде, который проверяется, а не в прозе, разбросанной по
        скиллам, где ошибка молчит.

        Цена варианта — расхождение копий, и оно ловится здесь. Первая же
        правка одной из них без второй сделает часть скиллов ищущими по старым
        правилам, и обнаружится это только у человека при установке.
        """
        copies = sorted(REPO.glob("plugins/*/tools/where.py"))
        self.assertGreaterEqual(len(copies), 2, "копий резолвера меньше двух")
        first = copies[0].read_bytes()
        for c in copies[1:]:
            with self.subTest(copy=str(c.relative_to(REPO))):
                self.assertEqual(c.read_bytes(), first,
                                 "копии резолвера разошлись — часть скиллов "
                                 "ищет инструменты по другим правилам")

    def test_a_plugin_reaching_for_a_sibling_tool_has_the_resolver(self):
        """Резолвер нужен там, где скиллы тянутся ЗА ПРЕДЕЛЫ своего пакета.

        Первая версия этого теста требовала резолвер у каждого пакета со
        скиллами и покраснела на `superstack-control`, который зовёт только три
        своих инструмента. Требовать резолвер там значило бы завести четвёртую
        копию ради ничего — и ещё одно место, где копии разойдутся.

        Правило точное: тянешься к соседу — имей резолвер; не тянешься — не
        имей. Пакет без него, но с сиблинг-вызовом, вынужден строить путь
        руками, то есть повторить ровно ту ошибку, ради которой резолвер и
        появился.
        """
        import re
        for skills in sorted(REPO.glob("plugins/*/skills")):
            plug = skills.parent
            own = {p.name for p in (plug / "tools").glob("*.py")}
            called = set()
            for s in skills.rglob("*.md"):
                t = s.read_text("utf-8")
                called |= set(re.findall(r"\$\(T ([\w.]+\.py)\)", t))
                called |= set(re.findall(
                    r"\$\{?CLAUDE_PLUGIN_ROOT\}?/tools/([\w.]+\.py)", t))
            foreign = called - own
            with self.subTest(plugin=plug.name):
                if foreign:
                    self.assertTrue(
                        (plug / "tools" / "where.py").is_file(),
                        f"{plug.name} зовёт чужие инструменты "
                        f"({', '.join(sorted(foreign)[:4])}) и не имеет резолвера")

    def _cache(self, name, *pairs):
        """Установленная раскладка: `<пакет>/<версия>/tools/`.

        Искомый `verify.py` кладётся ТОЛЬКО соседям. Первая версия этой фикстуры
        давала его и своему пакету — тогда `find` возвращала свой файл, путь
        содержал нужную строку, и тест зеленел, ничего не проверив.
        """
        cache = Path(self.tmp.name) / name
        for plug, ver in pairs:
            d = cache / f"superstack-{plug}" / ver
            (d / "tools").mkdir(parents=True)
            if plug != "build":
                (d / "tools" / "verify.py").write_text("x = 1\n", encoding="utf-8")
            (d / ".claude-plugin").mkdir()
            (d / ".claude-plugin" / "plugin.json").write_text(
                '{"name": "superstack-%s", "version": "%s"}' % (plug, ver),
                encoding="utf-8")
        return cache

    def test_a_stale_version_left_in_the_cache_does_not_win(self):
        """Обновление плагина не удаляет прежнюю версию из кэша.

        Измерено на живой установке сразу после подъёма семи пакетов до 0.2.1:
        рядом лежали `superstack-guard/0.2.0/` и `.../0.2.1/`, резолвер брал
        `sorted(hits)[0]` и возвращал 0.2.0. Скилл свежей версии работал по
        инструментам прежней — и ничего при этом не падало.

        Это худший вид отказа: правка выглядит не применившейся, и человек идёт
        искать её у себя.
        """
        cache = self._cache("cache", ("build", "0.2.1"), ("guard", "0.2.0"),
                            ("guard", "0.2.1"))
        hit = wh.find("verify.py", cache / "superstack-build" / "0.2.1")
        self.assertIsNotNone(hit)
        self.assertIn("0.2.1", str(hit),
                      "резолвер выбрал прежнюю версию соседа — свежий скилл "
                      "работает по старым инструментам, и это молчит")

    def test_without_a_matching_version_the_newest_wins(self):
        """Своей версии среди соседей нет — берётся старшая, а не первая по
        алфавиту. Иначе 0.10.0 проиграет 0.9.0, что верно как строка и неверно
        как версия."""
        cache = self._cache("c2", ("build", "0.3.0"), ("guard", "0.9.0"),
                            ("guard", "0.10.0"))
        hit = wh.find("verify.py", cache / "superstack-build" / "0.3.0")
        self.assertIn("0.10.0", str(hit))

    def test_the_repo_layout_without_version_dirs_still_resolves(self):
        """Обратный контроль: выбор версии не должен ломать раскладку
        репозитория, где каталога версии нет вовсе."""
        hit = wh.find("verify.py", self.start)
        self.assertIsNotNone(hit)
        self.assertIn("superstack-guard", str(hit))

    def test_the_real_repo_resolves_every_tool_the_skills_call(self):
        """Обратный контроль на живом дереве: каждый инструмент, названный в
        скиллах через резолвер, обязан находиться. Иначе проверка выше
        доказывает работу резолвера на выдуманной раскладке."""
        import re
        start = REPO / "plugins" / "superstack-build"
        called = set()
        for s in REPO.glob("plugins/*/skills/**/*.md"):
            called |= set(re.findall(r'\$\(T ([\w.]+\.py)\)', s.read_text("utf-8")))
        self.assertTrue(called, "скиллы не зовут инструменты через резолвер")
        for name in sorted(called):
            with self.subTest(tool=name):
                self.assertIsNotNone(wh.find(name, start), name)


if __name__ == "__main__":
    unittest.main()


class TestTheOwnerMapMatchesTheTree(unittest.TestCase):
    """Карта «инструмент → пакет» в резолвере.

    Нужна потому, что движок 2.1.42 не тянет зависимости между пакетами:
    человек ставит часть набора, инструмент соседнего пакета не находится, и
    отказ «нет такого файла» читается как поломка продукта. С картой отказ
    называет пакет и команду.

    Карта статична — в установленной раскладке спросить неоткуда, маркетплейса
    рядом нет. Значит она обязана сверяться с деревом, иначе разойдётся молча и
    начнёт называть не тот пакет, что хуже молчания.
    """

    def _map(self):
        import importlib.util
        p = REPO / "plugins" / "superstack-build" / "tools" / "where.py"
        s = importlib.util.spec_from_file_location("_owner_map", p)
        m = importlib.util.module_from_spec(s)
        s.loader.exec_module(m)
        return m.OWNER

    def test_every_tool_in_the_tree_is_in_the_map(self):
        real = {p.name: p.parts[-3] for p in REPO.glob("plugins/*/tools/*.py")}
        missing = sorted(set(real) - set(self._map()))
        self.assertEqual(missing, [], f"нет в карте: {missing}")

    def test_the_map_names_the_right_plugin(self):
        real = {p.name: p.parts[-3] for p in REPO.glob("plugins/*/tools/*.py")}
        for name, plug in sorted(self._map().items()):
            with self.subTest(tool=name):
                self.assertEqual(real.get(name), plug,
                                 f"{name}: карта говорит {plug}, лежит в {real.get(name)}")

    def test_the_map_has_no_ghosts(self):
        real = {p.name for p in REPO.glob("plugins/*/tools/*.py")}
        ghosts = sorted(set(self._map()) - real)
        self.assertEqual(ghosts, [], f"в карте есть несуществующие: {ghosts}")
