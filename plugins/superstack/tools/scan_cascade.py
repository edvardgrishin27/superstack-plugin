#!/usr/bin/env python3
"""SUPERSTACK — сканеры секретов и кода: каскад, а не одна проверка.

Зачем три слоя, если сканер один.

`gitleaks` и `semgrep` дешевле любого ревью моделью: офлайн, секунды, ноль
токенов, жёсткий блок. Но слой, на котором они стоят, решает всё, и каждый
слой по отдельности дырявый:

  · ТОЛЬКО ДО КОММИТА — обходится одним флагом `--no-verify`, и обходится он
    именно в спешке, то есть тогда, когда ошибаются чаще всего;
  · ТОЛЬКО ДО СЛИЯНИЯ — ловит секрет, который УЖЕ в истории ветки. Отменить
    это нельзя: то, что попало в git и уехало на сервер, считается утёкшим,
    даже если коммит потом переписали;
  · ТОЛЬКО ПО РАСПИСАНИЮ — находит вчерашнее.

Вместе они закрывают друг друга: первый ловит дёшево, второй ловит обойдённое
первым, третий ловит то, что появилось не через коммит — например, ключ,
дописанный в файл настроек руками.

Проверка читает файлы, а не запускает сканеры: вопрос здесь «стоит ли», а не
«находит ли». Найденное — работа самого сканера.

  python3 scan_cascade.py <корень проекта> [--json]

  код 0 — все три слоя на месте, 1 — каких-то нет, 2 — прочитать не удалось,
  3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

#: Чем распознаётся детерминированный сканер. Список узкий намеренно: увидеть
#: «security» в имени шага и записать это в защиту значит обмануть себя.
СКАНЕРЫ = ("gitleaks", "semgrep", "trufflehog", "detect-secrets", "bandit")

#: Слово, которым обходится любой локальный хук. Названо константой, потому что
#: это и есть причина, по которой одного слоя мало.
ОБХОДИТСЯ = "--no-verify"

#: Где живут описания хука до коммита у разных менеджеров.
ДО_КОММИТА = (".git/hooks/pre-commit", ".pre-commit-config.yaml",
              ".pre-commit-config.yml", "lefthook.yml", "lefthook.yaml",
              ".husky/pre-commit", ".hooks/pre-commit")


def _текст(p: Path) -> str:
    try:
        return p.read_text("utf-8", errors="replace")
    except OSError:
        return ""


def _есть_сканер(текст: str) -> "str | None":
    низ = текст.lower()
    for имя in СКАНЕРЫ:
        if имя in низ:
            return имя
    return None


def _рабочие_потоки(root: Path) -> list:
    пути = []
    for каталог in (".github/workflows", ".gitlab", ".circleci"):
        d = root / каталог
        if d.is_dir():
            пути += [f for f in sorted(d.rglob("*"))
                     if f.is_file() and f.suffix in (".yml", ".yaml")]
    for имя in (".gitlab-ci.yml", ".gitlab-ci.yaml"):
        if (root / имя).is_file():
            пути.append(root / имя)
    return пути


def layers(root: Path) -> dict:
    """Три слоя каскада: до коммита, до слияния, по расписанию.

    Каждый слой отвечает своим адресом — файлом, в котором сканер найден.
    Слой без адреса считается отсутствующим: «где-то настроено» проверить
    нельзя, а значит нельзя и засчитать.
    """
    итог = {"pre_commit": None, "pre_merge": None, "scheduled": None}

    for имя in ДО_КОММИТА:
        p = root / имя
        if p.is_file():
            сканер = _есть_сканер(_текст(p))
            if сканер:
                итог["pre_commit"] = {"file": имя, "scanner": сканер}
                break

    for f in _рабочие_потоки(root):
        текст = _текст(f)
        сканер = _есть_сканер(текст)
        if not сканер:
            continue
        адрес = str(f.relative_to(root))
        по_расписанию = re.search(r"(?m)^\s*schedule\s*:", текст) or \
            re.search(r"(?m)^\s*-?\s*cron\s*:", текст)
        if по_расписанию and итог["scheduled"] is None:
            итог["scheduled"] = {"file": адрес, "scanner": сканер}
        if итог["pre_merge"] is None and re.search(
                r"(?m)^\s*(pull_request|merge_request|on)\s*:", текст):
            итог["pre_merge"] = {"file": адрес, "scanner": сканер}
    return итог


def run(root: Path) -> dict:
    if not (root / ".git").exists():
        return {"status": "unknown",
                "detail": "это не git-репозиторий — каскад ставить не на что"}
    сл = layers(root)
    нет = [к for к, v in сл.items() if v is None]
    имена = {"pre_commit": "до коммита", "pre_merge": "до слияния",
             "scheduled": "по расписанию"}
    if not нет:
        return {"status": "pass", "layers": сл,
                "detail": "каскад полон: " + ", ".join(
                    f"{имена[к]} ({v['scanner']})" for к, v in сл.items())}
    return {"status": "fail", "layers": сл, "missing": нет,
            "detail": "слоёв нет: " + ", ".join(имена[к] for к in нет),
            "next": f"один слой каскадом не считается: локальный хук обходится "
                    f"{ОБХОДИТСЯ}, а проверка до слияния ловит секрет, который "
                    "уже в истории — отменить это нельзя"}


def human(v: dict) -> str:
    голова = {"pass": "КАСКАД ПОЛОН", "fail": "КАСКАД ДЫРЯВ",
              "unknown": "ПРОВЕРИТЬ НЕ СМОГ"}
    строки = [f"{голова[v['status']]}: {v['detail']}"]
    for к, знач in (v.get("layers") or {}).items():
        метка = f"{знач['scanner']} — {знач['file']}" if знач else "нет"
        строки.append(f"  {'+' if знач else '!'} {к:<12} {метка}")
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
    if len(plain) != 1:
        print("вызов: scan_cascade.py <корень проекта> [--json]", file=sys.stderr)
        return 3
    root = Path(plain[0]).resolve()
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {root}", file=sys.stderr)
        return 3
    v = run(root)
    if "--json" not in argv:
        print(human(v), file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return {"pass": 0, "fail": 1, "unknown": 2}[v["status"]]


if __name__ == "__main__":
    sys.exit(main())
