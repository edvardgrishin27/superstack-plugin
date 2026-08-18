#!/usr/bin/env python3
"""SUPERSTACK — ПЛАНКА. Одна команда отвечает: система построена или нет.

Зачем это отдельный инструмент, а не суждение модели.

Цикл «строитель → критик» разваливается в одном месте: когда останов решает
тот, кто строил. Оценщик `/goal` в Claude Code **не вызывает инструменты** —
он читает стенограмму. Значит цель «все ошибки исправлены» удовлетворяется
тем, что агент СКАЗАЛ, будто исправлены. Планка, которую можно пройти словами,
не планка.

Здесь останов считает код. Семь ворот, каждые со своим способом соврать:

  · набор       — тесты зелёные. Слабейшее из ворот: зелёное можно нарисовать.
  · герметичность — то же число тестов при другом HOME. Ловит «зелено, потому
                   что на ЭТОЙ машине так сложилось»: набор уже уезжал с 260 на
                   282 теста на неизменном коде.
  · мутации     — зарегистрированные поломки обязаны ронять набор. Единственные
                   ворота, доказывающие, что тесты вообще что-то держат.
  · проводка    — до инструмента дотягивается хоть одна точка входа. Ворота,
                   которых не хватало: 14 инструментов из 29 были недостижимы,
                   а оба вызова сборочного скилла указывали в пустоту — и набор
                   при этом был зелёным, потому что файлы на месте и функции
                   работают. «Есть файл» и «подключено» — разные утверждения.
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
  python3 gauntlet.py --gate мутации --mutation a.b,c.d  -> названные поломки

Частичный прогон (--gate, --quick) НИКОГДА не даёт «планка взята»: незапущенные
ворота остаются в отчёте как skipped и держат код 2. Иначе «планка взята»
покупается за секунду прогоном самого дешёвого из шести ворот.

  код 0 — планка взята, 1 — что-то красное, 2 — что-то не проверено, 3 — вызов
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


# log.py лежит в пакете, а планка — в корне репозитория: путь считается от
# структуры дерева, а не относительным прыжком через ".." — прыжок ломается при
# любом перемещении и делает это без единого сообщения.
_TOOLS = Path(__file__).resolve().parents[1] / "plugins" / "superstack" / "tools"
if _TOOLS.is_dir() and str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))


from log import event as _log_event  # noqa: E402

PLUG = Path(__file__).resolve().parent.parent
SUITE_TIMEOUT = 900
MUTATION_TIMEOUT = 600

#: Какие мутации проверять поимённо (`--mutation id,id`). Пусто = все.
#: Заведено после того, как проверка трёх свежих поломок обошлась в прогон всех
#: 241: незнакомый флаг молча игнорировался, а разница между «проверил три» и
#: «проверил всё» — пятнадцать минут против часа. Выборка ОБЯЗАНА помечать себя
#: в отчёте, иначе три пойманные мутации читаются как взятая планка.
ONLY_MUTATIONS: set = set()


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


def _lock() -> Path:
    return PLUG / ".mutation-lock"


def _alive(pid: int) -> bool:
    """Жив ли процесс. Сомнение решается в сторону «жив».

    Ошибиться в эту сторону стоит одного отказа мерить; в обратную — двух
    харнессов, мутирующих одно дерево, и это уже было трижды.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def acquire_lock() -> "str | None":
    """Занять исключительное право мутировать дерево. Беда либо None.

    Зачем замок. Два процесса, мутирующих одно дерево, портят измерения друг
    друга молча: каждый видит чужую поломку и относит её на счёт своей. За одну
    сессию это случилось трижды, и в третий раз — когда я сам запустил починку
    поверх идущей проверки. Механизм без замка не защищает даже от автора.

    Мёртвый замок снимается сам: процесс, убитый по SIGKILL, файл за собой не
    уберёт, и вечная блокировка от прошлого прогона — это отказ мерить навсегда.
    """
    p = _lock()
    if p.is_file():
        try:
            pid = int(p.read_text("utf-8").split()[0])
        except (OSError, ValueError, IndexError):
            pid = None
        if pid and pid != os.getpid() and _alive(pid):
            return (f"дерево уже мутирует процесс {pid} — второй харнесс увидит "
                    "чужую поломку и отнесёт её на свой счёт; дождись первого "
                    f"или сними {p}")
    try:
        p.write_text(f"{os.getpid()}\n", encoding="utf-8")
    except OSError as e:
        return f"замок не поставлен ({e}) — мутировать вслепую нельзя"
    return None


def release_lock() -> None:
    try:
        _lock().unlink()
    except OSError:
        pass


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

    total = len(muts)
    picked = None
    if ONLY_MUTATIONS:
        known = {m["id"] for m in muts}
        unknown = sorted(ONLY_MUTATIONS - known)
        if unknown:
            # Опечатка в имени иначе даёт «все пойманы», не проверив ничего —
            # то есть самый убедительный из возможных зелёных отчётов.
            return {"status": "unknown",
                    "detail": "нет таких мутаций: " + ", ".join(unknown)}
        picked = [m for m in muts if m["id"] in ONLY_MUTATIONS]
        muts = picked

    held = acquire_lock()
    if held:
        return {"status": "unknown", "detail": held}
    try:
        v = _mutate_all(muts)
    finally:
        release_lock()

    if picked is not None:
        # Подмножество не имеет права выглядеть взятой планкой: «проверено 3»
        # и «проверены все» — разные утверждения, и второе тут не доказано.
        v["subset"] = True
        v["detail"] = (f"{v['detail']} — ВЫБОРКА {len(picked)} из {total}, "
                       "остальные не проверялись")
    return v


def _backup_dir() -> Path:
    return PLUG / ".mutation-backup"


def stash(mid: str, target: Path, original: bytes) -> None:
    """Отложить оригинал ДО внесения поломки.

    Восстановление обратной заменой — угадывание, и оно уже испортило файл:
    поломка `crew.missing-stamps-read-as-clean` заменяла блок на строку
    `continue`, а такая строка в файле не одна. Обратная замена вернула блок в
    ПЕРВОЕ вхождение — в другую ветку. Файл остался синтаксически валидным и
    стал семантически неверным, `--unstick` отчитался «вернул 1», и увидели это
    только десять упавших тестов.

    Байты, отложенные заранее, снимают вопрос целиком: возвращать нечего
    искать. Живёт рядом с замком и переживает убийство процесса — ровно тот
    случай, ради которого всё это и нужно.
    """
    d = _backup_dir()
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.-]", "_", mid)
    (d / f"{safe}.bak").write_bytes(original)
    (d / f"{safe}.json").write_text(json.dumps(
        {"id": mid, "file": str(target.relative_to(PLUG)),
         "sha256": hashlib.sha256(original).hexdigest()}, ensure_ascii=False),
        encoding="utf-8")


def _stashed(mid: str) -> "bytes | None":
    """Отложенный оригинал этой поломки, если он есть."""
    safe = re.sub(r"[^\w.-]", "_", mid)
    p = _backup_dir() / f"{safe}.bak"
    try:
        return p.read_bytes()
    except OSError:
        return None


def unstash(mid: str) -> None:
    safe = re.sub(r"[^\w.-]", "_", mid)
    for suf in (".bak", ".json"):
        try:
            (_backup_dir() / f"{safe}{suf}").unlink()
        except OSError:
            pass


def _mutate_all(muts: list) -> dict:
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
        stash(m["id"], target, original)
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
                          MUTATION_TIMEOUT,
                          # Сторож в conftest обязан молчать ИМЕННО здесь. Он
                          # обрывает набор, увидев мутацию в дереве, — а тут она
                          # применена намеренно. Без этого флага pytest вышел бы
                          # с ненулевым кодом до единого теста, ворота прочли бы
                          # это как «мутация поймана», и весь набор мутаций
                          # отчитался бы пойманным, не проверив ни одной.
                          env={"SUPERSTACK_MUTATION_RUN": "1"})
            caught = code != 0
        finally:
            # Восстановление байт-в-байт и ДО любой другой работы: мутация,
            # пережившая прогон в файле, отравит все следующие ворота.
            target.write_bytes(original)
            # Отложенное снимается только ПОСЛЕ удачного возврата: пока файл не
            # восстановлен, единственная целая копия — эта.
            unstash(m["id"])
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
    # Каталоги правил перечисляются глобом, а не именем: линт по одному
    # каталогу молча проверял бы часть файлов и печатал «правила целы» — то
    # есть врал бы ровно тем способом, против которого написан.
    linter = PLUG / "plugins" / "superstack" / "tools" / "lint_rules.py"
    globs = [str(d / "*.json") for d in sorted((PLUG / "plugins").glob("*/rules"))]
    if not globs:
        return {"status": "unknown", "detail": "ни одного каталога правил не найдено"}
    code, out = _sh([sys.executable, str(linter)] + globs, 300)
    if code == 0:
        return {"status": "pass", "detail": out.strip().splitlines()[-1][:200] if out.strip() else "правила целы"}
    return {"status": "fail", "detail": out.strip()[-500:]}


def marketplace_crosscheck() -> dict:
    """Объявленное против существующего — в обе стороны, без подпроцессов.

    Ворота подписались строкой «плагин ставится, а не только лежит», а
    проверяли один файл из восьми: `claude plugin validate .` валидирует
    ТОЛЬКО `marketplace.json` и молчит о том, существует ли каталог из
    `source`. Живой случай: манифест объявлял `./plugins/superstack`, каталога
    не было, семь настоящих пакетов не были объявлены вовсе — и ворота были
    зелёными. Продукт не устанавливался с момента разделения на семь, и
    заметить это было нечем.

    Проверка нарочно НЕ зовёт `claude`: на машине без него сильная половина
    ворот иначе исчезала бы молча.

    Третье множество — сирота — обязательно. Каталог, потерявший свой
    `plugin.json`, выпадает из обеих разностей сразу, и проверка зеленеет на
    пустоте.
    """
    mk = PLUG / ".claude-plugin" / "marketplace.json"
    if not mk.is_file():
        return {"status": "unknown", "detail": f"нет манифеста маркетплейса: {mk}"}
    try:
        entries = json.loads(mk.read_text("utf-8"))["plugins"]
    except (ValueError, KeyError) as e:
        return {"status": "unknown", "detail": f"манифест не разобран: {e}"}

    pkgs, orphans = {}, []
    for d in sorted((PLUG / "plugins").glob("*")):
        if not d.is_dir():
            continue
        pj = d / ".claude-plugin" / "plugin.json"
        if not pj.is_file():
            orphans.append(d.name)
            continue
        try:
            pkgs[d.name] = json.loads(pj.read_text("utf-8"))
        except ValueError as e:
            orphans.append(f"{d.name} (манифест не разобран: {e})")

    bad, seen = [], set()
    for e in entries:
        name, src = e.get("name", ""), (e.get("source") or "")
        if name in seen:
            bad.append(f"{name}: объявлен дважды — ключ установки перестаёт "
                       "указывать на одно")
        seen.add(name)
        d = PLUG / src.lstrip("./") if isinstance(src, str) else None
        if d is None or not d.is_dir():
            bad.append(f"{name}: source `{src}` не существует — запись в пустоту")
            continue
        pj = d / ".claude-plugin" / "plugin.json"
        if not pj.is_file():
            bad.append(f"{name}: в `{src}` нет .claude-plugin/plugin.json")
            continue
        p = pkgs.get(d.name, {})
        if p.get("name") != name:
            bad.append(f"{name}: пакет представляется как «{p.get('name')}» — "
                       "ключ установки указывал бы не на тот пакет")
        if e.get("version") != p.get("version"):
            bad.append(f"{name}: версия записи {e.get('version')} ≠ версии "
                       f"пакета {p.get('version')} — semver удовлетворяется "
                       "не тем кодом, а имя каталога установки берётся отсюда")

    for n in sorted(set(pkgs) - seen):
        bad.append(f"{n}: пакет есть, записи в маркетплейсе нет — не поставится")

    # Объявленное поле ГАСИТ каталог: движок берёт дефолтный каталог только
    # когда поля нет. Файл, лежащий рядом и не названный, не грузится никогда,
    # и ошибки при этом нет. Так уже потерялся агент слепой приёмки.
    for n, p in sorted(pkgs.items()):
        for field, sub in (("agents", "agents"), ("skills", "skills"),
                           ("commands", "commands")):
            declared = p.get(field)
            if not isinstance(declared, list):
                continue
            named = {Path(x).name for x in declared if isinstance(x, str)}
            on_disk = {f.name for f in (PLUG / "plugins" / n / sub).glob("*.md")}
            missed = sorted(on_disk - named)
            if missed and not any(str(x).rstrip("/").endswith(sub) for x in declared):
                bad.append(f"{n}: в `{sub}/` лежит незаявленное — {', '.join(missed[:3])}; "
                           "объявленное поле гасит каталог, и это не грузится молча")

    if bad:
        return {"status": "fail", "detail": f"{len(bad)} расхождений: {bad[0]}",
                "mismatches": bad}
    if orphans:
        return {"status": "unknown",
                "detail": "каталоги без манифеста: " + ", ".join(orphans[:5])}
    return {"status": "pass",
            "detail": f"{len(entries)} записей = {len(pkgs)} пакетов, версии сходятся"}


def gate_manifest() -> dict:
    cross = marketplace_crosscheck()
    if cross["status"] == "fail":
        return cross
    if not shutil.which("claude"):
        return {"status": "unknown",
                "detail": "сверка прошла, но схему проверить нечем: claude не найден"}
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    try:
        p = subprocess.run(["claude", "plugin", "validate", "."],
                           cwd=str(PLUG), capture_output=True,
                           text=True, timeout=180, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"status": "unknown", "detail": str(e)[:200]}
    if p.returncode != 0:
        return {"status": "fail",
                "detail": "корневой манифест: "
                          + ((p.stdout or "") + (p.stderr or ""))[-300:]}

    # Каждый пакет — отдельно. Раньше валидировался только корень, и шесть
    # манифестов из семи были невалидны при зелёных воротах: движок 2.1.42
    # отвергает ключ `dependencies`, а узнать об этом было неоткуда.
    broken = []
    for d in sorted((PLUG / "plugins").glob("*")):
        if not (d / ".claude-plugin" / "plugin.json").is_file():
            continue
        r = subprocess.run(["claude", "plugin", "validate", str(d)],
                           cwd=str(PLUG), capture_output=True, text=True,
                           timeout=180, env=env)
        if r.returncode != 0:
            tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
            broken.append(f"{d.name}: {tail[-1][:120] if tail else 'провал'}")
    if broken:
        return {"status": "fail",
                "detail": f"невалидных пакетов {len(broken)}: {broken[0]}",
                "packages": broken}
    return {"status": "pass",
            "detail": f"{cross['detail']}; схема валидна у корня и всех пакетов"}


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


#: Точки, из которых Claude Code вообще что-либо запускает. Всё остальное —
#: библиотеки, до которых надо дотянуться отсюда.
ENTRY_GLOBS = ("*/skills/**/*.md", "*/hooks/*.sh", "*/hooks/*.json",
               "*/agents/*.md", "*/commands/*.md")


def gate_wiring() -> dict:
    """Инструмент обязан быть ДОСТИЖИМ, а не просто существовать.

    Единственные ворота, которые ловят болезнь этого проекта в его собственном
    исходнике. Она уже случалась трижды, и каждый раз выглядела построенной
    системой: `hooks.json` объявлял 34 хука при девяти подключённых; `learn.py`
    был написан целиком — планка из трёх условий, маршрутизация, слияние — и не
    вызывался НИКЕМ; в один заход сюда добавилось шесть инструментов, до
    которых не дотягивался ни один скилл.

    Механизм, который не с чего запустить, неотличим от отсутствующего — и хуже
    его, потому что занимает место в отчёте и в голове. Тесты его не ловят:
    файл на месте, функции работают, набор зелёный.

    Достижимость считается транзитивно от точек входа: скилл зовёт инструмент,
    тот зовёт следующий. Инструмент, который человек запускает сам, — законный
    случай, но он обязан быть НАЗВАН в `data/entrypoints.json` вместе с
    причиной. Разница между «решили так» и «забыли» должна быть записана, иначе
    через месяц её не отличить.
    """
    tools = sorted(PLUG.glob("plugins/*/tools/*.py"))
    if not tools:
        return {"status": "unknown", "detail": "инструментов не найдено"}

    entries = []
    for g in ENTRY_GLOBS:
        entries += list(PLUG.glob("plugins/" + g))

    declared, why = set(), {}
    ep = PLUG / "data" / "entrypoints.json"
    if ep.is_file():
        try:
            for e in json.loads(ep.read_text("utf-8"))["entrypoints"]:
                declared.add(e["tool"])
                why[e["tool"]] = e.get("why", "")
        except (ValueError, KeyError, TypeError) as e:
            return {"status": "unknown",
                    "detail": f"список точек входа не разобран: {e}"}

    text = {}
    for f in entries + tools:
        try:
            text[f] = f.read_text("utf-8", errors="replace")
        except OSError:
            text[f] = ""

    by_name = {t.name: t for t in tools}

    def зовёт(где: str, имя: str) -> bool:
        """Вызов инструмента — это и путь к файлу, и питоновский импорт.

        Проверка только по «имя.py» пропускает `import derive_phase`: модуль
        пишется без расширения, и на глаз разницы нет. Один такой инструмент
        уже числился достижимым по ложной причине — его имя встречалось в
        карте владельцев внутри резолвера, то есть в строке справочника, а не
        в вызове. Слияние удалило резолвер, маска слетела, и ворота показали
        мёртвым инструмент, который на самом деле работает каждый прогон.

        Обратная ошибка тоже возможна и хуже: искать голое имя без границ
        значило бы считать вызовом любое упоминание в комментарии. Поэтому
        импорт распознаётся формой оператора, а не подстрокой.
        """
        if имя in где:
            return True
        мод = имя[:-3]
        return re.search(rf"^\s*(?:import|from)\s+{re.escape(мод)}\b",
                         где, re.M) is not None

    # Достижимое от точек входа, потом транзитивно через сами инструменты.
    reached = {n for n in by_name
               if any(зовёт(text[e], n) for e in entries) or n in declared}
    for _ in range(len(by_name) + 1):
        grown = set(reached)
        for n, t in by_name.items():
            if n in reached:
                continue
            if any(зовёт(text[by_name[r]], n) for r in reached if r in by_name):
                grown.add(n)
        if grown == reached:
            break
        reached = grown

    dead = sorted(n for n in by_name if n not in reached)
    stale = sorted(d for d in declared if d not in by_name)

    # Второй отказ этих же ворот, и он тише первого. `${CLAUDE_PLUGIN_ROOT}`
    # разворачивается в корень ТОГО плагина, чей скилл исполняется. Скилл,
    # зовущий `$CLAUDE_PLUGIN_ROOT/tools/verify.py` из пакета, где verify.py
    # нет, выглядит подключённым и падает «нет такого файла» — то есть как
    # поломка окружения, а не как опечатка в пути. Так и было: оба вызова
    # единственного сборочного скилла указывали в пустоту несколько заходов.
    wrong = []
    for e in entries:
        plug = e.relative_to(PLUG / "plugins").parts[0]
        for m in re.finditer(r"\$\{?CLAUDE_PLUGIN_ROOT\}?/tools/([\w.]+\.py)",
                             text[e]):
            if not (PLUG / "plugins" / plug / "tools" / m.group(1)).is_file():
                wrong.append(f"{plug}: зовёт {m.group(1)} через свой корень, "
                             f"а его там нет")
    if wrong:
        return {"status": "fail",
                "detail": f"пути в пустоту: {len(wrong)} — {wrong[0]}",
                "wrong_paths": sorted(set(wrong)),
                "next": "поправить имя инструмента: все они лежат в "
                        "plugins/superstack/tools, и путь от корня пакета верен"}

    if dead:
        return {"status": "fail",
                "detail": f"инструментов не достать ниоткуда: {len(dead)} из "
                          f"{len(tools)} — {', '.join(dead[:6])}",
                "dead": [str(by_name[d].relative_to(PLUG)) for d in dead],
                "next": "подключить к скиллу, хуку или агенту — либо записать "
                        "в data/entrypoints.json с причиной, почему инструмент "
                        "запускает человек"}
    if stale:
        return {"status": "unknown",
                "detail": f"в списке точек входа названы несуществующие "
                          f"инструменты: {', '.join(stale[:5])}"}
    return {"status": "pass",
            "detail": f"все {len(tools)} инструментов достижимы"
                      + (f" ({len(declared)} — вручную, с причиной)" if declared else "")}


GATES = [
    ("набор", gate_suite),
    ("герметичность", gate_hermetic),
    ("мутации", gate_mutations),
    ("проводка", gate_wiring),
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

    ЧЕГО ЭТОТ ПРИЗНАК НЕ РАЗЛИЧАЕТ. У многих мутаций замена — общая строка
    (`true`, `continue`, `pass`, `return False`), которая встречается в файле и
    без всякой поломки. Пока якорь на месте, всё честно. Но стоит обычной
    правке сдвинуть якорь — и признак срабатывает на ЧИСТОМ дереве: оригинала
    нет (его переписали), замена «есть» (она есть всегда). Сторож обрывает
    набор одной строкой, и человек идёт чинить несуществующую поломку. Так и
    случилось: правка скилла сдвинула якорь, и весь набор перестал запускаться.

    Соблазн лечить это ЗДЕСЬ — сделать оговорку про отложенный оригинал — был
    и отвергнут: оговорка делает сторожа слепым к поломкам, применённым не этой
    версией планки, то есть покупает удобство ценой самого механизма. Настоящее
    место починки — набор мутаций, и он уже заперт двумя инвариантами: якорь
    обязан встречаться в файле ровно один раз, а сторож обязан молчать на
    чистом файле каждой зарегистрированной пары. Протухший якорь красит их — и
    краснеет он за доли секунды, а не через сорок минут полного прогона.
    """
    src = PLUG / "tests" / "mutations.json"
    if not src.is_file():
        return []
    try:
        muts = json.loads(src.read_text("utf-8"))["mutations"]
    except (ValueError, KeyError):
        return []
    stuck, stale = [], []
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


def restore_stuck() -> dict:
    """Вернуть застрявшие мутации обратно в исходный код.

    Обнаружение без починки — половина механизма. Пока её не было, чинить
    приходилось `git checkout`: в этом репозитории он сработал, потому что
    поломка не была закоммичена, — а в чужом проекте, куда мутационные ворота
    и предназначены, рабочее дерево обычно грязное, и откат файла целиком
    снёс бы вместе с мутацией живую работу человека.

    Порядок такой:

      1. ОТЛОЖЕННЫЕ БАЙТЫ — оригинал, сохранённый перед внесением поломки.
         Возврат точный, искать нечего.
      2. Бэкапа нет (поломка внесена прежней версией планки) — обратная замена,
         но ТОЛЬКО если она однозначна: заменяющая строка встречается в файле
         ровно один раз.
      3. Неоднозначно — отказ с названием файла, а не угадывание.

    Пункт 3 написан кровью. Обратная замена вслепую уже испортила исходник:
    поломка `crew.missing-stamps-read-as-clean` заменяла блок на строку
    `continue`, такая строка в файле не одна, и блок вернулся в ПЕРВОЕ
    вхождение — в чужую ветку. Файл остался валидным и стал неверным,
    `--unstick` отчитался «вернул 1», проверка «мутации больше нет» прошла, и
    увидели это только десять упавших тестов часом позже.

    Восстановление ПРОВЕРЯЕТСЯ, а не объявляется: после записи файл читается
    заново. Из бэкапа сверяется отпечаток — это доказывает возврат к оригиналу,
    а не просто отсутствие поломки, которое верно и для испорченного файла.
    """
    # Починка поверх ИДУЩЕЙ проверки вырывает файл у неё из-под рук: её
    # измерение становится враньём, а моё — тем более. Ровно так я и сделал,
    # разбирая эту самую сессию.
    p = _lock()
    if p.is_file():
        try:
            pid = int(p.read_text("utf-8").split()[0])
        except (OSError, ValueError, IndexError):
            pid = None
        if pid and pid != os.getpid() and _alive(pid):
            return {"restored": [], "failed": [], "status": "unknown",
                    "detail": f"процесс {pid} прямо сейчас мутирует дерево — "
                              "то, что выглядит застрявшим, применено намеренно; "
                              "починка сорвала бы его измерение"}

    src = PLUG / "tests" / "mutations.json"
    if not src.is_file():
        return {"restored": [], "failed": [], "status": "unknown",
                "detail": f"нет набора мутаций: {src}"}
    try:
        muts = json.loads(src.read_text("utf-8"))["mutations"]
    except (ValueError, KeyError) as e:
        return {"restored": [], "failed": [], "status": "unknown",
                "detail": f"набор мутаций не разобран: {e}"}

    restored, failed = [], []
    for m in muts:
        f = PLUG / m["file"]
        if not f.is_file():
            continue
        try:
            t = f.read_text("utf-8", errors="replace")
        except OSError:
            continue
        if not _looks_applied(t, m["find"], m["replace"]):
            continue

        saved = _stashed(m["id"])
        if saved is not None:
            try:
                f.write_bytes(saved)
                ok = hashlib.sha256(f.read_bytes()).hexdigest() == \
                    hashlib.sha256(saved).hexdigest()
            except OSError as e:
                failed.append({"id": m["id"], "file": m["file"], "why": str(e)})
                continue
            if ok:
                unstash(m["id"])
                restored.append({"id": m["id"], "file": m["file"],
                                 "how": "из отложенных байт"})
            else:
                failed.append({"id": m["id"], "file": m["file"],
                               "why": "запись не совпала с оригиналом"})
            continue

        # Бэкапа нет. Угадывать место можно только там, где угадывать нечего.
        if t.count(m["replace"]) != 1:
            failed.append({
                "id": m["id"], "file": m["file"],
                "why": f"строка замены встречается {t.count(m['replace'])} раз "
                       "— вернуть её вслепую значит вставить код в чужую ветку; "
                       "верните файл из git и прогоните набор"})
            continue
        try:
            f.write_text(t.replace(m["replace"], m["find"], 1), encoding="utf-8")
            back = f.read_text("utf-8", errors="replace")
        except OSError as e:
            failed.append({"id": m["id"], "file": m["file"], "why": str(e)})
            continue
        if _looks_applied(back, m["find"], m["replace"]):
            failed.append({"id": m["id"], "file": m["file"],
                           "why": "обратная замена не сняла поломку — чинить руками"})
        else:
            restored.append({"id": m["id"], "file": m["file"],
                             "how": "обратной заменой (единственное вхождение)"})

    # Байткод сносится и здесь: восстановленный исходник той же длины в ту же
    # секунду неотличим для инвалидации, и следующий прогон пошёл бы по .pyc
    # с ещё сломанным кодом — то есть починка выглядела бы не сработавшей.
    if restored:
        purge_bytecode()
    return {"restored": restored, "failed": failed,
            "status": "fail" if failed else "pass",
            "detail": (f"вернул {len(restored)}"
                       + (f", не смог {len(failed)}" if failed else ""))}


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

    # Незнакомый флаг здесь опаснее ошибки вызова: он молча игнорируется, и
    # `--mutation где-то.там` превращается в часовой прогон всего набора,
    # отчитывающийся так же, как выборка из трёх.
    known = {"--json", "--quick", "--unstick", "--gate", "--mutation"}
    stray = [a for a in argv if a.startswith("--") and a not in known]
    if stray:
        print(f"НЕ УДАЛОСЬ: неизвестный флаг {', '.join(stray)}. Есть: "
              + ", ".join(sorted(known)), file=sys.stderr)
        return 3
    if "--mutation" in argv:
        i = argv.index("--mutation")
        if i + 1 >= len(argv):
            print("вызов: gauntlet.py --gate мутации --mutation id[,id]",
                  file=sys.stderr)
            return 3
        globals()["ONLY_MUTATIONS"] = {
            s.strip() for s in argv[i + 1].split(",") if s.strip()}

    # Починка идёт ДО всего: планка с застрявшей поломкой отказывается стартовать,
    # поэтому «сначала прогнать, потом чинить» здесь невозможно в принципе.
    if "--unstick" in argv:
        r = restore_stuck()
        if not quiet:
            print("ДЕРЕВО ВОССТАНОВЛЕНО" if r["status"] == "pass"
                  else "ВОССТАНОВИТЬ НЕ УДАЛОСЬ", file=sys.stderr)
            for x in r["restored"]:
                print(f"  вернул: {x['id']} в {x['file']}", file=sys.stderr)
            for x in r["failed"]:
                print(f"  ! {x['id']} в {x['file']} — {x['why']}", file=sys.stderr)
            if not r["restored"] and not r["failed"]:
                print("  застрявших мутаций нет", file=sys.stderr)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        code = {"pass": 0, "fail": 1}.get(r["status"], 2)
        _log_event("gauntlet", "восстановление", r["detail"],
                   duration_ms=(time.monotonic() - _t0) * 1000, exit_code=code)
        return code

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
