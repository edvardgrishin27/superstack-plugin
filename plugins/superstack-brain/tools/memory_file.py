#!/usr/bin/env python3
"""SUPERSTACK — память проекта: что следующая сессия прочитает первым.

Зачем это отдельный инструмент.

Вторая сессия обычно начинается с получаса разведки: агент открывает готовый
проект и заново выясняет, что тут построено, — за деньги человека и в его же
контекстное окно. Лечится одним файлом в корне, и вся сложность не в том, чтобы
его написать, а в трёх вещах, которые ломают его молча.

  1. КАКОЙ ФАЙЛ — РЕШАЕТСЯ ДЕТЕКЦИЕЙ, НЕ ВОПРОСОМ. `CLAUDE.md` или `AGENTS.md`
     зависит от того, чем человек работает. Спрашивать об этом — тратить его
     время на процессное решение; угадывать молча — получить два полупустых
     файла, расходящихся через месяц. Существующий файл всегда бьёт детекцию:
     репозиторий уже ответил на этот вопрос.

  2. ЧУЖОЙ ТЕКСТ НЕПРИКОСНОВЕНЕН. В `CLAUDE.md` живого проекта лежат правила,
     выстраданные командой. Инструмент, который перезаписывает файл целиком,
     стирает их — один раз, тихо, и заметят это нескоро. Поэтому пишем ТОЛЬКО
     между маркерами, и байты снаружи обязаны остаться теми же до единого.

  3. ПУТЬ, КОТОРОГО НЕТ, — КРАСНОЕ. Файл памяти умирает не от старости, а от
     несовпадения: он выглядит источником правды и тихо расходится с кодом.
     У AutoPilot про это сказано «проверь команды, упавшая не идёт в файл» —
     и не проверяется ничем. Здесь пути сверяются с репозиторием, а команда
     без отметки о живом прогоне не считается проверенной.

  python3 memory_file.py detect <корень>
  python3 memory_file.py init   <корень> --project "Название" [--about "одна строка"]
  python3 memory_file.py set    <корень> --section "Команды" --body "..."
  python3 memory_file.py check  <корень>

  код 0 — чисто, 1 — нарушено, 2 — не смог проверить, 3 — ошибка вызова
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

START = "<!-- superstack:start -->"
END = "<!-- superstack:end -->"

CLAUDE, AGENTS = "CLAUDE.md", "AGENTS.md"

#: Порядок детекции. Останавливаемся на первом совпадении — и первые два
#: пункта не случайно вперёд всех: существующий файл уже отвечает на вопрос,
#: и переспрашивать его признаками окружения значит завести второй.
def detect(root: Path, env: dict = None) -> dict:
    env = os.environ if env is None else env
    has_c, has_a = (root / CLAUDE).is_file(), (root / AGENTS).is_file()
    if has_c and has_a:
        # Оба на месте — берём тот, где УЖЕ лежит описание, и второй не трогаем.
        for f in (CLAUDE, AGENTS):
            if START in (root / f).read_text("utf-8", errors="replace"):
                return {"file": f, "pointer": None, "why": "в нём уже лежит описание"}
        return {"file": AGENTS, "pointer": None,
                "why": "оба файла есть, описания нет ни в одном — второй не трогаем"}
    if has_c:
        return {"file": CLAUDE, "pointer": None, "why": "файл уже существует"}
    if has_a:
        return {"file": AGENTS, "pointer": None, "why": "файл уже существует"}
    if (root / ".claude").is_dir() or env.get("CLAUDECODE") or \
            env.get("CLAUDE_CODE_ENTRYPOINT"):
        return {"file": CLAUDE, "pointer": None, "why": "признаки Claude Code"}
    if (root / ".cursor").is_dir():
        return {"file": AGENTS, "pointer": None, "why": "признаки Cursor"}
    if (root / ".codex").is_dir() or (root / ".github" / "copilot-instructions.md").is_file():
        return {"file": AGENTS, "pointer": None, "why": "признаки Codex или Copilot"}
    # Ничего не опознано — файл-указатель пишется ТОЛЬКО здесь. Когда агент
    # опознан, второй файл это второе место для рассинхрона, и оно рассинхронится.
    return {"file": AGENTS, "pointer": CLAUDE, "why": "агент не опознан"}


SKELETON = """# {project}

{about}

## Команды

| Команда | Что делает |
|---------|------------|
| — | заполняется, когда команда впервые отработала |

## Как здесь работает SUPERSTACK

Требования, спецификация и таски — в `.superstack/`. Снять требование из
манифеста может только человек, своей цитатой.

Если работа прервалась, скажи «продолжи» — состояние поднимется из файлов,
переспрашивать нечего.
"""

#: Пути, которые скелет обещает, обязаны существовать в момент записи. Правило
#: «файл документирует то, что ЕСТЬ» ловится собственной же проверкой: первый
#: прогон покраснел на `dashboard.html`, которого в этот момент ещё нет, — он
#: появляется позже и вписывается тогда же.
SKELETON_DIRS = (".superstack",)


def split(text: str) -> "tuple[str, str, str] | None":
    """(до, наше, после). None — маркеров нет или они сломаны."""
    i, j = text.find(START), text.find(END)
    if i < 0 or j < 0 or j < i:
        return None
    if text.count(START) > 1 or text.count(END) > 1:
        return None
    return text[:i], text[i + len(START):j], text[j + len(END):]


def write_block(path: Path, block: str) -> dict:
    """Заменить НАШ блок, не тронув ни байта снаружи.

    Отпечатки внешнего текста снимаются до и после и сверяются здесь же:
    правило «пишем только между маркерами» без проверки — это обещание, а
    стирание чужих правил происходит ровно один раз и замечается нескоро.
    """
    old = path.read_text("utf-8") if path.is_file() else ""
    parts = split(old)
    if parts is None:
        head = (old + "\n\n") if old.strip() else ""
        new = f"{head}{START}\n{block.strip()}\n{END}\n"
        before_sha = hashlib.sha256(old.encode()).hexdigest()
    else:
        before, _, after = parts
        new = f"{before}{START}\n{block.strip()}\n{END}{after}"
        before_sha = hashlib.sha256((before + after).encode()).hexdigest()

    path.write_text(new, encoding="utf-8")
    check = split(path.read_text("utf-8"))
    after_sha = hashlib.sha256(
        ((check[0] + check[2]) if check else "").encode()).hexdigest()
    if parts is not None and before_sha != after_sha:
        raise RuntimeError("текст вне маркеров изменился — запись отменена бы "
                           "быть должна; это тот самый случай, когда стираются "
                           "правила, выстраданные командой")
    return {"file": str(path), "outside_sha": after_sha[:12]}


_PATH = re.compile(r"`([\w./-]+\.[\w]{1,6}|[\w./-]+/)`")
_SECRETISH = re.compile(
    r"(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{12,}"
    r"|AIza[0-9A-Za-z_-]{20,}|xox[bpa]-[0-9A-Za-z-]{10,})")


def check(root: Path, name: str) -> dict:
    p = root / name
    broken, unmeasured = [], []
    if not p.is_file():
        return {"status": "unknown", "broken": [],
                "unmeasured": [f"файла памяти нет: {name}"],
                "detail": "память проекта не заведена"}

    text = p.read_text("utf-8", errors="replace")
    parts = split(text)
    if parts is None:
        broken.append(
            "маркеры сломаны или их нет — без них правка означает перезапись "
            "файла целиком, вместе с тем, что писал человек")
        return {"status": "fail", "broken": broken, "unmeasured": unmeasured,
                "detail": "маркеры"}
    ours = parts[1]

    for m in _PATH.finditer(ours):
        rel = m.group(1)
        if rel.startswith(("http", "-")) or " " in rel:
            continue
        if not (root / rel).exists():
            broken.append(f"путь `{rel}` не существует — файл памяти разошёлся "
                          "с кодом, а выглядит источником правды")
    if _SECRETISH.search(ours):
        broken.append("в блоке похоже на значение ключа — имена переменных, "
                      "никогда значения")
    if len(ours.strip()) < 40:
        unmeasured.append("блок почти пуст — описания проекта в нём нет")

    return {"status": "fail" if broken else ("unknown" if unmeasured else "pass"),
            "broken": broken, "unmeasured": unmeasured,
            "detail": (f"{len(broken)} нарушений" if broken
                       else "маркеры целы, пути на месте")}


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


_TAKES = {"--project", "--about", "--section", "--body"}


def _one(argv, name, default=""):
    return argv[argv.index(name) + 1] if name in argv and \
        argv.index(name) + 1 < len(argv) else default


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    plain, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a in _TAKES:
            skip = True
        elif not a.startswith("--"):
            plain.append(a)
    if len(plain) != 2 or plain[0] not in ("detect", "init", "set", "check"):
        print("вызов: memory_file.py detect|init|set|check <корень> ...",
              file=sys.stderr)
        return 3
    cmd, root = plain[0], Path(plain[1])
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {root}", file=sys.stderr)
        return 3

    d = detect(root)
    if cmd == "detect":
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0

    if cmd == "init":
        project = _one(argv, "--project")
        if not project:
            print("НЕ УДАЛОСЬ: нужно --project", file=sys.stderr)
            return 3
        for sub in SKELETON_DIRS:
            (root / sub).mkdir(parents=True, exist_ok=True)
        r = write_block(root / d["file"], SKELETON.format(
            project=project, about=_one(argv, "--about", "")))
        if d["pointer"]:
            # Указатель пишется только когда агент не опознан: при опознанном
            # второй файл — второе место для рассинхрона, и оно рассинхронится.
            (root / d["pointer"]).write_text(f"См. @{d['file']}\n", encoding="utf-8")
            r["pointer"] = d["pointer"]
        print(json.dumps({**d, **r}, ensure_ascii=False, indent=1))
        return 0

    if cmd == "set":
        section, body = _one(argv, "--section"), _one(argv, "--body")
        if not section or not body:
            print("НЕ УДАЛОСЬ: нужны --section и --body", file=sys.stderr)
            return 3
        p = root / d["file"]
        parts = split(p.read_text("utf-8") if p.is_file() else "")
        ours = parts[1] if parts else ""
        pat = re.compile(rf"(?ms)^## {re.escape(section)}\s*$.*?(?=^## |\Z)")
        block = f"## {section}\n\n{body.strip()}\n\n"
        ours = pat.sub(block, ours) if pat.search(ours) else (ours.rstrip() + "\n\n" + block)
        print(json.dumps(write_block(p, ours), ensure_ascii=False, indent=1))
        return 0

    v = check(root, d["file"])
    if "--json" not in argv:
        head = {"pass": "ПАМЯТЬ В ПОРЯДКЕ", "fail": "ПАМЯТЬ РАЗОШЛАСЬ С КОДОМ",
                "unknown": "ПРОВЕРИТЬ НЕ СМОГ"}
        print(head[v["status"]], file=sys.stderr)
        for b in v["broken"]:
            print(f"  ! {b}", file=sys.stderr)
        for u in v["unmeasured"]:
            print(f"  ? {u}", file=sys.stderr)
    print(json.dumps({**d, **v}, ensure_ascii=False, indent=1))
    return {"pass": 0, "fail": 1, "unknown": 2}[v["status"]]


if __name__ == "__main__":
    sys.exit(main())
