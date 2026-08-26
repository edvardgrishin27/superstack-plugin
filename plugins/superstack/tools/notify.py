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
  python3 notify.py <корень> --set <ссылка>   записать канал самому — такой
                                               файл человеку не собрать руками
  python3 notify.py <корень> --off            записать явный отказ человека:
                                               дальше работать молча

  код 0 — доставлено (или канал молчит намеренно, или --set/--off записали
  файл), 1 — канал ответил ошибкой, 2 — отправлять некуда, 3 — ошибка вызова
  (для --set — в том числе ссылка не годится)
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
        return None, ("канал объявлен наполовину: нужна ссылка именно по "
                      "https — по http итог уедет открытым текстом")
    return d, ""


def link_problem(сырое: str) -> str:
    """Что не так со ссылкой человеческими словами; "" — годится.

    Пишет не разработчик — он не даст url по спецификации, он даст то, что
    скопировал. Проверка ловит три реальных промаха нетехнического человека:
    пустую строку, фразу вместо адреса и адрес без защиты. Разбирать URL по
    частям смысла нет — это подсказка, что поправить, а не парсер.
    """
    s = (сырое or "").strip()
    if not s:
        return "ссылка не пришла — здесь пусто"
    if any(ch.isspace() for ch in s):
        return ("это похоже на фразу, а не на ссылку — пришли только сам "
                "адрес, без лишних слов")
    if s.startswith("http://"):
        return ("адрес без защиты: начинается с http, а не https — по "
                "такому текст уйдёт открытым текстом")
    if not s.startswith("https://"):
        return "не похоже на ссылку — она обязана начинаться с https://"
    if len(s) <= len("https://") + 3:
        return "ссылка обрублена — после https:// почти ничего не осталось"
    return ""


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


def _set_channel(root: Path, сырое: str, выключить: bool) -> dict:
    """Записать решение человека о канале — файлом, который он не составит сам.

    Не-разработчик не напишет `{"webhook": "..."}` руками: одна забытая
    кавычка — и канал молчит, а причину не видит никто. Здесь ровно две
    развилки: ссылка или явный отказ, и обе входят готовым значением, а не
    текстом, который потом кто-то вручную кладёт в файл настроек.
    """
    if выключить:
        спека = {"quiet": True}
        деталь = "канал выключен по просьбе человека — сообщений не будет"
    else:
        беда = link_problem(сырое)
        if беда:
            return {"status": "fail", "detail": беда}
        спека = {"webhook": сырое.strip()}
        деталь = "канал записан — сообщения пойдут по этой ссылке"
    (root / ".superstack").mkdir(parents=True, exist_ok=True)
    (root / СПЕКА).write_text(json.dumps(спека, ensure_ascii=False), encoding="utf-8")
    return {"status": "pass", "detail": деталь}


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    берут = {"--text", "--set"}
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
        print("вызов: notify.py <корень> --text «итог» | --check | "
              "--set <ссылка> | --off", file=sys.stderr)
        return 3
    root = Path(plain[0]).resolve()
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {root}", file=sys.stderr)
        return 3

    if "--set" in argv or "--off" in argv:
        # Отдельная ветка, а не общий код 0/1/2: неверная ссылка — это ошибка
        # вызова (3), а не «канал ответил ошибкой» (1) — с сервером ещё не
        # разговаривали, отказало распознавание того, что дал человек.
        i = argv.index("--set") if "--set" in argv else -1
        сырое = argv[i + 1] if i >= 0 and i + 1 < len(argv) else ""
        v = _set_channel(root, сырое, выключить="--off" in argv)
        if v["status"] == "fail":
            print(f"НЕ ЗАПИСАНО: {v['detail']}", file=sys.stderr)
            return 3
        if "--json" not in argv:
            print(f"ЗАПИСАНО: {v['detail']}", file=sys.stderr)
        print(json.dumps(v, ensure_ascii=False, indent=1))
        return 0

    if "--check" in argv:
        спека, отказ = channel(root)
        v = ({"status": "pass", "detail": "канал объявлен: "
              + ("выключен намеренно" if спека.get("quiet") else "ссылка на https")}
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
