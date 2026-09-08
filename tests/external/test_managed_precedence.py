#!/usr/bin/env python3
"""Управляемая политика читается в ту же сторону, в какую её применяет хост.

Сценарий пришёл из внешней проверки и переписан здесь регрессионным тестом
ПРАВИЛЬНОГО поведения.

Список областей настроек был объявлен «в порядке приоритета» и начинался с
управляемой. А победитель выбирался так:

    enabled.update(ep)

У `update` побеждает ПОСЛЕДНИЙ записавший. При списке, отсортированном сильной
областью вперёд, побеждала самая СЛАБАЯ: локальный файл проекта перекрывал
политику организации. Воспроизведено: управляемая настройка с
`enabledPlugins: {…: false}` при пользовательской `true` давала плагин
ВКЛЮЧЁННЫМ в докладе. Администратор запретил — доклад говорил «работает».

Цена дефекта здесь не в точности числа. Это диагностический инструмент: он не
меняет поведение хоста, он о нём докладывает. Ложь в пользу разрешения —
худший из возможных исходов такого доклада, потому что её некому опровергнуть:
человек читает «плагин включён» и закрывает вопрос.

Направление приоритета взято из документации хоста, а не из памяти. Уровни там
перечислены «highest precedence first»: управляемая политика, аргументы
командной строки, локальные настройки проекта, общие настройки проекта,
пользовательские настройки, — и про управляемую сказано отдельно, что её не
перекрывает ничто из того, что человек задаёт себе сам. Списки прав хост
СКЛАДЫВАЕТ по файлам, а одиночное значение берёт из самой приоритетной
области, которая его назвала.

Что здесь заперто:
  · направление приоритета названо ДАННЫМИ и совпадает с порядком файлов:
    управляемая первой, пользовательская последней;
  · управляемый запрет сильнее пользовательского разрешения, и плагин в
    докладе выключен;
  · проектная область — ни общая, ни локальная — управляемую не перебивает;
  · в фактах названа область, ПРИНЯВШАЯ решение, и перечислено, чьё решение
    этим перекрыто: «включён» без «кем» администратору бесполезен;
  · разделы `permissions` управляемой политики участвуют в фактах, а не
    молчат: режим — за самой приоритетной областью, списки прав сложены;
  · существующая, но непрочитанная управляемая область названа поимённо и
    делает вывод неполным, а не сводится к пустоте;
  · отказ в доступе к области отличается от её отсутствия;
  · обратный контроль: без управляемой политики пользовательское решение
    работает ровно как прежде.

Настоящий профиль человека и настоящая управляемая политика машины здесь НЕ
ЧИТАЮТСЯ ни разу. Профиль сборщика подменён временным каталогом, каталог
проекта задан явно, адреса управляемых файлов переставлены во временный
каталог — и это проверяется отдельным тестом, а не обещается словами. Иначе
«зелёно» описывало бы машину, на которой прогнали, а не код.

Порядок областей тест НЕ переписывает у себя: он берёт его у самого сборщика и
подменяет только адреса управляемых файлов. Два независимых списка разошлись
бы при первой же правке, и тогда проверка приоритета проверяла бы список
теста, а не поведение продукта.

Тест самодостаточен: только стандартная библиотека и сам инструмент.
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

РЕПО = Path(__file__).resolve().parents[2]
СБОРЩИК = РЕПО / "plugins" / "superstack" / "tools" / "probe" / "collect.py"


def _загрузить():
    spec = importlib.util.spec_from_file_location("ss_collect_managed", СБОРЩИК)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


collect = _загрузить()

#: Ключ плагина. Имя выдуманное: совпади оно с настоящим — и проверка начала
#: бы зависеть от того, что установлено на машине.
ПЛАГИН = "надзор@маркет"


class Машина(unittest.TestCase):
    """Временная машина: свой профиль, свой проект, своя управляемая политика."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.корень = Path(tmp.name)
        self.профиль = self.корень / ".claude"
        self.проект = self.корень / "проект"
        self.политика = self.корень / "политика"
        self.установлен = self.корень / "установлен"
        for каталог in (self.профиль, self.проект, self.политика,
                        self.установлен / "skills"):
            каталог.mkdir(parents=True)

        # Модуль общий на весь набор — всё подменённое возвращается обратно.
        прежний_профиль = collect.CLAUDE
        collect.CLAUDE = self.профиль
        self.addCleanup(setattr, collect, "CLAUDE", прежний_профиль)

        прежний_проект = os.environ.get("SUPERSTACK_PROJECT_DIR")
        os.environ["SUPERSTACK_PROJECT_DIR"] = str(self.проект)
        self.addCleanup(self._вернуть_проект, прежний_проект)

        # Каталог установки существует. Без него «включён, но не установлен»
        # скрыло бы разницу между запретом и отсутствием: плагин пропал бы из
        # доклада по другой причине, и тест зеленел бы на сломанном коде.
        self.записать(self.профиль / "plugins" / "installed_plugins.json",
                      {"plugins": {ПЛАГИН: [
                          {"installPath": str(self.установлен)}]}})
        self._подменить_управляемую()

    # ---- обвязка ------------------------------------------------------
    @staticmethod
    def _вернуть_проект(прежний) -> None:
        if прежний is None:
            os.environ.pop("SUPERSTACK_PROJECT_DIR", None)
        else:
            os.environ["SUPERSTACK_PROJECT_DIR"] = прежний

    def _подменить_управляемую(self) -> None:
        """Управляемая политика — во временном каталоге, а не на машине.

        Порядок областей берётся У САМОГО СБОРЩИКА. Подменяются только адреса
        управляемых файлов: остальные области и так живут во временном
        каталоге, потому что подменены профиль и каталог проекта.
        """
        подмена = {"managed": self.политика / "managed-settings.json",
                   "managed-linux": self.политика / "linux-managed.json"}
        список = [(метка, подмена.get(метка, путь))
                  for метка, путь in collect._settings_files()]
        self.управляемая = подмена["managed"]
        прежний = collect._settings_files
        collect._settings_files = lambda: список
        self.addCleanup(setattr, collect, "_settings_files", прежний)

    def записать(self, путь: Path, данные) -> Path:
        путь.parent.mkdir(parents=True, exist_ok=True)
        текст = данные if isinstance(данные, str) else json.dumps(
            данные, ensure_ascii=False)
        путь.write_text(текст, encoding="utf-8")
        return путь

    def плагин_в(self, файл: Path, включён: bool) -> Path:
        return self.записать(файл, {"enabledPlugins": {ПЛАГИН: включён}})

    def собрать(self) -> dict:
        collect.facts.clear()
        collect.SECRET_SCAN_UNREADABLE.clear()
        collect.probe_claude()
        return collect.facts

    def значение(self, ключ: str):
        факты = collect.facts
        self.assertIn(ключ, факты, f"факт {ключ} не собран")
        return факты[ключ]["value"]

    def решение(self) -> dict:
        """Запись доклада про наш плагин: кто решил и что перекрыто."""
        self.собрать()
        по_плагинам = {d["plugin"]: d for d in
                       self.значение("cc.plugins.by_scope")}
        self.assertIn(ПЛАГИН, по_плагинам, "плагин пропал из доклада целиком")
        return по_плагинам[ПЛАГИН]

    def включённые(self) -> list:
        return [a["plugin"] for a in collect.enabled_plugins()[0]]


class НеЧитаетНастоящуюМашину(Машина):
    """Обещание из заголовка, проверенное кодом, а не словами."""

    def test_все_области_лежат_во_временном_каталоге(self):
        for метка, путь in collect._settings_files():
            self.assertTrue(str(путь).startswith(str(self.корень)),
                            f"область {метка} ведёт наружу: {путь}")

    def test_управляемая_область_не_адрес_машины(self):
        адреса = {str(путь) for _метка, путь in collect._settings_files()}
        self.assertNotIn(
            "/Library/Application Support/ClaudeCode/managed-settings.json",
            адреса, "набор читает настоящую управляемую политику машины")
        self.assertNotIn("/etc/claude-code/managed-settings.json", адреса)


class ПорядокОбластейНазванЯвно(unittest.TestCase):
    """Направление приоритета — данные, а не побочное свойство `dict.update`.

    Здесь настоящий сборщик и настоящие адреса, но НИ ОДНОГО чтения: список
    областей только строится. Читать машину этому тесту незачем.
    """

    def test_управляемая_первая_пользовательская_последняя(self):
        порядок = list(collect.SCOPES_STRONGEST_FIRST)
        self.assertEqual(порядок[0], "managed",
                         "управляемая политика не самая приоритетная")
        self.assertEqual(порядок[-1], "user",
                         "пользовательская область не самая слабая")

    def test_проектные_области_между_управляемой_и_пользовательской(self):
        порядок = list(collect.SCOPES_STRONGEST_FIRST)
        self.assertLess(порядок.index("managed"), порядок.index("project-local"))
        self.assertLess(порядок.index("project-local"), порядок.index("project"))
        self.assertLess(порядок.index("project"), порядок.index("user"))

    def test_список_файлов_идёт_в_объявленном_порядке(self):
        метки = [метка for метка, _путь in collect._settings_files()]
        self.assertEqual(метки, list(collect.SCOPES_STRONGEST_FIRST),
                         "порядок файлов разошёлся с объявленным приоритетом")


class УправляемыйЗапретСильнееРазрешения(Машина):
    """Сценарий (а): управляемая запрещает, пользователь разрешает."""

    def setUp(self):
        super().setUp()
        self.записать(self.управляемая, {"enabledPlugins": {ПЛАГИН: False}})
        self.плагин_в(self.профиль / "settings.json", True)

    def test_в_докладе_плагин_выключен(self):
        self.assertEqual(
            self.включённые(), [],
            "управляемый запрет перекрыт пользовательским разрешением: "
            "администратор запретил, а доклад говорит «работает»")

    def test_названа_область_принявшая_решение(self):
        d = self.решение()
        self.assertFalse(d["enabled"])
        self.assertEqual(d["decided_by"], "managed",
                         "доклад не говорит, ЧЬИМ решением плагин выключен")

    def test_перекрытое_решение_названо(self):
        d = self.решение()
        self.assertEqual([o["scope"] for o in d["overridden"]], ["user"],
                         "перекрытое пользовательское решение потеряно")
        self.assertEqual([o["enabled"] for o in d["overridden"]], [True])


class ПроектНеПеребиваетУправляемую(Машина):
    """Сценарий (в): обе проектные области против управляемой."""

    def setUp(self):
        super().setUp()
        self.записать(self.управляемая, {"enabledPlugins": {ПЛАГИН: False}})
        self.плагин_в(self.проект / ".claude" / "settings.json", True)
        self.плагин_в(self.проект / ".claude" / "settings.local.json", True)

    def test_плагин_остаётся_выключенным(self):
        self.assertEqual(self.включённые(), [],
                         "проектная область перебила политику организации")

    def test_решила_управляемая_а_перекрыты_обе_проектные(self):
        d = self.решение()
        self.assertEqual(d["decided_by"], "managed")
        self.assertEqual([o["scope"] for o in d["overridden"]],
                         ["project-local", "project"],
                         "перекрытые области названы не в порядке приоритета")


class ПроектСильнееПользователя(Машина):
    """Между собой области тоже упорядочены так, как их применяет хост."""

    def setUp(self):
        super().setUp()
        self.плагин_в(self.проект / ".claude" / "settings.json", True)
        self.плагин_в(self.профиль / "settings.json", False)

    def test_решение_за_проектом(self):
        d = self.решение()
        self.assertTrue(d["enabled"])
        self.assertEqual(d["decided_by"], "project")
        self.assertEqual([o["scope"] for o in d["overridden"]], ["user"])

    def test_плагин_включён(self):
        self.assertEqual(self.включённые(), [ПЛАГИН])


class БезУправляемойВсёКакПрежде(Машина):
    """Сценарий (б): обратный контроль.

    Чинили направление приоритета, а не переписывали сборщик. Без управляемой
    политики пользовательское решение обязано работать ровно как раньше —
    иначе «починка» была бы просто другой поломкой.
    """

    def setUp(self):
        super().setUp()
        self.плагин_в(self.профиль / "settings.json", True)

    def test_плагин_включён(self):
        self.assertEqual(self.включённые(), [ПЛАГИН])

    def test_решение_за_пользователем_и_перекрывать_нечего(self):
        d = self.решение()
        self.assertTrue(d["enabled"])
        self.assertEqual(d["decided_by"], "user")
        self.assertEqual(d["overridden"], [])

    def test_вывод_не_помечен_неполным(self):
        self.собрать()
        self.assertEqual(self.значение("cc.settings_scopes_unreadable"), [])
        self.assertEqual(collect.facts["cc.plugins.by_scope"]["provenance"],
                         collect.EXTRACTED,
                         "все области прочитаны, а вывод объявлен неполным")


class УправляемыеПраваУчаствуютВФактах(Машина):
    """Раздел `permissions` управляемой политики в факты не попадал вовсе."""

    def setUp(self):
        super().setUp()
        self.записать(self.управляемая,
                      {"permissions": {"defaultMode": "plan",
                                       "allow": ["Read", "Grep"],
                                       "deny": ["Bash(rm:*)"]}})
        self.записать(self.профиль / "settings.json",
                      {"permissions": {"defaultMode": "bypassPermissions",
                                       "allow": ["Bash", "Read"]}})
        self.собрать()

    def test_режим_взят_у_управляемой_области(self):
        self.assertEqual(
            self.значение("cc.default_mode"), "plan",
            "режим прав взят у пользователя поверх политики организации")

    def test_улика_называет_решившую_область(self):
        улика = collect.facts["cc.default_mode"]["evidence"] or ""
        self.assertIn("managed", улика,
                      f"доклад не говорит, чей это режим: {улика!r}")

    def test_управляемая_область_видна_поимённо(self):
        по_областям = {s["scope"]: s for s in
                       self.значение("cc.permissions.by_scope")}
        self.assertIn("managed", по_областям,
                      "управляемые права молчат — неотличимо от «там пусто»")
        self.assertEqual(по_областям["managed"]["defaultMode"], "plan")
        self.assertEqual(по_областям["managed"]["deny"], 1)

    def test_списки_прав_сложены_а_не_взяты_из_одной_области(self):
        # Хост складывает списки прав по файлам. Дубль — то же самое правило,
        # и считать его дважды значило бы завысить измерение.
        self.assertEqual(self.значение("cc.allow_count"), 3)


class НедоступнаяУправляемаяОбластьНазвана(Машина):
    """Сценарий (г): область есть, прочитать не удалось.

    Молчание тут неотличимо от «там пусто», а разница решающая: на одной
    машине политики нет, на другой она есть и запрещает.
    """

    def setUp(self):
        super().setUp()
        self.записать(self.управляемая, "{это не JSON")
        self.плагин_в(self.профиль / "settings.json", True)
        self.собрать()

    def test_область_названа_непрочитанной(self):
        непрочитанные = self.значение("cc.settings_scopes_unreadable")
        области = [s["scope"] for s in непрочитанные]
        self.assertIn("managed", области,
                      "непрочитанная управляемая область сведена к пустоте")

    def test_названо_где_именно_не_прочитали(self):
        непрочитанные = self.значение("cc.settings_scopes_unreadable")
        адреса = [s["path"] for s in непрочитанные]
        self.assertIn(str(self.управляемая), адреса,
                      "область названа без адреса — проверить нечем")

    def test_вывод_про_плагины_помечен_неполным(self):
        self.assertEqual(
            collect.facts["cc.plugins.by_scope"]["provenance"],
            collect.AMBIGUOUS,
            "управляемая область не прочитана, а «плагин включён» подано "
            "как измерение")


class ОтказВДоступеЭтоНеОтсутствие(unittest.TestCase):
    """`Path.is_file()` проглатывает отказ в доступе и возвращает False.

    Для управляемой политики закрытый каталог — не поломка, а замысел
    администратора. Объявить такую область отсутствующей значит соврать про
    политику в пользу разрешения.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.корень = Path(tmp.name)

    def test_нет_файла_это_нет(self):
        self.assertEqual(collect._scope_state(self.корень / "нет.json"),
                         collect.НЕТ)

    def test_файл_на_месте_это_есть(self):
        файл = self.корень / "есть.json"
        файл.write_text("{}", encoding="utf-8")
        self.assertEqual(collect._scope_state(файл), collect.ЕСТЬ)

    def test_каталог_на_месте_файла_это_не_файл(self):
        каталог = self.корень / "каталог.json"
        каталог.mkdir()
        self.assertEqual(collect._scope_state(каталог), collect.НЕТ)

    def test_закрытый_каталог_это_не_отсутствие(self):
        закрытый = self.корень / "закрытый"
        закрытый.mkdir()
        цель = закрытый / "managed-settings.json"
        цель.write_text("{}", encoding="utf-8")
        os.chmod(закрытый, 0o000)
        self.addCleanup(os.chmod, закрытый, 0o700)
        # Права каталога не действуют на привилегированного пользователя.
        # Тогда проверяется ровно то, что проверяемо: файл виден, и это
        # «есть», а не выдуманная пустота. Утверждение остаётся истинным, а
        # не подгоняется под ожидание.
        try:
            os.listdir(закрытый)
            ожидание = collect.ЕСТЬ
        except OSError:
            ожидание = collect.ЗАКРЫТО
        self.assertEqual(collect._scope_state(цель), ожидание,
                         "отказ в доступе выдан за отсутствие файла")


if __name__ == "__main__":
    unittest.main()
