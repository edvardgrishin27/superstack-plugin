#!/usr/bin/env python3
"""SUPERSTACK — итог сверяется у ВНЕШНЕЙ стороны, а не у себя.

Зачем нужен шаг, который нельзя сделать самому.

Всё остальное доказательство система производит сама: сама пишет код, сама
гоняет тесты, сама читает свой вывод. Это честно ровно до тех пор, пока
инструменты исправны, — а сломанный прибор рисует зелёное с той же
уверенностью, что и рабочий. Нужен хотя бы один шаг, результат которого
система не может себе нарисовать: ответ чужой стороны о том, что видит ОНА.

Для работы, кончающейся публикацией, такой стороной служит хостинг. Вопрос к
нему буквальный: существует ли PR, открыт ли он и есть ли в нём добавленные
строки. Не «всё прошло успешно», а три проверяемых условия.

Три правила:

  1. УСЛОВИЯ БУКВАЛЬНЫЕ. `state == "open"` и `additions > 0`. «Похоже, что
     создан» — не ответ: PR без единой добавленной строки открывается так же
     легко, как и с ними.
  2. НЕТ ОТВЕТА — НЕТ ПОДТВЕРЖДЕНИЯ. Сеть недоступна, лимит исчерпан, ключ
     не подошёл — это код 2 «сверить не удалось», а не «подтверждено».
     Молчание чужой стороны в свою пользу не толкуется.
  3. ЗАПРОС ПОДАЁТСЯ СНАРУЖИ. Проверка, обязательно ходящая в сеть, измеряет
     чужой сервер и не может жить в наборе тестов.

  python3 external_check.py <владелец/репозиторий> <номер PR> [--json]

  код 0 — подтверждено внешней стороной, 1 — условия не выполнены,
  2 — сверить не удалось, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

СЛАГ = re.compile(r"^[\w.-]+/[\w.-]+$")
ТАЙМАУТ = 20


def _спросить(slug: str, номер: int) -> tuple:
    """(ответ, причина отказа). Сеть отвечает или не отвечает — оба исхода честны."""
    url = f"https://api.github.com/repos/{slug}/pulls/{номер}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "superstack"})
    try:
        with urllib.request.urlopen(req, timeout=ТАЙМАУТ) as r:
            return json.loads(r.read().decode("utf-8")), ""
    except urllib.error.HTTPError as e:
        return None, f"хостинг ответил {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, f"спросить не удалось: {e}"


def verdict(ответ: dict) -> dict:
    """Три буквальных условия. «Похоже, что создан» ответом не является."""
    состояние = str(ответ.get("state") or "")
    добавлено = ответ.get("additions")
    провалы = []
    if состояние != "open":
        провалы.append(f"состояние «{состояние or 'нет поля'}», а не open")
    if not isinstance(добавлено, int):
        провалы.append("хостинг не назвал число добавленных строк")
    elif добавлено <= 0:
        # PR без единой добавленной строки открывается так же легко, как и с
        # ними, — и выглядит доказательством работы.
        провалы.append("добавленных строк ноль")
    if провалы:
        return {"status": "fail", "state": состояние, "additions": добавлено,
                "detail": "внешняя сторона не подтверждает: " + "; ".join(провалы)}
    return {"status": "pass", "state": состояние, "additions": добавлено,
            "detail": f"подтверждено хостингом: открыт, добавлено строк {добавлено}"}


def check(slug: str, номер: int, спросить=_спросить) -> dict:
    if not СЛАГ.fullmatch(slug):
        return {"status": "unknown",
                "detail": f"имя репозитория не похоже на owner/name: {slug}"}
    ответ, отказ = спросить(slug, номер)
    if ответ is None:
        # Молчание чужой стороны в свою пользу не толкуется.
        return {"status": "unknown", "detail": отказ}
    return verdict(ответ)


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
    if len(plain) != 2 or not plain[1].isdigit():
        print("вызов: external_check.py <владелец/репозиторий> <номер PR>",
              file=sys.stderr)
        return 3
    v = check(plain[0], int(plain[1]))
    if "--json" not in argv:
        голова = {"pass": "ПОДТВЕРЖДЕНО СНАРУЖИ", "fail": "СНАРУЖИ НЕ ВИДНО",
                  "unknown": "СВЕРИТЬ НЕ УДАЛОСЬ"}
        print(f"{голова[v['status']]}: {v['detail']}", file=sys.stderr)
        if v["status"] == "unknown":
            print("  молчание чужой стороны не толкуется в свою пользу",
                  file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return {"pass": 0, "fail": 1, "unknown": 2}[v["status"]]


if __name__ == "__main__":
    sys.exit(main())
