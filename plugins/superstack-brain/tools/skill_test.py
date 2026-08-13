#!/usr/bin/env python3
"""SUPERSTACK — проверка скилла: держит ли он то, что обещает.

Зачем это отдельный инструмент, а не абзац в инструкции.

Скилл — единственный актив системы, за которым не стоит ни одного шлюза.
Правила держит lint_rules.py, код держит verify.py, останов держит gauntlet.py,
а скилл живёт на честном слове: описание обещает «использовать, когда…», тело
зовёт файлы по путям, внутри лежат команды оболочки — и ни одно из этих
обещаний ни с чем не сверяется до первого живого запуска у человека. Улучшать
скилл в таком состоянии — править вслепую: правка не проверяется, и регресс
обнаруживает пользователь. Во всём разобранном корпусе такого шлюза нет ни у кого.

Модель здесь НЕ запускается. Это сознательное ограничение, а не упрощение:
прогон модели недетерминирован, стоит денег и требует сети, поэтому он не может
стоять в петле правки. Офлайн проверяется то, что и ломается чаще всего, — не
качество формулировок, а исполнимость обещаний:

  1. фронтматтер   — name есть и совпадает с именем каталога; description непуст
  2. КОГДА         — description говорит, при каком поводе брать скилл. Это
                     ЭВРИСТИКА ПО ПРОЗЕ, а не поведенческая проверка, и она
                     помечена как вывод: скилл выбирается по description, и
                     описание «что я делаю» вместо «когда меня брать» не
                     подцепится, но доказать это офлайн нечем
  3. стоимость     — len(name)+len(description) против потолка. Описание сверх
                     потолка молча обрезается платформой, и скилл перестаёт
                     подхватываться — отказ без единого сообщения об ошибке
  4. пути          — каждый упомянутый в теле путь к инструменту существует
  5. оболочка      — каждый bash-блок разбирается интерпретатором (-n). Опечатка
                     в команде внутри скилла молчит до первого живого запуска
  6. коды возврата — коды, заявленные таблицей в теле, инструмент действительно
                     умеет отдавать. Если сверить нечем — так и сказано

«Не нашёл» и «не смог проверить» здесь разные утверждения и разные коды
возврата. Путь с подстановкой оболочки (`$W/facts.json`) не объявляется
несуществующим — он объявляется НЕПРОВЕРЕННЫМ и гасит доверие к прогону.

Инструмент ничего не правит: ни одной записи на диск, только чтение.

  python3 skill_test.py <каталог-скилла> [--json] [--budget N] [--root PATH]

  код 0 — чисто, 1 — есть замечания, 2 — не смог проверить, 3 — ошибка вызова
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# Потолок на ОДИН скилл. Это не измерение, а объявленный формой скилла предел
# поля description; поэтому он вынесен в константу и переопределяется --budget,
# а не выдаётся за замер. Смысл проверки — арифметика, а не угадывание числа.
LISTING_BUDGET_CHARS = 1024
NAME_LIMIT_CHARS = 64

SHELL_TIMEOUT = 20

FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)^---[ \t]*\r?$", re.S | re.M)
# Ключ фронтматтера тянет значение до следующего ключа: description часто
# занимает несколько строк, и обрезка по первой строке молча теряет половину
# описания — ровно ту, где обычно и написано, КОГДА брать скилл.
FM_FIELD = re.compile(r"^([A-Za-z][\w-]*):[ \t]*(.*?)(?=^[A-Za-z][\w-]*:|\Z)",
                      re.S | re.M)

FENCE = re.compile(r"^```[ \t]*(bash|sh|shell)[ \t]*\r?\n(.*?)^```", re.S | re.M)

# Путь = токен с косой чертой и знакомым расширением. Расширение обязательно:
# без него в улов попадают куски прозы и адреса каталогов, а ложная находка
# в шлюзе дороже пропущенной — на неё перестают смотреть.
# Хвост `(?![\w-])` не украшение: без него «facts.json» опознавался как
# «facts.js», и путь уходил в отчёт под чужим именем — то есть шлюз врал
# о том, что именно он проверял.
PATH_TOKEN = re.compile(
    r"[$\w~.{}-]*(?:/[$\w~.{}-]+)+"
    r"\.(?:py|sh|bash|mjs|js|ts|jsonc|json|md|yaml|yml|toml)(?![\w-])")
MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
URL = re.compile(r"\w+://\S+")
# Цель перенаправления — то, что скилл СОЗДАЁТ. Требовать её существования
# заранее — значит объявлять дефектом нормальную запись отчёта.
REDIRECT = re.compile(r"(?:\d?>>?|&>)[ \t]*([^\s;|)]+)")

PLUGIN_ROOT_VAR = re.compile(r"^\$(?:CLAUDE_PLUGIN_ROOT|\{CLAUDE_PLUGIN_ROOT\})/")
ANY_VAR = re.compile(r"\$\w+|\$\{\w+\}")

# Поводы, по которым описание можно считать ответом на вопрос «когда брать».
# Список НЕ является доказательством: см. пункт 2 в заголовке.
WHEN_MARKERS = (
    "когда", "если пользователь", "если человек", "триггер", "просит",
    "пишет /", "use when", "when the user", "use this when", "для случая",
)


# --------------------------------------------------------------------------
# разбор скилла
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Skill:
    """Всё, что нужно проверкам, добывается один раз и не перечитывается."""
    dir: Path
    file: Path
    text: str
    fields: dict
    body: str
    root: Path | None
    budget: int


@dataclass
class Check:
    """Итог одной проверки.

    `state` различает три исхода, а не два: «не смог» не сливается ни с
    «прошло», ни с «замечание» — лечатся они по-разному.
    """
    id: str
    label: str
    state: str                       # pass | fail | unknown
    detail: str = ""
    problems: list = field(default_factory=list)
    unverified: list = field(default_factory=list)
    inferred: bool = False           # вывод эвристики, а не измерение

    def as_dict(self) -> dict:
        d = {"check": self.id, "label": self.label, "state": self.state,
             "detail": self.detail, "problems": self.problems,
             "unverified": self.unverified}
        if self.inferred:
            d["basis"] = "эвристика по прозе, не поведенческая проверка"
        return d


def grade(problems: list, unverified: list) -> str:
    """Исход проверки по её уликам. Один порядок для всех проверок сразу.

    Ключевое здесь второе условие: ЛЮБОЕ непроверенное место гасит проверку до
    «не смог», даже если рядом что-то проверилось успешно. Иначе один
    разобранный путь маскирует три неразобранных, и прогон докладывает
    зелёное о том, чего не видел.
    """
    if problems:
        return "fail"
    if unverified:
        return "unknown"
    return "pass"


def parse_frontmatter(text: str) -> tuple[dict | None, str]:
    m = FRONTMATTER.match(text)
    if not m:
        return None, text
    fields = {}
    for fm in FM_FIELD.finditer(m.group(1)):
        fields[fm.group(1)] = " ".join(fm.group(2).split())
    return fields, text[m.end():]


def guess_root(skill_dir: Path) -> Path | None:
    """Корень, относительно которого скилл называет свои инструменты.

    Ищется не глубже трёх уровней вверх: неограниченный подъём рано или поздно
    упирается в чужой каталог с именем `tools`, и результат проверки начинает
    зависеть от того, где лежит скилл, а не от того, что в нём написано.
    """
    for parent in list(skill_dir.parents)[:3]:
        if (parent / ".claude-plugin").is_dir() or (parent / "tools").is_dir():
            return parent
    return None


def load(skill_dir: Path, budget: int, root: Path | None) -> Skill:
    f = skill_dir / "SKILL.md"
    text = f.read_text(encoding="utf-8-sig", errors="replace") if f.is_file() else ""
    fields, body = parse_frontmatter(text)
    return Skill(dir=skill_dir, file=f, text=text, fields=fields or {},
                 body=body, root=root, budget=budget)


# --------------------------------------------------------------------------
# 1-2. фронтматтер и повод
# --------------------------------------------------------------------------
def check_frontmatter(s: Skill) -> Check:
    problems = []
    if not s.file.is_file():
        return Check("frontmatter", "фронтматтер", "fail",
                     "SKILL.md отсутствует",
                     [f"нет файла {s.file.name}: каталог не является скиллом"])
    if s.fields is None or not s.fields:
        return Check("frontmatter", "фронтматтер", "fail", "нет фронтматтера",
                     ["нет блока --- ... ---: без него скилл не описан и "
                      "не попадёт в листинг"])

    name = s.fields.get("name", "")
    desc = s.fields.get("description", "")
    if not name:
        problems.append("нет поля name")
    elif name != s.dir.name:
        problems.append(
            f"name={name!r} не совпадает с именем каталога {s.dir.name!r}: "
            "вызов по имени уйдёт в пустоту")
    if len(name) > NAME_LIMIT_CHARS:
        problems.append(f"name длиной {len(name)} против предела {NAME_LIMIT_CHARS}")
    if not desc:
        problems.append("description пуст: скилл выбирается по описанию, "
                        "пустое описание не подцепится никогда")

    state = "fail" if problems else "pass"
    return Check("frontmatter", "фронтматтер", state,
                 f"name={name!r}, description {len(desc)} знаков", problems)


def check_when(s: Skill) -> Check:
    """Говорит ли описание, КОГДА брать скилл.

    Проверка признаётся эвристикой прямо в выводе: она ищет повод в прозе и
    поведением не является. Оставлена потому, что описание «что я умею» вместо
    «когда меня брать» — самый частый и самый тихий дефект: скилл существует,
    но не выбирается, и это неотличимо от «скилл не помог».
    """
    desc = (s.fields or {}).get("description", "")
    if not desc:
        return Check("when", "повод", "unknown", "описания нет — судить не о чем",
                     unverified=["description пуст: см. проверку фронтматтера"],
                     inferred=True)
    low = desc.lower()
    hit = [m for m in WHEN_MARKERS if m in low]
    if hit:
        return Check("when", "повод", "pass", f"повод назван: {hit[0]!r}",
                     inferred=True)
    return Check("when", "повод", "fail", "повод не найден",
                 ["description говорит, что скилл делает, но не говорит, когда "
                  "его брать — по такому описанию он не подцепится"],
                 inferred=True)


# --------------------------------------------------------------------------
# 3. стоимость листинга
# --------------------------------------------------------------------------
def listing_cost(s: Skill) -> int:
    """Ровно то, что платится за право быть в листинге: имя плюс описание."""
    return len((s.fields or {}).get("name", "")) + \
        len((s.fields or {}).get("description", ""))


def check_budget(s: Skill) -> Check:
    cost = listing_cost(s)
    detail = f"{cost} из {s.budget} знаков"
    if cost > s.budget:
        return Check("budget", "стоимость", "fail", detail,
                     [f"листинг стоит {cost} знаков при потолке {s.budget}: "
                      f"лишние {cost - s.budget} обрезаются молча, и скилл "
                      "перестаёт подхватываться без единой ошибки"])
    return Check("budget", "стоимость", "pass", detail)


# --------------------------------------------------------------------------
# 4. пути
# --------------------------------------------------------------------------
def _strip_redirect_targets(block: str) -> str:
    return REDIRECT.sub(" ", block)


def mentioned_paths(s: Skill) -> list[str]:
    """Пути, которые тело скилла называет как существующие.

    Из блоков оболочки цели перенаправления вырезаются: `> report.html` — это
    то, что скилл создаёт, а не то, на что он ссылается.
    """
    seen: list[str] = []

    def add(tok: str) -> None:
        tok = tok.strip("\"'`(),;:")
        if not tok or "://" in tok or tok.startswith("#"):
            return
        if tok not in seen:
            seen.append(tok)

    # Адреса вымарываются ПЕРВЫМИ. Отсеивать их потом по «://» в токене
    # бесполезно: совпадение начинается уже после схемы, и от
    # https://example.com/docs/guide.md остаётся «/example.com/docs/guide.md» —
    # абсолютный путь, которого на диске нет. Ссылка на документацию
    # превращалась в непроверенный путь и гасила доверие ни за что.
    text = URL.sub(" ", s.body)
    for m in FENCE.finditer(text):
        text = text.replace(m.group(0), _strip_redirect_targets(m.group(0)))
    for m in PATH_TOKEN.finditer(text):
        add(m.group(0))
    for m in MD_LINK.finditer(text):
        add(m.group(1))
    return seen


def resolve(token: str, s: Skill) -> tuple[Path | None, str | None]:
    """Куда указывает путь. Второй элемент — причина, по которой сверить нечем."""
    raw = PLUGIN_ROOT_VAR.sub("", token)
    anchored_to_root = raw != token
    if ANY_VAR.search(raw):
        return None, "подстановка оболочки: значение известно только в прогоне"
    if raw.startswith("~") or raw.startswith("/"):
        return None, "путь вне каталога скилла: это состояние машины, а не скилла"
    if anchored_to_root:
        if s.root is None:
            return None, "путь от корня плагина, а корень не задан (--root)"
        return s.root / raw, None
    for base in (s.dir, s.root):
        if base is not None and (base / raw).exists():
            return base / raw, None
    return (s.dir / raw), None


def check_paths(s: Skill) -> Check:
    problems, unverified, checked = [], [], 0
    for token in mentioned_paths(s):
        target, why = resolve(token, s)
        if why:
            unverified.append(f"{token} — {why}")
            continue
        checked += 1
        if not target.exists():
            problems.append(f"{token}: файла нет ({target})")
    return Check("paths", "пути", grade(problems, unverified),
                 f"проверено {checked}, не проверено {len(unverified)}",
                 problems, unverified)


# --------------------------------------------------------------------------
# 5. блоки оболочки
# --------------------------------------------------------------------------
def _interpreter(lang: str) -> tuple[str | None, bool]:
    """Чем разбирать блок и пришлось ли понизить интерпретатор.

    Блок, помеченный bash, исполняется bash'ем. Разбирать его через sh можно,
    но тогда законная bash-конструкция читается как опечатка — поэтому падение
    на пониженном интерпретаторе даёт «не смог проверить», а не замечание.
    """
    want = "bash" if lang in ("bash", "shell") else "sh"
    if shutil.which(want):
        return want, False
    fallback = "sh" if want == "bash" else "bash"
    if shutil.which(fallback):
        return fallback, True
    return None, False


def check_shell(s: Skill) -> Check:
    problems, unverified, ok = [], [], 0
    blocks = list(FENCE.finditer(s.body))
    if not blocks:
        return Check("shell", "оболочка", "pass", "команд оболочки нет")

    tmpdir = tempfile.mkdtemp(prefix="superstack-skill-test-")
    try:
        for i, m in enumerate(blocks, 1):
            lang, code = m.group(1), m.group(2)
            first = next((ln for ln in code.splitlines() if ln.strip()), "")
            sh, downgraded = _interpreter(lang)
            if sh is None:
                unverified.append(f"блок {i} ({lang}): интерпретатора нет в PATH")
                continue
            f = Path(tmpdir) / f"block-{i}.sh"
            f.write_text(code, encoding="utf-8")
            try:
                p = subprocess.run([sh, "-n", str(f)], capture_output=True,
                                   text=True, timeout=SHELL_TIMEOUT)
            except (OSError, subprocess.TimeoutExpired) as e:
                unverified.append(f"блок {i}: разбор не состоялся — {e}")
                continue
            if p.returncode == 0:
                ok += 1
            elif downgraded:
                unverified.append(
                    f"блок {i}: разобран не тем интерпретатором ({sh} вместо "
                    f"{lang}) и не сошёлся — судить нельзя")
            else:
                # Имя временного файла вымарывается: иначе замечание содержит
                # путь, который на другой машине другой, и один и тот же дефект
                # выглядит двумя разными — сравнивать прогоны становится нечем.
                msg = (p.stderr or p.stdout or "").replace(str(f), f"блок {i}")
                problems.append(f"блок {i} ({first[:60]!r}): "
                                f"{' '.join(msg.split())[:200]}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return Check("shell", "оболочка", grade(problems, unverified),
                 f"разобрано {ok} из {len(blocks)}", problems, unverified)


# --------------------------------------------------------------------------
# 6. коды возврата
# --------------------------------------------------------------------------
def exit_code_tables(body: str) -> list[tuple[int, set]]:
    """Таблицы кодов возврата: (смещение начала таблицы, множество кодов)."""
    out = []
    lines = body.splitlines(keepends=True)
    offset, i = 0, 0
    while i < len(lines):
        if not lines[i].lstrip().startswith("|"):
            offset += len(lines[i])
            i += 1
            continue
        start, block = offset, []
        while i < len(lines) and lines[i].lstrip().startswith("|"):
            block.append(lines[i])
            offset += len(lines[i])
            i += 1
        codes = _codes_from_table(block)
        if codes:
            out.append((start, codes))
    return out


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _codes_from_table(block: list) -> set:
    if len(block) < 3:
        return set()
    header = [c.lower() for c in _cells(block[0])]
    if not any(h in ("код", "коды", "code", "exit", "код возврата") for h in header):
        return set()
    codes = set()
    for line in block[2:]:
        cell = _cells(line)[0]
        cell = re.sub(r"[*`]", "", cell).strip()
        if re.fullmatch(r"\d+", cell):
            codes.add(int(cell))
    return codes


def _int_const(node) -> int | None:
    """Целое из литерала. bool исключён нарочно: True по типу и есть int, и
    без этой оговорки `return True` из main() подтвердил бы таблицу, обещающую
    код 1, — сверка стала бы совпадением по случайности."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) \
            and not isinstance(node.value, bool):
        return node.value
    return None


def tool_exit_codes(path: Path) -> tuple[set, str | None]:
    """Коды, которые инструмент реально умеет отдать. Второе — почему не всё.

    Разбирается исходник, а не запускается программа: запуск чужого
    инструмента ради опроса кодов — побочный эффект, которого у проверки быть
    не должно. Нерасшифрованный возврат честно превращает вывод в «не смог»,
    иначе проверка объявила бы честную таблицу враньём.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError) as e:
        return set(), f"исходник не разобрать: {e}"

    tables: dict = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        vals = [c for c in (_int_const(v) for v in node.value.values)
                if c is not None]
        if len(vals) == len(node.value.values):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    tables[t.id] = set(vals)

    codes: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            named = (isinstance(f, ast.Attribute) and f.attr == "exit") or \
                    (isinstance(f, ast.Name) and f.id == "SystemExit")
            if named and node.args and _int_const(node.args[0]) is not None:
                codes.add(_int_const(node.args[0]))

    main = next((n for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    if main is None:
        return codes, "в инструменте нет main(): откуда берутся коды — неизвестно"

    unresolved = []
    for node in ast.walk(main):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        v = node.value
        if _int_const(v) is not None:
            codes.add(_int_const(v))
        elif isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) \
                and v.func.attr == "get" and isinstance(v.func.value, ast.Name) \
                and v.func.value.id in tables:
            # `EXIT.get(status, 1)` отдаёт и значения таблицы, и запасное:
            # забыть запасное значит объявить его отсутствующим у инструмента,
            # который его как раз и возвращает чаще всего.
            codes |= tables[v.func.value.id]
            if len(v.args) > 1 and _int_const(v.args[1]) is not None:
                codes.add(_int_const(v.args[1]))
        elif isinstance(v, ast.Subscript) and isinstance(v.value, ast.Name) \
                and v.value.id in tables:
            codes |= tables[v.value.id]
        else:
            unresolved.append(ast.dump(v)[:60])
    if unresolved:
        return codes, f"не всякий возврат из main() разобран ({len(unresolved)})"
    return codes, None


def _tool_before(body: str, pos: int, s: Skill) -> Path | None:
    """К какому инструменту относится таблица: к последнему названному до неё."""
    last = None
    for m in PATH_TOKEN.finditer(body[:pos]):
        if m.group(0).endswith(".py"):
            last = m.group(0)
    if last is None:
        return None
    target, why = resolve(last, s)
    return None if why else target


def check_exit_codes(s: Skill) -> Check:
    tables = exit_code_tables(s.body)
    if not tables:
        return Check("exit_codes", "коды возврата", "pass",
                     "таблицы кодов в теле нет")

    problems, unverified, compared = [], [], 0
    for pos, declared in tables:
        tool = _tool_before(s.body, pos, s)
        if tool is None:
            unverified.append(f"таблица кодов {sorted(declared)}: "
                              "рядом не назван инструмент — сверить не с чем")
            continue
        if not tool.is_file():
            unverified.append(f"таблица кодов {sorted(declared)}: "
                              f"инструмента нет на месте ({tool})")
            continue
        if tool.suffix != ".py":
            unverified.append(f"таблица кодов {sorted(declared)}: "
                              f"{tool.name} не разбирается этой проверкой")
            continue
        real, why = tool_exit_codes(tool)
        missing = sorted(declared - real)
        if missing and why:
            unverified.append(
                f"{tool.name}: коды {missing} в исходнике не найдены, но {why} — "
                "объявить таблицу неверной нельзя")
            continue
        compared += 1
        if missing:
            problems.append(
                f"{tool.name}: таблица обещает коды {missing}, а инструмент их "
                "не отдаёт — человек ждёт сигнала, которого не будет")
    return Check("exit_codes", "коды возврата", grade(problems, unverified),
                 f"сверено таблиц {compared} из {len(tables)}",
                 problems, unverified)


# --------------------------------------------------------------------------
# сборка вердикта
# --------------------------------------------------------------------------
CHECKS = (check_frontmatter, check_when, check_budget,
          check_paths, check_shell, check_exit_codes)


def review(s: Skill) -> dict:
    checks = [fn(s) for fn in CHECKS]
    failed = [c for c in checks if c.state == "fail"]
    unknown = [c for c in checks if c.state == "unknown"]
    # Замечание перекрывает «не смог»: названный дефект действеннее, чем
    # непроверенное место, а непроверенное всё равно поимённо остаётся в JSON
    # и в человеческом выводе — оно не растворяется.
    if failed:
        status = "findings"
        nxt = f"починить: {failed[0].problems[0]}"
    elif unknown:
        status = "unknown"
        first = (unknown[0].unverified or ["причина не названа"])[0]
        nxt = f"не проверено: {unknown[0].label} — {first}"
    else:
        status = "clean"
        nxt = "скилл держит то, что обещает описанием"

    return {
        "gate": "skill-test",
        "skill": s.dir.name,
        "path": str(s.dir),
        "status": status,
        "budget_chars": s.budget,
        "listing_chars": listing_cost(s),
        "checks": [c.as_dict() for c in checks],
        "problems": [p for c in checks for p in c.problems],
        "unverified": [u for c in checks for u in c.unverified],
        "next": nxt,
    }


HEAD = {"clean": "СКИЛЛ ДЕРЖИТ ОБЕЩАННОЕ",
        "findings": "ЕСТЬ ЗАМЕЧАНИЯ",
        "unknown": "ПРОВЕРИТЬ УДАЛОСЬ НЕ ВСЁ"}
EXIT = {"clean": 0, "findings": 1, "unknown": 2}
MARK = {"pass": "+", "fail": "x", "unknown": "?"}


def human(v: dict) -> str:
    lines = [f"{HEAD.get(v['status'], v['status'])}: {v['skill']}"]
    for c in v["checks"]:
        tail = "  (вывод, не измерение)" if c.get("basis") else ""
        lines.append(f"  {MARK.get(c['state'], '?')} {c['label']:<14} "
                     f"{c['detail']}{tail}")
        for p in c["problems"][:8]:
            lines.append(f"      ! {p}")
        for u in c["unverified"][:8]:
            lines.append(f"      ? {u}")
    lines.append(f"  дальше: {v['next']}")
    return "\n".join(lines)


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    flag = Path.home() / ".claude" / "superstack" / "PAUSE"
    if flag.exists():
        print(f"ОСТАНОВЛЕНО: система на паузе\n  флаг: {flag}\n"
              f"  снять: tools/pause.sh off", file=sys.stderr)
        raise SystemExit(10)


USAGE = "вызов: skill_test.py <каталог-скилла> [--json] [--budget N] [--root PATH]"


def parse_args(argv: list) -> tuple:
    """Разбор аргументов отдельной функцией: ошибку вызова нельзя спутать с
    находкой в скилле — у них разные коды возврата и разные адресаты."""
    quiet = "--json" in argv
    rest = [a for a in argv if a != "--json"]
    budget, root = LISTING_BUDGET_CHARS, None
    for flag, cast in (("--budget", int), ("--root", str)):
        if flag not in rest:
            continue
        i = rest.index(flag)
        if i + 1 >= len(rest):
            raise ValueError(f"у {flag} нет значения")
        try:
            value = cast(rest[i + 1])
        except ValueError:
            raise ValueError(f"значение {flag} не разбирается: {rest[i + 1]!r}")
        if flag == "--budget":
            if value <= 0:
                raise ValueError("--budget должен быть больше нуля")
            budget = value
        else:
            root = Path(value).expanduser()
        del rest[i:i + 2]
    positional = [a for a in rest if not a.startswith("--")]
    unknown = [a for a in rest if a.startswith("--")]
    if unknown:
        raise ValueError(f"неизвестный ключ: {unknown[0]}")
    if len(positional) != 1:
        raise ValueError("нужен ровно один каталог скилла")
    return Path(positional[0]).expanduser(), quiet, budget, root


def main() -> int:
    halt_if_paused()
    try:
        skill_dir, quiet, budget, root = parse_args(sys.argv[1:])
    except ValueError as e:
        print(f"НЕ УДАЛОСЬ: {e}\n{USAGE}", file=sys.stderr)
        return 3
    if not skill_dir.is_dir():
        print(f"НЕ УДАЛОСЬ: каталога нет — {skill_dir}", file=sys.stderr)
        return 3
    skill_dir = skill_dir.resolve()
    if root is None:
        root = guess_root(skill_dir)
    elif not root.is_dir():
        print(f"НЕ УДАЛОСЬ: корня нет — {root}", file=sys.stderr)
        return 3

    v = review(load(skill_dir, budget, root.resolve() if root else None))
    if not quiet:
        print(human(v), file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return EXIT.get(v["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
