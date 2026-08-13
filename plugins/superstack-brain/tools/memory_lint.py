#!/usr/bin/env python3
"""SUPERSTACK — линт памяти. Индекс, который не дочитали, молчит об этом.

Зачем это отдельный инструмент, а не глаза человека.

Память гниёт тише кода. Код ломается заметно: тест краснеет, сборка падает.
Память отказывает БЕЗ единого сигнала — и отказывает ровно тем способом,
который невозможно заметить изнутри сессии:

  1. ИНДЕКС МОЛЧА ОБРЕЗАЕТСЯ. MEMORY.md грузится с потолком (~25 КБ / ~200
     строк). Всё, что за потолком, для агента просто не существует. Никакой
     ошибки при этом не будет: агент не знает, что у списка был хвост.
  2. ССЫЛКА В НИКУДА не отличается от ссылки в файл, пока по ней не пошли.
     [[тема]] на удалённый файл читается как «знание есть».
  3. ЗАПИСЬ БЕЗ ИСТОЧНИКА — слух. Через месяц её нельзя ни подтвердить, ни
     опровергнуть, но она продолжает влиять на решения наравне с измеренным.
  4. ДУБЛЬ ПО СМЫСЛУ расходится с оригиналом. Две записи об одном, одна
     обновлена, вторая нет — и агент возьмёт ту, что попалась.
  5. ЗАПИСЬ, НА КОТОРУЮ НИКТО НЕ ССЫЛАЕТСЯ и которая сама ни на что не
     ссылается, не участвует в работе. Она не вредна, она просто мёртвая —
     и её присутствие завышает субъективную оценку «памяти много».

Три решения, ради которых инструмент выглядит именно так:

  · КАТАЛОГ — ПАРАМЕТР, а не зашитый ~/.claude. Инструмент, который знает
    только один каталог, невозможно проверить: его вердикт описывает машину
    проверяющего, а не код. Здесь каталог обязателен в вызове.
  · ЧАСЫ — ПАРАМЕТР (--today). Устаревание считается от поданной даты, а не
    от системных часов, иначе один и тот же каталог даёт разный вердикт
    завтра и на соседней машине.
  · НИЧЕГО НЕ ПРАВИТСЯ И НЕ УДАЛЯЕТСЯ. Линт говорит, решает человек. Память —
    единственное, что нельзя пересобрать из исходников: автоправка здесь
    дороже любой найденной проблемы.

Отдельно: «не смог прочитать» НЕ засчитывается за «чисто». Файл, который не
разобрался, попадает в unchecked, и вердикт перестаёт быть полным — иначе
нечитаемая половина памяти выглядела бы как порядок.

Отдельный узел — раскладка raw/ и wiki/. Document и Memory — разные вещи, и
натив (просто каталог .md-файлов) их не различает:

  · raw/  — неизменяемые ИСТОЧНИКИ: захваченный текст (лог, транскрипт,
    цитата), а не вывод агента. Источник не переписывают, его подтверждают
    заново или заводят новый файл — поэтому шапка raw-документа несёт поле
    checksum (sha256 тела на момент захвата), а линт при каждом проходе
    пересчитывает сумму и ловит расхождение. Без этого «источник» ничем не
    отличается от заметки: его можно молча подправить, и никто не узнает,
    что цитата уже не то, что было процитировано.
  · wiki/ — то, что ПОРОЖДАЕТ И ЛИНТУЕТ САМ АГЕНТ: разбор, вывод, свод. Поле
    source в шапке — вольный текст автора («сессия 04.01») и до файла им не
    дойти. Ссылка [[raw/…]] — дойти можно, поэтому запись в wiki/ без такой
    ссылки на raw/ считается записью без источника: её нечем перепроверить,
    хотя формально фронтматтер заполнен.

  python3 memory_lint.py КАТАЛОГ [--json] [--today ГГГГ-ММ-ДД]
                         [--index MEMORY.md] [--limit-bytes N]
                         [--limit-lines N] [--stale-days N]

  код 0 — чисто, 1 — есть замечания, 2 — не смог проверить, 3 — ошибка вызова
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

#: Имя индекса. Именно он грузится каждую сессию и именно он обрезается.
INDEX_NAME = "MEMORY.md"
#: Верхнеуровневые каталоги раскладки Document/Memory. Имя без слэша:
#: сравнение идёт с первым сегментом относительного пути, а не префиксом
#: строки, — иначе "raw-набросок.md" в корне ложно засчитался бы за raw/.
RAW_DIR = "raw"
WIKI_DIR = "wiki"
#: Потолок загрузки индекса. Значение приблизительное и потому вынесено в
#: параметр: врать точной цифрой хуже, чем дать подвинуть порог.
LIMIT_BYTES = 25_600
LIMIT_LINES = 200
#: После скольких дней запись считается протухшей. Не удаляется — называется.
STALE_DAYS = 180

#: Схема фронтматтера. «source» обязателен не из любви к порядку: факт без
#: источника нечем перепроверить, а значит он влияет на решения как измерение,
#: не будучи измерением.
REQUIRED_KEYS = ("title", "source", "updated")
#: «checksum» — не общее поле схемы, оно осмысленно только в raw/, но должно
#: быть известным ключом, иначе каждый источник получал бы лишнее замечание
#: frontmatter-unknown-key поверх честной проверки чек-суммы ниже.
KNOWN_KEYS = {"title", "source", "updated", "tags", "status", "confidence", "checksum"}

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

WIKILINK = re.compile(r"\[\[([^\[\]]+)\]\]")
#: Блоки кода вырезаются ДО поиска ссылок: [[пример]] внутри ``` — это показ
#: синтаксиса, а не ссылка. Иначе документация о формате памяти сама себя
#: обвиняет в битых ссылках.
FENCE = re.compile(r"```.*?```", re.S)
WORD = re.compile(r"[0-9a-zа-я]+")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: Служебные слова выбрасываются из заголовка перед сравнением: «Правила о
#: деньгах» и «Правила денег» — одна тема, а не две.
STOPWORDS = {"и", "в", "на", "о", "об", "по", "для", "с", "к", "от", "при",
             "the", "a", "an", "of", "for", "to", "in", "on"}


# --------------------------------------------------------------------------
# чтение
# --------------------------------------------------------------------------
@dataclass
class Front:
    """Разобранный фронтматтер. Ошибка разбора — это состояние, а не исключение."""
    present: bool
    fields: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class Doc:
    path: Path
    rel: str
    size: int          # в БАЙТАХ: потолок загрузки меряется байтами, не буквами
    lines: int
    front: Front
    heading: str       # первый «# …» в теле: заголовок чаще там, чем в шапке
    links: list        # ссылки [[…]] в порядке появления, с повторами
    text: str          # полный декодированный текст: нужен, чтобы посчитать
                        # чек-сумму тела raw-документа без повторного чтения файла


def parse_front(text: str) -> Front:
    """Фронтматтер разбирается нарочно примитивно: «ключ: значение».

    PyYAML в этом проекте нет и не будет — зависимость ради разбора шапки из
    трёх полей стоит дороже, чем сам разбор. Строка, которая в эту форму не
    укладывается, не «поддерживается частично», а называется вслух: сложный
    YAML в шапке памяти сам по себе замечание, потому что читать его будут
    разные инструменты и разберут по-разному.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return Front(present=False)
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            body = lines[1:i]
            break
    else:
        # Незакрытый блок опаснее отсутствующего: половина заметки утекает в
        # шапку и не показывается там, где её ждут прочитать.
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


def links_in(text: str) -> list:
    """Ссылки [[…]] вне блоков кода."""
    return WIKILINK.findall(FENCE.sub(" ", text))


def heading_in(text: str) -> str:
    """Первый заголовок первого уровня. Ищется в теле, а не в шапке: люди
    пишут название темы строкой «# …», а поле title заполняют не всегда."""
    for line in text.split("\n"):
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def load(root: Path) -> tuple:
    """Прочитать каталог памяти. Возвращает (документы, непрочитанное).

    Непрочитанное не выбрасывается и не глотается: файл, до которого линт не
    добрался, обязан гасить доверие к вердикту, а не исчезать из него.
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
        docs.append(Doc(path=path, rel=rel, size=len(raw),
                        lines=len(text.splitlines()),
                        front=parse_front(text), heading=heading_in(text),
                        links=links_in(text), text=text))
    return docs, unchecked


# --------------------------------------------------------------------------
# разбор смысла
# --------------------------------------------------------------------------
def title_of(doc: Doc) -> str:
    """Чем эта запись называется. Порядок источников — от явного к неявному."""
    return doc.front.fields.get("title") or doc.heading or Path(doc.rel).stem


def title_key(title: str) -> str:
    """Ключ сравнения заголовков «по смыслу», а не по буквам.

    Регистр, знаки, порядок слов и служебные слова смыслом не являются:
    «Тон общения», «тон общения!» и «Общения тон» — одна запись в трёх видах.
    """
    flat = title.lower().replace("ё", "е")
    words = [w for w in WORD.findall(flat) if w not in STOPWORDS]
    return " ".join(sorted(set(words)))


def top_dir(rel: str) -> str:
    """Верхнеуровневый каталог относительного пути, или "" в корне."""
    parts = rel.split("/", 1)
    return parts[0] if len(parts) > 1 else ""


def body_after_front(text: str) -> str:
    """Тело документа без шапки фронтматтера.

    Вызывать только когда фронтматтер разобран без ошибки (front.present и
    не front.error) — иначе закрывающего «---» может не быть, и деление на
    шапку/тело неоднозначно. Чек-сумма считается именно от ЭТОЙ строки, а не
    от всего файла: сама чек-сумма живёт в шапке и не может быть частью
    того, что она подписывает, иначе любое изменение шапки (не только тела)
    двигало бы сумму, и «источник переписали» стало бы неотличимо от
    «поправили дату в шапке».
    """
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return "\n".join(lines[i + 1:])
    return text  # защитный путь: сюда не попасть при present=True без error


def content_checksum(body: str) -> str:
    """sha256 тела документа, полная шестнадцатеричная запись."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def link_key(raw: str) -> str:
    """Нормализовать цель ссылки: алиас и якорь целью не являются."""
    target = raw.split("|", 1)[0].split("#", 1)[0]
    return target.strip().strip("/").lower()


def target_map(docs: list) -> dict:
    """Под какими именами документ достижим по [[…]].

    Регистр в имени ссылки пишут руками, и смысла он не несёт; путь может быть
    написан как с .md, так и без него.
    """
    names: dict = {}
    for d in docs:
        stem = d.rel[:-3] if d.rel.endswith(".md") else d.rel
        base = Path(d.rel).name
        for key in (d.rel, stem, base, Path(base).stem):
            names.setdefault(key.lower(), d.rel)
    return names


# --------------------------------------------------------------------------
# проверки
# --------------------------------------------------------------------------
def _finding(check: str, severity: str, file: str, detail: str, fix: str) -> dict:
    return {"check": check, "severity": severity, "file": file,
            "detail": detail, "fix": fix}


def scan(root: Path, today: date, index_name: str = INDEX_NAME,
         limit_bytes: int = LIMIT_BYTES, limit_lines: int = LIMIT_LINES,
         stale_days: int = STALE_DAYS) -> dict:
    """Осмотреть каталог памяти. Ничего не пишет и не удаляет.

    `today` — параметр, а не системные часы: вердикт обязан повторяться
    завтра и на чужой машине.
    """
    docs, unchecked = load(root)

    if not docs:
        # Пусто — это НЕ «чисто». Иначе удалить память выгоднее, чем вести её:
        # тот же стимул, из-за которого в проектах пропадают тесты.
        return {
            "lint": "memory", "dir": str(root), "status": "unknown",
            "complete": not unchecked, "files": 0, "index": index_name,
            "findings": [], "unchecked": unchecked,
            "next": "в каталоге нет ни одного .md — линтовать нечего; "
                    "проверять надо тот каталог, где память действительно лежит",
        }

    by_rel = {d.rel: d for d in docs}
    index = by_rel.get(index_name)
    names = target_map(docs)
    findings: list = []

    # 1. Индекс: он грузится каждую сессию и он же молча обрезается.
    if index is None:
        findings.append(_finding(
            "index-missing", "high", index_name,
            f"индекса {index_name} нет — заметки не собраны ни в один список",
            f"завести {index_name} и перечислить в нём темы ссылками [[…]]"))
    else:
        if index.size > limit_bytes:
            findings.append(_finding(
                "index-over-limit-bytes", "high", index.rel,
                f"{index.size} байт при потолке загрузки {limit_bytes}",
                "вынести часть строк в тематические файлы: хвост индекса "
                "не загружается и об этом никто не сообщит"))
        if index.lines > limit_lines:
            findings.append(_finding(
                "index-over-limit-lines", "high", index.rel,
                f"{index.lines} строк при потолке {limit_lines}",
                "сократить список: строки за потолком для агента не существуют"))

    # 2. Ссылки в никуда.
    for d in docs:
        for raw in d.links:
            key = link_key(raw)
            if not key:
                findings.append(_finding(
                    "broken-link", "high", d.rel,
                    "пустая ссылка [[]]", "убрать или дописать имя темы"))
            elif key not in names:
                findings.append(_finding(
                    "broken-link", "high", d.rel,
                    f"[[{raw}]] — такого файла в каталоге нет",
                    "поправить имя или завести тему: ссылка в никуда читается "
                    "как «знание есть»"))

    # 3. Провенанс и схема шапки.
    for d in docs:
        if d.rel == index_name:
            continue                      # индекс — оглавление, а не факт
        f = d.front
        if not f.present:
            findings.append(_finding(
                "frontmatter-missing", "high", d.rel,
                "нет фронтматтера — ни источника, ни даты",
                "добавить шапку --- title/source/updated ---"))
            continue
        if f.error:
            findings.append(_finding(
                "frontmatter-unparsed", "medium", d.rel, f.error,
                "привести шапку к простому виду «ключ: значение»"))
            continue
        for key in REQUIRED_KEYS:
            if not f.fields.get(key):
                sev = "high" if key == "source" else "medium"
                why = ("факт без источника — слух: перепроверить его будет нечем"
                       if key == "source" else "поле обязательно по схеме")
                findings.append(_finding(
                    "no-provenance" if key == "source" else "frontmatter-missing-key",
                    sev, d.rel, f"нет поля «{key}»", why))
        for key in sorted(set(f.fields) - KNOWN_KEYS):
            findings.append(_finding(
                "frontmatter-unknown-key", "low", d.rel,
                f"поле «{key}» вне схемы",
                "убрать или внести в схему: молчаливое поле никто не читает"))

        updated = f.fields.get("updated", "")
        if updated and not ISO_DATE.match(updated):
            findings.append(_finding(
                "frontmatter-bad-date", "medium", d.rel,
                f"«updated: {updated}» не в форме ГГГГ-ММ-ДД",
                "дату нельзя сравнить, пока её формат не задан"))
        elif updated:
            try:
                when = date(int(updated[:4]), int(updated[5:7]), int(updated[8:10]))
            except ValueError:
                findings.append(_finding(
                    "frontmatter-bad-date", "medium", d.rel,
                    f"«updated: {updated}» не существует как дата",
                    "исправить дату"))
            else:
                if when < today - timedelta(days=stale_days):
                    findings.append(_finding(
                        "stale", "medium", d.rel,
                        f"обновлялась {updated}, порог {stale_days} дней",
                        "перепроверить и обновить дату либо убрать в архив"))

    # 4. Дубли по смыслу заголовка.
    groups: dict = {}
    for d in docs:
        if d.rel == index_name:
            continue
        key = title_key(title_of(d))
        if key:
            groups.setdefault(key, []).append(d.rel)
    for key, members in sorted(groups.items()):
        if len(members) > 1:
            findings.append(_finding(
                "duplicate-title", "medium", members[0],
                "одна тема в нескольких файлах: " + ", ".join(sorted(members)),
                "свести в один файл: расходящиеся дубли дают разный ответ "
                "в зависимости от того, какой попался первым"))

    # 5. Мёртвые записи: на них никто не ссылается и они сами ни на кого.
    if index is None:
        unchecked.append({
            "file": "<весь каталог>",
            "why": f"нет {index_name} — «на кого никто не ссылается» "
                   f"считать не от чего"})
    else:
        inbound: dict = {d.rel: 0 for d in docs}
        for d in docs:
            for raw in d.links:
                target = names.get(link_key(raw))
                if target and target != d.rel:
                    inbound[target] += 1
        for d in docs:
            if d.rel == index_name:
                continue
            outbound = sum(1 for raw in d.links if names.get(link_key(raw)))
            if inbound[d.rel] == 0 and outbound == 0:
                findings.append(_finding(
                    "orphan", "low", d.rel,
                    "на файл никто не ссылается и он сам ни на что не ссылается",
                    "связать с индексом или убрать в архив: в работе он "
                    "не участвует, но создаёт ощущение полноты памяти"))

    # 6. raw/ — неизменяемость источника. Правку после захвата линт обязан
    # заметить: это единственное, что отличает источник от обычной заметки.
    for d in docs:
        if top_dir(d.rel) != RAW_DIR:
            continue
        if not d.front.present or d.front.error:
            continue  # уже названо выше как frontmatter-missing/-unparsed
        checksum = d.front.fields.get("checksum")
        if not checksum:
            findings.append(_finding(
                "raw-missing-checksum", "medium", d.rel,
                "источник в raw/ без поля «checksum» — неизменяемость нечем подтвердить",
                "посчитать sha256 тела файла (без шапки) и вписать в поле checksum"))
            continue
        computed = content_checksum(body_after_front(d.text))
        if computed != checksum:
            findings.append(_finding(
                "raw-modified", "critical", d.rel,
                f"чек-сумма не совпадает: в шапке {checksum[:12]}…, "
                f"по факту тела {computed[:12]}… — источник правили после захвата",
                "источники в raw/ не переписывают: вернуть исходный текст "
                "либо завести новый файл с новой чек-суммой"))

    # 7. wiki/ — заметка обязана указывать, ИЗ какого источника она сделана.
    # Поле source — вольный текст, до файла им не дойти; ссылка [[raw/…]] —
    # дойти можно, поэтому она и есть провенанс для записи в wiki/.
    for d in docs:
        if top_dir(d.rel) != WIKI_DIR or d.rel == index_name:
            continue
        linked_to_raw = any(
            top_dir(names.get(link_key(raw), "")) == RAW_DIR
            for raw in d.links
        )
        if not linked_to_raw:
            findings.append(_finding(
                "wiki-missing-source-link", "high", d.rel,
                "в wiki/ нет ни одной ссылки на raw/ — записи нечем подтвердить, "
                "хотя поле source в шапке может быть заполнено",
                "добавить [[raw/имя-источника]] на документ, из которого сделана заметка"))

    findings.sort(key=lambda f: (SEVERITY_RANK.get(f["severity"], 9),
                                 f["check"], f["file"]))
    complete = not unchecked
    if findings:
        status = "findings"
    elif complete:
        status = "clean"
    else:
        # Замечаний нет, но осмотрено не всё. Это не «чисто»: «не нашёл» и
        # «не смог проверить» — разные утверждения.
        status = "unknown"

    return {"lint": "memory", "dir": str(root), "status": status,
            "complete": complete, "files": len(docs), "index": index_name,
            "findings": findings, "unchecked": unchecked,
            "next": _next_step(status, findings, unchecked)}


def _next_step(status: str, findings: list, unchecked: list) -> str:
    if status == "clean":
        return "память цела: индекс влезает, ссылки ведут в файлы, у записей есть источник"
    if findings:
        top = findings[0]
        tail = ""
        if unchecked:
            tail = f"; и осмотрено не всё — {len(unchecked)} файл(ов) не прочитано"
        return f"начать с [{top['severity']}] {top['check']} в {top['file']}{tail}"
    return f"замечаний нет, но {len(unchecked)} файл(ов) не прочитано — вердикт неполный"


# --------------------------------------------------------------------------
# вывод
# --------------------------------------------------------------------------
HEAD = {"clean": "ПАМЯТЬ ЦЕЛА", "findings": "ЕСТЬ ЗАМЕЧАНИЯ",
        "unknown": "НЕ СМОГ ПРОВЕРИТЬ"}
EXIT = {"findings": 1, "unknown": 2, "clean": 0}


def human(report: dict) -> str:
    lines = [f"{HEAD.get(report['status'], report['status'])}: "
             f"замечаний {len(report['findings'])}, "
             f"файлов {report['files']} ({report['dir']})"]
    for f in report["findings"]:
        lines.append(f"  [{f['severity']}] {f['check']}  {f['file']}")
        lines.append(f"      {f['detail']}")
        lines.append(f"      -> {f['fix']}")
    for u in report["unchecked"]:
        lines.append(f"  ? НЕ ПРОВЕРЕНО: {u['file']} — {u['why']}")
    lines.append(f"  дальше: {report['next']}")
    return "\n".join(lines)


def halt_if_paused() -> None:
    """Тормоз соблюдается, а не только попадает в отчёт."""
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    flag = Path.home() / ".claude" / "superstack" / "PAUSE"
    if flag.exists():
        print(f"ОСТАНОВЛЕНО: система на паузе\n  флаг: {flag}\n"
              f"  снять: tools/pause.sh off", file=sys.stderr)
        raise SystemExit(10)


USAGE = ("вызов: memory_lint.py КАТАЛОГ-ПАМЯТИ [--json] [--today ГГГГ-ММ-ДД]\n"
         "       [--index ИМЯ] [--limit-bytes N] [--limit-lines N] [--stale-days N]")


#: Опции со значением. Разбор идёт ОДНИМ проходом по argv: попытка вычислить
#: позиционный аргумент вычитанием «уже занятых значений» ломается ровно там,
#: где каталог назван так же, как значение опции.
VALUE_OPTS = {"--index", "--today", "--limit-bytes", "--limit-lines",
              "--stale-days"}
NUMERIC_OPTS = {"--limit-bytes": "limit_bytes", "--limit-lines": "limit_lines",
                "--stale-days": "stale_days"}


def parse_argv(argv: list) -> tuple:
    """Разобрать вызов. Возвращает (опции, позиционные, ошибки).

    Ошибка вызова НЕ подменяется значением по умолчанию: «--limit-bytes»
    без числа означает, что человек хотел другой порог, а получил бы тихо
    прежний — и прочитал бы чужой вердикт как свой.
    """
    opts = {"json": False, "index": INDEX_NAME, "today": None,
            "limit_bytes": LIMIT_BYTES, "limit_lines": LIMIT_LINES,
            "stale_days": STALE_DAYS}
    positional, errors = [], []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--json":
            opts["json"] = True
        elif arg in VALUE_OPTS:
            if i + 1 >= len(argv):
                errors.append(f"у {arg} нет значения")
                break
            value = argv[i + 1]
            i += 1
            if arg == "--index":
                opts["index"] = value
            elif arg == "--today":
                opts["today"] = value
            else:
                try:
                    opts[NUMERIC_OPTS[arg]] = int(value)
                except ValueError:
                    errors.append(f"{arg}: «{value}» не число")
        elif arg.startswith("--"):
            errors.append(f"неизвестная опция {arg}")
        else:
            positional.append(arg)
        i += 1
    return opts, positional, errors


def main() -> int:
    halt_if_paused()
    opts, positional, errors = parse_argv(sys.argv[1:])

    if len(positional) != 1:
        print("НЕ УДАЛОСЬ: каталог памяти обязателен и ровно один.\n"
              "Каталог не подставляется молча: инструмент, который сам решает, "
              "какую память смотреть, невозможно ни проверить, ни повторить.\n"
              + USAGE, file=sys.stderr)
        return 3

    # Часы читаются ОДИН раз и только как значение по умолчанию: дальше вниз
    # уходит уже дата-параметр, поэтому тот же каталог с той же датой даёт тот
    # же вердикт на любой машине.
    today_s = opts["today"] or date.today().isoformat()
    today = None
    if ISO_DATE.match(today_s):
        try:
            today = date(int(today_s[:4]), int(today_s[5:7]), int(today_s[8:10]))
        except ValueError:
            today = None
    if today is None:
        errors.append(f"--today: «{today_s}» не дата в форме ГГГГ-ММ-ДД")

    if errors:
        print("НЕ УДАЛОСЬ: " + "; ".join(errors) + "\n" + USAGE, file=sys.stderr)
        return 3

    root = Path(positional[0]).expanduser()
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: каталога памяти нет — {root}", file=sys.stderr)
        return 3

    report = scan(root.resolve(), today=today, index_name=opts["index"],
                  limit_bytes=opts["limit_bytes"], limit_lines=opts["limit_lines"],
                  stale_days=opts["stale_days"])
    if not opts["json"]:
        print(human(report), file=sys.stderr)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return EXIT.get(report["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
