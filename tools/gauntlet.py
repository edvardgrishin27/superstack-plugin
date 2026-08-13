#!/usr/bin/env python3
"""SUPERSTACK — ПЛАНКА. Одна команда отвечает: система построена или нет.

Зачем это отдельный инструмент, а не суждение модели.

Цикл «строитель → критик» разваливается в одном месте: когда останов решает
тот, кто строил. Оценщик `/goal` в Claude Code **не вызывает инструменты** —
он читает стенограмму. Значит цель «все ошибки исправлены» удовлетворяется
тем, что агент СКАЗАЛ, будто исправлены. Планка, которую можно пройти словами,
не планка.

Здесь останов считает код. Шесть ворот, каждые со своим способом соврать:

  · набор       — тесты зелёные. Слабейшее из ворот: зелёное можно нарисовать.
  · герметичность — то же число тестов при другом HOME. Ловит «зелено, потому
                   что на ЭТОЙ машине так сложилось»: набор уже уезжал с 260 на
                   282 теста на неизменном коде.
  · мутации     — зарегистрированные поломки обязаны ронять набор. Единственные
                   ворота, доказывающие, что тесты вообще что-то держат.
  · правила     — схема, дубли, имена фактов, подстановки.
  · манифест    — плагин ставится, а не только лежит.
  · план        — механизмы из плана присутствуют. Превращает 928 строк прозы
                   в счётный список: сколько есть, чего нет, что делать дальше.

Ворота, которое не удалось выполнить, даёт `unknown`, а не `pass`. «Не смог
проверить» и «прошло» — разные утверждения, и планка их не смешивает.

  python3 gauntlet.py              -> человеку в stderr + JSON в stdout
  python3 gauntlet.py --json       -> только JSON
  python3 gauntlet.py --quick      -> без мутаций (быстрая петля строителя)
  python3 gauntlet.py --gate план  -> одни ворота

Частичный прогон (--gate, --quick) НИКОГДА не даёт «планка взята»: незапущенные
ворота остаются в отчёте как skipped и держат код 2. Иначе «планка взята»
покупается за секунду прогоном самого дешёвого из шести ворот.

  код 0 — планка взята, 1 — что-то красное, 2 — что-то не проверено, 3 — вызов
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


# log.py живёт в superstack-core: инструмент из другого пакета обязан найти его
# по структуре репозитория, а не по относительному прыжку через ".." — прыжок
# ломается при любом перемещении и делает это без единого сообщения.
_CORE = Path(__file__).resolve().parents[1] / "plugins" / "superstack-core" / "tools"
if _CORE.is_dir() and str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


from log import event as _log_event  # noqa: E402

PLUG = Path(__file__).resolve().parent.parent
SUITE_TIMEOUT = 900
MUTATION_TIMEOUT = 600


_TRIPLE = re.compile(r'"""[\s\S]*?"""' + r"|'''[\s\S]*?'''")


def executable_text(path: Path) -> str:
    """Текст файла БЕЗ комментариев и докстрингов.

    Ворота «план» искали улику простым вхождением подстроки в файл. Внешняя
    проверка выпотрошила четыре механизма, оставив их названия в докстрингах, —
    и ворота отчитались «на месте» по всем четырём. Улика, живущая в прозе,
    доказывает, что кто-то написал про механизм, а не что механизм есть.

    Для .py и .sh проза вырезается. Для .md и .json — нет: там текст И ЕСТЬ
    артефакт, вырезать нечего, и это ограничение названо, а не замаскировано.

    Направление ошибки выбрано намеренно: вырезав лишнее, ворота объявят
    механизм отсутствующим и заставят перепривязать улику. Обратная ошибка —
    засчитать пустышку — тише и дороже.
    """
    text = path.read_text("utf-8", errors="replace")
    suffix = path.suffix.lower()
    if suffix == ".py":
        try:
            text = _blank_prose(text)
        except Exception:                            # noqa: BLE001
            # Файл не разбирается — вырезаем хотя бы очевидное. Молчаливый
            # возврат сырого текста вернул бы ровно ту дыру, что чиним.
            text = _TRIPLE.sub(" ", re.sub(r"(?m)^\s*#.*$", "", text))
    elif suffix == ".sh":
        text = re.sub(r"(?m)^\s*#.*$", "", text)
    return text


def _blank_prose(text: str) -> str:
    """Затереть комментарии и докстринги ПРОБЕЛАМИ, не трогая остальное.

    Именно затереть, а не выбросить: перестроение файла из токенов рвёт
    соседство слов — `def probe_runtime` превращается в `def\\nprobe_runtime`,
    и улика перестаёт находиться в коде, который на месте. Пробелы сохраняют
    все смещения, поэтому вхождение подстроки остаётся тем же вопросом.

    Тройная кавычка в этом проекте — всегда докстринг или длинный комментарий.
    Обычные строковые литералы не трогаем: `"proven-local"` — настоящий код
    и законная улика.
    """
    import io
    import tokenize

    lines = text.splitlines(keepends=True)
    starts = [0]
    for ln in lines:
        starts.append(starts[-1] + len(ln))

    def offset(row: int, col: int) -> int:
        return starts[row - 1] + col

    spans = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        prose = tok.type == tokenize.COMMENT or (
            tok.type == tokenize.STRING and tok.string.lstrip("rbuRBUf")[:3]
            in ('"""', "'''"))
        if prose:
            spans.append((offset(*tok.start), offset(*tok.end)))

    out = list(text)
    for a, b in spans:
        for i in range(a, min(b, len(out))):
            if out[i] != "\n":                       # переводы строк сохраняем
                out[i] = " "
    return "".join(out)


def _looks_applied(text: str, find: str, replace: str) -> bool:
    """Применена ли эта мутация к тексту прямо сейчас.

    Наивное правило «замена есть, оригинала нет» ложно молчит, когда искомое
    является ПОДСТРОКОЙ заменяющего. Живой случай: `tools: Read, Grep, Glob`
    против `tools: Read, Grep, Glob, Task`. В мутированном файле оригинал
    формально присутствует — внутри замены, — и сторож рапортовал «застрявших 0»
    на файле с оставшейся поломкой. Именно так `Task` пережил убитый прогон,
    и красным это стало не у сторожа, а у набора тестов.

    Разбираем три случая отдельно, потому что они и различаются по-разному:
      · искомое внутри заменяющего (добавление) — судим только по заменяющему;
      · заменяющее внутри искомого (удаление) — наличие искомого значит оригинал;
      · непересекающиеся — прежнее правило верно.

    Ошибка склоняется в сторону «застряла»: лишний отказ мерить стоит одного
    прогона, пропущенная поломка отравляет все последующие.
    """
    if find in replace:
        return replace in text
    if replace in find:
        return find not in text and replace in text
    return replace in text and find not in text


def purge_bytecode() -> int:
    """Снести кэш байткода. Не гигиена, а условие правильности измерения.

    Python признаёт кэш устаревшим по паре (mtime, размер). Мутация и её
    восстановление часто совпадают в РАЗМЕРЕ («GATE» -> «AUTO», `_key=_key` ->
    `_key=None`) и укладываются в ОДНУ секунду — тогда пара совпадает, и
    интерпретатор берёт старый .pyc. Ворота при этом честно докладывают исход
    прогона, который шёл по другому коду: мутация объявляется выжившей, ни разу
    не исполнившись, — либо, что хуже, пойманной.

    Воспроизводится за три строки, поэтому лечится не рассуждением, а чисткой.
    """
    killed = 0
    for d in PLUG.rglob("__pycache__"):
        try:
            shutil.rmtree(d)
            killed += 1
        except OSError:
            pass
    return killed


def _sh(cmd: list, timeout: int, env: dict = None, cwd: Path = None) -> tuple:
    try:
        p = subprocess.run(cmd, cwd=str(cwd or PLUG), capture_output=True,
                           text=True, timeout=timeout,
                           # Не писать .pyc вовсе: то, чего нет, не может
                           # устареть и подменить измеряемый код.
                           env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1",
                                "NO_COLOR": "1", "PYTHONDONTWRITEBYTECODE": "1",
                                **(env or {})})
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"не завершилось за {timeout} с"
    except OSError as e:
        return 127, str(e)


_COUNT = re.compile(r"(\d+) passed")
_FAILED = re.compile(r"(\d+) failed")


def _suite(env: dict = None) -> tuple:
    """(код, сколько прошло, сколько упало, хвост вывода)."""
    code, out = _sh([sys.executable, "-m", "pytest", "tests/", "-q"],
                    SUITE_TIMEOUT, env)
    passed = int(_COUNT.search(out).group(1)) if _COUNT.search(out) else 0
    failed = int(_FAILED.search(out).group(1)) if _FAILED.search(out) else 0
    return code, passed, failed, out[-1500:]


# --------------------------------------------------------------------------
# ворота
# --------------------------------------------------------------------------
def gate_suite() -> dict:
    code, passed, failed, tail = _suite()
    if code == 0 and passed:
        return {"status": "pass", "detail": f"{passed} тестов зелёные"}
    if code in (124, 127):
        return {"status": "unknown", "detail": tail.strip()[:300]}
    return {"status": "fail", "detail": f"упало {failed} из {passed + failed}",
            "output": tail}


def gate_hermetic() -> dict:
    """Одно и то же число тестов при разном HOME.

    Набор читал настоящий ~/.claude и за одну сессию уехал с 260 на 282 теста
    БЕЗ единой правки кода. Такое «зелено» — свойство машины, а не кода, и
    отчитываться им нельзя.
    """
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        ca, pa, fa, _ = _suite({"HOME": a})
        cb, pb, fb, _ = _suite({"HOME": b})
    if 124 in (ca, cb):
        return {"status": "unknown", "detail": "прогон не завершился"}
    if pa != pb or fa != fb:
        return {"status": "fail",
                "detail": f"при разном HOME собрано разное: {pa}+{fa} против {pb}+{fb}"}
    if fa:
        return {"status": "fail", "detail": f"герметично, но красное: {fa} падений"}
    return {"status": "pass", "detail": f"{pa} тестов при любом HOME"}


def gate_mutations() -> dict:
    """Зарегистрированная поломка обязана ронять набор.

    Единственные ворота, которые доказывают, что тесты держат смысл, а не
    форму. Выжившая мутация означает: механизм можно удалить, и никто не
    заметит. Прошлая проверка дала 7 выживших из 10.
    """
    src = PLUG / "tests" / "mutations.json"
    if not src.is_file():
        return {"status": "unknown", "detail": f"нет набора мутаций: {src}"}
    try:
        muts = json.loads(src.read_text("utf-8"))["mutations"]
    except (ValueError, KeyError) as e:
        return {"status": "unknown", "detail": f"набор мутаций не разобран: {e}"}

    survived, checked, broken = [], 0, []
    for m in muts:
        target = PLUG / m["file"]
        if not target.is_file():
            broken.append(f"{m['id']}: нет файла {m['file']}")
            continue
        original = target.read_bytes()
        text = original.decode("utf-8")
        if m["find"] not in text:
            broken.append(f"{m['id']}: якорь не найден в {m['file']}")
            continue
        try:
            target.write_text(text.replace(m["find"], m["replace"], 1), encoding="utf-8")
            # Кэш байткода сносится ПЕРЕД каждым прогоном, а не один раз на
            # ворота: замена того же размера в ту же секунду неотличима для
            # инвалидации, и без чистки прогон пошёл бы по старому .pyc —
            # то есть измерял бы не ту мутацию, о которой отчитывается.
            purge_bytecode()
            # -x: первое падение и есть ответ «поймана». Гонять весь набор до
            # конца на заведомо сломанном коде — платить минутами за то, что
            # уже известно.
            code, _ = _sh([sys.executable, "-m", "pytest", "tests/", "-q", "-x"],
                          MUTATION_TIMEOUT)
            caught = code != 0
        finally:
            # Восстановление байт-в-байт и ДО любой другой работы: мутация,
            # пережившая прогон в файле, отравит все следующие ворота.
            target.write_bytes(original)
        checked += 1
        if not caught:
            survived.append({"id": m["id"], "why": m["why"]})

    if broken:
        return {"status": "unknown",
                "detail": "мутации не применились: " + "; ".join(broken[:5]),
                "survived": survived}
    if survived:
        return {"status": "fail",
                "detail": f"выжило {len(survived)} из {checked}",
                "survived": survived}
    return {"status": "pass", "detail": f"все {checked} мутаций пойманы"}


def gate_rules() -> dict:
    # Правила разъехались по пакетам: core держит ядро и дисциплину, brain —
    # эволюцию. Линт по одному каталогу молча проверял бы два файла из трёх и
    # печатал «правила целы» — то есть врал бы ровно тем способом, против
    # которого написан.
    linter = PLUG / "plugins" / "superstack-core" / "tools" / "lint_rules.py"
    globs = [str(d / "*.json") for d in sorted((PLUG / "plugins").glob("*/rules"))]
    if not globs:
        return {"status": "unknown", "detail": "ни одного каталога правил не найдено"}
    code, out = _sh([sys.executable, str(linter)] + globs, 300)
    if code == 0:
        return {"status": "pass", "detail": out.strip().splitlines()[-1][:200] if out.strip() else "правила целы"}
    return {"status": "fail", "detail": out.strip()[-500:]}


def gate_manifest() -> dict:
    if not shutil.which("claude"):
        return {"status": "unknown", "detail": "нечем проверить: claude не найден"}
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    try:
        p = subprocess.run(["claude", "plugin", "validate", "."],
                           cwd=str(PLUG), capture_output=True,
                           text=True, timeout=180, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"status": "unknown", "detail": str(e)[:200]}
    if p.returncode == 0:
        return {"status": "pass", "detail": "манифест валиден"}
    return {"status": "fail", "detail": ((p.stdout or "") + (p.stderr or ""))[-400:]}


def gate_plan() -> dict:
    """Механизмы плана — счётным списком, а не на глаз.

    План — 928 строк прозы. Пока «построено» проверяется чтением, ответ
    зависит от того, кто читал. Здесь у каждого механизма есть улика: файл и
    строка, которая обязана в нём быть.
    """
    src = PLUG / "data" / "plan-coverage.json"
    if not src.is_file():
        return {"status": "unknown", "detail": f"нет карты плана: {src}"}
    try:
        items = json.loads(src.read_text("utf-8"))["mechanisms"]
    except (ValueError, KeyError) as e:
        return {"status": "unknown", "detail": f"карта плана не разобрана: {e}"}

    present, missing, weak = [], [], []
    for it in items:
        ev = it["evidence"]
        # Улика без `contains` удовлетворяется ПУСТЫМ ФАЙЛОМ. Это не мелкая
        # неточность, а дыра в самом приборе: `touch tools/что_угодно.py`
        # засчитывался за построенный механизм, и «N из N» горело зелёным
        # поверх пустоты. Такая запись — не «пройдена» и не «провалена», она
        # НЕПРОВЕРЯЕМА, и ворота обязаны сказать это отдельным словом.
        if not ev.get("contains"):
            weak.append(it)
            continue
        f = PLUG / ev["file"]
        ok = f.is_file()
        if ok:
            try:
                ok = ev["contains"] in executable_text(f)
            except OSError:
                ok = False
        (present if ok else missing).append(it)

    detail = f"{len(present)} из {len(items)} механизмов на месте"
    if weak:
        detail += f"; {len(weak)} записей без проверяемой улики"
    out = {"detail": detail}
    if missing:
        out["missing"] = [{"id": m["id"], "layer": m["layer"],
                           "mechanism": m["mechanism"],
                           "file": m["evidence"]["file"]} for m in missing]
    if weak:
        out["unverifiable"] = [{"id": m["id"], "layer": m["layer"],
                                "mechanism": m["mechanism"],
                                "file": m["evidence"]["file"],
                                "why": "нет поля contains: пустой файл прошёл бы"}
                               for m in weak]
    if missing:
        return {"status": "fail", **out}
    if weak:
        # Не «провалено» — «не смог проверить». Отдельный статус держит код 2
        # и не даёт объявить планку взятой, но и не обвиняет в отсутствии того,
        # что, возможно, на месте.
        return {"status": "unknown", **out}
    return {"status": "pass", **out}


GATES = [
    ("набор", gate_suite),
    ("герметичность", gate_hermetic),
    ("мутации", gate_mutations),
    ("правила", gate_rules),
    ("манифест", gate_manifest),
    ("план", gate_plan),
]
QUICK_SKIP = {"мутации", "герметичность"}


def stuck_mutations() -> list:
    """Мутации, ОСТАВШИЕСЯ применёнными в рабочем дереве.

    Ворота мутаций восстанавливают файл в `finally`, и этого достаточно, пока
    процесс умирает по-человечески. Убитый по SIGKILL прогон (закрыли окно,
    сняли задачу, истёк таймаут агента) оставляет поломку в коде НАВСЕГДА.

    Дальше происходит худшее: следующий прогон видит красные тесты и объявляет
    провалом чужую невосстановленную мутацию. Так и вышло — четыре теста из
    пяти падали не из-за кода, а из-за трёх застрявших поломок, и найти это
    удалось случайно. Проверка стоит миллисекунды и снимает целый класс
    отравленных замеров.

    Признак: замена в файле есть, а оригинала нет. Обратное (оба или ни одного)
    мутацией не считается — это нормальный код.
    """
    src = PLUG / "tests" / "mutations.json"
    if not src.is_file():
        return []
    try:
        muts = json.loads(src.read_text("utf-8"))["mutations"]
    except (ValueError, KeyError):
        return []
    stuck = []
    for m in muts:
        f = PLUG / m["file"]
        if not f.is_file():
            continue
        try:
            t = f.read_text("utf-8", errors="replace")
        except OSError:
            continue
        if _looks_applied(t, m["find"], m["replace"]):
            stuck.append({"id": m["id"], "file": m["file"], "why": m["why"]})
    return stuck


def run(only: str = None, quick: bool = False) -> dict:
    # Первым делом — до единого запуска. Меряя дерево с застрявшей поломкой,
    # планка отчитается о чужом провале как о своём и отправит чинить рабочий код.
    stuck = stuck_mutations()
    if stuck:
        return {
            "bar": "superstack", "done": False,
            "gates": [{"gate": "дерево", "status": "fail",
                       "detail": f"в рабочем дереве застряло мутаций: {len(stuck)}",
                       "stuck": stuck}],
            "next": (f"вернуть код: {stuck[0]['id']} в {stuck[0]['file']} — "
                     "прогон мутаций был прерван между применением и восстановлением; "
                     "мерить дерево с чужой поломкой бессмысленно"),
        }

    gates = []
    for name, fn in GATES:
        if only and name != only:
            # Незапущенные ворота ОСТАЮТСЯ в отчёте как skipped, а не исчезают.
            # Пока они исчезали, `--gate план` печатал «ПЛАНКА ВЗЯТА» с кодом 0
            # за секунду прогона самого дешёвого из шести ворот: в стенограмме
            # это неотличимо от полного прогона, то есть планка проходилась
            # словами — ровно тем способом, против которого написана.
            gates.append({"gate": name, "status": "skipped",
                          "detail": "одни ворота (--gate): не проверялось"})
            continue
        if quick and not only and name in QUICK_SKIP:
            gates.append({"gate": name, "status": "skipped",
                          "detail": "быстрый режим: не проверялось"})
            continue
        r = fn()
        gates.append({"gate": name, **r})

    red = [g for g in gates if g["status"] == "fail"]
    grey = [g for g in gates if g["status"] == "unknown"]
    skipped = [g for g in gates if g["status"] == "skipped"]

    if red:
        nxt = _next_step(red[0])
    elif grey:
        nxt = f"не проверено: {grey[0]['gate']} — {grey[0]['detail']}"
    elif skipped:
        nxt = ("не запускалось: " + ", ".join(g["gate"] for g in skipped)
               + " — прогнать планку целиком, прежде чем объявлять готово")
    else:
        nxt = "планка взята"

    return {"bar": "superstack", "done": not red and not grey and not skipped,
            "gates": gates, "next": nxt}


def _next_step(g: dict) -> str:
    if g["gate"] == "план" and g.get("missing"):
        m = g["missing"][0]
        return f"построить: [{m['layer']}] {m['mechanism']} -> {m['file']}"
    if g["gate"] == "мутации" and g.get("survived"):
        s = g["survived"][0]
        return f"запереть тестом: {s['id']} — {s['why']}"
    return f"починить: {g['gate']} — {g['detail']}"


MARK = {"pass": "+", "fail": "x", "unknown": "?", "skipped": "-"}


def human(v: dict) -> str:
    lines = ["ПЛАНКА ВЗЯТА" if v["done"] else "ПЛАНКА НЕ ВЗЯТА"]
    for g in v["gates"]:
        lines.append(f"  {MARK.get(g['status'], '?')} {g['gate']:<15} {g['detail']}")
        for m in (g.get("missing") or [])[:8]:
            lines.append(f"      нет: [{m['layer']}] {m['mechanism']}")
        for s in (g.get("survived") or [])[:8]:
            lines.append(f"      выжила: {s['id']} — {s['why']}")
        for s in (g.get("stuck") or [])[:8]:
            lines.append(f"      застряла: {s['id']} в {s['file']}")
    lines.append(f"  дальше: {v['next']}")
    return "\n".join(lines)


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    # Журнал подключён минимально — точка входа и точка выхода, без записи на
    # каждые из шести ворот: шумный журнал не читают, а значит его нет. Отказ
    # самого journal.event() сюда никогда не долетает — он гасится внутри.
    _t0 = time.monotonic()
    _log_event("gauntlet", "запуск", "начало", **{"argv": " ".join(sys.argv[1:])})

    halt_if_paused()
    argv = sys.argv[1:]
    quick = "--quick" in argv
    quiet = "--json" in argv
    only = None
    if "--gate" in argv:
        i = argv.index("--gate")
        if i + 1 >= len(argv):
            print("вызов: gauntlet.py [--json] [--quick] [--gate ИМЯ]", file=sys.stderr)
            _log_event("gauntlet", "запуск", "ошибка вызова",
                       duration_ms=(time.monotonic() - _t0) * 1000, exit_code=3)
            return 3
        only = argv[i + 1]
        if only not in {n for n, _ in GATES}:
            print(f"НЕ УДАЛОСЬ: нет ворот «{only}». Есть: "
                  + ", ".join(n for n, _ in GATES), file=sys.stderr)
            _log_event("gauntlet", "запуск", "ошибка вызова",
                       duration_ms=(time.monotonic() - _t0) * 1000, exit_code=3)
            return 3

    v = run(only, quick)
    if not quiet:
        print(human(v), file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))

    code = 0
    if any(g["status"] == "fail" for g in v["gates"]):
        code = 1
    elif any(g["status"] in ("unknown", "skipped") for g in v["gates"]):
        code = 2
    _log_event("gauntlet", "запуск", "планка взята" if v["done"] else "планка не взята",
               duration_ms=(time.monotonic() - _t0) * 1000, exit_code=code)
    return code


if __name__ == "__main__":
    sys.exit(main())
