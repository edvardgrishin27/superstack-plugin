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
    жил в пакете `superstack-build` и звал `$CLAUDE_PLUGIN_ROOT/tools/verify.py`,
    хотя `verify.py` лежал в `superstack-guard`.

Последнее — самый тихий из четырёх отказов. Он выглядит подключённым и падает
«нет такого файла», то есть как поломка окружения; человек идёт чинить
установку. Тесты его не видят: файл на месте, функции работают, набор зелёный.

Слияние семи пакетов в один убрало ПРИЧИНУ четвёртого отказа — чужих пакетов
больше нет, и резолвер с картой владельцев удалены вместе с ними. Проверка
осталась и стала строже: раньше половина вызовов пряталась за резолвером и
воротам была не видна, теперь каждый вызов написан полным путём и читается
ими буквально. Опечатка в имени инструмента даёт тот же тихий отказ, что и
межпакетный путь, — поэтому ворота отвечают на оба вопроса разом: дотянется ли
кто-нибудь до инструмента и существует ли путь, которым его зовут.
"""
from __future__ import annotations

import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

from paths import PKG, REPO, packages

_g = importlib.util.spec_from_file_location("gauntlet_wiring",
                                            REPO / "tools" / "gauntlet.py")
gt = importlib.util.module_from_spec(_g)
_g.loader.exec_module(gt)


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

    def test_a_tool_reached_only_by_python_import_is_alive(self):
        """`import derive_phase` — это вызов, хотя расширения в нём нет.

        Тест написан по живому отказу: инструмент числился достижимым потому,
        что его имя встречалось в карте владельцев внутри резолвера — то есть
        в строке справочника, а не в вызове. Слияние пакетов удалило резолвер,
        маска слетела, и ворота назвали мёртвым инструмент, который работает
        на каждом прогоне панели.
        """
        self.tool("a", "panel.py", "import helper\n")
        self.tool("a", "helper.py")
        self.skill("a", "зовём panel.py")
        self.assertEqual(gt.gate_wiring()["status"], "pass")

    def test_a_bare_mention_is_not_a_call(self):
        """Обратный контроль, без которого предыдущий тест опасен.

        Если считать вызовом любое упоминание имени, ворота позеленеют на
        строке справочника и на комментарии — то есть перестанут ловить ровно
        ту болезнь, ради которой написаны. Именно так и вышло с картой
        владельцев: имя было, вызова не было, ворота молчали.
        """
        self.tool("a", "panel.py", "# помощник живёт в helper и когда-нибудь\n"
                                   "# пригодится: helper\n")
        self.tool("a", "helper.py")
        self.skill("a", "зовём panel.py")
        v = gt.gate_wiring()
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("helper.py" in x for x in v["dead"]), v)

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


class TestTheRealTreeCallsOnlyToolsThatExist(unittest.TestCase):
    """Обратный контроль на ЖИВОМ дереве, а не на выдуманной фикстуре.

    Классы выше доказывают, что ворота умеют краснеть на поддельном дереве.
    Это не то же самое, что «настоящие скиллы зовут существующие файлы»:
    ворота могли бы работать безупречно и не быть применены к настоящему
    дереву ни разу.

    Здесь читаются все точки входа продукта и проверяется каждый вызов
    буквально. После слияния это покрывает ВСЕ вызовы: до него половина
    пряталась за резолвером, и написанное в скилле имя инструмента никакая
    проверка сопоставить с диском не могла.
    """

    ГЛОБЫ = ("skills/**/*.md", "hooks/*.sh", "hooks/*.json",
             "agents/*.md", "commands/*.md")
    ВЫЗОВ = re.compile(r"\$\{?CLAUDE_PLUGIN_ROOT\}?/tools/([\w./]+\.py)")

    def _вызовы(self):
        out = {}
        for pkg in packages():
            for g in self.ГЛОБЫ:
                for f in sorted(pkg.glob(g)):
                    for m in self.ВЫЗОВ.finditer(f.read_text("utf-8", "replace")):
                        out.setdefault(m.group(1), set()).add(f)
        return out

    def test_every_called_tool_exists_in_the_package(self):
        вызовы = self._вызовы()
        self.assertTrue(вызовы, "точки входа не зовут ни одного инструмента "
                                "полным путём — проверять нечего, и это само "
                                "по себе отказ")
        for имя, где in sorted(вызовы.items()):
            with self.subTest(tool=имя):
                кто = sorted(w.parent.parent.parent.name for w in где)
                self.assertTrue(
                    (PKG / "tools" / имя).is_file(),
                    f"{имя} зовут из {кто}, а в пакете его нет")

    def test_no_entry_point_climbs_out_of_the_package(self):
        """Путь наружу пакета — возврат к межпакетной адресации.

        `$CLAUDE_PLUGIN_ROOT/../superstack-guard/tools/verify.py` работает в
        репозитории и разваливается в установке: там между пакетом и корнем
        стоит каталог версии. Отказ тихий, поэтому запрет явный.
        """
        плохие = []
        for pkg in packages():
            for g in self.ГЛОБЫ:
                for f in sorted(pkg.glob(g)):
                    t = f.read_text("utf-8", "replace")
                    if re.search(r"\$\{?CLAUDE_PLUGIN_ROOT\}?/\.\.", t):
                        плохие.append(str(f.relative_to(REPO)))
        self.assertEqual(плохие, [],
                         "точка входа строит путь ЗА пределы своего пакета — "
                         "в установленной раскладке он ведёт не туда")


if __name__ == "__main__":
    unittest.main()
