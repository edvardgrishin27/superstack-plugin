#!/usr/bin/env python3
"""Тесты движка правил SUPERSTACK.

Система заставляет писать тесты — значит сама обязана быть покрыта.
Только stdlib, ноль зависимостей, запускается одной командой:

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import atexit
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG, REPO, at  # noqa: E402

ROOT = REPO
sys.path.insert(0, str(PKG / "tools"))
sys.path.insert(0, str(at("tests", "fixtures")))

from adjudicate import RuleError, evaluate, substitute  # noqa: E402
import fake_machine  # noqa: E402

COLLECT = at("tools", "probe", "collect.py")
ADJUDICATE = at("tools", "adjudicate.py")


# --------------------------------------------------------------------------
# подставная машина
# --------------------------------------------------------------------------
# Заявление «набор зелёный» обязано быть утверждением о КОДЕ. Пока пробы
# запускались как есть, оно было утверждением о машине: на неизменном коде
# число сабтестов уезжало вслед за содержимым настоящего ~/.claude, потому что
# сабтест заводился на каждый собранный факт и на каждую находку. Проверяющий
# видел «260 тестов» и «282 теста» подряд — и обе цифры были правдой.
#
# Поэтому сборщик запускается ТОЛЬКО на дереве, которое тест построил сам
# (tests/fixtures/fake_machine.py), с урезанным окружением. Прогон один на весь
# модуль: он же самый дорогой шаг в наборе, а результат от повтора не меняется —
# в этом весь смысл починки.
_RUNS: dict = {}


def fake_machine_run(kind: str) -> dict:
    """Собранные факты подставной машины: {'env', 'root', 'raw', 'facts'}.

    kind: 'populated' — обжитая машина, 'bare' — HOME без ~/.claude вовсе.
    """
    if kind not in _RUNS:
        builder = {"populated": fake_machine.build_populated,
                   "bare": fake_machine.build_bare}[kind]
        tmp = tempfile.TemporaryDirectory(prefix=f"superstack-fake-{kind}-")
        atexit.register(tmp.cleanup)
        root = Path(tmp.name)
        env = builder(root)
        r = subprocess.run([sys.executable, str(COLLECT)],
                           capture_output=True, text=True, timeout=180,
                           env=env, cwd=str(root))
        if r.returncode != 0:
            raise AssertionError(
                f"сборщик упал на подставной машине ({kind}), код {r.returncode}:\n"
                f"{r.stderr[-2000:]}")
        _RUNS[kind] = {"env": env, "root": root, "raw": r.stdout,
                       "facts": json.loads(r.stdout)}
    return _RUNS[kind]


def adjudicate(facts_raw: str, rules_glob: str, env: dict) -> dict:
    """Решатель на готовом тексте фактов. Окружение подаётся, а не наследуется:
    иначе флаг паузы или HOME запускающего меняют результат проверки."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(facts_raw)
        path = fh.name
    r = subprocess.run([sys.executable, str(ADJUDICATE), path, rules_glob],
                       capture_output=True, text=True, timeout=30, env=env)
    if r.returncode != 0:
        raise AssertionError(f"решатель вернул {r.returncode}:\n{r.stderr[-2000:]}")
    return json.loads(r.stdout)


class TestExpressionGrammar(unittest.TestCase):
    """Грамматика выражений: что должно считаться."""

    def setUp(self):
        self.facts = {
            "a.count": 3,
            "a.list": [1, 2, 3],
            "a.flag": True,
            "a.none": None,
            "a.ratio": 5.58,
        }

    def test_comparison(self):
        self.assertTrue(evaluate("a.count > 0", self.facts))
        self.assertFalse(evaluate("a.count > 10", self.facts))

    def test_len_of_list(self):
        self.assertTrue(evaluate("len(a.list) == 3", self.facts))

    def test_len_of_none_is_zero(self):
        """Отсутствующие данные не должны ронять правило."""
        self.assertTrue(evaluate("len(a.none) == 0", self.facts))

    def test_boolean_identity(self):
        self.assertTrue(evaluate("a.flag == True", self.facts))
        self.assertTrue(evaluate("a.none == None", self.facts))

    def test_and_or_not(self):
        self.assertTrue(evaluate("a.count > 0 and a.flag == True", self.facts))
        self.assertTrue(evaluate("a.count > 99 or a.flag == True", self.facts))
        self.assertTrue(evaluate("not a.count > 99", self.facts))

    def test_float_comparison(self):
        self.assertTrue(evaluate("a.ratio > 1", self.facts))

    def test_key_with_dots_resolves(self):
        """Ключи фактов содержат точки и не являются атрибутами."""
        self.assertTrue(evaluate("a.count == 3", self.facts))

    def test_longest_key_wins(self):
        """Подстановка идёт от длинных ключей к коротким — иначе префикс съест."""
        facts = {"x.a": 1, "x.a.b": 99}
        self.assertTrue(evaluate("x.a.b == 99", facts))


class TestResourceLimitsSurviveChaining(unittest.TestCase):
    """Предел на память ставится и на РЕЗУЛЬТАТ, а не только на множитель.

    Статическая проверка ловит одну большую константу. Цепочку из нескольких
    допустимых она пропускает: `9999*9999` — сто мегабайт, и каждый множитель
    в пределах. Отказ громкий (RuleError → правило в skipped), но платить
    сотней мегабайт за него не нужно.
    """

    def test_single_huge_multiplier_is_rejected(self):
        with self.assertRaises(RuleError):
            evaluate("'A' * 100000", {})

    def test_chain_of_allowed_multipliers_is_rejected_too(self):
        with self.assertRaises(RuleError):
            evaluate("'A' * 9999 * 9999", {})

    def test_ordinary_arithmetic_still_works(self):
        """Обратный контроль: предел, отвергающий всё, бесполезен."""
        self.assertEqual(evaluate("2 * 3 + 1", {}), 7)
        self.assertEqual(evaluate("'ок' * 3", {}), "ококок")


class TestGrammarIsNotCodeExecution(unittest.TestCase):
    """Главное свойство безопасности: правило не может выполнить код.

    Правила лежат в репозитории и приходят через git. Если бы выражение
    исполнялось как код, любой pull становился бы удалённым исполнением.
    """

    def _rejects(self, expr: str):
        with self.assertRaises((RuleError, SyntaxError, ValueError),
                               msg=f"должно быть отклонено: {expr}"):
            evaluate(expr, {"a.count": 1})

    def test_rejects_import(self):
        self._rejects("__import__('os').system('echo pwned')")

    def test_rejects_arbitrary_call(self):
        self._rejects("open('/etc/passwd').read()")

    def test_rejects_attribute_access(self):
        self._rejects("a.count.__class__.__bases__")

    def test_rejects_lambda(self):
        self._rejects("(lambda: 1)()")

    def test_rejects_subscript(self):
        self._rejects("[1,2,3][0]")

    def test_rejects_comprehension(self):
        self._rejects("[x for x in range(3)]")

    def test_rejects_walrus_assignment(self):
        self._rejects("(y := 5)")

    def test_only_len_is_callable(self):
        self._rejects("sum(a.count)")
        self.assertTrue(evaluate("len(a.count) == 0", {"a.count": None}))

    def test_unknown_fact_is_error_not_silent_false(self):
        """Опечатка в правиле обязана быть видна, а не молча давать False."""
        with self.assertRaises(RuleError):
            evaluate("nonexistent.key > 0", {"a.count": 1})


class TestSubstitution(unittest.TestCase):
    def test_n_placeholder(self):
        self.assertEqual(substitute("нашёл {n} штук", {}, 7), "нашёл 7 штук")

    def test_missing_n_is_marked_not_invented(self):
        """Неизмеренное число печатается как ?, а не выдумывается."""
        self.assertEqual(substitute("нашёл {n}", {}, None), "нашёл ?")

    def test_fact_placeholder(self):
        out = substitute("всего {a.count}", {"a.count": 42}, None)
        self.assertEqual(out, "всего 42")


class TestEndToEnd(unittest.TestCase):
    """Прогон настоящих правил из репозитория на синтетических машинах."""

    RULES = str(str(PKG / "rules" / "*.json"))

    def _run(self, values: dict) -> list[dict]:
        facts = {k: {"value": v, "probe": "test"} for k, v in values.items()}
        # Окружение подставное, а не унаследованное: решатель первым же
        # действием читает ~/.claude/superstack/PAUSE и выходит с кодом 10.
        # Пока окружение наследовалось, человек, нажавший стоп на своей машине,
        # красил весь набор — то есть исход теста зависел от состояния хоста.
        data = adjudicate(json.dumps(facts, ensure_ascii=False), self.RULES,
                          fake_machine_run("bare")["env"])
        self.assertEqual(data["skipped_rules"], [],
                         f"правила не должны падать: {data['skipped_rules']}")
        return data["findings"]

    # Фикстуры лежат на диске и версионируются. Раньше они ВЫВОДИЛИСЬ из
    # прогона проб на машине, где запущены тесты. Последствий два, и оба
    # тяжёлые: сюита переставала быть герметичной (на чистой машине — на той
    # самой, ради которой всё строится, — набор фактов другой), а тест
    # «фикстура покрывает контракт проб» становился тавтологией: уменьшаемое
    # и вычитаемое строились одним и тем же вызовом, и разность была пуста
    # по построению. Тест структурно не мог упасть.
    #
    # Теперь фикстура — независимый источник. Расхождение с пробами ловится
    # честным сравнением двух РАЗНЫХ вещей.
    FIXTURES = at("tests", "fixtures")

    @classmethod
    def setUpClass(cls):
        cls.GREENFIELD = json.loads(
            (cls.FIXTURES / "greenfield.json").read_text(encoding="utf-8"))
        cls.BROWNFIELD = json.loads(
            (cls.FIXTURES / "brownfield.json").read_text(encoding="utf-8"))

    def test_fixture_does_not_lag_behind_the_probes(self):
        """Новая проба обязана появиться в фикстуре — иначе правило на неё упадёт.

        Смысл прежний, источник сравнения — другой. Раньше сборщик читал
        НАСТОЯЩИЙ ~/.claude, и тест отвечал на вопрос «что стоит на этой машине
        сегодня». Теперь он гоняется на дереве, которое тест построил сам:
        расхождение означает изменение КОДА проб, а не установку плагина.

        Сравниваются по-прежнему две РАЗНЫЕ вещи — живой вывод сборщика и файл
        в репозитории, — поэтому тест по-прежнему может упасть.
        """
        produced = set(fake_machine_run("populated")["facts"])
        missing = produced - set(self.GREENFIELD)
        self.assertEqual(missing, set(),
                         f"фикстура отстала от проб, добавь в tests/fixtures: {sorted(missing)}")

    def test_fact_key_set_does_not_depend_on_the_machine(self):
        """Набор КЛЮЧЕЙ одинаков на обжитой и на пустой машине.

        Это и есть починенное обвинение, выраженное как поведение: число фактов
        (а значит и число сабтестов, и число вычисляемых правил) обязано быть
        свойством кода, а не содержимого HOME. Тест ловит два разных дефекта:
        раннее возвращение из пробы при отсутствии настроек — так уже было, и
        тогда 18 правил из 22 просто не вычислялись на чистой машине, — и
        упавшую пробу, которая добавляет ключ error.* только на одной из машин.
        """
        rich = set(fake_machine_run("populated")["facts"])
        bare = set(fake_machine_run("bare")["facts"])
        self.assertEqual(
            rich, bare,
            "набор фактов зависит от содержимого HOME; расходятся: "
            f"{sorted(rich ^ bare)}")
        for kind in ("populated", "bare"):
            crashed = {k: v.get("value") for k, v
                       in fake_machine_run(kind)["facts"].items()
                       if k.startswith("error.")}
            self.assertEqual(crashed, {},
                             f"проба упала на подставной машине ({kind}): {crashed}")

    def test_greenfield_produces_findings(self):
        """Пустая машина проходит через тот же движок, а не через отдельную ветку."""
        ids = {f["id"] for f in self._run(self.GREENFIELD)}
        self.assertIn("gap.no-git", ids)
        self.assertIn("gap.no-hooks-at-all", ids)
        self.assertIn("ctx.default-mode-unset", ids)

    def test_greenfield_does_not_fire_brownfield_rules(self):
        ids = {f["id"] for f in self._run(self.GREENFIELD)}
        self.assertNotIn("hooks.dormant-manifest", ids)
        self.assertNotIn("ctx.skill-listing-over-budget", ids)
        self.assertNotIn("sec.secret-in-settings", ids)

    def test_brownfield_fires_the_right_rules(self):
        ids = {f["id"] for f in self._run(self.BROWNFIELD)}
        self.assertIn("sec.secret-in-settings", ids)
        self.assertIn("hooks.dormant-manifest", ids)
        self.assertIn("ctx.skill-listing-over-budget", ids)
        # Движок 2.1.42 игнорирует model: в агенте: «второе мнение» даёт та же
        # модель, что писала код, и об этом никто не предупреждает.
        self.assertIn("ass.second-opinion-degraded", ids)
        self.assertNotIn("gap.no-git", ids)

    def test_clean_machine_is_silent(self):
        """Нечего сказать — молчи. Отчёт не обязан быть непустым."""
        # Здоровая машина — это не «пусто», а «всё на месте»: есть тормоз,
        # есть накопленное знание, судьи без права записи, модели разнесены
        # по тирам, движок достаточно свежий, чтобы проверку вела другая
        # модель. Новое правило дисциплины обязано это учитывать.
        clean = {**self.BROWNFIELD, "sec.secret_matches": [],
                 "hooks.dormant.count": 0, "inv.skills.over_budget_ratio": 0.4,
                 "disc.verifier_theater": [], "disc.all_on_top_tier": False,
                 "disc.kill_switch_present": True, "disc.learned_entries": 12,
                 "rt.subagent_model_routing": True,
                 # У здоровой машины конституция ПОМЕЩАЕТСЯ: она читается
                 # каждой сессией целиком, и её объём — плата, а не вкус.
                 "cc.constitution_lines": 120}
        self.assertEqual(self._run(clean), [])

    def test_secret_finding_never_carries_the_value(self):
        """Находка о секрете не имеет права нести сам секрет."""
        found = [f for f in self._run(self.BROWNFIELD)
                 if f["id"] == "sec.secret-in-settings"]
        self.assertEqual(len(found), 1)
        blob = json.dumps(found[0], ensure_ascii=False)
        for leak in ("sshpass -p '", "password=", "api_key="):
            self.assertNotIn(leak, blob)

    def test_severity_ordering(self):
        """Прежняя версия гонялась на фикстуре, где находки И БЕЗ сортировки
        лежали по возрастанию. Удаление строки сортировки не роняло ничего.
        Здесь порядок проверяется на входе, заведомо перемешанном."""
        sev = [f["severity"] for f in self._run(self.BROWNFIELD)]
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        self.assertEqual(sev, sorted(sev, key=lambda s: order[s]),
                         "критичное обязано идти первым")
        self.assertGreater(len(set(sev)), 1,
                           "фикстура даёт находки одной тяжести — тест ничего не проверяет")

    def test_sorting_actually_reorders(self):
        """Решатель переупорядочивает находки САМ — проверено на входе, где
        порядок объявления заведомо неверен.

        Прежняя версия была тавтологией: она импортировала SEVERITY_ORDER из
        решателя, сортировала список ЭТИМ ЖЕ ключом и сверяла результат сама с
        собой. Ожидаемое значение приходило из тестируемого кода, поэтому
        удаление строки сортировки в adjudicate.py тест НЕ роняло — он вообще
        не вызывал решатель.

        Здесь запускается настоящий adjudicate.py на фикстуре правил, где
        объявление идёт low -> medium -> high -> high -> critical, а
        идентификаторы подобраны так, что алфавит не совпадает ни с порядком
        объявления, ни с порядком тяжести. Ожидаемая последовательность
        записана буквой и ниоткуда не выводится.
        """
        rules = at("tests", "fixtures", "severity_order.rules.json")
        facts = {"probe.marker": {"value": True, "probe": "test",
                                  "provenance": "EXTRACTED"}}
        data = adjudicate(json.dumps(facts), str(rules),
                          fake_machine_run("bare")["env"])
        self.assertEqual(data["skipped_rules"], [], data["skipped_rules"])
        self.assertEqual(
            [f["id"] for f in data["findings"]],
            ["zz.critical", "aa.high", "yy.high", "mm.medium", "bb.low"],
            "порядок находок не по тяжести (внутри тяжести — по id)")

    def test_blocking_finding_is_marked_block(self):
        found = [f for f in self._run(self.BROWNFIELD)
                 if f["id"] == "sec.secret-in-settings"][0]
        self.assertEqual(found["class"], "BLOCK")
        self.assertEqual(found["verdict"], "ASK",
                         "секрет чинится только с согласия человека")


class TestRuleFilesAreWellFormed(unittest.TestCase):
    """Правила — контракт. Кривое правило обязано падать здесь, а не у человека."""

    def setUp(self):
        self.docs = [json.loads(p.read_text(encoding="utf-8"))
                     for p in sorted((PKG / "rules").glob("*.json"))]
        self.rules = [r for d in self.docs for r in (d or {}).get("rules", [])]

    def test_rules_exist(self):
        self.assertGreater(len(self.rules), 0)

    def test_ids_are_unique(self):
        ids = [r["id"] for r in self.rules]
        self.assertEqual(len(ids), len(set(ids)), "id правил должны быть уникальны")

    def test_required_fields(self):
        for r in self.rules:
            with self.subTest(rule=r.get("id")):
                for field in ("id", "when", "severity", "class", "verdict"):
                    self.assertIn(field, r)
                self.assertIn(r["severity"], ("critical", "high", "medium", "low"))
                self.assertIn(r["class"], ("AUTO", "GATE", "INFORM", "BLOCK"))

    def test_every_rule_speaks_both_languages(self):
        """Одна структура — три глубины. Значит нужны обе формулировки."""
        for r in self.rules:
            with self.subTest(rule=r["id"]):
                self.assertTrue(r.get("beginner", {}).get("headline"),
                                "нет строки для новичка")
                self.assertTrue(r.get("expert", {}).get("claim"),
                                "нет машинной формулировки для эксперта")

    def test_expert_claims_cite_evidence(self):
        """Утверждение без фактов — это мнение."""
        for r in self.rules:
            with self.subTest(rule=r["id"]):
                self.assertTrue(r.get("expert", {}).get("evidence"),
                                "утверждение не сослалось ни на один факт")

    def test_critical_rules_never_auto_apply(self):
        for r in self.rules:
            if r["severity"] == "critical":
                with self.subTest(rule=r["id"]):
                    self.assertNotEqual(r["class"], "AUTO",
                                        "критичное не применяется молча")




class TestProvenanceIsHonest(unittest.TestCase):
    """Вывод эвристики не имеет права выглядеть как измерение.

    Принцип заимствован из graphify: провенанс на каждом ребре. Без него
    человек не может понять, чему верить, и одинаково доверяет замеру
    и догадке.
    """

    # Все три проверки гоняются на ПОДСТАВНОЙ машине. Раньше каждая заново
    # запускала сборщик на настоящем ~/.claude: сабтест заводился на каждый
    # факт и на каждую находку, поэтому число сабтестов было функцией машины —
    # ставишь плагин, и «зелёный набор» показывает другую цифру. Прогон теперь
    # один на весь модуль и его результат воспроизводим.

    def test_probe_labels_every_fact(self):
        facts = fake_machine_run("populated")["facts"]
        self.assertGreater(len(facts), 0, "сборщик не отдал ни одного факта")
        for key, rec in facts.items():
            with self.subTest(fact=key):
                self.assertIn(rec.get("provenance"),
                              ("EXTRACTED", "INFERRED", "AMBIGUOUS"))

    def test_heuristics_are_not_labelled_as_measurements(self):
        """Факты, которые заведомо являются догадкой, обязаны быть помечены."""
        facts = fake_machine_run("populated")["facts"]
        must_be_inferred = [
            "sec.secret_matches",        # регулярка по тексту, не разбор команды
            "hooks.dormant",             # сопоставление подстрокой
            "inv.skills.over_budget_ratio",  # оценка, делённая на оценку
            "mcp.local_binaries",        # признак по форме пути
        ]
        # Раньше здесь стояло `if key in facts` — на машине, где факта нет,
        # проверка молча вырождалась в ноль сабтестов. Отсутствие факта теперь
        # само по себе провал: подставная машина устроена так, что все четыре
        # обязаны быть собраны.
        for key in must_be_inferred:
            with self.subTest(fact=key):
                self.assertIn(key, facts, f"{key} не собран на подставной машине")
                self.assertEqual(facts[key]["provenance"], "INFERRED",
                                 f"{key} — это вывод, а не измерение")

    def test_finding_carries_the_warning(self):
        """Находка обязана нести флаг, если построена на догадке."""
        run = fake_machine_run("populated")
        data = adjudicate(run["raw"], str(str(PKG / "rules" / "*.json")), run["env"])
        findings = data["findings"]
        # Проверка по пустому списку проходит, ничего не проверив. Подставная
        # машина собрана так, чтобы находки БЫЛИ, — молчание здесь означает,
        # что сломан не флаг, а сама постановка проверки.
        self.assertGreater(len(findings), 0,
                           "на подставной машине не сработало ни одно правило — "
                           "проверять флаг не на чем")
        self.assertTrue(any(f["rests_on_inference"] for f in findings),
                        "ни одна находка не стоит на догадке — "
                        "положительная ветка проверки не исполняется")
        for f in findings:
            with self.subTest(finding=f["id"]):
                self.assertIn("rests_on_inference", f)
                expected = any(p == "INFERRED" for p in f["provenance"].values())
                self.assertEqual(f["rests_on_inference"], expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
