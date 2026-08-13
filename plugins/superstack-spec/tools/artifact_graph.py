#!/usr/bin/env python3
"""SUPERSTACK — граф проектных документов. Связность держит скрипт, а не память.

Зачем это отдельный инструмент, а не абзац в скилле.

Агентский markdown гниёт МОЛЧА. Спека ссылается на план, который переименовали
или удалили в прошлой сессии — файл читается нормально, ошибки никто не видит,
потому что никто не читает ВСЁ дерево целиком за один раз. План ссылается сам
на себя транзитивно (A -> B -> A) — оба документа при беглом чтении выглядят
разумными по отдельности, и цикл заметен только тому, кто держит в голове весь
граф сразу. Спека годами живёт без единого теста, потому что «Как проверить»
в спеке (см. `spec_lint.py`) — это описание проверки прозой, а не гарантия,
что проверка СУЩЕСТВУЕТ на диске в виде файла с кодом возврата.

Это ровно тот класс отказов, который человек не ловит чтением по кругу, а
скрипт — ловит всегда одинаково, потому что читает целиком и без усталости.

Контракт с документами (frontmatter, простая форма «ключ: значение», без
YAML-библиотек — см. обоснование в `memory_lint.py`):

    ---
    type: spec | plan | task      # необязательно; вне этого набора документ
                                   # остаётся узлом графа, но не спекой
    id: login-flow                # необязательно; по умолчанию — путь без .md
    links: auth-plan, session-task.md   # рёбра графа, через запятую
    tests: tests/test_login.py, tests/test_session.py   # только для type: spec
    ---

Три правила, ради которых инструмент выглядит именно так:

  1. ЦИКЛЫ. `links` образуют ориентированный граф; A, транзитивно зависящий
     от самого себя, не является планом — это петля, у которой нет ни начала,
     ни условия остановки.
  2. ССЫЛКИ В НИКУДА. Запись в `links`, которая не резолвится ни в один
     документ каталога, выглядит как связь, которой нет: читатель поверит,
     что зависимость учтена, хотя цели не существует.
  3. СПЕКА БЕЗ ЕДИНОГО ТЕСТА. `type: spec` без поля `tests`, ИЛИ с полем,
     где ни один путь не существует на диске как файл, — спека, которую
     нечем закрыть: «Как проверить» осталось прозой и никогда не станет
     кодом возврата.

Отдельно, по тому же правилу, что и в `memory_lint.py`: «не нашёл» и
«не смог прочитать» — разные утверждения. Файл, который не разобрался (не
UTF-8, битый фронтматтер), попадает в `unchecked` и гасит вердикт до
`unchecked`, а не пропадает молча из отчёта — иначе непрочитанная часть
дерева выглядела бы как проверенная.

Пустой каталог — тоже НЕ «чисто»: `absent` — отдельный статус с отдельным
кодом возврата, а не синоним успеха. Проверять там нечего, и подавать это
как пройденный гейт значит поощрять пустой каталог сильнее настоящего.

  python3 artifact_graph.py [каталог]          -> вердикт человеку + JSON
  python3 artifact_graph.py --json [каталог]   -> только JSON

  код 0 — граф цел, 1 — есть проблемы, 2 — не смог прочитать всё,
  3 — ошибка вызова, 4 — каталог пуст (проверять нечего), 10 — система на паузе
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --- поиск соседних пакетов -------------------------------------------------
# После разделения на семь плагинов соседние инструменты лежат в других
# каталогах. Пути берутся из МАНИФЕСТА этого плагина (поле dependencies), а не
# перечисляются здесь вторым списком: два списка расходятся молча, и расхождение
# всплывает импортом, упавшим у пользователя, а не у нас.
def _wire_siblings(here: Path) -> None:
    import json
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    plug_root = here.parent
    manifest = plug_root / ".claude-plugin" / "plugin.json"
    repo_plugins = plug_root.parent
    try:
        deps = json.loads(manifest.read_text("utf-8")).get("dependencies", [])
    except (OSError, ValueError):
        deps = []
    for dep in deps:
        d = repo_plugins / dep["name"] / "tools"
        if d.is_dir() and str(d) not in sys.path:
            sys.path.insert(0, str(d))


_wire_siblings(Path(__file__).resolve().parent)


from adjudicate import halt_if_paused  # noqa: E402

#: Поля фронтматтера, которые понимает граф. Всё остальное — не ошибка (это
#: не линт схемы, как memory_lint), просто не участвует в построении рёбер.
FIELD_TYPE = "type"
FIELD_ID = "id"
FIELD_LINKS = "links"
FIELD_TESTS = "tests"

#: Типы документов, для которых действует правило «без теста — не гейт».
#: Значение сравнивается в нижнем регистре, чтобы «Spec» и «spec» не спорили.
SPEC_TYPES = {"spec"}


# --------------------------------------------------------------------------
# чтение
# --------------------------------------------------------------------------
@dataclass
class Front:
    """Разобранный фронтматтер. Ошибка разбора — состояние, а не исключение."""
    present: bool
    fields: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class Doc:
    path: Path
    rel: str            # posix-путь от корня сканирования — канонический ключ узла
    front: Front
    doc_type: str        # нормализованный type, "" если не задан
    id_field: str         # явный id из фронтматтера, "" если не задан
    links: list           # сырые значения поля links, в порядке появления
    tests: list            # сырые значения поля tests, в порядке появления


def parse_front(text: str) -> Front:
    """Фронтматтер разбирается нарочно примитивно: «ключ: значение».

    Список хранится ОДНОЙ строкой через запятую, а не YAML-списком с «- »:
    зависимости от PyYAML в проекте нет и не будет (см. то же решение в
    `memory_lint.py`) — цена библиотеки ради разбора трёх полей шапки выше,
    чем цена самого разбора. Строка, которая в форму «ключ: значение» не
    укладывается, не «поддерживается частично», а называется вслух.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return Front(present=False)
    body = None
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            body = lines[1:i]
            break
    if body is None:
        # Незакрытый блок опаснее отсутствующего: половина шапки утекает
        # в тело и её поля молча теряются из графа.
        return Front(present=True, error="блок фронтматтера не закрыт")

    fields: dict = {}
    for raw in body:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            return Front(present=True, fields=fields,
                         error=f"строка не разбирается как «ключ: значение»: {line[:60]}")
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip().strip("'\"")
    return Front(present=True, fields=fields)


def split_list(value: str) -> list:
    """Значение поля списком: запятая — разделитель, пустые элементы отброшены."""
    if not value:
        return []
    return [p.strip() for p in value.split(",") if p.strip()]


def load(root: Path) -> tuple:
    """Прочитать все *.md рекурсивно. Возвращает (документы, непрочитанное).

    Рекурсивно — намеренно: спека и план почти всегда лежат в разных
    подпапках, и проверка одного верхнего уровня давала бы «чисто» ровно
    потому, что не заглянула туда, где лежит проблема.

    Непрочитанное не выбрасывается и не глотается: файл, до которого граф
    не добрался, обязан гасить доверие к вердикту, а не исчезать из него.
    """
    docs, unchecked = [], []
    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        try:
            raw = path.read_bytes()
        except OSError as e:
            unchecked.append({"file": rel, "why": f"не читается: {e.strerror or e}"})
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            unchecked.append({"file": rel, "why": "не декодируется как UTF-8"})
            continue

        front = parse_front(text)
        if front.error:
            unchecked.append({"file": rel, "why": f"фронтматтер не разобрался: {front.error}"})
            continue

        fields = front.fields
        doc_type = fields.get(FIELD_TYPE, "").strip().lower()
        id_field = fields.get(FIELD_ID, "").strip()
        links = split_list(fields.get(FIELD_LINKS, ""))
        tests = split_list(fields.get(FIELD_TESTS, ""))
        docs.append(Doc(path=path, rel=rel, front=front, doc_type=doc_type,
                        id_field=id_field, links=links, tests=tests))
    return docs, unchecked


# --------------------------------------------------------------------------
# разбор смысла
# --------------------------------------------------------------------------
def norm_key(raw: str) -> str:
    """Нормализовать имя цели ссылки: alias, регистр, слэши и «.md» — не смысл.

    `[[план|подпись]]`-подобный alias здесь не нужен, но символ «|» отрезаем
    на всякий случай — авторы копируют синтаксис из памяти (`memory_lint.py`)
    по привычке, и такая ссылка не должна тихо провалить резолюцию.
    """
    target = raw.split("|", 1)[0].strip()
    target = target.strip("/").lower()
    while target.startswith("./"):
        target = target[2:]
    if target.endswith(".md"):
        target = target[:-3]
    return target


def build_index(docs: list) -> dict:
    """Все имена, по которым документ достижим из чужого `links`.

    Порядок приоритета: сперва явный `id` (автор написал его нарочно, и путь
    не должен его перебивать), затем сам путь, путь без «.md» и голое имя
    файла. Коллизия имён по пути невозможна (пути в дереве уникальны);
    коллизия по `id` или basename решается по порядку обхода — та же
    неоднозначность, что документирована в `memory_lint.py`, и она не
    является предметом ЭТОЙ проверки (она не про циклы, битые ссылки или
    тесты).
    """
    index: dict = {}
    for d in docs:
        if d.id_field:
            index.setdefault(norm_key(d.id_field), d.rel)
    for d in docs:
        stem = d.rel[:-3] if d.rel.endswith(".md") else d.rel
        base = Path(d.rel).name
        for key in (d.rel, stem, base, Path(base).stem):
            index.setdefault(norm_key(key), d.rel)
    return index


def find_cycles(nodes: list, edges: dict) -> list:
    """Найти циклы в ориентированном графе `links` через DFS с тремя цветами.

    Не претендует на исчерпывающий список ВСЕХ циклов графа (их количество
    может расти экспоненциально) — гарантирует то, что требует правило:
    если цикл есть, DFS обязательно пройдёт по обратному ребру и найдёт хотя
    бы один. Обратное ребро `u -> v`, где `v` ещё серый (на текущем пути DFS),
    и есть цикл: путь от `v` до `u` в стеке вызовов плюс замыкающий `v`.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in nodes}
    stack: list = []
    found: list = []
    seen_keys: set = set()

    def dfs(u: str) -> None:
        color[u] = GRAY
        stack.append(u)
        for v in edges.get(u, ()):
            if color.get(v) == GRAY:
                i = stack.index(v)
                cycle = stack[i:] + [v]
                dedup_key = tuple(sorted(set(cycle)))
                if dedup_key not in seen_keys:
                    seen_keys.add(dedup_key)
                    found.append(cycle)
            elif color.get(v) == WHITE:
                dfs(v)
        stack.pop()
        color[u] = BLACK

    for n in nodes:
        if color[n] == WHITE:
            dfs(n)
    return found


def resolve_test_path(root: Path, raw: str) -> Path:
    """Путь из `tests:` — ОТ КОРНЯ СКАНИРОВАНИЯ, а не от каталога документа.

    Та же причина, что у команд в `spec_lint.py`: путь, воспроизводимый
    только от каталога, где лежит спека, работает лишь на машине автора.
    Корень графа — единственная точка отсчёта, общая для всех документов.
    """
    t = raw.strip()
    while t.startswith("./"):
        t = t[2:]
    return root / t.lstrip("/")


def check_tests(doc: Doc, root: Path) -> dict:
    """Есть ли у спеки хоть один РЕАЛЬНО существующий файл теста.

    «Путь написан» и «файл существует» — разные утверждения. Первое ничего
    не гарантирует: путь мог устареть в тот же день, когда тест переименовали.
    Гейт смотрит на диск, а не на текст поля.
    """
    if not doc.tests:
        return {"ok": False, "reason": "поле tests пусто или отсутствует"}
    existing = [t for t in doc.tests if resolve_test_path(root, t).is_file()]
    if not existing:
        return {"ok": False,
                "reason": "ни один путь из tests не существует на диске: "
                          + ", ".join(doc.tests)}
    return {"ok": True, "tests": existing}


# --------------------------------------------------------------------------
# вердикт
# --------------------------------------------------------------------------
def _finding(check: str, doc_file: str, detail: str, why: str) -> dict:
    return {"check": check, "file": doc_file, "detail": detail, "why": why}


def validate(root: Path) -> dict:
    """Построить граф проектных документов из `root` и провалить по трём правилам.

    Это единственная функция, которая принимает решение — `main()` только
    печатает то, что вернула она, и переводит статус в код возврата.
    """
    docs, unchecked = load(root)

    if not docs:
        # Пусто — НЕ «чисто». Иначе удалить документы выгоднее, чем вести их:
        # тот же стимул, из-за которого в проектах пропадают тесты (verify.py).
        return {
            "gate": "artifact_graph", "status": "absent", "root": str(root),
            "nodes": 0, "problems": [], "unchecked": unchecked,
            "next": "в каталоге нет ни одного .md — граф строить не из чего; "
                    "проверять надо тот каталог, где реально лежат "
                    "спеки/планы/задачи",
        }

    index = build_index(docs)
    problems: list = []

    # 1. Ссылки в никуда — до циклов, потому что от резолюции зависит, из
    #    каких рёбер вообще строится граф для их поиска: битая ссылка ребра
    #    не даёт, и подставлять её в поиск цикла значило бы искать цикл там,
    #    где на самом деле нет связи.
    edges: dict = {d.rel: [] for d in docs}
    for d in docs:
        for raw in d.links:
            key = norm_key(raw)
            if not key:
                problems.append(_finding(
                    "dangling-link", d.rel, "пустой элемент в поле links",
                    "убрать пустой элемент или дописать цель"))
                continue
            target = index.get(key)
            if target is None:
                problems.append(_finding(
                    "dangling-link", d.rel,
                    f"links: «{raw}» не резолвится ни в один документ каталога",
                    "поправить ссылку или завести документ: ссылка в никуда "
                    "выглядит как связь, которой не существует"))
                continue
            edges[d.rel].append(target)

    # 2. Циклы — по рёбрам, которые реально резолвились на шаге 1.
    for cycle in find_cycles([d.rel for d in docs], edges):
        problems.append(_finding(
            "cycle", cycle[0], "цикл в links: " + " -> ".join(cycle),
            "разорвать цикл: документ не может транзитивно зависеть сам от "
            "себя — это уже не план, а петля без начала и условия остановки"))

    # 3. Спека без единого теста.
    for d in docs:
        if d.doc_type not in SPEC_TYPES:
            continue
        check = check_tests(d, root)
        if not check["ok"]:
            problems.append(_finding(
                "spec-without-test", d.rel, check["reason"],
                "добавить в frontmatter поле tests со списком реально "
                "существующих файлов — спека без теста не гейт, а пожелание"))

    if problems:
        status = "problems"
    elif unchecked:
        # Замечаний нет, но осмотрено не всё. Это не «чисто»: «не нашёл» и
        # «не смог прочитать» — разные утверждения (правило 1).
        status = "unchecked"
    else:
        status = "clean"

    return {
        "gate": "artifact_graph", "status": status, "root": str(root),
        "nodes": len(docs), "problems": problems, "unchecked": unchecked,
        "next": _next_step(status, problems, unchecked),
    }


def _next_step(status: str, problems: list, unchecked: list) -> str:
    if status == "clean":
        return "граф цел: ссылки резолвятся, циклов нет, у каждой спеки есть тест"
    if status == "unchecked":
        return f"замечаний нет, но {len(unchecked)} файл(ов) не прочитано — вердикт неполный"
    top = problems[0]
    tail = f"; и осмотрено не всё — {len(unchecked)} файл(ов) не прочитано" if unchecked else ""
    return f"начать с [{top['check']}] в {top['file']}: {top['detail']}{tail}"


# --------------------------------------------------------------------------
# вывод
# --------------------------------------------------------------------------
HEAD = {"clean": "ГРАФ ЦЕЛ", "problems": "ЕСТЬ ПРОБЛЕМЫ",
        "unchecked": "НЕ СМОГ ПРОЧИТАТЬ ВСЁ", "absent": "ПРОВЕРЯТЬ НЕЧЕГО"}
EXIT = {"clean": 0, "problems": 1, "unchecked": 2, "absent": 4}


def human(v: dict) -> str:
    lines = [f"{HEAD.get(v['status'], v['status'])}  [{v['root']}]  узлов: {v['nodes']}"]
    for p in v["problems"]:
        lines.append(f"  ! [{p['check']}] {p['file']}: {p['detail']}")
        lines.append(f"      почему важно: {p['why']}")
    for u in v["unchecked"]:
        lines.append(f"  ? не прочитан {u['file']}: {u['why']}")
    lines.append(f"  дальше: {v['next']}")
    return "\n".join(lines)


def main() -> int:
    halt_if_paused()
    args = [a for a in sys.argv[1:] if a != "--json"]
    quiet = "--json" in sys.argv[1:]
    if len(args) > 1:
        print("вызов: artifact_graph.py [--json] [каталог]", file=sys.stderr)
        return 3
    root = Path(args[0]).expanduser().resolve() if args else Path.cwd()
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: каталога нет — {root}", file=sys.stderr)
        return 3

    v = validate(root)
    if not quiet:
        print(human(v), file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return EXIT.get(v["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
