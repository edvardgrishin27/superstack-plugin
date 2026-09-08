#!/usr/bin/env python3
"""Откат возвращает СВОИ поля, а не файл целиком.

Три сценария внешней проверки, переписанные здесь регрессионными тестами
ПРАВИЛЬНОГО поведения.

  · Откат копировал файл целиком (`shutil.copy2`). Человек правил настройки
    ПОСЛЕ применения — и откат стирал его правку вместе со своей: плагин
    отменял не своё, а код возврата при этом был ноль.
  · Имя каталога копии имело точность до секунды и создавалось со слиянием.
    Два настоящих применения в одну секунду писали в один каталог: исходное
    состояние терялось, `undo` отвечал успехом, настройка оставалась.
    Там же: отсутствие файла до применения не записывалось вовсе — плагин
    создавал `settings.json`, и убрать его было нечем.
  · Имена файлов брались из манифеста и присоединялись к каталогу настроек
    без единой проверки: `../` в подменённой записи выводил запись наружу.

Фикстуры нарочно без строк, похожих на настоящий ключ: место секрета
обозначается словом, а не образцом. Тест самодостаточен — только стандартная
библиотека и сам инструмент.

  python3 -m pytest tests/external/test_undo_keeps_later_edits.py -q
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

РЕПО = Path(__file__).resolve().parents[2]

_спек = importlib.util.spec_from_file_location(
    "ss_apply_undo_fields",
    РЕПО / "plugins" / "superstack" / "tools" / "apply.py")
ap = importlib.util.module_from_spec(_спек)
_спек.loader.exec_module(ap)

#: Находка, ради которой плагин вообще трогает настройки. Одна и та же во
#: всех сценариях: проверяется откат, а не решатель.
НАХОДКА = {"id": "ctx.default-mode-unset", "class": "AUTO", "verdict": "FIX",
           "headline": "режим прав не задан"}

#: Поле, которое плагин записывает по этой находке, и его значение.
ПОЛЕ, ЗНАЧЕНИЕ_ПЛАГИНА = "permissions.defaultMode", "plan"

#: Место «секрета» в фикстуре. Настоящего ключа здесь нет и быть не должно:
#: проба сообщает ПУТЬ, а не значение, — значит для теста достаточно пути и
#: любого нейтрального текста на его месте.
СЛОВО_ВМЕСТО_СЕКРЕТА = "строка-которую-проба-назвала-секретом"


class Дом(unittest.TestCase):
    """Подставной дом: настоящий ~/.claude не участвует ни одной строкой."""

    def setUp(self):
        врем = tempfile.TemporaryDirectory()
        self.addCleanup(врем.cleanup)
        self.дом = Path(врем.name)
        self.настройки = self.дом / ".claude"
        self.настройки.mkdir(parents=True)
        прежние = (ap.HOME, ap.CLAUDE, ap.STATE, ap.BACKUPS, ap.QUARANTINE)

        def вернуть():
            (ap.HOME, ap.CLAUDE, ap.STATE, ap.BACKUPS,
             ap.QUARANTINE) = прежние

        self.addCleanup(вернуть)
        ap.HOME = self.дом
        ap.CLAUDE = self.настройки
        ap.STATE = self.настройки / "superstack"
        ap.BACKUPS = ap.STATE / "backups"
        ap.QUARANTINE = ap.STATE / "quarantine"

    # ---- вспомогательное ---------------------------------------------------
    @property
    def файл(self) -> Path:
        return self.настройки / "settings.json"

    def записать(self, данные: dict) -> None:
        self.файл.write_text(json.dumps(данные, ensure_ascii=False),
                             encoding="utf-8")

    def прочитать(self) -> dict:
        return json.loads(self.файл.read_text(encoding="utf-8"))

    def находки(self, список: list = None) -> str:
        путь = self.дом / f"находки-{len(list(self.дом.glob('находки-*')))}.json"
        путь.write_text(json.dumps({"findings": список or [НАХОДКА]},
                                   ensure_ascii=False), encoding="utf-8")
        return str(путь)

    def применить(self, список: list = None) -> str:
        """Прогнать применение и вернуть имя каталога копии."""
        with contextlib.redirect_stdout(io.StringIO()):
            код = ap.cmd_run([self.находки(список)])
        self.assertEqual(код, 0, "применение не прошло — откатывать нечего")
        каталоги = sorted(ap.BACKUPS.iterdir())
        self.assertTrue(каталоги, "копии не появилось")
        return каталоги[-1].name

    def откатить(self, ид: str) -> tuple:
        вывод, ошибки = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(вывод), contextlib.redirect_stderr(ошибки):
            код = ap.cmd_undo([ид])
        return код, вывод.getvalue(), ошибки.getvalue()


# ==========================================================================
# F01 — правка человека, сделанная ПОСЛЕ применения, переживает откат
# ==========================================================================
class ПравкаЧеловекаПереживаетОткат(Дом):
    """Сценарий внешней проверки дословно: тема, поле человека, откат."""

    def test_чужие_поля_остаются_на_месте(self):
        self.записать({"theme": "old"})
        ид = self.применить()
        self.assertEqual(self.прочитать()["permissions"]["defaultMode"],
                         ЗНАЧЕНИЕ_ПЛАГИНА, "плагин ничего не записал")

        # Человек правит настройки ПОСЛЕ применения — своими руками.
        живое = self.прочитать()
        живое["theme"] = "new"
        живое["userAdded"] = {"своё": 1}
        self.записать(живое)

        код, _, ошибки = self.откатить(ид)
        стало = self.прочитать()
        self.assertEqual(код, 0, f"откат не состоялся: {ошибки}")
        self.assertEqual(стало.get("theme"), "new",
                         "откат вернул тему к старому значению — стёр правку человека")
        self.assertEqual(стало.get("userAdded"), {"своё": 1},
                         "поле, добавленное человеком, исчезло при откате")
        self.assertNotIn("defaultMode", стало.get("permissions", {}),
                         "своё плагин не убрал")

    def test_поле_плагина_изменённое_человеком_не_откатывается(self):
        """Обратная сторона того же правила: чужим стало ЕГО поле.

        Плагин записал `plan`, человек переставил на своё. Возврат «как было»
        здесь означал бы стереть решение человека — поэтому поле остаётся, а
        человеку названо, что именно не откатили.
        """
        self.записать({})
        ид = self.применить()
        живое = self.прочитать()
        живое["permissions"]["defaultMode"] = "acceptEdits"
        self.записать(живое)

        код, _, ошибки = self.откатить(ид)
        self.assertEqual(self.прочитать()["permissions"]["defaultMode"],
                         "acceptEdits", "откат затёр значение, выбранное человеком")
        self.assertEqual(код, ap.UNDO_NOTHING_RESTORED,
                         "вернуть было нечего, а код говорит об успехе")
        self.assertIn(ПОЛЕ, ошибки, "не сказано, какое поле не откатили")
        self.assertNotIn("откатил", ошибки,
                         "подтверждение в прошедшем времени о том, чего не было")

    def test_обычный_откат_по_прежнему_возвращает_поле_плагина(self):
        """Обратный контроль: откат, не возвращающий ничего, тоже бесполезен."""
        было = {"theme": "old", "model": "opus"}
        self.записать(было)
        ид = self.применить()
        код, _, ошибки = self.откатить(ид)
        self.assertEqual(код, 0, f"откат не состоялся: {ошибки}")
        self.assertEqual(self.прочитать(), было,
                         "файл не вернулся к исходному состоянию")


# ==========================================================================
# F02 — своя копия на каждое применение
# ==========================================================================
class КопииНеСталкиваются(Дом):
    """Две копии в одну секунду обязаны быть двумя копиями."""

    def test_два_вызова_подряд_дают_разные_каталоги(self):
        """Без подмены часов: ровно то, что делала внешняя проверка."""
        self.записать({"theme": "первое"})
        первый = ap.make_backup([])
        self.записать({"theme": "второе"})
        второй = ap.make_backup([])
        self.assertNotEqual(первый["dir"], второй["dir"],
                            "второе применение пишет в каталог первого")
        сохранено = json.loads((Path(первый["dir"]) / "settings.json")
                               .read_text(encoding="utf-8"))
        self.assertEqual(сохранено["theme"], "первое",
                         "исходное состояние затёрто более поздней копией")

    def test_одна_и_та_же_секунда_всё_равно_даёт_разные_копии(self):
        """То же самое, но детерминированно: метка времени зафиксирована.

        Прогон на быстрой машине и так укладывается в секунду, но полагаться
        на это значило бы иметь проверку, которая иногда не проверяет.
        """
        прежний = ap.stamp
        ap.stamp = lambda: "20260101-000000"
        self.addCleanup(setattr, ap, "stamp", прежний)
        self.записать({"theme": "первое"})
        первый = ap.make_backup([])
        self.записать({"theme": "второе"})
        второй = ap.make_backup([])
        self.assertNotEqual(первый["dir"], второй["dir"])
        сохранено = json.loads((Path(первый["dir"]) / "settings.json")
                               .read_text(encoding="utf-8"))
        self.assertEqual(сохранено["theme"], "первое")

    def test_имена_копий_сортируются_по_времени(self):
        """Порядок имён — то, как слой выше находит последнюю копию."""
        прежний = ap.stamp
        ap.stamp = lambda: "20260101-000000"
        self.addCleanup(setattr, ap, "stamp", прежний)
        self.записать({})
        первое = Path(ap.make_backup([])["dir"]).name
        второе = Path(ap.make_backup([])["dir"]).name
        ap.stamp = lambda: "20260101-000001"
        третье = Path(ap.make_backup([])["dir"]).name
        self.assertEqual([первое, второе, третье],
                         sorted([первое, второе, третье]),
                         "имена копий перестали быть упорядоченными по времени")


class СозданныйФайлУбирается(Дом):
    """Отсутствие файла — тоже состояние, и оно записывается до записи."""

    def test_отсутствие_файла_записано_в_копию(self):
        сведения = ap.make_backup([])
        self.assertIs(сведения["before"]["settings.json"]["before_exists"], False,
                      "о том, что файла не было, в копии не осталось ни строки")

    def test_файла_не_было_откат_его_убирает(self):
        self.assertFalse(self.файл.exists(), "фикстура начинается не с пустоты")
        ид = self.применить()
        self.assertTrue(self.файл.is_file(), "плагин не создал файл — нечего убирать")
        код, _, ошибки = self.откатить(ид)
        self.assertEqual(код, 0, f"откат не состоялся: {ошибки}")
        self.assertFalse(self.файл.exists(),
                         "файл, созданный плагином, остался после отката")

    def test_чужое_в_созданном_файле_держит_его_на_месте(self):
        """Появилось чужое — файл остаётся, и человеку сказано, что в нём."""
        ид = self.применить()
        живое = self.прочитать()
        живое["userAdded"] = "моё"
        self.записать(живое)
        код, _, ошибки = self.откатить(ид)
        self.assertTrue(self.файл.is_file(),
                        "файл удалён вместе с тем, что человек в него дописал")
        self.assertEqual(self.прочитать(), {"userAdded": "моё"},
                         "убрано не только своё")
        self.assertEqual(код, 0, f"поле плагина всё же вернулось: {ошибки}")
        self.assertIn("userAdded", ошибки, "не названо, что осталось в файле")


# ==========================================================================
# F13 — имена из манифеста не берутся на веру
# ==========================================================================
class ИмяИзМанифестаПроверяется(Дом):
    """Свой же JSON, написанный вчера, — внешние данные."""

    def копия_с_именем(self, имя: str, ид: str = "20260101-000000") -> Path:
        """Копия ПРЕЖНЕГО формата с подставленным именем файла."""
        каталог = ap.BACKUPS / ид
        каталог.mkdir(parents=True)
        (каталог / "manifest.json").write_text(json.dumps({
            "created": "2026-01-01T00:00:00+00:00",
            "backup": {"dir": str(каталог), "files": [имя], "redacted": []},
            "applied": [{"id": "ctx.default-mode-unset", "action": "тест",
                         "changed": True, "field": ПОЛЕ,
                         "before": None, "after": ЗНАЧЕНИЕ_ПЛАГИНА}],
        }, ensure_ascii=False), encoding="utf-8")
        подмена = каталог / Path(имя).name
        подмена.parent.mkdir(parents=True, exist_ok=True)
        подмена.write_text('{"подменено": true}', encoding="utf-8")
        return каталог

    def test_имя_с_двумя_точками_отвергается(self):
        снаружи = self.дом / "outside-settings.txt"
        снаружи.write_text("файл человека вне настроек", encoding="utf-8")
        каталог = self.копия_с_именем("../outside-settings.txt")
        код, _, ошибки = self.откатить(каталог.name)
        self.assertEqual(снаружи.read_text(encoding="utf-8"),
                         "файл человека вне настроек",
                         "откат переписал файл ЗА каталогом настроек")
        self.assertNotEqual(код, 0, "выход за каталог настроек засчитан за успех")
        self.assertIn("outside-settings.txt", ошибки, "отказ не назвал имя")

    def test_чужое_имя_внутри_каталога_настроек_тоже_отвергается(self):
        """Путь никуда не уводит, но файл не из тех, что откату разрешено
        трогать. Одной проверки пути мало: `notes.md` лежит ровно там же."""
        чужой = self.настройки / "notes.md"
        чужой.write_text("заметки человека", encoding="utf-8")
        каталог = self.копия_с_именем("notes.md")
        код, _, ошибки = self.откатить(каталог.name)
        self.assertEqual(чужой.read_text(encoding="utf-8"), "заметки человека",
                         "откат переписал файл, которого не касался")
        self.assertNotEqual(код, 0)
        self.assertIn("notes.md", ошибки)

    def test_ссылка_наружу_под_разрешённым_именем_отвергается(self):
        """Имя из списка, а путь после разбора — снаружи.

        Список имён этого не ловит: `settings.json` в нём есть. Ловит только
        разбор пути, поэтому проверок две, а не одна.
        """
        снаружи = self.дом / "вне-настроек.json"
        снаружи.write_text('{"человек": "своё"}', encoding="utf-8")
        try:
            self.файл.symlink_to(снаружи)
        except (OSError, NotImplementedError) as e:      # noqa: BLE001
            self.skipTest(f"символические ссылки недоступны: {e}")
        каталог = self.копия_с_именем("settings.json")
        код, _, ошибки = self.откатить(каталог.name)
        self.assertEqual(снаружи.read_text(encoding="utf-8"), '{"человек": "своё"}',
                         "откат прошёл по ссылке и переписал файл снаружи")
        self.assertNotEqual(код, 0)
        self.assertIn("settings.json", ошибки)

    def test_разрешённое_имя_проходит(self):
        """Обратный контроль: проверка, отвергающая всё, тоже бесполезна."""
        self.assertEqual(ap.safe_target("settings.json"), self.файл)
        self.assertEqual(ap.safe_target("settings.local.json"),
                         self.настройки / "settings.local.json")

    def test_имя_копии_из_командной_строки_тоже_проверяется(self):
        код, _, ошибки = self.откатить("../../куда-нибудь")
        self.assertEqual(код, 1)
        self.assertNotIn("Traceback", ошибки)


# ==========================================================================
# секреты: правило переехало с файла на поле
# ==========================================================================
class СекретНеВозвращаетсяПолем(Дом):
    """Вырезанное не восстанавливается, соседнее по файлу — восстанавливается."""

    #: Значение, которое по журналу оставил на этом месте сам инструмент.
    #: Живое значение обязано СОВПАДАТЬ с ним — иначе решает не запрет на
    #: метку, а сравнение с чужой правкой, и запрет остаётся непроверенным.
    ЗАПИСАННОЕ_ИНСТРУМЕНТОМ = "значение-которое-оставил-инструмент"

    def запись_с_вырезанным(self) -> Path:
        """Копия НОВОГО формата, где значение «было» — метка, а не значение."""
        каталог = ap.BACKUPS / "20260101-000000"
        каталог.mkdir(parents=True)
        метка = "<ВЫРЕЗАНО-SUPERSTACK len=8 fp=0123456789ab>"
        (каталог / "manifest.json").write_text(json.dumps({
            "created": "2026-01-01T00:00:00+00:00",
            "backup": {"dir": str(каталог), "files": ["settings.json"],
                       "redacted": [{"file": "settings.json", "count": 1}],
                       "format": ap.UNDO_FORMAT,
                       "before": {"settings.json": {"before_exists": True}}},
            "applied": [{"id": "ctx.default-mode-unset", "action": "тест",
                         "changed": True, "file": "settings.json",
                         "field": "внешний.ключ", "before_present": True,
                         "before": метка,
                         "after": self.ЗАПИСАННОЕ_ИНСТРУМЕНТОМ}],
        }, ensure_ascii=False), encoding="utf-8")
        (каталог / "settings.json").write_text("{}", encoding="utf-8")
        return каталог

    def test_метка_не_записывается_поверх_живого_значения(self):
        каталог = self.запись_с_вырезанным()
        self.записать({"внешний": {"ключ": self.ЗАПИСАННОЕ_ИНСТРУМЕНТОМ}})
        код, _, ошибки = self.откатить(каталог.name)
        self.assertEqual(self.прочитать()["внешний"]["ключ"],
                         self.ЗАПИСАННОЕ_ИНСТРУМЕНТОМ,
                         "живое значение затёрто меткой из копии")
        self.assertEqual(код, ap.UNDO_NOTHING_RESTORED)
        self.assertIn("секрет", ошибки.lower(), "причина отказа не названа")

    def test_журнал_не_хранит_секрет_открытым_текстом(self):
        """Журнал лежит рядом с очищенной копией и обязан жить по её правилу."""
        запись = {"id": "x", "changed": True, "file": "settings.json",
                  "field": "внешний.ключ", "before_present": True,
                  "before": СЛОВО_ВМЕСТО_СЕКРЕТА, "after": "новое"}
        итог = ap._redact_journal(
            запись, [{"location": "внешний.ключ", "file": "settings.json"}])
        self.assertNotIn(СЛОВО_ВМЕСТО_СЕКРЕТА,
                         json.dumps(итог, ensure_ascii=False),
                         "значение, названное секретом, уехало в manifest.json")
        self.assertIn("ВЫРЕЗАНО-SUPERSTACK", итог["before"])

    def test_соседнее_поле_того_же_файла_возвращается(self):
        """Прежде вырезанный секрет запирал ВЕСЬ файл: не возвращалось ничего.

        Теперь запрет держится за поле, а всё остальное в том же файле
        откатывается как обычно.
        """
        self.записать({"permissions": {"allow": [СЛОВО_ВМЕСТО_СЕКРЕТА]}})
        ид = self.применить([
            {"id": "sec.secret-in-settings", "class": "ASK", "verdict": "ASK",
             "headline": "секрет в настройках",
             "evidence": {"sec.secret_matches": [
                 {"location": "permissions.allow[0]", "file": "settings.json"}]}},
            НАХОДКА])
        копия = (Path(ap.BACKUPS / ид) / "settings.json").read_text(encoding="utf-8")
        self.assertIn("ВЫРЕЗАНО-SUPERSTACK", копия,
                      "секрет не вырезан — проверять нечего")
        код, _, ошибки = self.откатить(ид)
        self.assertEqual(код, 0, f"соседнее поле не вернулось: {ошибки}")
        self.assertNotIn("defaultMode", self.прочитать()["permissions"],
                         "поле плагина осталось")
        self.assertEqual(self.прочитать()["permissions"]["allow"],
                         [СЛОВО_ВМЕСТО_СЕКРЕТА], "живое значение изменилось")


# ==========================================================================
# копии, снятые прежней версией
# ==========================================================================
class СтараяКопияНеПортится(Дом):
    """Формат сменился — прежние копии обязаны или работать, или отказать."""

    def старая_копия(self, содержимое: dict) -> Path:
        каталог = ap.BACKUPS / "20250101-000000"
        каталог.mkdir(parents=True)
        (каталог / "manifest.json").write_text(json.dumps({
            "created": "2025-01-01T00:00:00+00:00",
            "backup": {"dir": str(каталог), "files": ["settings.json"],
                       "redacted": []},
            "applied": [{"id": "ctx.default-mode-unset", "action": "тест",
                         "changed": True, "field": ПОЛЕ,
                         "before": None, "after": ЗНАЧЕНИЕ_ПЛАГИНА}],
        }, ensure_ascii=False), encoding="utf-8")
        (каталог / "settings.json").write_text(
            json.dumps(содержимое, ensure_ascii=False), encoding="utf-8")
        return каталог

    def test_старая_копия_возвращается_как_раньше(self):
        каталог = self.старая_копия({"theme": "old"})
        self.записать({"theme": "old", "permissions": {"defaultMode": "plan"}})
        код, _, ошибки = self.откатить(каталог.name)
        self.assertEqual(код, 0, f"старая копия перестала откатываться: {ошибки}")
        self.assertEqual(self.прочитать(), {"theme": "old"},
                         "прежний откат перестал возвращать файл целиком")

    def test_старая_копия_с_вырезанным_секретом_по_прежнему_отказывает(self):
        каталог = ap.BACKUPS / "20250101-000001"
        каталог.mkdir(parents=True)
        (каталог / "manifest.json").write_text(json.dumps({
            "created": "2025-01-01T00:00:00+00:00",
            "backup": {"dir": str(каталог), "files": ["settings.json"],
                       "redacted": [{"file": "settings.json", "count": 1}]},
            "applied": [],
        }, ensure_ascii=False), encoding="utf-8")
        (каталог / "settings.json").write_text(
            '{"ключ": "<ВЫРЕЗАНО-SUPERSTACK len=8 fp=0123456789ab>"}',
            encoding="utf-8")
        self.записать({"ключ": СЛОВО_ВМЕСТО_СЕКРЕТА})
        код, _, ошибки = self.откатить(каталог.name)
        self.assertEqual(код, ap.UNDO_NOTHING_RESTORED)
        self.assertEqual(self.прочитать()["ключ"], СЛОВО_ВМЕСТО_СЕКРЕТА,
                         "живое значение затёрто меткой из старой копии")
        self.assertIn("секрет", ошибки.lower())

    def test_незнакомый_формат_копии_отвергается_с_причиной(self):
        """Копия из будущего: метка есть, но эта версия её не знает.

        Разобрать её прежним способом значило бы вернуть не то и не туда,
        отчитавшись успехом. Отказ здесь дешевле молчаливой порчи.
        """
        каталог = ap.BACKUPS / "20270101-000000"
        каталог.mkdir(parents=True)
        (каталог / "manifest.json").write_text(json.dumps({
            "created": "2027-01-01T00:00:00+00:00",
            "backup": {"dir": str(каталог), "files": ["settings.json"],
                       "redacted": [], "format": "superstack.undo.v9"},
            "applied": [],
        }, ensure_ascii=False), encoding="utf-8")
        (каталог / "settings.json").write_text('{"из": "будущего"}',
                                               encoding="utf-8")
        self.записать({"моё": 1})
        код, _, ошибки = self.откатить(каталог.name)
        self.assertEqual(код, 1, "чужой формат разобран как свой")
        self.assertEqual(self.прочитать(), {"моё": 1},
                         "настройки тронуты копией неизвестного формата")
        self.assertIn("superstack.undo.v9", ошибки, "формат не назван")

    def test_новая_копия_помечена_форматом(self):
        """Различить форматы можно только по метке — иначе старая копия
        разбиралась бы как новая и молча не вернула бы ничего."""
        self.записать({})
        сведения = ap.make_backup([])
        self.assertEqual(сведения["format"], ap.UNDO_FORMAT)


class ЖурналЗаписанВМоментПрименения(Дом):
    """Что записано — то и откатывается; записывается это при применении."""

    def test_запись_называет_поле_и_обе_величины(self):
        self.записать({})
        ид = self.применить()
        манифест = json.loads((ap.BACKUPS / ид / "manifest.json")
                              .read_text(encoding="utf-8"))
        запись = манифест["applied"][0]
        self.assertEqual(запись["file"], "settings.json")
        self.assertEqual(запись["field"], ПОЛЕ)
        self.assertIs(запись["before_present"], False,
                      "не записано, было ли поле до применения")
        self.assertEqual(запись["after"], ЗНАЧЕНИЕ_ПЛАГИНА)

    def test_состояние_файлов_записано_до_первой_записи(self):
        self.записать({})
        ид = self.применить()
        манифест = json.loads((ap.BACKUPS / ид / "manifest.json")
                              .read_text(encoding="utf-8"))
        состояние = манифест["backup"]["before"]
        self.assertIs(состояние["settings.json"]["before_exists"], True)
        self.assertIs(состояние["settings.local.json"]["before_exists"], False)


if __name__ == "__main__":
    unittest.main()
