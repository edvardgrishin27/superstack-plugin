#!/usr/bin/env python3
"""SUPERSTACK — отчёт догоняет человека там, где его нет за компьютером.

Зачем это вообще.

Автономия без исходящего канала ненастоящая. Система может работать час, но
если итог лежит в терминале, человек всё равно привязан к столу: он либо ждёт,
либо возвращается проверять. Ровно это и делает «автономность» словом.

Нужен один исходящий шаг: короткий итог уходит туда, где человека застанут.
Канал объявляет он сам — вебхук чата, куда он и так смотрит.

Четыре правила, ради которых это код, а не строчка в инструкции:

  1. КАНАЛ ОБЪЯВЛЯЕТ ЧЕЛОВЕК. Не объявлен — код 2 «отправлять некуда», а не
     тихое ничего. Молчащая доставка неотличима от работающей ровно до того
     дня, когда она понадобится.
  2. СЕКРЕТЫ НЕ УЕЗЖАЮТ. Отчёт проходит через ту же редактуру, что и журнал:
     ключ, попавший в текст, не отправляется никуда. Исходящий канал — самый
     дешёвый способ вынести секрет за пределы машины.
  3. ДОСТАВКА ПОДТВЕРЖДАЕТСЯ КОДОМ ОТВЕТА. «Отправлено» без ответа сервера —
     это «вызвали функцию»; провайдер, ответивший 500, доставил ничего.
  4. СБОЙ ДОСТАВКИ НЕ РОНЯЕТ РАБОТУ. Отчёт — последний шаг, а не условие
     готовности: упавший вебхук не имеет права отменять сделанное.

  .superstack/notify.json:
    {"webhook": "https://...", "quiet": false}

  python3 notify.py <корень> --text «итог» [--json]
  python3 notify.py <корень> --check          проверить, куда пойдёт

  код 0 — доставлено (или канал молчит намеренно), 1 — канал ответил ошибкой,
  2 — отправлять некуда, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

СПЕКА = ".superstack/notify.json"
ТАЙМАУТ = 15

#: Формы секретов, которые нельзя выпускать наружу. Тот же список, что у
#: сканера: канал доставки — самый дешёвый способ вынести ключ с машины.
СЕКРЕТЫ = (
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"sshpass\s+-p\s+['\"][^'\"]+['\"]"),
    re.compile(r"(?i)(password|passwd|api[_-]?key|token)\s*[:=]\s*\S{6,}"),
)


def redact(текст: str) -> tuple:
    """(текст без секретов, сколько вырезано). Значения не сохраняются."""
    сколько = 0
    for рег in СЕКРЕТЫ:
        текст, n = рег.subn("<вырезано>", текст)
        сколько += n
    return текст, сколько


def channel(root: Path) -> tuple:
    """(спека, причина отказа). Не объявлен — это не «тихо ок»."""
    p = root / СПЕКА
    if not p.is_file():
        return None, f"отправлять некуда: нет {СПЕКА}"
    try:
        d = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError) as e:
        return None, f"{СПЕКА} не разобран ({e})"
    if not isinstance(d, dict):
        return None, f"{СПЕКА} — не объект"
    if d.get("quiet"):
        return d, ""
    if not (d.get("webhook") or "").startswith("https://"):
        return None, ("канал объявлен наполовину: нужен webhook по https — "
                      "по http итог уедет открытым текстом")
    return d, ""


def _post(url: str, тело: dict) -> tuple:
    данные = json.dumps(тело, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=данные, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=ТАЙМАУТ) as r:
            return r.getcode(), ""
    except urllib.error.HTTPError as e:
        return e.code, f"канал ответил {e.code}"
    except (urllib.error.URLError, OSError) as e:
        return None, f"канал недоступен: {e}"


def send(root: Path, текст: str, отправитель=_post) -> dict:
    """Отправитель подаётся снаружи — иначе тест уходил бы в сеть.

    Проверка, стучащаяся в интернет, измеряет чужой сервер, а не наш код.
    """
    спека, отказ = channel(root)
    if спека is None:
        return {"status": "unknown", "detail": отказ,
                "next": f"объявить канал в {СПЕКА}: автономия без исходящего "
                        "шага привязывает человека к столу"}
    чистый, вырезано = redact(текст)
    if спека.get("quiet"):
        return {"status": "pass", "delivered": False, "redacted": вырезано,
                "detail": "канал выключен человеком намеренно"}
    код, почему = отправитель(спека["webhook"], {"text": чистый})
    if код is None or код >= 400:
        # Сбой доставки не отменяет сделанного: отчёт — последний шаг, а не
        # условие готовности.
        return {"status": "fail", "code": код, "redacted": вырезано,
                "detail": почему or f"канал ответил {код}",
                "next": "работа не отменяется: доставка — последний шаг, а не "
                        "условие готовности"}
    return {"status": "pass", "delivered": True, "code": код,
            "redacted": вырезано,
            "detail": f"доставлено (код {код})" +
                      (f", вырезано секретов: {вырезано}" if вырезано else "")}


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    берут = {"--text"}
    plain, пропуск = [], False
    for a in argv:
        if пропуск:
            пропуск = False
            continue
        if a in берут:
            пропуск = True
        elif not a.startswith("--"):
            plain.append(a)
    if len(plain) != 1:
        print("вызов: notify.py <корень> --text «итог» | --check", file=sys.stderr)
        return 3
    root = Path(plain[0]).resolve()
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {root}", file=sys.stderr)
        return 3

    if "--check" in argv:
        спека, отказ = channel(root)
        v = ({"status": "pass", "detail": "канал объявлен: "
              + ("выключен намеренно" if спека.get("quiet") else "вебхук по https")}
             if спека else {"status": "unknown", "detail": отказ})
    else:
        i = argv.index("--text") if "--text" in argv else -1
        текст = argv[i + 1] if i >= 0 and i + 1 < len(argv) else ""
        if not текст.strip():
            print("НЕ УДАЛОСЬ: нечего отправлять — нужен --text", file=sys.stderr)
            return 3
        v = send(root, текст)

    if "--json" not in argv:
        голова = {"pass": "ОТЧЁТ УШЁЛ", "fail": "ДОСТАВИТЬ НЕ УДАЛОСЬ",
                  "unknown": "ОТПРАВЛЯТЬ НЕКУДА"}
        print(f"{голова[v['status']]}: {v['detail']}", file=sys.stderr)
        if v.get("next"):
            print(f"  дальше: {v['next']}", file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return {"pass": 0, "fail": 1, "unknown": 2}[v["status"]]


if __name__ == "__main__":
    sys.exit(main())
