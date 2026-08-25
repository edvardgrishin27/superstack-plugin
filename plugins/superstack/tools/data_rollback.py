#!/usr/bin/env python3
"""SUPERSTACK — откат ДАННЫХ: у кода есть git, у данных его нет.

Зачем это пришлось конструировать, а не брать из корпуса.

Во всех 24 разобранных репозиториях бэкапов и отката баз — ноль механизмов.
Ноль вхождений `pg_dump`, ноль обратных миграций, ноль восстановления на точку
во времени. Единственный настоящий бэкап во всём корпусе — копия markdown-файла.
Причина простая и общая: это инструменты работы с КОДОМ, а модель отката кода —
git. Агент, снёсший файл, ловится `git checkout`. Агент, снёсший строки в
таблице, не ловится ничем.

Поэтому здесь проверяется ровно одно, зато кодом: **правка данных не считается
готовой, пока у проекта нет объявленного пути отката** — пары команд «снять
снимок» и «восстановить». Не намерения сделать бэкап, не строчки в README, а
двух команд, которые можно запустить.

Три отказа, ради которых всё написано:

  1. НАМЕРЕНИЕ — НЕ ОТКАТ. «Сделаем бэкап перед миграцией» выполняется словами.
     Путь отката либо объявлен командами в `.superstack/data-rollback.json`,
     либо его нет.
  2. НЕОБРАТИМОЕ БЫВАЕТ, И ЭТО НОРМАЛЬНО — но названо вслух. Удаление колонки
     необратимо по существу; провести его можно, объявив необратимым и с
     причиной. Молча — нельзя.
  3. «НЕ СМОГ ОПРЕДЕЛИТЬ» — НЕ «ПРАВОК ДАННЫХ НЕТ». Нет git, нет истории,
     незнакомый формат миграции — это код 2, а не зелёное.

  .superstack/data-rollback.json:
    {"snapshot": "pg_dump -Fc $DATABASE_URL > snap.dump",
     "restore":  "pg_restore -c -d $DATABASE_URL snap.dump",
     "irreversible": [{"file": "migrations/0007_drop_legacy.py",
                       "why":  "колонка удаляется по требованию юриста"}]}

  python3 data_rollback.py <корень проекта> [--base <ветка/коммит>] [--json]

  код 0 — путь отката есть либо данные не тронуты, 1 — данные тронуты без
  отката, 2 — определить не удалось, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

#: Слово, которым правка объявляется необратимой. Отдельная константа, потому
#: что это единственная законная дверь мимо проверки, и она должна быть видна
#: в исходнике с одного взгляда.
IRREVERSIBLE = "irreversible"

СПЕКА = ".superstack/data-rollback.json"

#: По каким путям узнаётся правка данных. Список намеренно узкий: ложное
#: срабатывание здесь стоит доверия ко всей проверке, а пропуск ловится
#: человеком на ревью.
ПРИЗНАКИ = (
    "migrations/", "migrate/", "alembic/versions/", "prisma/migrations/",
    "db/migrate/", "schema.prisma", "schema.sql", "structure.sql",
)
СУФФИКСЫ = (".sql",)

#: Чем в разных экосистемах выглядит обратная миграция.
ОБРАТНОЕ = ("def downgrade", "func Down", "-- migrate:down", "exports.down",
            "reverse_code", "async down", "def backwards")

#: Чем выглядит ПРЯМАЯ. Нужна отдельно: формат, который мы узнали, но обратной
#: в нём нет, — это «обратной нет», а незнакомый формат — «не смог определить».
#: Без этого различия миграция без downgrade уходила в серое вместо красного,
#: то есть ровно тот случай, ради которого проверка написана, не срабатывал.
ПРЯМОЕ = ("def upgrade", "class Migration", "func Up", "-- migrate:up",
          "exports.up", "async up")


def touches_data(файлы: list) -> list:
    """Какие из изменённых файлов трогают данные, а не код.

    У кода откат — git. У данных отката нет ни у кого, поэтому список
    признаков решает, включится ли вообще проверка.
    """
    из_них = []
    for f in файлы:
        имя = f.replace("\\", "/")
        if any(п in имя for п in ПРИЗНАКИ) or имя.endswith(СУФФИКСЫ):
            из_них.append(имя)
    return из_них


def restore_path(root: Path) -> tuple:
    """(спека, причина отказа). Путь отката — это ДВЕ команды, а не одна.

    Снимок без восстановления — файл неизвестной годности: годным его делает
    только проверенная обратная команда.
    """
    p = root / СПЕКА
    if not p.is_file():
        return None, f"путь отката не объявлен: нет {СПЕКА}"
    try:
        d = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError) as e:
        return None, f"{СПЕКА} не разобран ({e})"
    if not isinstance(d, dict):
        return None, f"{СПЕКА} — не объект"
    нет = [k for k in ("snapshot", "restore") if not (d.get(k) or "").strip()]
    if нет:
        return None, ("объявлено наполовину: нет " + ", ".join(нет) +
                      " — снимок без восстановления это файл неизвестной годности")
    return d, ""


#: Три взгляда на «что изменилось». Первый пропускает НОВЫЕ файлы, а свежая
#: миграция почти всегда именно новый файл — проверка, смотрящая только на
#: `git diff`, молчала бы ровно в том случае, ради которого написана.
_ВЗГЛЯДЫ = (("diff", "--name-only"), ("diff", "--name-only", "--cached"),
            ("ls-files", "-o", "--exclude-standard"))


def _changed(root: Path, base: str) -> tuple:
    """(файлы, причина отказа). Нет git — это «не смог», а не «правок нет»."""
    файлы: set = set()
    for взгляд in _ВЗГЛЯДЫ:
        # `core.quotepath=false` — иначе git отдаёт нелатинские имена в
        # восьмеричных escape-последовательностях и в кавычках. Путь
        # «миграции/0003_странное.rb» превращался в строку, которой на диске
        # нет: проверка честно не находила файл и молчала.
        cmd = ["git", "-c", "core.quotepath=false", *взгляд]
        if взгляд[0] == "diff" and "--cached" not in взгляд:
            cmd.append(base)
        try:
            p = subprocess.run(cmd, cwd=str(root), capture_output=True,
                               text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            return None, f"git не отработал ({e}) — изменённое определить нечем"
        if p.returncode != 0:
            return None, (f"git {взгляд[0]} вернул {p.returncode}: "
                          f"{(p.stderr or '').strip()[-160:]}")
        файлы |= {s for s in p.stdout.splitlines() if s.strip()}
    return sorted(файлы), ""


def _reversible(root: Path, файл: str) -> "bool | None":
    """Есть ли у этой правки обратная. None — формат незнаком, а не «нет»."""
    p = root / файл
    if not p.is_file():
        return None
    try:
        текст = p.read_text("utf-8", errors="replace")
    except OSError:
        return None
    if any(з in текст for з in ОБРАТНОЕ):
        return True
    if p.suffix.lower() == ".sql":
        # Сам файл `*.down.sql` и ЕСТЬ обратная — спрашивать у него обратную
        # к обратной значит требовать бесконечность.
        if p.stem.endswith(".down"):
            return True
        return True if (p.parent / (p.stem + ".down.sql")).is_file() else False
    if any(з in текст for з in ПРЯМОЕ):
        return False
    return None


def run(root: Path, base: str) -> dict:
    файлы, отказ = _changed(root, base)
    if файлы is None:
        return {"status": "unknown", "detail": отказ}

    данные = touches_data(файлы)
    if not данные:
        return {"status": "pass", "detail": "правок данных нет — откат не нужен",
                "changed": len(файлы)}

    спека, почему = restore_path(root)
    объявлено = {i.get("file"): i.get("why", "")
                 for i in ((спека or {}).get(IRREVERSIBLE) or [])}

    if спека is None:
        return {"status": "fail", "data_files": данные, "detail": почему,
                "next": "объявить пару команд снимка и восстановления в "
                        f"{СПЕКА}; у кода откат это git, у данных его нет"}

    без_обратной, неясные = [], []
    for f in данные:
        if f in объявлено:
            continue
        r = _reversible(root, f)
        if r is None:
            неясные.append(f)
        elif not r:
            без_обратной.append(f)

    if без_обратной:
        return {"status": "fail", "data_files": данные,
                "irreversible_undeclared": без_обратной,
                "detail": f"правок без обратной: {len(без_обратной)}",
                "next": "дописать обратную миграцию либо объявить правку "
                        f"необратимой с причиной в {СПЕКА} → {IRREVERSIBLE}"}
    if неясные:
        # Незнакомый формат — не обвинение и не оправдание. Человек смотрит
        # сам, а вердикт остаётся серым.
        return {"status": "unknown", "data_files": данные, "unclear": неясные,
                "detail": f"обратимость не определена у {len(неясные)}: "
                          "формат миграции незнаком",
                "next": "посмотреть глазами или объявить в " + СПЕКА}
    return {"status": "pass", "data_files": данные,
            "detail": f"данные тронуты в {len(данные)} файлах, путь отката "
                      "объявлен, обратные есть"}


def human(v: dict) -> str:
    голова = {"pass": "ОТКАТ ЕСТЬ", "fail": "ОТКАТА НЕТ",
              "unknown": "ОПРЕДЕЛИТЬ НЕ СМОГ"}
    строки = [f"{голова[v['status']]}: {v['detail']}"]
    for f in v.get("irreversible_undeclared", [])[:10]:
        строки.append(f"  ! без обратной: {f}")
    for f in v.get("unclear", [])[:10]:
        строки.append(f"  ? формат незнаком: {f}")
    if v.get("next"):
        строки.append(f"  дальше: {v['next']}")
    return "\n".join(строки)


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    plain = [a for a in argv if not a.startswith("--")]
    if "--base" in argv:
        i = argv.index("--base")
        if i + 1 < len(argv) and argv[i + 1] in plain:
            plain.remove(argv[i + 1])
    if len(plain) != 1:
        print("вызов: data_rollback.py <корень проекта> [--base <ветка>]",
              file=sys.stderr)
        return 3
    root = Path(plain[0]).resolve()
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {root}", file=sys.stderr)
        return 3
    base = argv[argv.index("--base") + 1] if "--base" in argv else "HEAD"

    v = run(root, base)
    if "--json" not in argv:
        print(human(v), file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return {"pass": 0, "fail": 1, "unknown": 2}[v["status"]]


if __name__ == "__main__":
    sys.exit(main())
