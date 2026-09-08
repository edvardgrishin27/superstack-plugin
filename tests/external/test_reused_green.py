#!/usr/bin/env python3
"""Зелёное не переиспользуется, когда изменился вход, которого не видит git.

Сценарий пришёл из внешней проверки и переписан здесь регрессионным тестом
ПРАВИЛЬНОГО поведения. Stop-хук пропускал полный прогон, если отпечаток дерева
совпадал с отпечатком прошлого ЗЕЛЁНОГО прогона. Отпечаток собирался из
`git diff HEAD` и `git ls-files -o --exclude-standard`, то есть уважал
`.gitignore`. Тест читал `.env.local`, лежащий в `.gitignore`: значение в нём
менялось с `1` на `0`, дерево для git оставалось прежним, хук молча возвращал
ноль и закрывал ход — а свежий прогон в этом же состоянии ПАДАЛ.

Цена ровно в том, ради чего гейт заведён. Молчание хука — это утверждение о
РАВЕНСТВЕ ДВУХ СОСТОЯНИЙ, и делалось оно, зная о них не всё. Хуже красного
прогона: красный виден, а это молчание неотличимо от пройденной проверки.

У отпечатка два потребителя, и требования у них разные. Счётчику попыток
(`verify.apply_attempt_ceiling`) грубого отпечатка достаточно: худшее, что
бывает, — потрачена лишняя попытка. Молчанию хука — нет. Поэтому чинится
второй, а первый остаётся ровно таким, каким был.

Что здесь заперто:
  · смена значения в игнорируемом git входе обесценивает прошлое зелёное:
    набор гоняется заново и красный прогон блокирует ход;
  · обратный контроль — БЕЗ единой правки зелёное переиспользуется
    по-прежнему, иначе хук снова начнёт гонять полный набор на каждом
    разговорном ходу, включая «расскажи анекдот»;
  · условие сценария проверяется отдельно: отпечаток ДЕРЕВА на той же смене
    значения не меняется — иначе всё ниже было бы зелёным мимо цели;
  · секрет человека попадает в отпечаток только хэшем: ни значения, ни
    фрагмента нет ни в отпечатке, ни в следе зелёного, ни в выводе хука;
  · отпечаток знает больше дерева — смену объявленной проверки, версию
    плагина, правку самого проверяющего и объявленные проектом входы;
  · неизвестный набор входов гасит переиспользование: «не знаю» не читается
    как «не менялось».

Git и pytest — НАСТОЯЩИЕ, во временной папке: подставной прогон доказывал бы
только то, что автор теста и автор проверки одинаково представляют себе вывод
инструментов. Тест самодостаточен: стандартная библиотека и файлы репозитория.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

РЕПО = Path(__file__).resolve().parents[2]
ПАКЕТ = РЕПО / "plugins" / "superstack"
ПРОВЕРКА = ПАКЕТ / "tools" / "verify.py"
ХУК = ПАКЕТ / "hooks" / "verify-gate.py"


def загрузить(путь: Path, имя: str):
    """Модуль из файла. В `sys.modules` — ОБЯЗАТЕЛЬНО: `@dataclass` ищет там
    модуль собственного класса, и без записи verify.py не грузится вовсе."""
    спец = importlib.util.spec_from_file_location(имя, путь)
    модуль = importlib.util.module_from_spec(спец)
    sys.modules[имя] = модуль
    try:
        спец.loader.exec_module(модуль)
    finally:
        sys.modules.pop(имя, None)
    return модуль


vf = загрузить(ПРОВЕРКА, "ss_a5_verify")

#: Значение, которого не должно быть НИГДЕ: ни в отпечатке, ни в следе
#: зелёного, ни в выводе хука. Нарочно не похоже на настоящий ключ — фикстура
#: с видом секрета сама по себе плохая идея.
КАНАРЕЙКА = "канарейка-не-ключ"

#: Проверка проекта читает ОДНО значение и сравнивает его. Разбор всего файла
#: в словарь был бы дырой в самом тесте: pytest печатает содержимое сравнения
#: при падении, и канарейка уехала бы в вывод из-за формы теста, а не из-за
#: дефекта.
ТЕСТ_ПРОЕКТА = '''import os
from pathlib import Path


def _значение(ключ):
    for строка in Path(".env.local").read_text("utf-8").splitlines():
        имя, _, значение = строка.partition("=")
        if имя.strip() == ключ:
            return значение.strip()
    return ""


def test_флаг_включён():
    метка = os.environ.get("A5_RUN_MARKER")
    if метка:
        with open(метка, "a", encoding="utf-8") as f:
            f.write("прогон\\n")
    assert _значение("FEATURE_ENABLED") == "1"
'''

ИГНОР = ".env.local\nsecrets/\npackage.json\n__pycache__/\n.pytest_cache/\n"


def есть_pytest() -> bool:
    """Спрашиваем ТОТ python3, которым проверка и будет запускать прогон."""
    try:
        p = subprocess.run(["python3", "-c", "import pytest"],
                           capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return p.returncode == 0


@unittest.skipUnless(shutil.which("git"), "git не установлен — проверять нечем")
@unittest.skipUnless(есть_pytest(), "pytest не установлен — прогонять нечем")
class НастоящийРепозиторий(unittest.TestCase):
    """Git-репозиторий и pytest во временной папке, ничего подставного."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # resolve обязателен: на macOS /var — симлинк на /private/var, и хук,
        # который разрешает путь проекта, не совпал бы с отметкой области.
        self.база = Path(tmp.name).resolve()
        self.корень = self.база / "проект"
        self.состояние = self.база / "состояние"
        self.метка = self.база / "прогоны.log"
        self.корень.mkdir()
        self.git("init", "-q", "-b", "основная")
        self.файл(".gitignore", ИГНОР)
        self.файл("tests/test_флаг.py", ТЕСТ_ПРОЕКТА)
        self.git("add", "-A")
        self.git("commit", "-qm", "начало")
        self.env_local("1")

    # --- сцена -------------------------------------------------------------

    @property
    def среда(self) -> dict:
        """Своя настройка git, свой каталог состояния, свой счётчик попыток.

        Без этого тест описывал бы машину автора: чужой ~/.gitconfig умеет
        и подписывать коммиты, и переименовывать ветку, а настоящий
        ~/.claude/superstack получал бы отметки от тестового прогона.
        """
        e = {**os.environ,
             "GIT_CONFIG_GLOBAL": os.devnull,
             "GIT_CONFIG_SYSTEM": os.devnull,
             "GIT_CONFIG_NOSYSTEM": "1",
             "GIT_AUTHOR_NAME": "тест", "GIT_AUTHOR_EMAIL": "т@т",
             "GIT_COMMITTER_NAME": "тест", "GIT_COMMITTER_EMAIL": "т@т",
             "GIT_TERMINAL_PROMPT": "0",
             "SUPERSTACK_STATE_DIR": str(self.состояние),
             "SUPERSTACK_PROJECT_DIR": str(self.корень),
             "SUPERSTACK_VERIFY_STATE": str(self.база / "попытки.json"),
             "SUPERSTACK_GATE_TIMEOUT": "600",
             "SUPERSTACK_IGNORE_PAUSE": "1",
             "A5_RUN_MARKER": str(self.метка)}
        e.pop("SUPERSTACK_DISABLE", None)
        return e

    def git(self, *арг: str) -> str:
        p = subprocess.run(["git", *арг], cwd=str(self.корень), env=self.среда,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, f"git {' '.join(арг)}: {p.stderr}")
        return p.stdout

    def файл(self, имя: str, текст: str) -> None:
        p = self.корень / имя
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(текст, encoding="utf-8")

    def env_local(self, флаг: str) -> None:
        """Тот самый игнорируемый git вход, от которого зависит прогон."""
        self.файл(".env.local", f"FEATURE_ENABLED={флаг}\n"
                                f"CANARY_NOT_A_KEY={КАНАРЕЙКА}\n")

    def позвали_здесь(self) -> None:
        self.состояние.mkdir(parents=True, exist_ok=True)
        (self.состояние / "projects").write_text(f"{self.корень}\n",
                                                 encoding="utf-8")

    def прогонов(self) -> int:
        try:
            return len(self.метка.read_text("utf-8").splitlines())
        except OSError:
            return 0

    def хук(self, сессия: str = "s1") -> subprocess.CompletedProcess:
        вход = json.dumps({"session_id": сессия, "hook_event_name": "Stop",
                           "stop_hook_active": False, "cwd": str(self.корень)},
                          ensure_ascii=False)
        return subprocess.run([sys.executable, str(ХУК)], input=вход,
                              capture_output=True, text=True, timeout=900,
                              env=self.среда, cwd=str(self.корень))

    def решение(self, p: subprocess.CompletedProcess) -> dict:
        self.assertEqual(p.returncode, 0,
                         f"хук вышел ненулём: {(p.stdout + p.stderr)[-600:]}")
        return json.loads(p.stdout) if p.stdout.strip() else {}


class ЗелёноеНеПереиспользуетсяПриСменеВхода(НастоящийРепозиторий):
    """Тот самый сценарий внешней проверки, целиком через настоящий хук."""

    def test_условие_сценария_отпечаток_дерева_слеп_к_этому_входу(self):
        """Условие, а не вывод из него.

        Если бы git видел правку `.env.local`, дальше проверялся бы другой
        случай, и весь набор был бы зелёным мимо цели.
        """
        дерево1 = vf._tree_fingerprint(self.корень)
        свой1 = vf._reuse_fingerprint(self.корень)
        self.assertIsNotNone(дерево1, "в свежем репозитории отпечаток не снялся")
        self.assertIsNotNone(свой1, "отпечаток переиспользования не снялся")
        self.env_local("0")
        self.assertEqual(дерево1, vf._tree_fingerprint(self.корень),
                         "git увидел правку игнорируемого файла — "
                         "проверяется не тот случай")
        self.assertNotEqual(свой1, vf._reuse_fingerprint(self.корень),
                            "смена значения во входе не обесценила зелёное")

    def test_счётчик_попыток_остаётся_на_грубом_отпечатке(self):
        """Не ломай первого, чиня второго.

        Счётчику попыток грубый отпечаток допустим: худшее — потрачена лишняя
        попытка. Ему остаётся ровно тот отпечаток, что был, и он по-прежнему
        гасит повтор БЕЗ изменений в дереве.
        """
        путь = self.база / "попытки-модульно.json"
        провал = {"status": "fail", "blockers": ["тесты: код возврата 1"],
                  "next": "почини"}
        снимок = vf._tree_fingerprint(self.корень)
        v1 = vf.apply_attempt_ceiling(провал, self.корень, путь, снимок)
        v2 = vf.apply_attempt_ceiling(провал, self.корень, путь, снимок)
        self.assertEqual((v1["attempt"], v2["attempt"]), (1, 1),
                         "повтор без изменений в дереве стал новым заходом")

    def test_ход_с_изменившимся_входом_проверяется_заново(self):
        """Сквозной прогон: настоящий хук, настоящая проверка, настоящий pytest.

        Три шага подряд, и средний важнее крайних: он и есть обратный
        контроль. Без него достаточно было бы никогда не переиспользовать
        зелёное — и хук снова гонял бы полный набор на каждом закрытии хода.
        """
        self.позвали_здесь()

        self.assertEqual(self.решение(self.хук()), {},
                         "зелёный прогон не дал закрыть ход")
        self.assertEqual(self.прогонов(), 1, "первый ход набор не прогнал")

        self.assertEqual(self.решение(self.хук()), {},
                         "второй ход не закрылся на зелёном отпечатке")
        self.assertEqual(self.прогонов(), 1,
                         "ничего не трогали, а набор прогнали заново")

        self.env_local("0")
        решение = self.решение(self.хук())
        self.assertEqual(решение.get("decision"), "block",
                         f"вход сменился, а ход закрылся молча: {решение}")
        self.assertEqual(self.прогонов(), 2,
                         "вход сменился, а набор не прогнали заново")

    def test_свежий_прогон_в_этом_же_состоянии_падает(self):
        """Вторая половина находки: молчание хука утверждало равенство с тем
        состоянием, в котором проверка НЕ проходит. Проверяется кодом
        возврата настоящей проверки, а не рассуждением."""
        self.env_local("0")
        p = subprocess.run([sys.executable, str(ПРОВЕРКА), "--json",
                            str(self.корень)],
                           capture_output=True, text=True, timeout=900,
                           env=self.среда)
        self.assertEqual(p.returncode, 1,
                         f"проверка не упала: {(p.stdout + p.stderr)[-600:]}")

    def test_секрет_не_выходит_ни_в_отпечаток_ни_в_след_ни_в_вывод(self):
        """Вход попадает в отпечаток ТОЛЬКО хэшем содержимого."""
        self.позвали_здесь()
        отпечаток = vf._reuse_fingerprint(self.корень)
        self.assertRegex(отпечаток or "", r"^[0-9a-f]{64}$",
                         "отпечаток не выглядит хэшем")

        self.решение(self.хук())
        следы = list((self.состояние / "gate-green").glob("*.fp"))
        self.assertTrue(следы, "зелёный прогон не оставил следа")
        for след in следы:
            self.assertNotIn(КАНАРЕЙКА, след.read_text("utf-8"),
                             f"секрет уехал в след зелёного: {след.name}")

        self.env_local("0")
        p = self.хук()
        for где, текст in (("stdout", p.stdout), ("stderr", p.stderr)):
            self.assertNotIn(КАНАРЕЙКА, текст, f"секрет уехал в {где} хука")
        # Отдельно: FEATURE_ENABLED в улику попасть МОЖЕТ (это тело чужого
        # assert, и его печатает pytest), а вот содержимое остальных строк
        # входа никакой дорогой наружу не идёт.
        self.assertNotIn("CANARY_NOT_A_KEY=" + КАНАРЕЙКА, p.stdout + p.stderr)


class ОтпечатокЗнаетБольшеДерева(НастоящийРепозиторий):
    """Дерево — только часть входа. Остальные части проверяются поимённо."""

    def test_без_единой_правки_отпечаток_тот_же(self):
        """Обратный контроль всего класса: отпечаток, который меняется сам по
        себе, выключил бы переиспользование целиком."""
        self.assertEqual(vf._reuse_fingerprint(self.корень),
                         vf._reuse_fingerprint(self.корень))

    def test_объявленный_проектом_вход_учитывается_наравне_со_своими(self):
        self.файл(".superstack/verify-inputs.json",
                  json.dumps({"inputs": ["secrets/*.ini"]}, ensure_ascii=False))
        self.файл("secrets/настройки.ini", "[раздел]\nрежим = боевой\n")
        было = vf._reuse_fingerprint(self.корень)
        дерево = vf._tree_fingerprint(self.корень)
        self.файл("secrets/настройки.ini", "[раздел]\nрежим = учебный\n")
        self.assertEqual(дерево, vf._tree_fingerprint(self.корень),
                         "git увидел игнорируемый вход — проверяется не тот случай")
        self.assertNotEqual(было, vf._reuse_fingerprint(self.корень),
                            "правка объявленного входа не обесценила зелёное")

    def test_неразобранное_объявление_входов_гасит_переиспользование(self):
        """«Набор входов неизвестен» — не «входов нет»."""
        self.файл(".superstack/verify-inputs.json", "{это не json")
        self.assertIsNone(vf._reuse_fingerprint(self.корень),
                          "неизвестный набор входов не погасил переиспользование")

    def test_смена_объявленной_проверки_обесценивает_зелёное(self):
        """package.json лежит в `.gitignore` — git его не видит, а проверка да.

        Случай нечастый, но он единственный, где смену объявленной команды
        видно ОТДЕЛЬНО от дерева: обычно такой файл отслеживается, и дерево
        меняется вместе с ним.
        """
        было = vf._reuse_fingerprint(self.корень)
        дерево = vf._tree_fingerprint(self.корень)
        self.файл("package.json", json.dumps({"scripts": {"test": "vitest run"}}))
        self.assertEqual(дерево, vf._tree_fingerprint(self.корень),
                         "git увидел игнорируемый package.json — не тот случай")
        self.assertNotEqual(было, vf._reuse_fingerprint(self.корень),
                            "новая объявленная проверка не обесценила зелёное")

    # --- версия проверяющего ------------------------------------------------

    def копия_плагина(self, версия: str, хвост: str = "") -> Path:
        """Копия проверки со своим манифестом — чтобы менять версию плагина,
        не трогая установленный."""
        корень = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, корень, ignore_errors=True)
        (корень / ".claude-plugin").mkdir(parents=True)
        (корень / "tools").mkdir()
        файл = корень / "tools" / "verify.py"
        файл.write_text(ПРОВЕРКА.read_text("utf-8") + хвост, encoding="utf-8")
        (корень / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "superstack", "version": версия}),
            encoding="utf-8")
        return файл

    def test_обновление_плагина_обесценивает_зелёное(self):
        a = загрузить(self.копия_плагина("0.0.1"), "ss_a5_verify_версия_a")
        b = загрузить(self.копия_плагина("0.0.2"), "ss_a5_verify_версия_b")
        первый, второй = (a._reuse_fingerprint(self.корень),
                          b._reuse_fingerprint(self.корень))
        self.assertIsNotNone(первый)
        self.assertNotEqual(первый, второй,
                            "смена версии плагина не обесценила зелёное")

    def test_правка_проверяющего_обесценивает_зелёное(self):
        a = загрузить(self.копия_плагина("0.0.1"), "ss_a5_verify_код_a")
        b = загрузить(self.копия_плагина("0.0.1", "\n# правка проверяющего\n"),
                      "ss_a5_verify_код_b")
        первый, второй = (a._reuse_fingerprint(self.корень),
                          b._reuse_fingerprint(self.корень))
        self.assertIsNotNone(первый)
        self.assertNotEqual(первый, второй,
                            "правка самой проверки не обесценила зелёное")

    def test_вход_входит_в_отпечаток_только_хэшем(self):
        """Ни значения, ни фрагмента: в собранную строку идёт sha256, и это
        проверяется сравнением с посчитанным здесь же хэшем."""
        свод = vf._inputs_digest(self.корень)
        содержимое = (self.корень / ".env.local").read_bytes()
        цифра = hashlib.sha256(содержимое).hexdigest()
        self.assertIn(f".env.local:{цифра}", свод)
        self.assertNotIn(КАНАРЕЙКА, свод)


if __name__ == "__main__":
    unittest.main()
