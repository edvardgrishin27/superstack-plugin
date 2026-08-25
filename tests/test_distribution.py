#!/usr/bin/env python3
"""Тесты слоя дистрибуции: как система попадает к человеку и как заговаривает.

Четыре вещи, каждая со своим способом молча сломаться:

  · хук первой сессии — ломается тем, что напоминает ВЕЧНО;
  · хук гейта верификации — ломается двумя способами сразу: перестаёт
    блокировать (правило снова становится пожеланием) или блокирует всегда
    (ход нечем закрыть, и плагин сносят в первый день);
  · SKILL.md — ломается тем, что ссылается на несуществующий инструмент
    или на переменную оболочки, которая не переживает вызов Bash;
  · манифест плагина — ломается тем, что объявляет hooks/hooks.json,
    который и так грузится по соглашению.

Все отказы тихие: ни ошибки, ни предупреждения.

Отдельно оговорено: тесты, проверяющие ФОРМУЛИРОВКУ, а не поведение,
названы так в своих докстрингах. Проза не проверяется тестом на подстроку,
и выдавать такую проверку за поведенческую нельзя.
"""
from __future__ import annotations

import concurrent.futures
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG, REPO, at, packages  # noqa: E402

ROOT = REPO
#: Хук переехал на Python: `sh` был лишней зависимостью, а без
#: python3 продукт не работает вообще. `.sh` остался обёрткой.
HOOK = at("hooks", "first-run.py")
#: Гейт переехал на Python вместе с остальными хуками: `sh` был лишней
#: зависимостью, а экранирование JSON руками — самой хрупкой его частью.
GATE = at("hooks", "verify-gate.py")
SKILL = at("skills", "superstack", "SKILL.md")
MANIFESTS = [d / ".claude-plugin" / "plugin.json" for d in packages()]
ENV = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"}

#: команда отказа, как она напечатана в подсказке. Путь может содержать
#: пробелы, поэтому «до слова done», а не «одно слово».
REFUSAL_RE = re.compile(r"(python3 .+? done)")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


collect = _load("ds_collect", at("tools", "probe", "collect.py"))


def run_hook(script: Path, state: Path, *args, disable: str = "",
             env_extra: dict | None = None,
             cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Запуск хука в песочнице.

    SUPERSTACK_STATE_DIR задаётся ВСЕГДА: без него хук пишет в настоящий
    ~/.claude, и тест начинает зависеть от машины, на которой запущен.
    """
    env = {**os.environ, "SUPERSTACK_STATE_DIR": str(state)}
    env.pop("SUPERSTACK_DISABLE", None)
    if disable:
        env["SUPERSTACK_DISABLE"] = disable
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, str(script)] + list(args),
                          capture_output=True, text=True, timeout=60, env=env,
                          cwd=str(cwd) if cwd else None)


def hook(state: Path, *args, disable: str = "") -> subprocess.CompletedProcess:
    return run_hook(HOOK, state, *args, disable=disable)


def facts_file(values: dict) -> str:
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({k: {"value": v, "probe": "t", "evidence": None,
                   "provenance": "EXTRACTED"} for k, v in values.items()},
              fh, ensure_ascii=False)
    fh.close()
    return fh.name


def facts_blob(values: dict) -> str:
    """Тот же формат фактов, но строкой — для подкладывания в конвейер."""
    return json.dumps({k: {"value": v, "probe": "t", "evidence": None,
                           "provenance": "EXTRACTED"} for k, v in values.items()},
                      ensure_ascii=False)


def tree(root: Path) -> dict:
    """Слепок каталога: путь -> содержимое. Сравнение двух слепков отвечает
    на вопрос «изменилось ли здесь хоть что-нибудь», а не «существует ли файл»."""
    out: dict = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_symlink():
            out[rel] = "symlink:" + os.readlink(p)
        elif p.is_dir():
            out[rel] = "dir"
        else:
            out[rel] = p.read_bytes()
    return out


class TestFirstRunHook(unittest.TestCase):
    """Хук предлагает проверку. Единственный способ сделать хуже, чем ничего, —
    предлагать бесконечно."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "superstack"

    def tearDown(self):
        self.tmp.cleanup()

    def copy_hook_into(self, dirname: str) -> Path:
        """Копия хука в каталоге с заданным именем: путь к самому скрипту —
        это данные, которые хук печатает, и имя каталога решает, выживут они
        или нет."""
        d = Path(self.tmp.name) / dirname
        d.mkdir(parents=True)
        dst = d / "first-run.py"
        shutil.copy2(HOOK, dst)
        return dst

    def refusal_command(self, script: Path, state: Path) -> str:
        out = run_hook(script, state).stdout
        # Разбор здесь — часть проверки: путь с кавычкой ломал весь JSON.
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        m = REFUSAL_RE.search(ctx)
        self.assertIsNotNone(m, f"в подсказке нет команды отказа: {ctx}")
        return m.group(1)

    def test_offers_exactly_three_times_then_never(self):
        seen = [bool(hook(self.state).stdout.strip()) for _ in range(6)]
        self.assertEqual(seen, [True, True, True, False, False, False],
                         f"предложений не три, а по-другому: {seen}")

    def test_counter_is_visible_to_the_person(self):
        """Иначе «сколько мне ещё это терпеть» — вопрос без ответа."""
        hook(self.state)
        self.assertIn("1 из 3", hook(self.state, "status").stdout)

    def test_explicit_no_stops_it_before_the_third_time(self):
        hook(self.state)
        done = hook(self.state, "done")
        self.assertEqual(done.returncode, 0)
        self.assertFalse(hook(self.state).stdout.strip(),
                         "человек отказался, а напоминание пришло снова")
        self.assertIn("выключено", hook(self.state, "status").stdout)

    def test_printed_refusal_command_actually_runs_from_a_path_with_a_space(self):
        """Напечатанная команда — ЕДИНСТВЕННЫЙ способ отказаться навсегда.

        Каталог этого проекта содержит пробел («Super стэк»). Без кавычек
        вокруг пути напечатанная строка при исполнении даёт «No such file or
        directory», rc=127 — то есть выход есть на бумаге и нет на деле,
        и человек узнаёт об этом, только попробовав.
        """
        script = self.copy_hook_into("каталог с пробелом")
        state = Path(self.tmp.name) / "st-space"
        cmd = self.refusal_command(script, state)
        r = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True,
                           timeout=60,
                           env={**os.environ, "SUPERSTACK_STATE_DIR": str(state)})
        self.assertEqual(r.returncode, 0,
                         f"напечатанная команда отказа не исполняется: {cmd!r}\n{r.stderr}")
        self.assertFalse(run_hook(script, state).stdout.strip(),
                         "команда отработала, а напоминание всё равно пришло")
        self.assertIn("выключено", run_hook(script, state, "status").stdout)

    def test_json_survives_a_quote_and_a_backslash_in_its_own_path(self):
        """Путь подставляется в строку JSON. Кавычка или обратный слэш в нём
        ломают разбор ВСЕГО вывода — хук перестаёт существовать для Claude Code
        целиком, и ни одного сообщения об этом никто не увидит."""
        script = self.copy_hook_into('кавычка"и\\слэш')
        state = Path(self.tmp.name) / "st-quote"
        out = run_hook(script, state).stdout
        data = json.loads(out)  # именно здесь падал неэкранированный путь
        self.assertIn("first-run.py",
                      data["hookSpecificOutput"]["additionalContext"])
        cmd = self.refusal_command(script, Path(self.tmp.name) / "st-quote2")
        r = subprocess.run(
            ["sh", "-c", cmd], capture_output=True, text=True, timeout=60,
            env={**os.environ,
                 "SUPERSTACK_STATE_DIR": str(Path(self.tmp.name) / "st-quote2")})
        self.assertEqual(r.returncode, 0,
                         f"команда отказа не пережила кавычку в пути: {cmd!r}\n{r.stderr}")

    def witness_cat(self) -> tuple:
        """Подменный `cat` на PATH: доносит, стоял ли замок в тот момент,
        когда хук читал счётчик.

        Зачем подмена, а не гонка: настоящая гонка ловит снятый замок только
        по удаче планировщика (замеряли — примерно два прогона из трёх), и на
        чужой машине результат будет другим. Момент чтения счётчика — это и
        есть внутренняя точка критической секции, и `cat` — единственная
        внешняя программа, которую хук в ней вызывает. Значит про этот момент
        можно спросить прямо, ничего не угадывая.
        """
        # Единственная связь с машиной — путь к настоящему cat. Если его нет,
        # мёртв и сам хук: он читает им счётчик. Молчаливого пропуска здесь
        # быть не должно — «не смог проверить» обязано звучать вслух.
        real_cat = shutil.which("cat")
        self.assertIsNotNone(real_cat, "на этой машине нет cat — хук нечем читать")
        bindir = Path(self.tmp.name) / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        witness = Path(self.tmp.name) / "witness.txt"
        stub = bindir / "cat"
        stub.write_text(
            "#!/bin/sh\n"
            'if [ -d "$SS_LOCK" ]; then printf "взят\\n" >> "$SS_WITNESS"\n'
            'else printf "не взят\\n" >> "$SS_WITNESS"; fi\n'
            # exec настоящего cat: подмена обязана остаться наблюдателем.
            # Подменив ещё и поведение, тест начал бы проверять сам себя.
            'exec %s "$@"\n' % real_cat,
            encoding="utf-8")
        stub.chmod(0o755)
        return bindir, witness

    def test_lock_is_held_while_the_counter_is_read_and_rewritten(self):
        """Замок обязан стоять ВНУТРИ чтения-изменения-записи, а не рядом.

        Проверка «сколько уже предложили» и запись нового значения — один
        неделимый шаг. Если замок снят, каждое из одновременно открытых окон
        читает одно и то же число и печатает своё предложение: замеряли
        11-24 предложения вместо трёх.

        Счётчик подкладывается заранее, иначе хук на первом запуске отвечает
        «0» не читая файла, и про чтение спросить будет нечего.
        """
        # Наблюдается ИСХОД, а не механизм. Прежняя версия подменяла `cat` на
        # PATH и смотрела, читается ли счётчик под замком, — способ, который
        # работал ровно пока хук был скриптом оболочки. Питоновский читает файл
        # сам, никакого `cat` не зовёт, и наблюдатель молчал бы на исправном
        # коде. Проверка исхода не зависит от того, чем хук написан, и ловит
        # ровно то, ради чего замок стоит: одновременные окна не должны
        # напечатать больше, чем осталось до потолка.
        import concurrent.futures as futures

        self.state.mkdir(parents=True)
        (self.state / "first-run.count").write_text("2", encoding="utf-8")

        def запуск(_):
            return run_hook(HOOK, self.state).stdout.strip()

        with futures.ThreadPoolExecutor(max_workers=8) as пул:
            ответы = [о for о in пул.map(запуск, range(8)) if о]

        self.assertEqual(len(ответы), 1,
                         f"до потолка оставалось одно предложение, напечатано "
                         f"{len(ответы)} — счётчик читался без замка")
        self.assertEqual((self.state / "first-run.count").read_text("utf-8").strip(),
                         "3", "счётчик не досчитал до потолка")

    def test_a_lock_held_by_someone_else_silences_it(self):
        """Не взяли замок — молчим, и чужой замок не трогаем.

        Обратная сторона того же механизма, и её видно без всякой гонки:
        пока замок занят соседним окном, второе окно обязано выйти, ничего
        не напечатав и НЕ потратив попытку. Молчание здесь дешевле: цена
        ошибки — одно непоказанное предложение, а не бесконечный поток.
        """
        self.state.mkdir(parents=True)
        lock = self.state / "first-run.lock"
        lock.mkdir()
        counter = self.state / "first-run.count"
        counter.write_text("1", encoding="utf-8")

        r = hook(self.state)

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(r.stdout.strip(),
                         "замок занят, а хук всё равно предложил")
        self.assertEqual(counter.read_text(encoding="utf-8"), "1",
                         "хук потратил попытку, не показав предложения")
        self.assertTrue(lock.is_dir(),
                        "хук снёс чужой замок — сосед остался без защиты")

    def test_the_lock_is_released_after_a_normal_run(self):
        """Замок, забытый после обычного прохода, глушит хук навсегда:
        следующее окно упрётся в него и промолчит — и так до конца времён."""
        self.assertTrue(hook(self.state).stdout.strip(), "хук не предложил")
        self.assertFalse((self.state / "first-run.lock").exists(),
                         "замок остался лежать после обычного прохода")

    def test_parallel_starts_cannot_exceed_the_limit(self):
        """Несколько открытых окон — обычный сценарий, а не редкость.

        Без атомарности проверка «сколько уже предложили» и запись нового
        значения разъезжаются: каждый процесс читает одно и то же и печатает.
        Замеряли: 8 параллельных стартов × 6 раундов давали от 11 до 24
        предложений вместо трёх.

        Оговорка: со снятым замком этот тест краснеет НЕ всегда — исход
        зависит от планировщика (замеряли: 3 зелёных прогона из 12 при
        заведомо снятом замке). Держит механизм не он, а два теста выше;
        этот остаётся как сквозная проверка на настоящих процессах.
        """
        workers, rounds = 8, 6
        offers = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            for _ in range(rounds):
                outs = list(pool.map(lambda _i: hook(self.state).stdout.strip(),
                                     range(workers)))
                offers += sum(1 for o in outs if o)
        self.assertEqual(offers, 3,
                         f"при параллельных стартах предложений {offers}, а не 3")

    def test_garbage_in_the_counter_closes_it_instead_of_resetting_it(self):
        """Мусор в счётчике не имеет права обнулять лимит.

        Строка «3 sessions» вместо числа — и прежняя версия считала это нулём:
        ещё три напоминания, и так каждый раз, пока мусор лежит. Правильное
        поведение — закрыться: нечитаемый счётчик считается исчерпанным.
        """
        self.state.mkdir(parents=True)
        counter = self.state / "first-run.count"
        counter.write_text("3 sessions", encoding="utf-8")
        r = hook(self.state)
        self.assertFalse(r.stdout.strip(), "мусор в счётчике обнулил лимит")
        self.assertFalse(r.stderr.strip(),
                         f"мусор в счётчике вылез человеку в stderr: {r.stderr!r}")
        self.assertEqual(counter.read_text(encoding="utf-8"), "3 sessions",
                         "счётчик переписан — значит лимит начался заново")
        self.assertFalse(hook(self.state).stdout.strip(),
                         "второй запуск снова предложил")

    def test_kill_switch_silences_it(self):
        self.assertFalse(hook(self.state, disable="1").stdout.strip())

    def test_kill_switch_does_not_burn_an_attempt(self):
        """Обратный контроль: выключенный хук не должен тратить счётчик —
        иначе три сессии с выключателем съедали бы все предложения молча."""
        for _ in range(3):
            hook(self.state, disable="1")
        self.assertTrue(hook(self.state).stdout.strip(),
                        "выключатель израсходовал попытки, которых никто не видел")

    def test_silent_when_the_counter_cannot_be_persisted(self):
        """Худший исход из всех: напоминание, которое некому сосчитать.

        Если хранилище недоступно, счётчик не растёт — и хук, печатающий
        безусловно, будет предлагать проверку каждую сессию до конца времён.
        Правильное поведение — молчать.
        """
        blocked = Path(self.tmp.name) / "ro"
        blocked.mkdir()
        os.chmod(blocked, 0o500)
        try:
            out = hook(blocked / "superstack").stdout.strip()
        finally:
            os.chmod(blocked, 0o700)
        self.assertFalse(out, "счётчик не пишется, а предложение всё равно вышло")

    def test_silent_when_the_write_returns_zero_but_nothing_persists(self):
        """Каталог создаётся, запись возвращает ноль — а счётчик не растёт.

        Тот случай, который проверка «удалось ли создать каталог» пропускает:
        она срабатывает раньше и до записи дело не доходит. Здесь каталог
        есть, запись формально успешна, и единственное, что отделяет от
        вечного напоминания, — перечитывание счётчика после записи.
        """
        self.state.mkdir(parents=True)
        (self.state / "first-run.count").symlink_to("/dev/null")
        for i in range(3):
            r = hook(self.state)
            self.assertFalse(r.stdout.strip(),
                             f"запуск {i + 1}: счётчик не сохранился, а предложение вышло")

    @unittest.skipIf(os.geteuid() == 0, "под root права на файл записи не запрещают")
    def test_no_noise_on_stderr_when_the_counter_is_not_writable(self):
        """stdout чист, а человек всё равно видит «Permission denied».

        Перенаправление в файл применяется раньше, чем 2>/dev/null, если
        стоит левее его: жалоба оболочки успевает уйти человеку на каждом
        старте сессии. Молчать надо в обе стороны, а не только в stdout.
        """
        self.state.mkdir(parents=True)
        counter = self.state / "first-run.count"
        counter.write_text("0\n", encoding="utf-8")
        os.chmod(counter, 0o444)
        try:
            r = hook(self.state)
        finally:
            os.chmod(counter, 0o644)
        self.assertFalse(r.stdout.strip(), "счётчик не пишется, а предложение вышло")
        self.assertFalse(r.stderr.strip(),
                         f"ошибка оболочки ушла человеку: {r.stderr!r}")

    def test_output_is_json_claude_code_can_read(self):
        out = hook(self.state).stdout
        data = json.loads(out)
        spec = data["hookSpecificOutput"]
        self.assertEqual(spec["hookEventName"], "SessionStart")
        self.assertIn("superstack", spec["additionalContext"])

    def test_does_not_fabricate_a_user_message(self):
        """initialUserMessage завёл бы разговор сам: приписал человеку слова,
        которых он не говорил, и запустил работу до согласия."""
        data = json.loads(hook(self.state).stdout)
        self.assertNotIn("initialUserMessage", data["hookSpecificOutput"])

    def test_tells_how_to_turn_it_off(self):
        ctx = json.loads(hook(self.state).stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("done", ctx)
        self.assertIn("first-run.py", ctx)

    def test_always_exits_zero(self):
        """SessionStart блокировать не умеет, но ненулевой код показывает
        человеку stderr при каждом старте — это тоже спам."""
        for _ in range(5):
            self.assertEqual(hook(self.state).returncode, 0)

    def test_changes_nothing_outside_its_own_state_dir(self):
        """Хук ПРЕДЛАГАЕТ. Всё, что он трогает, — свой каталог состояния.

        Проверяется слепком: домашний каталог и каталог, из которого хук
        запущен, обязаны совпасть побайтово до и после. Заявление «ничего не
        меняет» иначе остаётся заявлением.
        """
        base = Path(self.tmp.name)
        home = base / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text('{"permissions":{}}', encoding="utf-8")
        (home / ".claude.json").write_text("{}", encoding="utf-8")
        work = base / "work"
        work.mkdir()
        (work / "src.py").write_text("x = 1\n", encoding="utf-8")
        state = base / "state"

        before_home, before_work = tree(home), tree(work)
        r = run_hook(HOOK, state, env_extra={"HOME": str(home)}, cwd=work)

        # Положительный контроль: если хук промолчал, сравнивать было нечего.
        self.assertTrue(r.stdout.strip(), "хук ничего не сделал — тест пустой")
        self.assertTrue((state / "first-run.count").is_file(),
                        "счётчик не появился — тест ничего не проверил")
        self.assertEqual(tree(home), before_home,
                         "хук изменил что-то в домашнем каталоге")
        self.assertEqual(tree(work), before_work,
                         "хук изменил каталог, в котором его запустили")

    def test_does_not_launch_anything(self):
        """Хук не имеет права ЗАПУСКАТЬ проверку — он её предлагает.

        В начало PATH кладутся подставки с именами всего, чем можно запустить
        проверку или изменить машину. Каждая записывает свой вызов в журнал.
        Пустой журнал — доказательство, а не обещание.
        """
        base = Path(self.tmp.name)
        binpath = base / "bin"
        binpath.mkdir()
        log = base / "calls.log"
        shimmed = ("claude", "python3", "python", "node", "npm", "npx", "git",
                   "curl", "wget", "rm", "mv", "cp", "chmod", "open",
                   "osascript", "defaults", "ssh", "launchctl")
        for name in shimmed:
            p = binpath / name
            p.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$0 $*" >> "{log}"\nexit 0\n',
                         encoding="utf-8")
            os.chmod(p, 0o755)

        r = run_hook(HOOK, self.state,
                     env_extra={"PATH": f"{binpath}:{os.environ['PATH']}"})

        # Положительный контроль: подставки не должны были сломать сам хук.
        self.assertTrue(r.stdout.strip(), "хук замолчал — значит проверять нечего")
        calls = log.read_text(encoding="utf-8") if log.exists() else ""
        self.assertEqual(calls, "", f"хук что-то запустил: {calls!r}")

    def test_offer_text_forbids_retelling_it_verbatim(self):
        """ПРОВЕРКА ФОРМУЛИРОВКИ, А НЕ ПОВЕДЕНИЯ.

        Текст предложения исполняет не этот тест, а модель, и заставить её
        тестом нельзя. Здесь проверяется один смысловой инвариант: рядом с
        глаголом пересказа обязано стоять отрицание. Инверсия смысла
        («перескажи дословно») отрицание убирает и роняет тест; безобидный
        парафраз («дословно не повторяй») его сохраняет.

        Чего этот тест НЕ доказывает: что модель послушается, и что инверсию
        нельзя переписать другими словами так, чтобы отрицание уцелело.
        """
        ctx = json.loads(hook(self.state).stdout)["hookSpecificOutput"]["additionalContext"]
        verbatim = [s for s in re.split(r"[.!?]", ctx) if "дословн" in s.lower()]
        self.assertTrue(verbatim, "в предложении вообще нет речи о дословном пересказе")
        negated = re.compile(
            r"\bне\s+(?:\w+\s+){0,2}(?:пересказ|повтор|цитир|воспроизвод)",
            re.I)
        self.assertTrue(any(negated.search(s) for s in verbatim),
                        f"запрет на дословный пересказ потерял отрицание: {verbatim}")


class TestVerifyGateHook(unittest.TestCase):
    """Stop-хук, который САМ гоняет проверку и решает по её коду возврата.

    Проверяемое утверждение одно: правило «не закрывай ход при коде ≠ 0»
    перестало быть пожеланием. Пожелание исполняет та же модель, которую
    проверяют, и её неисполнение не видно никому.

    Проверка подставная НАМЕРЕННО. Хук ищет tools/verify.py рядом с собой,
    поэтому копия хука в песочнице зовёт подставной verify.py с заданным кодом
    возврата. Так тест описывает КОНТРАКТ хука (какой код возврата к какому
    решению ведёт), а не поведение verify.py на машине, где его запустили:
    настоящий verify.py зависит от того, что установлено, и одинакового
    результата на другой машине не даёт. Что настоящий путь всё-таки сходится —
    отдельным тестом ниже.
    """

    #: то, что хук печатает, когда запрещает закрыть ход. Значение взято из
    #: документации Claude Code по событию Stop, а не из кода хука: ожидаемое,
    #: списанное с проверяемого, — тавтология.
    BLOCK_DECISION = "block"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.state = base / "state"
        self.project = base / "project"
        self.project.mkdir()
        self.plug = base / "plug"
        (self.plug / "hooks").mkdir(parents=True)
        (self.plug / "tools").mkdir(parents=True)
        self.script = self.plug / "hooks" / "verify-gate.py"
        shutil.copy2(GATE, self.script)
        #: отметка «подставная проверка была запущена». Пустой файл — улика,
        #: что хук дошёл до запуска; его отсутствие — что не дошёл.
        self.ran = base / "verify-ran"

    def tearDown(self):
        self.tmp.cleanup()

    # --- песочница ---------------------------------------------------------

    def stub(self, code: int, text: str = "") -> None:
        """Подставная проверка: печатает что велено и выходит заданным кодом."""
        src = ("import sys\n"
               "open(%r, 'a').close()\n" % str(self.ran) +
               "sys.stderr.write(%r)\n" % text +
               "sys.exit(%d)\n" % code)
        (self.plug / "tools" / "verify.py").write_text(src, encoding="utf-8")

    def stub_hangs(self, seconds: int = 60) -> None:
        (self.plug / "tools" / "verify.py").write_text(
            "import time\n"
            "open(%r, 'a').close()\n" % str(self.ran) +
            "time.sleep(%d)\n" % seconds, encoding="utf-8")

    def stdin_for(self, session: str = "s1", active: bool = False) -> str:
        return json.dumps({"session_id": session, "hook_event_name": "Stop",
                           "stop_hook_active": active,
                           "cwd": str(self.project)}, ensure_ascii=False)

    def gate(self, stdin: str | None = None, env_extra: dict | None = None,
             script: Path | None = None, project: Path | None = None,
             timeout: int = 90) -> subprocess.CompletedProcess:
        """Запуск хука в песочнице.

        SUPERSTACK_STATE_DIR и SUPERSTACK_PROJECT_DIR задаются ВСЕГДА: без них
        хук читает настоящий ~/.claude и настоящий рабочий каталог, и тест
        начинает описывать машину, а не продукт. Выключатель и обход паузы
        из окружения вычищаются по той же причине.
        """
        env = {**os.environ,
               "SUPERSTACK_STATE_DIR": str(self.state),
               "SUPERSTACK_PROJECT_DIR": str(project or self.project)}
        env.pop("SUPERSTACK_DISABLE", None)
        env.pop("SUPERSTACK_IGNORE_PAUSE", None)
        env.pop("SUPERSTACK_GATE_TIMEOUT", None)
        if env_extra:
            env.update(env_extra)
        # Проект заводится ПОСЛЕ env_extra: часть тестов подменяет каталог
        # состояния, и отметка должна лечь в тот, который увидит хук.
        # Без отметки хук молчит — он работает только там, где SUPERSTACK
        # позвали, — и все эти тесты доказывали бы одну лишь немоту. Сам гейт
        # области проверяется отдельно, в test_project_scope.py.
        # Отдельный тест делает каталог состояния нечитаемым для записи и сам
        # готовит реестр заранее — там отметка уже лежит, и падать на ней
        # нельзя: упавшая фикстура выглядела бы дефектом продукта.
        try:
            state_dir = Path(env["SUPERSTACK_STATE_DIR"])
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "projects").write_text(
                str(project or self.project) + "\n", encoding="utf-8")
        except OSError:
            pass
        return subprocess.run([sys.executable, str(script or self.script)],
                              input=self.stdin_for() if stdin is None else stdin,
                              capture_output=True, text=True, timeout=timeout,
                              env=env)

    def decision(self, r: subprocess.CompletedProcess):
        """Решение хука: разбор здесь — часть проверки.

        Невалидный JSON означает, что хука для Claude Code не существует вовсе,
        и узнать об этом иначе неоткуда: ни ошибки, ни предупреждения.
        """
        self.assertEqual(r.returncode, 0,
                         f"хук вышел ненулевым кодом: {r.returncode}\n{r.stderr}")
        if not r.stdout.strip():
            return None
        return json.loads(r.stdout)

    # --- главное: гейт закрывает ход только по коду возврата ----------------

    def test_failing_check_blocks_the_turn(self):
        """Код возврата 1 — и ход не закрывается. Это весь смысл хука."""
        self.stub(1, "НЕ ПРОШЛО\n  x тесты  (make test)\n")
        d = self.decision(self.gate())
        self.assertIsNotNone(d, "проверка красная, а хук промолчал")
        self.assertEqual(d.get("decision"), self.BLOCK_DECISION,
                         f"хук не заблокировал закрытие хода: {d}")
        self.assertTrue(str(d.get("reason", "")).strip(),
                        "блокировка без причины: модель не узнает, что чинить")

    def test_passing_check_says_nothing(self):
        """Обратный контроль: зелёная проверка не имеет права мешать.

        Хук, который блокирует всегда, неотличим от хука, который блокирует
        по делу, — и первым же делом его выключают.
        """
        self.stub(0)
        self.assertIsNone(self.decision(self.gate()),
                          "проверка зелёная, а ход всё равно не закрыть")

    def test_verify_output_is_carried_into_the_reason(self):
        """Причина берётся из ВЫВОДА проверки, а не сочиняется хуком.

        Фиксированный текст «почини тесты» прошёл бы тест на блокировку и не
        сказал бы модели ничего: что именно красное, осталось бы неизвестным.
        """
        self.stub(1, "НЕ ПРОШЛО\n  x линт  (npm run lint)\nМЕТКА-УЛИКИ-7719\n")
        d = self.decision(self.gate())
        self.assertIn("МЕТКА-УЛИКИ-7719", d["reason"],
                      "вывод проверки до модели не доехал")

    def test_reason_survives_a_quote_and_a_backslash_from_the_check(self):
        """Кавычка или слэш в выводе проверки ломают разбор ВСЕГО объекта.

        Отказ тихий и полный: хук перестаёт существовать для Claude Code
        целиком, гейт исчезает, и никакого сообщения об этом нет. Пути в выводе
        проверок содержат и то и другое регулярно.
        """
        self.stub(1, 'x тесты  (node "C:\\tmp\\a b\\test.js")\n\tтаб\rвозврат\n')
        d = self.decision(self.gate())  # именно здесь падал неэкранированный вывод
        self.assertEqual(d.get("decision"), self.BLOCK_DECISION)
        self.assertIn("C:\\tmp\\a b\\test.js", d["reason"])

    # --- ограничения, без которых хук становится вредителем -----------------

    def test_kill_switch_silences_it(self):
        self.stub(1)
        self.assertIsNone(self.decision(self.gate(env_extra={"SUPERSTACK_DISABLE": "1"})))

    def test_kill_switch_fires_before_anything_is_run(self):
        """«Первой строкой» — проверяемое утверждение, а не оборот речи.

        Выключатель, стоящий после запуска проверки, гасит только вывод: полный
        прогон тестов на каждом закрытии хода при этом остаётся.
        """
        self.stub(1)
        self.gate(env_extra={"SUPERSTACK_DISABLE": "1"})
        self.assertFalse(self.ran.exists(),
                         "выключатель сработал, а проверка всё равно запустилась")

    def test_pause_stops_it(self):
        """Пауза — тормоз, который обязан работать, когда всё остальное сломано.

        Остановленная система не имеет права держать человека в ходе, который
        нечем закрыть, и не имеет права ничего запускать.
        """
        self.state.mkdir(parents=True)
        (self.state / "PAUSE").write_text("2026-01-01T00:00:00Z\n", encoding="utf-8")
        self.stub(1)
        self.assertIsNone(self.decision(self.gate()), "на паузе хук всё равно заблокировал")
        self.assertFalse(self.ran.exists(), "на паузе хук всё равно запустил проверку")

    def test_project_without_declared_checks_is_not_blocked(self):
        """Код 2 — «проверять нечем». Блокировать нельзя.

        В чужом репозитории без тестов заблокированный ход не закрыть ничем:
        поставить плагин означало бы перестать работать. Такой инструмент
        не устанавливают, а сносят.
        """
        self.stub(2)
        d = self.decision(self.gate())
        self.assertNotIn("decision", d or {},
                         "проект без объявленных проверок заблокирован намертво")

    def test_nothing_to_check_is_named_to_the_person(self):
        """…но молча это пройти не может.

        «Не нашёл» и «не смог проверить» — разные утверждения, и ни одно из них
        не равно «проверено». Закрытый без доказательства ход обязан быть
        назван человеку, иначе тишина читается как пройденный гейт.
        """
        self.stub(2)
        d = self.decision(self.gate())
        self.assertIn("systemMessage", d or {},
                      "ход закрыт без доказательства, и никто об этом не сказал")
        self.assertNotIn("decision", d,
                         "systemMessage не должен превращаться в блокировку")

    def test_it_is_named_once_per_session_not_every_turn(self):
        """Хук стоит на КАЖДОМ закрытии хода.

        Предупреждение без счётчика — это одна и та же строка десять раз за час:
        ровно тот способ, которым сообщение перестают читать. Новая сессия —
        новый человек за экраном, ему сказать надо.
        """
        self.stub(2)
        first = self.decision(self.gate(self.stdin_for("s-A")))
        second = self.decision(self.gate(self.stdin_for("s-A")))
        third = self.decision(self.gate(self.stdin_for("s-B")))
        self.assertIn("systemMessage", first or {})
        self.assertIsNone(second, "предупреждение повторилось в той же сессии")
        self.assertIn("systemMessage", third or {},
                      "в новой сессии человеку не сказали ничего")

    def test_silent_when_the_note_cannot_be_persisted(self):
        """Некому сосчитать — молчим.

        Тот же урок, что и в hooks/first-run.sh: предупреждение, которое не
        удаётся отметить, будет повторяться каждый ход до конца времён.
        Не сказать ни разу — меньшее зло, чем говорить бесконечно.
        """
        # Каталог состояния СУЩЕСТВУЕТ и содержит реестр — проект заведён,
        # хук обязан работать. Нельзя только записать в него отметку. Иначе
        # хук замолчал бы из-за незаведённого проекта, и тест зеленел бы по
        # причине, которую не проверяет.
        blocked = Path(self.tmp.name) / "ro"
        state = blocked / "state"
        state.mkdir(parents=True)
        (state / "projects").write_text(str(self.project) + "\n",
                                        encoding="utf-8")
        os.chmod(state, 0o500)
        self.stub(2)
        try:
            r = self.gate(env_extra={"SUPERSTACK_STATE_DIR": str(state)})
        finally:
            os.chmod(state, 0o700)
        self.assertIsNone(self.decision(r),
                          "отметку не записать, а предупреждение всё равно вышло")

    def test_second_pass_does_not_block_again(self):
        """stop_hook_active=true — ход уже продолжен нашим же блоком.

        Второй одинаковый блок означает, что починить не вышло; дальше гейт
        перестаёт быть проверкой и становится ловушкой, где каждый круг стоит
        полного прогона тестов. Разрыв цикла обязателен.
        """
        self.stub(1)
        d = self.decision(self.gate(self.stdin_for(active=True)))
        self.assertIsNone(d, "хук заблокировал ход второй раз подряд")
        self.assertFalse(self.ran.exists(),
                         "на повторном заходе проверка запустилась снова")

    def test_it_does_not_hang(self):
        """Хук стоит на каждом закрытии хода: зависший хук — это зависшая работа.

        Прерванная по бюджету проверка — это «не смог проверить», а не «провал»:
        блокировать по ней нельзя, а вот назвать обязательно, иначе тишина
        читается как пройденный гейт.

        Отдельно проверяется, что в предупреждении стоит ИМЕННО ТОТ бюджет,
        который был задан: убитый по таймауту процесс возвращает код сигнала,
        и без этой ветки человек прочитал бы «не смог проверить (код 143)» —
        число, по которому нельзя догадаться, какую ручку крутить.
        """
        self.stub_hangs(60)
        r = self.gate(env_extra={"SUPERSTACK_GATE_TIMEOUT": "2"}, timeout=30)
        d = self.decision(r)
        self.assertIn("systemMessage", d or {},
                      "проверка не уложилась в бюджет, и этого никто не назвал")
        self.assertNotIn("decision", d,
                         "по неслучившейся проверке нельзя ни блокировать, ни отчитываться")
        self.assertIn("2", d["systemMessage"],
                      f"заданный бюджет в предупреждении не назван: {d['systemMessage']!r}")

    def test_unknown_exit_code_is_named_not_hidden(self):
        """Код, которого verify.py не отдаёт, — это «не смог проверить»,
        а не «прошло». Тихо закрыть ход по такому коду значит соврать."""
        self.stub(3)
        d = self.decision(self.gate())
        self.assertIn("systemMessage", d or {})

    def test_it_does_not_fix_anything_itself(self):
        """Хук ПОКАЗЫВАЕТ отказ, а не чинит его.

        В начало PATH кладутся подставки с именами всего, чем можно поправить
        проект или машину. Пустой журнал — доказательство, а не обещание.

        Чего этот тест НЕ покрывает: `rm` из списка исключён намеренно — хук
        сносит им СВОЙ временный каталог, и подставка вместо него превратила бы
        уборку за собой в улику. Что хук не трогает чужого, проверяет слепком
        соседний тест.
        """
        base = Path(self.tmp.name)
        binpath = base / "bin"
        binpath.mkdir()
        log = base / "calls.log"
        for name in ("claude", "node", "npm", "npx", "git", "curl", "wget",
                     "mv", "cp", "chmod", "open", "osascript", "defaults",
                     "ssh", "launchctl", "pip3", "brew", "docker"):
            p = binpath / name
            p.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$0 $*" >> "{log}"\nexit 0\n',
                         encoding="utf-8")
            os.chmod(p, 0o755)
        self.stub(1)
        d = self.decision(self.gate(
            env_extra={"PATH": f"{binpath}:{os.environ['PATH']}"}))
        # Положительный контроль: подставки не должны были сломать сам хук.
        self.assertEqual((d or {}).get("decision"), self.BLOCK_DECISION,
                         "хук замолчал — значит проверять нечего")
        calls = log.read_text(encoding="utf-8") if log.exists() else ""
        self.assertEqual(calls, "", f"хук что-то запустил помимо проверки: {calls!r}")

    def test_changes_nothing_outside_its_own_state_dir(self):
        """Слепком, а не обещанием: домашний каталог и каталог проекта обязаны
        совпасть побайтово до и после."""
        base = Path(self.tmp.name)
        home = base / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text('{"permissions":{}}',
                                                        encoding="utf-8")
        (self.project / "src.py").write_text("x = 1\n", encoding="utf-8")
        self.stub(1)
        before_home, before_project = tree(home), tree(self.project)
        d = self.decision(self.gate(env_extra={"HOME": str(home)}))
        self.assertEqual((d or {}).get("decision"), self.BLOCK_DECISION,
                         "хук ничего не сделал — тест пустой")
        self.assertEqual(tree(home), before_home, "хук изменил домашний каталог")
        self.assertEqual(tree(self.project), before_project,
                         "хук изменил каталог проекта")

    # --- подставной verify.py доказывает контракт, но не адрес ---------------

    def test_the_real_verify_is_where_the_hook_looks_for_it(self):
        """Подставная проверка ловит логику и НЕ ловит опечатку в пути.

        Хук ищет проверку рядом с собой; если настоящий файл лежит не там,
        гейт молча выродится в «не смог проверить» на каждой машине.
        """
        self.assertTrue((GATE.parent / ".." / "tools" / "verify.py").resolve().is_file(),
                        "хук ищет verify.py не там, где он лежит")

    def test_the_real_verify_runs_and_an_empty_project_is_not_blocked(self):
        """Сквозной прогон с НАСТОЯЩИМ verify.py.

        Пустой каталог не объявляет проверок ни на какой машине — поэтому
        результат здесь одинаковый везде, в отличие от прогона по проекту,
        где всё зависит от того, что установлено.
        """
        d = self.decision(self.gate(script=GATE,
                                    env_extra={"SUPERSTACK_IGNORE_PAUSE": "1"}))
        self.assertNotIn("decision", d or {},
                         "пустой каталог заблокировал закрытие хода")

    @unittest.skipIf(shutil.which("make") is None,
                     "нет make: сквозной прогон настоящей проверки не выполнен")
    def test_the_real_verify_blocks_a_project_whose_declared_check_fails(self):
        """Сквозной прогон: объявленная проверка красная — ход не закрывается.

        Makefile, а не pytest: цель `test` понимает любой make, и результат не
        зависит от того, что установлено на машине. Отсутствие make названо
        пропуском, а не выдано за успех.
        """
        (self.project / "Makefile").write_text("test:\n\t@exit 1\n", encoding="utf-8")
        d = self.decision(self.gate(script=GATE,
                                    env_extra={"SUPERSTACK_IGNORE_PAUSE": "1"}))
        self.assertEqual((d or {}).get("decision"), self.BLOCK_DECISION,
                         f"красная объявленная проверка не заблокировала ход: {d}")
        (self.project / "Makefile").write_text("test:\n\t@echo ok\n", encoding="utf-8")
        self.assertIsNone(
            self.decision(self.gate(script=GATE,
                                    env_extra={"SUPERSTACK_IGNORE_PAUSE": "1"})),
            "зелёная объявленная проверка всё равно не дала закрыть ход")


class TestHookWiring(unittest.TestCase):
    def test_manifest_is_valid_and_points_at_the_script(self):
        cfg = json.loads((PKG / "hooks" / "hooks.json").read_text("utf-8"))
        entries = cfg["hooks"]["SessionStart"]
        self.assertEqual([e["matcher"] for e in entries], ["startup"],
                         "resume и compact — тот же человек посреди работы, не новый")
        cmd = entries[0]["hooks"][0]["command"]
        self.assertIn("first-run.py", cmd)
        self.assertIn("CLAUDE_PLUGIN_ROOT", cmd)
        self.assertTrue(HOOK.is_file())

    def test_the_gate_is_wired_as_a_stop_hook(self):
        """Улика для планки: без строки Stop в hooks.json скрипт гейта — файл,
        который никто никогда не вызовет, а правило снова становится
        пожеланием.

        Объявление живёт у guard: ${CLAUDE_PLUGIN_ROOT} указывает на СВОЙ пакет,
        и Stop, объявленный в install, искал бы verify-gate.py у себя.
        """
        cfg = json.loads((PKG / "hooks" / "hooks.json").read_text("utf-8"))
        self.assertIn("Stop", cfg["hooks"], "гейт не подключён ни к чему")
        cmds = [h["command"] for e in cfg["hooks"]["Stop"] for h in e["hooks"]]
        self.assertTrue(any("verify-gate.py" in c for c in cmds), cmds)
        self.assertTrue(all("CLAUDE_PLUGIN_ROOT" in c for c in cmds),
                        "путь к хуку не переживёт установку плагина в другой каталог")
        self.assertTrue(GATE.is_file())

    def test_wiring_the_gate_did_not_unwire_the_first_run_hook(self):
        """Обратный контроль: правка одного хука не имеет права стереть другой.
        После разделения на пакеты они лежат в разных файлах — тем важнее
        проверить, что SessionStart на месте у своего владельца."""
        cfg = json.loads((PKG / "hooks" / "hooks.json").read_text("utf-8"))
        cmds = [h["command"] for e in cfg["hooks"]["SessionStart"] for h in e["hooks"]]
        self.assertTrue(any("first-run.py" in c for c in cmds), cmds)

    def test_claude_code_does_not_kill_the_gate_before_it_can_speak(self):
        """Таймаут в hooks.json обязан пережить собственный бюджет хука.

        Если Claude Code убьёт хук раньше, чем тот успеет прервать проверку,
        «не смог проверить» не будет напечатано никогда: хук умрёт молча, и
        человек прочитает тишину как пройденный гейт.
        """
        cfg = json.loads((PKG / "hooks" / "hooks.json").read_text("utf-8"))
        # Именно хук ГЕЙТА, а не самый короткий из Stop-хуков. Раньше это было
        # одно и то же: Stop-хук был один на пакет. После слияния рядом встал
        # session-lesson со своими законными 10 секундами, и «самый короткий»
        # стал измерять не тот механизм — тест падал на верной конфигурации.
        сроки = [h.get("timeout", 600) for e in cfg["hooks"]["Stop"]
                 for h in e["hooks"] if "verify-gate.py" in h["command"]]
        self.assertEqual(len(сроки), 1,
                         "гейт объявлен не один раз — сроки перестают быть "
                         f"сравнимыми: {сроки}")
        outer = сроки[0]
        # Бюджет читается ИЗ КОДА хука, а не из формы его записи в оболочке:
        # прежний якорь `SUPERSTACK_GATE_TIMEOUT:-120` был синтаксисом sh и
        # исчез вместе с портом. Проверка привязывается к значению, которое
        # хук реально использует.
        budget = int(re.search(r"^БЮДЖЕТ = (\d+)", GATE.read_text("utf-8"),
                               re.M).group(1))
        self.assertGreater(outer, budget + 2,
                           f"внешний таймаут {outer}с не переживает бюджет хука {budget}с")

    def test_plugin_json_does_not_declare_the_standard_hooks_file(self):
        """Claude Code v2.1+ грузит hooks/hooks.json по соглашению. Явное
        объявление даёт ошибку дубликата и хуки не грузятся вовсе."""
        for m in MANIFESTS:
            with self.subTest(plugin=m.parent.parent.name):
                self.assertNotIn("hooks", json.loads(m.read_text("utf-8")))

    def test_the_set_ships_as_exactly_one_package(self):
        """Пакет один — и это утверждение, а не наблюдение.

        До 0.3.0 пакетов было семь. Разделение оправдано, когда пакеты ставят
        и обновляют по отдельности; не случилось ни того, ни другого, а платой
        была межпакетная адресация — скилл звал инструмент через путь, которого
        в его пакете нет. Так однажды оказались недостижимы 14 инструментов из
        29, и оба вызова единственного сборочного скилла несколько заходов
        указывали в пустоту.

        Второй пакет вернёт этот класс отказов целиком и сделает это тихо:
        всё соберётся, все тесты останутся зелёными, и обнаружится это на
        машине человека фразой «нет такого файла». Поэтому число пакетов
        зафиксировано здесь, а не подразумевается.
        """
        names = [d.name for d in packages()]
        self.assertEqual(
            names, ["superstack"],
            "пакетов должно быть ровно один; появился второй — вместе с ним "
            "вернулась межпакетная адресация, а резолвера больше нет")

    def test_the_gates_run_automatically_on_every_push(self):
        """CI обязан существовать И запускать набор.

        Оценка со стороны попала точно: инструмент требует «либо код возврата
        ноль, либо не готово», а сам жил без автопрогона. Числа в сообщениях
        коммитов ставит скрипт выкладки — и он честно блокирует красное, — но
        обычный `git push` идёт мимо него целиком.

        Проверяется не наличие файла, а то, что в нём есть прогон набора:
        рабочий процесс из одних шагов checkout ничем не отличается от
        отсутствующего, а выглядит как закрытая дыра.
        """
        wf = REPO / ".github" / "workflows"
        файлы = sorted(wf.glob("*.yml")) + sorted(wf.glob("*.yaml"))
        self.assertTrue(файлы, "нет ни одного рабочего процесса — гейт на "
                               "push отсутствует")
        текст = "\n".join(f.read_text("utf-8") for f in файлы)
        self.assertIn("pytest tests/", текст,
                      "рабочий процесс не запускает набор")
        self.assertIn("on:", текст)
        self.assertRegex(текст, r"\bpush\b",
                         "рабочий процесс не привязан к push")

    def test_the_install_key_in_the_readme_matches_the_manifests(self):
        """Ключ установки в витрине сверяется с манифестами, а не с памятью.

        `superstack@superstack` — это `<имя пакета>@<имя маркетплейса>`, и оба
        имени живут в JSON. Переименуй любое — и README продолжит печатать
        прежнее, а человек получит «plugin not found» и прочитает это как
        поломку продукта. Витрина обязана ломаться здесь, а не у него.
        """
        mk = json.loads((REPO / ".claude-plugin" / "marketplace.json")
                        .read_text("utf-8"))
        ключи = {f'{e["name"]}@{mk["name"]}' for e in mk["plugins"]}
        readme = (REPO / "README.md").read_text("utf-8")
        названы = set(re.findall(r"claude plugin install (\S+)", readme))
        self.assertTrue(названы, "README не показывает ни одной команды установки")
        self.assertEqual(
            названы - ключи, set(),
            f"README ставит {sorted(названы - ключи)}, а маркетплейс объявляет "
            f"{sorted(ключи)} — команда из витрины не сработает")

    def test_the_readme_covers_the_machine_that_already_had_it(self):
        """Проверка ФОРМУЛИРОВКИ, а не поведения — и названа так честно.

        Повод не теоретический. `marketplace add` на машине, где SUPERSTACK уже
        стоял, отказывает с кодом 1, и агент, ведомый прежним текстом из двух
        команд, на этом останавливался. Хуже: если бы он не остановился, то
        поставил бы пакет по СТАРОМУ списку из кэша — зелёная команда, не та
        версия. Спасает только `marketplace update`, и его в тексте не было.
        """
        readme = (REPO / "README.md").read_text("utf-8")
        mk = json.loads((REPO / ".claude-plugin" / "marketplace.json")
                        .read_text("utf-8"))["name"]
        self.assertIn(f"marketplace update {mk}", readme,
                      "витрина не велит обновлять маркетплейс — на машине с "
                      "прошлой установкой поставится версия из старого кэша")
        self.assertIn("already installed", readme,
                      "витрина не предупреждает про отказ первой команды — "
                      "агент остановится на нём, решив, что установка провалена")

    def test_the_workflow_is_one_github_will_actually_accept(self):
        """Проверять, что файл есть, — мало. Он должен ЗАПУСТИТЬСЯ.

        Первая версия этих ворот падала за ноль секунд: идентификаторы заданий
        были написаны кириллицей, а GitHub требует букву-или-подчёркивание в
        начале и только ASCII дальше. Кода
        прогон не касался вовсе, но помечался красным — то есть «красный CI»
        читался как «сломан код». Ровно та же болезнь, что ловят ворота
        проводки: объявлено не значит работает.

        Отображаемое имя при этом остаётся русским — ограничение на ключ, а не
        на подпись.
        """
        wf = REPO / ".github" / "workflows"
        for f in sorted(wf.glob("*.yml")) + sorted(wf.glob("*.yaml")):
            with self.subTest(workflow=f.name):
                # Разбор без сторонней библиотеки: ключи заданий — это строки
                # первого уровня вложенности под `jobs:`.
                текст = f.read_text("utf-8")
                блок = текст[текст.index("\njobs:"):]
                ключи = re.findall(r"^  ([^\s#][^:]*):\s*$", блок, re.M)
                self.assertTrue(ключи, f"{f.name}: не нашёл ни одного задания")
                for k in ключи:
                    self.assertRegex(
                        k, r"^[_a-zA-Z][a-zA-Z0-9_-]*$",
                        f"{f.name}: идентификатор задания «{k}» GitHub не "
                        "примет — прогон упадёт за ноль секунд, не коснувшись "
                        "кода, и это прочитают как поломку продукта")

    def test_ci_travels_to_the_public_tree(self):
        """Рабочий процесс, оставшийся в приватном репозитории, не гейт.

        Ставят и клонируют публичный; если `.github` туда не переносится, CI
        существует только у автора — то есть ровно у того, кому он не нужен.
        """
        sync = (REPO / "tools" / "sync_public.py").read_text("utf-8")
        carry = re.search(r"CARRY = \((.*?)\)", sync, re.S).group(1)
        self.assertIn(".github", carry,
                      "sync_public не переносит .github — CI не доедет до "
                      "публичного репозитория")

    def test_declared_agents_are_files_that_exist(self):
        """Валидатор принимает только явные пути к файлам, не каталоги."""
        for m in MANIFESTS:
          for rel in json.loads(m.read_text("utf-8")).get("agents", []):
            p = (m.parent.parent / rel).resolve()
            self.assertTrue(p.is_file(), f"объявлен несуществующий агент: {rel}")
            self.assertTrue(rel.endswith(".md"), f"не файл агента: {rel}")


class TestSkillContract(unittest.TestCase):
    """SKILL.md — исполняемый документ. Опечатка в пути молчит до первого
    живого запуска у человека."""

    def setUp(self):
        self.text = SKILL.read_text("utf-8")
        # Проверяем КОД, а не прозу вокруг него: комментарий «мы не используем
        # mktemp» иначе засчитывался бы как использование mktemp.
        self.bash = "\n".join(
            re.sub(r"^\s*#.*$", "", block, flags=re.M)
            for block in re.findall(r"```bash\n(.*?)```", self.text, re.S))

    def test_every_referenced_tool_exists(self):
        refs = set(re.findall(r"tools/[\w/]+\.py", self.text))
        self.assertTrue(refs, "в скилле не осталось ни одного вызова инструмента")
        for rel in sorted(refs):
            here = SKILL.parent.parent.parent / rel
            self.assertTrue(here.is_file(),
                            f"скилл зовёт несуществующее в своём пакете: {rel} ({here})")

    def test_verdict_is_published_as_a_page(self):
        self.assertIn("render_html.py", self.text)
        self.assertIn("Artifact", self.text)
        self.assertIn("report.html", self.text)

    def test_work_dir_survives_between_bash_calls(self):
        """Каждый вызов Bash — отдельная оболочка. mktemp с trap на EXIT сносил
        каталог до второй команды, а переменная приходила пустой: фазы 2 и 3
        читали пустоту. Отказ тихий — путь просто становился '/facts.json'.
        """
        self.assertTrue(self.bash, "в скилле не осталось исполняемых блоков")
        self.assertNotIn("mktemp", self.bash)
        self.assertNotRegex(self.bash, r"(?m)^\s*trap\b")
        # Каждый блок, читающий рабочие файлы, обязан сам знать путь: значение
        # переменной из прошлого вызова Bash до него не доедет.
        for block in re.findall(r"```bash\n(.*?)```", self.text, re.S):
            if "facts.json" in block or "findings.json" in block:
                self.assertIn("~/.claude/superstack/run", block,
                              f"блок полагается на переменную из прошлого вызова:\n{block}")

    def test_work_dir_is_private(self):
        self.assertIn("chmod 700", self.bash)

    def test_terminal_render_is_kept_as_the_fallback(self):
        """Обратный контроль: страница не должна выкинуть проверяемость руками."""
        self.assertIn("render.py", self.text)
        self.assertIn("why", self.text)

    # --- конвейер исполняется, а не только читается ------------------------

    #: факты, на которых заведомо срабатывает известное правило. Значения
    #: задаются здесь, а не берутся из машины: иначе тест зависит от того,
    #: на чём его запустили.
    PIPELINE_FACTS = {
        "rt.subagent_model_routing": False,
        "rt.active_version": "2.1.42",
        "rt.versions_installed": {"npm": "2.1.42"},
        "rt.entrypoint": "cli",
    }

    def _run_pipeline(self, skill_text: str, sandbox: Path):
        """Достать из SKILL.md блок с конвейером и ИСПОЛНИТЬ его.

        HOME подменяется на песочницу: блок работает с ~/.claude/superstack/run,
        и без подмены тест лез бы в настоящий каталог человека.
        """
        blocks = [b for b in re.findall(r"```bash\n(.*?)```", skill_text, re.S)
                  if "adjudicate.py" in b]
        self.assertEqual(len(blocks), 1,
                         f"блоков с adjudicate.py не один, а {len(blocks)}")
        home = sandbox / "home"
        run = home / ".claude" / "superstack" / "run"
        run.mkdir(parents=True)
        (run / "facts.json").write_text(facts_blob(self.PIPELINE_FACTS), encoding="utf-8")
        env = {**os.environ,
               "HOME": str(home),
               "CLAUDE_PLUGIN_ROOT": str(SKILL.parent.parent.parent),
               "SUPERSTACK_IGNORE_PAUSE": "1"}
        r = subprocess.run(["sh", "-c", blocks[0]], capture_output=True, text=True,
                           timeout=120, env=env, cwd=str(sandbox))
        findings = run / "findings.json"
        report = run / "report.html"
        return (r,
                findings.read_text("utf-8") if findings.is_file() else "",
                report.read_text("utf-8") if report.is_file() else "")

    def _assert_pipeline_ok(self, r, findings_text: str, report_text: str) -> None:
        if r.returncode != 0:
            self.fail(f"конвейер из SKILL.md упал, код {r.returncode}\n{r.stderr}")
        try:
            data = json.loads(findings_text)
        except Exception as e:
            self.fail(f"findings.json не разбирается как JSON: {e}")
        ids = [f["id"] for f in data.get("findings", [])]
        self.assertIn("ass.second-opinion-degraded", ids,
                      f"конвейер не выдал ожидаемую находку, выдал: {ids}")
        self.assertIn("html", report_text[:400].lower(),
                      "render_html не выдал страницу")

    def test_pipeline_from_the_skill_actually_runs(self):
        """Команды скилла ИСПОЛНЯЮТСЯ, а не сверяются глазами.

        Проверка «в тексте упомянут adjudicate.py» ловит опечатку в имени
        файла и не ловит ничего больше: переставленные аргументы оставляют
        оба имени на месте, и скилл ломается только у человека.
        """
        with tempfile.TemporaryDirectory() as tmp:
            self._assert_pipeline_ok(*self._run_pipeline(self.text, Path(tmp)))

    def test_swapped_arguments_would_be_noticed(self):
        """Обратный контроль к тесту выше: тот обязан краснеть от перестановки
        аргументов adjudicate — иначе он проверяет только наличие текста."""
        mutated = self.text.replace(
            '"$W/facts.json" "$CLAUDE_PLUGIN_ROOT/rules/*.json"',
            '"$CLAUDE_PLUGIN_ROOT/rules/*.json" "$W/facts.json"')
        self.assertNotEqual(mutated, self.text,
                            "мутация не применилась — обратный контроль пуст")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AssertionError):
                self._assert_pipeline_ok(*self._run_pipeline(mutated, Path(tmp)))


class TestVersionFloor(unittest.TestCase):
    """`model:` в агенте ниже порога игнорируется молча: ревьюер исполняется
    моделью сессии, то есть автор проверяет сам себя."""

    def test_string_comparison_would_have_lied(self):
        self.assertLess("2.1.170", "2.1.42")  # именно так ошибается строка
        self.assertFalse(collect._at_least("2.1.42", (2, 1, 170)))
        self.assertTrue(collect._at_least("2.1.170", (2, 1, 170)))
        self.assertTrue(collect._at_least("2.2.0", (2, 1, 170)))

    def test_unknown_version_is_not_a_verdict(self):
        self.assertIsNone(collect._at_least(None, (2, 1, 170)))
        self.assertIsNone(collect._at_least("", (2, 1, 170)))
        self.assertIsNone(collect._at_least("неизвестно", (2, 1, 170)))

    def test_short_version_does_not_crash(self):
        self.assertFalse(collect._at_least("2", (2, 1, 170)))

    def _run_rules(self, routing):
        src = facts_file({"rt.subagent_model_routing": routing,
                          "rt.active_version": "2.1.42",
                          "rt.versions_installed": {"npm": "2.1.42"},
                          "rt.entrypoint": "cli"})
        r = subprocess.run([sys.executable, str(at("tools", "adjudicate.py")),
                            src, str(PKG / "rules" / "discipline.rules.json")],
                           capture_output=True, text=True, timeout=60,
                           cwd=str(REPO), env=ENV)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [f["id"] for f in json.loads(r.stdout)["findings"]]

    def test_fires_when_routing_is_unavailable(self):
        self.assertIn("ass.second-opinion-degraded", self._run_rules(False))

    def test_silent_when_routing_works(self):
        self.assertNotIn("ass.second-opinion-degraded", self._run_rules(True))

    def test_silent_when_version_is_unknown(self):
        """«Не смог проверить» — не то же самое, что «не дотягивает».
        Правило, сработавшее на None, обвиняет в неизмеренном."""
        self.assertNotIn("ass.second-opinion-degraded", self._run_rules(None))


class TestSecondOpinionAgent(unittest.TestCase):
    #: ровно то, что судье разрешено. Список РАЗРЕШЁННОГО, а не запрещённого:
    #: см. докстринг теста ниже.
    ALLOWED_TOOLS = ("Glob", "Grep", "Read")

    def setUp(self):
        self.text = (at("agents", "second-opinion.md")).read_text("utf-8")
        self.fm = self.text.split("---")[1]
        # Перенос строки не должен решать, прошёл тест или нет.
        self.flat = " ".join(self.text.split())

    def test_reviewer_has_exactly_the_read_only_tools(self):
        """Проверяющий с правом записи начинает чинить вместо того, чтобы
        находить, — и его «всё хорошо» перестаёт что-либо значить.

        Список запрещённых имён эту границу не держит: достаточно добавить
        Task, и судья делегирует запись пишущему агенту — формально ни одного
        запрещённого имени, фактически право записи есть. Поэтому здесь
        перечислено ровно разрешённое, и ЛЮБОЕ другое имя роняет тест.
        """
        tools = re.search(r"^tools:\s*(.+)$", self.fm, re.M).group(1)
        got = tuple(sorted(t.strip() for t in tools.split(",") if t.strip()))
        self.assertEqual(got, self.ALLOWED_TOOLS,
                         f"у судьи не только чтение: {got}")

    def test_runs_on_a_different_model_than_the_session(self):
        self.assertRegex(self.fm, r"(?m)^model:[ \t]*fable[ \t]*$")

    def test_forbids_manufactured_objections(self):
        """ПРОВЕРКА ФОРМУЛИРОВКИ, А НЕ ПОВЕДЕНИЯ.

        Обратное смещение — тоже отказ: проверяющий, обязанный что-нибудь
        найти, находит что угодно, и его перестают слушать, когда он прав.
        Прежняя версия сверяла точную фразу и падала от безобидного парафраза,
        а инверсию смысла при этом не ловила вовсе. Здесь проверяется
        инвариант: в одном предложении с «возражениями» стоит отрицание при
        глаголе выдумывания. Снятие отрицания роняет тест, перестановка слов —
        нет.

        Чего этот тест НЕ доказывает: что модель этому следует.
        """
        invented = re.compile(
            r"\bне\s+(?:\w+\s+){0,2}(?:выдум|придум|сочин|изобрет|фабрик)", re.I)
        objection = re.compile(r"возраж|замечан|придирк|претенз", re.I)
        sentences = [s for s in re.split(r"[.!?]", self.flat) if objection.search(s)]
        self.assertTrue(sentences, "про возражения в агенте вообще ничего нет")
        self.assertTrue(any(invented.search(s) for s in sentences),
                        f"запрет выдумывать возражения потерял отрицание: {sentences}")

    def test_admits_when_it_is_not_independent(self):
        self.assertIn("не независимая проверка", self.flat)

    def test_names_the_rule_that_catches_silent_degradation(self):
        self.assertIn("ass.second-opinion-degraded", self.text)
        rules = json.loads((at("rules", "discipline.rules.json")).read_text("utf-8"))
        self.assertIn("ass.second-opinion-degraded", [r["id"] for r in rules["rules"]])

    def test_credits_the_source(self):
        self.assertIn("fable-advisor", self.text)
        self.assertIn("MIT", self.text)


if __name__ == "__main__":
    unittest.main()
