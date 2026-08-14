#!/usr/bin/env python3
"""SUPERSTACK — линт спеки. Форму прозы держит скрипт, а не просьба.

Зачем это отдельный инструмент, а не абзац в скилле.

Формат спеки объявлен в `skills/go/SKILL.md`, фаза 2: три обязательных раздела —
«Что должно получиться», «Как проверить», «Чего НЕ делаем». Объявление в
инструкции ничего не удерживает: спека гниёт молча. Раздел исчезает — и никто
не замечает, потому что текст по-прежнему выглядит как спека. Особенно тихо
разваливается критерий приёмки: «Как проверить» превращается в «работает» или
«должно работать», и это ЧИТАЕТСЯ как ответ, хотя проверить по нему нечего.
Разваливается всё это не сейчас, а на приёмке, когда спорить уже поздно.

Поэтому здесь три вещи проверяются кодом:

  1. РАЗДЕЛЫ НА МЕСТЕ. Все три, по заголовкам, а не по упоминанию в прозе.
  2. РАЗДЕЛ НЕ ПУСТ. Заголовок без тела — это исчезнувший раздел, который
     выглядит как присутствующий.
  3. «КАК ПРОВЕРИТЬ» СОДЕРЖИТ ПРОВЕРЯЕМОЕ УТВЕРЖДЕНИЕ — конкретную команду
     либо конкретное действие с ожидаемым результатом. «Работает», «готово»,
     «выглядит правильно» проверяемым утверждением не являются: по ним нельзя
     получить код возврата и нельзя разойтись во мнениях без спора.

Чего инструмент НЕ делает и не должен: не правит файл (спека — это решение
человека, а не поле для автозамены), не оценивает содержание по смыслу и не
меряет длину. «Не нашёл раздел» и «не смог прочитать файл» здесь — РАЗНЫЕ
исходы с разными кодами возврата: нечитаемая спека не имеет права выглядеть
как чистая.

  python3 spec_lint.py .claude/specs/login.md          вердикт человеку + JSON
  python3 spec_lint.py --json .claude/specs/login.md   только JSON

  код 0 — чисто, 1 — замечания, 2 — не смог проверить, 3 — ошибка вызова,
  10 — система на паузе
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# --- поиск соседних пакетов -------------------------------------------------
# После разделения на семь плагинов соседние инструменты лежат в других
# каталогах. Пути берутся из МАНИФЕСТА этого плагина (поле dependencies), а не
# перечисляются здесь вторым списком: два списка расходятся молча, и расхождение
# всплывает импортом, упавшим у пользователя, а не у нас.
def _wire_siblings(here: Path) -> None:
    """Положить в sys.path каталоги tools соседних пакетов.

    Раньше соседи брались из поля `dependencies` собственного манифеста. Поле
    пришлось убрать: движок Claude Code 2.1.42 отвергает его как неизвестный
    ключ, и с ним НИ ОДИН пакет не проходил валидацию, то есть продукт не
    ставился вовсе. Как только поле исчезло, вместе с ним исчезла и проводка —
    инструменты перестали находить друг друга.

    Теперь соседи ищутся по раскладке, а не по манифесту. Два уровня, потому
    что установленный плагин лежит не так, как репозиторий: между корнем
    маркетплейса и корнем пакета появляется каталог версии
    (`cache/<маркетплейс>/<плагин>/<версия>/tools`).
    """
    plug_root = here.parent
    for base in (plug_root.parent, plug_root.parent.parent):
        if not base.is_dir():
            continue
        for pat in ("*/tools", "*/*/tools"):
            for d in sorted(base.glob(pat)):
                if d.is_dir() and d != here and str(d) not in sys.path:
                    sys.path.append(str(d))
_wire_siblings(Path(__file__).resolve().parent)


from adjudicate import halt_if_paused  # noqa: E402

#: Обязательные разделы фазы 2. Порядок — как в SKILL.md, чтобы замечания
#: читались в том же порядке, в каком спека пишется.
#: Третье поле — ЧТО ИМЕННО отказывает без этого раздела: замечание без
#: причины человек закрывает формально, дописав заголовок и ничего под ним.
SECTIONS = (
    ("outcome", "Что должно получиться",
     "без него приёмка спорит о том, что вообще заказывали"),
    ("accept", "Как проверить",
     "без него «готово» объявляет тот, кого проверяют"),
    ("scope", "Чего НЕ делаем",
     "без границы объём растёт молча и ход не заканчивается"),
)

# Заголовок ищется как ЗАГОЛОВОК, а не как строка со словами. Иначе фраза
# «как проверить — решим потом» посреди абзаца засчиталась бы за раздел,
# и линт стал бы поиском подстроки в прозе, то есть ничем.
H_HASH = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
H_NUM_BOLD = re.compile(r"^\s*\d+[.)]\s*\*\*(.+?)\*\*\s*(.*)$")
H_BOLD = re.compile(r"^\s*\*\*(.+?)\*\*\s*:?\s*$")

#: Пустые формулировки критерия приёмки. Каждая встречалась в живой спеке и
#: каждая читается как ответ, не будучи им. Список нужен не для запрета слов,
#: а чтобы назвать человеку конкретного виновника вместо общего «нет проверки».
VACUOUS = (
    "работает", "должно работать", "должен работать", "будет работать",
    "выглядит правильно", "выглядит хорошо", "все хорошо", "все ок",
    "готово", "проверено", "тесты проходят", "должно быть хорошо",
    "все правильно", "нормально",
)

#: Начала строк, по которым видно команду, а не путь и не имя файла.
#: Имя запускалки обязано кончаться границей слова: без этого прозаическое
#: «go to the page» и «шаг` sh`одится» засчитывались бы за команду, и линт
#: пропускал бы ровно те спеки, ради которых написан.
RUNNER = re.compile(
    r"^(?:\$\s|\./|\.\\)"
    r"|^(?:python3?|pytest|npm|pnpm|yarn|bun|node|deno|go|cargo|make|bash|sh|"
    r"zsh|curl|git|docker|kubectl|ruff|mypy|tsc|eslint|gradle|mvn)(?=\s|$)",
    re.I)

#: Действие, которое человек может физически совершить.
ACTIONS = ("открыт", "откро", "запуст", "нажат", "нажм", "ввест", "введ",
           "перейт", "перейд", "отправ", "кликн", "выполн", "зайт", "загруз",
           "прогна", "прогон", "вызват", "собрат", "постав", "набрат")

#: Ожидаемый результат этого действия. Действие без результата — это шаг,
#: а не проверка: по нему нельзя сказать «сошлось» или «не сошлось».
RESULTS = ("увид", "появ", "показ", "вернёт", "вернет", "верну", "отобра",
           "ошибк", "остан", "остат", "ожида", "код возврата", "статус",
           "результат", "должен быть", "должна быть", "должно быть",
           "не должно", "равно", "содержит", "письмо прид", "прид")


def normalize(text: str) -> str:
    """Текст, приведённый к виду, в котором его можно сравнивать.

    Разметка, регистр и «ё» не являются содержанием: заголовок «## **Как
    проверить:**» и «Как проверить» — один и тот же раздел, и линт не должен
    зависеть от того, как человек его оформил в этот раз.
    """
    low = text.replace("ё", "е").replace("Ё", "Е").lower()
    low = re.sub(r"[*_`#~]+", " ", low)
    low = re.sub(r"[^0-9a-zA-Zа-яА-Я/\-.:,]+", " ", low)
    return re.sub(r"\s+", " ", low).strip(" .:,-")


def strip_fences(body: str):
    """Разделить тело на прозу и содержимое кодовых блоков.

    Команда внутри ``` — это улика, а её же текст, попавший в прозу, ломает
    поиск пустых формулировок. Поэтому блоки вынимаются, а не игнорируются.
    """
    prose, blocks, cur, fence = [], [], [], None
    for line in body.splitlines():
        m = re.match(r"^\s*(`{3,}|~{3,})", line)
        if m:
            if fence is None:
                fence = m.group(1)[0]
                cur = []
            else:
                blocks.append("\n".join(cur))
                fence, cur = None, []
            continue
        (cur if fence is not None else prose).append(line)
    if fence is not None:          # незакрытый блок — всё равно улика, не терять
        blocks.append("\n".join(cur))
    return "\n".join(prose), blocks


def looks_like_command(text: str) -> bool:
    """Похоже ли это на то, что можно запустить и получить код возврата."""
    for line in text.splitlines():
        s = line.strip().lstrip(">").strip()
        if s and RUNNER.match(s):
            return True
    return False


def has_verifiable_claim(body: str) -> dict:
    """Есть ли в разделе проверяемое утверждение — и какое именно.

    Проверяемым считается ровно два вида содержимого, оба названы в SKILL.md:
    конкретная команда (её итог решает не читатель, а код возврата) и
    конкретное действие с ожидаемым результатом (его можно повторить и
    разойтись во мнениях невозможно). Всё остальное — пересказ намерения.
    """
    prose, blocks = strip_fences(body)

    for block in blocks:
        if block.strip():
            return {"ok": True, "kind": "command",
                    "sample": block.strip().splitlines()[0][:200]}
    for inline in re.findall(r"`([^`\n]+)`", prose):
        if looks_like_command(inline):
            return {"ok": True, "kind": "command", "sample": inline.strip()[:200]}
    if looks_like_command(prose):
        for line in prose.splitlines():
            s = line.strip().lstrip(">").strip()
            if s and RUNNER.match(s):
                return {"ok": True, "kind": "command", "sample": s[:200]}

    flat = normalize(prose)
    act = next((a for a in ACTIONS if a in flat), None)
    res = next((r for r in RESULTS if r in flat), None)
    if act and res:
        return {"ok": True, "kind": "action", "sample": flat[:200]}

    return {"ok": False, "kind": None, "sample": None,
            "missing": "результат" if act else ("действие" if res else "оба")}


def vacuous_phrases(body: str) -> list:
    """Пустые формулировки, найденные в разделе."""
    prose, _ = strip_fences(body)
    flat = normalize(prose)
    return [p for p in VACUOUS if p in flat]


def split_sections(text: str) -> list:
    """Разбить документ на разделы по заголовкам.

    Возвращает список {'title', 'norm', 'body', 'line'} в порядке появления.
    Текст до первого заголовка разделом не считается: у него нет имени, и
    приписать его обязательному разделу значило бы засчитать преамбулу за
    критерий приёмки.
    """
    out: list = []
    for i, line in enumerate(text.splitlines(), start=1):
        title = tail = None
        m = H_HASH.match(line)
        if m:
            title, tail = m.group(1), ""
        else:
            m = H_NUM_BOLD.match(line)
            if m:
                title, tail = m.group(1), m.group(2)
            else:
                m = H_BOLD.match(line)
                if m:
                    title, tail = m.group(1), ""
        if title is None:
            if out:
                out[-1]["body"] += line + "\n"
            continue
        out.append({"title": title.strip(), "norm": normalize(title),
                    "body": (tail + "\n") if tail.strip() else "", "line": i})
    return out


def is_empty_body(body: str) -> bool:
    """Тело, в котором нет ни одного значащего знака.

    Тире, многоточие и «TBD» — это заголовок без раздела: выглядит как
    заполненное, держит ровно ноль.
    """
    flat = normalize(body)
    return flat in ("", "tbd", "todo", "-", "...", "позже", "потом")


def lint_text(text: str) -> dict:
    """Разбор готового текста спеки. Файловые отказы сюда не доходят."""
    found = split_sections(text)
    problems: list = []
    report: dict = {}

    for key, title, why in SECTIONS:
        want = normalize(title)
        hit = next((s for s in found if s["norm"].startswith(want)), None)
        if hit is None:
            report[key] = {"present": False, "line": None}
            problems.append({
                "code": "section.missing", "section": key, "title": title,
                "line": None,
                "message": f"нет раздела «{title}»",
                "why": why})
            continue

        report[key] = {"present": True, "line": hit["line"]}
        if is_empty_body(hit["body"]):
            problems.append({
                "code": "section.empty", "section": key, "title": title,
                "line": hit["line"],
                "message": f"раздел «{title}» пуст — заголовок есть, содержания нет",
                "why": why})
            continue

        if key != "accept":
            continue

        claim = has_verifiable_claim(hit["body"])
        report[key]["claim"] = claim["kind"]
        if claim["ok"]:
            continue
        empty = vacuous_phrases(hit["body"])
        if empty:
            problems.append({
                "code": "accept.vacuous", "section": key, "title": title,
                "line": hit["line"],
                "message": "критерий приёмки — пустая формулировка: "
                           + ", ".join(f"«{p}»" for p in empty),
                "why": "по такой фразе нельзя получить код возврата, "
                       "и на приёмке она означает то, что удобно говорящему"})
        problems.append({
            "code": "accept.no_check", "section": key, "title": title,
            "line": hit["line"],
            "message": "в разделе «Как проверить» нет ни команды, ни действия "
                       "с ожидаемым результатом",
            "why": "гейт приёмки, который нельзя запустить, закрывается словами"})

    status = "problems" if problems else "clean"
    nxt = ("спека держит форму: можно идти в план" if status == "clean" else
           "дописать спеку и прогнать линт снова — код 0 или ход не начат")
    return {"gate": "spec_lint", "status": status, "sections": report,
            "problems": problems, "next": nxt}


HEAD = {"clean": "СПЕКА ЦЕЛА", "problems": "СПЕКА: ЗАМЕЧАНИЯ",
        "unchecked": "НЕ СМОГ ПРОВЕРИТЬ"}
EXIT = {"clean": 0, "problems": 1, "unchecked": 2}


def human(v: dict) -> str:
    lines = [f"{HEAD.get(v['status'], v['status'])}  [{v.get('spec', '?')}]"]
    for p in v["problems"]:
        where = f"строка {p['line']}" if p.get("line") else "раздела нет"
        lines.append(f"  ! [{p['code']}] {p['message']}  ({where})")
        lines.append(f"      почему важно: {p['why']}")
    if v["status"] == "unchecked":
        lines.append(f"  ? {v.get('reason', 'причина не названа')}")
    lines.append(f"  дальше: {v['next']}")
    return "\n".join(lines)


def main() -> int:
    halt_if_paused()
    args = [a for a in sys.argv[1:] if a != "--json"]
    quiet = "--json" in sys.argv[1:]
    if len(args) != 1:
        print("вызов: spec_lint.py [--json] <путь-к-спеке.md>", file=sys.stderr)
        return 3
    spec = Path(args[0]).expanduser()
    if not spec.is_file():
        print(f"НЕ УДАЛОСЬ: это не файл спеки — {spec}", file=sys.stderr)
        return 3

    try:
        text = spec.read_text(encoding="utf-8-sig")
    except (OSError, ValueError, UnicodeDecodeError) as e:
        # Молчаливый ноль здесь был бы худшим исходом: нечитаемая спека
        # выглядела бы как проверенная. «Не смог» обязан гасить доверие.
        v = {"gate": "spec_lint", "status": "unchecked", "spec": str(spec),
             "sections": {}, "problems": [], "reason": f"файл не читается: {e}",
             "next": "починить файл спеки и прогнать линт снова"}
        if not quiet:
            print(human(v), file=sys.stderr)
        print(json.dumps(v, ensure_ascii=False, indent=1))
        return EXIT["unchecked"]

    v = lint_text(text)
    v["spec"] = str(spec)
    if not quiet:
        print(human(v), file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return EXIT.get(v["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
