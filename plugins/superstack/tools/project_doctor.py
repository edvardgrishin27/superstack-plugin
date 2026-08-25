#!/usr/bin/env python3
"""SUPERSTACK — осмотр ПРОЕКТА ЧЕЛОВЕКА: чем здесь доказывают, что работает.

Чем это не является. Планка (`tools/gauntlet.py`) проверяет сам SUPERSTACK,
`verify.py` отвечает «зелено ли сейчас», `prove_tests.py` — «держатся ли тесты».
Ни один не отвечает на вопрос, который человек задаёт первым и который дороже
всех: **можно ли вообще доверять зелёному в этом репозитории.**

Всё здесь — grep и проверка файлов. Ни одной модели, ни одного мнения, которое
нельзя проследить до строки. Причина простая: осмотр, который выносит суждение
моделью, сам нуждается в осмотре.

Что ищется — ровно то, из-за чего зелёный прогон врёт:

  · тестов нет вовсе, но есть команда `test`, которая всегда возвращает ноль;
  · «ни одного падения» засчитано за «прошло» — `|| true`, `continue-on-error`,
    `--passWithNoTests`, `set +e` в проверяющем скрипте;
  · решение «готово» принимает человек глазами, а не код возврата;
  · ни одной зарегистрированной поломки — значит «тесты держат» никто не мерил;
  · секрет лежит в отслеживаемом файле;
  · `.env` не закрыт от коммита.

Каждая находка называет ФАЙЛ И СТРОКУ. Находка без адреса — мнение.

  python3 project_doctor.py <корень проекта> [--json]

  код 0 — чисто (предупреждения допустимы), 1 — есть провалы, 2 — не смог,
  3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

FAIL, WARN, OK = "FAIL", "WARN", "OK"

#: Каталоги, которые не осматриваются: чужой код и сборочный мусор дают шум,
#: который топит настоящие находки.
SKIP = {".git", "node_modules", "__pycache__", "dist", "build", ".venv",
        "venv", ".next", "target", "vendor", ".pytest_cache"}

#: Формы «ни одного падения = прошло». Каждая встречается в живых проектах и
#: каждая делает зелёный прогон бессодержательным.
#: `|| true` ищется ТОЛЬКО в строке, где рядом стоит проверка. Иначе под нож
#: попадает уборка вида `rm -rf "$TMP" 2>/dev/null || true` в trap — она к коду
#: возврата отношения не имеет, а инструмент, кричащий на здоровом проекте,
#: перестают запускать, и вместе с шумом теряются настоящие находки.
_CHECKY = r"(?:test|pytest|jest|vitest|lint|eslint|ruff|mypy|tsc|check|verify|build|coverage)"
EMPTY_PASS = (
    (re.compile(_CHECKY + r"[^\n]*\|\|\s*true", re.I),
     "«|| true» после проверки гасит её код возврата — упасть она больше не может"),
    (re.compile(r"continue-on-error:\s*true"), "continue-on-error: провал шага не останавливает сборку"),
    (re.compile(r"--passWithNoTests"), "--passWithNoTests: ноль тестов засчитывается за успех"),
    (re.compile(r"set\s+\+e"), "set +e: дальнейшие ошибки в скрипте не видны коду возврата"),
    (re.compile(r"pytest.*--exitfirst.*\|\|"), "код возврата pytest поглощён"),
)

#: Секреты — по форме значения, а не по имени переменной: `API_KEY=` в примере
#: безвреден, а сорокасимвольный ключ в отслеживаемом файле — нет.
SECRETS = (
    (re.compile(r"\b(?:ghp|gho|ghs)_[A-Za-z0-9]{20,}"), "токен GitHub"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "ключ вида sk-…"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "ключ AWS"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "токен Slack"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "приватный ключ"),
)

TEST_HINTS = ("test", "spec", "_test.", ".test.", "test_")

#: Заглушка, а не ключ. Проверяется САМО ЗНАЧЕНИЕ, а не файл, в котором оно
#: лежит: `AKIAIOSFODNN7EXAMPLE` — канонический пример из документации AWS, а
#: `ghp_xxxxxxxxxxxx` — то, что человек пишет в README. Инструмент, краснеющий
#: на документации, выключают целиком, и вместе с ним пропадают настоящие
#: находки.
_PLACEHOLDER_WORD = re.compile(
    r"example|sample|placeholder|dummy|fake|redacted|your[-_]?key|пример", re.I)
_REPEATED = re.compile(r"(.)\1{5,}")


def looks_like_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER_WORD.search(value) or _REPEATED.search(value))


def walk(root: Path, limit: int = 4000) -> list:
    out = []
    for p in root.rglob("*"):
        if len(out) >= limit:
            break
        if not p.is_file():
            continue
        if any(part in SKIP for part in p.parts):
            continue
        if p.stat().st_size > 400_000:
            continue
        out.append(p)
    return out


def _text(p: Path) -> str:
    try:
        return p.read_text("utf-8", errors="replace")
    except OSError:
        return ""


def _hit(p: Path, root: Path, text: str, rx, skip_placeholders: bool = False):
    """Первое совпадение как «файл:строка», либо None.

    `skip_placeholders` пропускает значения-заглушки: см. `looks_like_placeholder`.
    """
    for n, line in enumerate(text.splitlines(), 1):
        m = rx.search(line)
        if not m:
            continue
        if skip_placeholders and looks_like_placeholder(m.group(0)):
            continue
        return f"{p.relative_to(root)}:{n}"
    return None


def test_command(root: Path) -> dict:
    """Есть ли способ узнать, что код работает."""
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            scripts = json.loads(_text(pkg)).get("scripts", {})
        except ValueError:
            return {"level": WARN, "what": "package.json не разобран",
                    "where": "package.json"}
        if "test" in scripts:
            return {"level": OK, "what": f"npm test → {scripts['test']}",
                    "where": "package.json"}
        return {"level": FAIL, "what": "в package.json нет скрипта test — "
                "проверять работу нечем, и «готово» будет означать «я посмотрел»",
                "where": "package.json"}
    # Список маркеров намеренно широкий: узкий давал FAIL на репозитории с
    # тысячей зелёных тестов, потому что тот держит настройки в conftest.py.
    # Ложный провал здесь дороже пропуска — он учит не верить инструменту.
    for marker in ("pytest.ini", "pyproject.toml", "tox.ini", "Makefile",
                   "setup.cfg", "tests/conftest.py", "conftest.py",
                   "jest.config.js", "vitest.config.ts", "go.mod", "Cargo.toml"):
        if (root / marker).is_file():
            return {"level": OK, "what": f"есть {marker}", "where": marker}
    return {"level": FAIL, "what": "команда тестов не найдена — "
            "зелёного прогона в этом проекте не существует", "where": "."}


def has_tests(files: list, root: Path) -> dict:
    hits = [p for p in files
            if any(h in p.name.lower() for h in TEST_HINTS)
            and p.suffix in (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs")]
    if hits:
        return {"level": OK, "what": f"тестовых файлов: {len(hits)}",
                "where": str(hits[0].relative_to(root))}
    return {"level": FAIL, "what": "ни одного тестового файла — "
            "команда тестов может возвращать ноль, ничего не проверив",
            "where": "."}


def empty_is_pass(files: list, root: Path) -> list:
    out = []
    for p in files:
        if p.suffix not in (".yml", ".yaml", ".sh", ".json", ".toml", ".mk") \
                and p.name not in ("Makefile", "package.json"):
            continue
        t = _text(p)
        for rx, why in EMPTY_PASS:
            where = _hit(p, root, t, rx)
            if where:
                out.append({"level": FAIL, "what": why, "where": where})
    return out


#: Где секретоподобная строка почти всегда является фикстурой. Понижаем до
#: предупреждения, а не молчим: настоящий ключ в тестах — тоже ключ, просто
#: обнаруживается он не здесь, а сверкой по ЗНАЧЕНИЮ (см. tools/sync_public.py).
FIXTURE_PATHS = re.compile(r"(?:^|/)(?:tests?|fixtures?|examples?|docs?|spec)/", re.I)


def secrets(files: list, root: Path) -> list:
    out = []
    for p in files:
        rel = str(p.relative_to(root))
        t = _text(p)
        for rx, why in SECRETS:
            where = _hit(p, root, t, rx, skip_placeholders=True)
            if where:
                fixture = bool(FIXTURE_PATHS.search("/" + rel))
                # Значение НЕ печатается никогда — ни в отчёте, ни в JSON.
                out.append({
                    "level": WARN if fixture else FAIL,
                    "what": (f"{why} — похоже на фикстуру, но проверь глазами"
                             if fixture else f"{why} в отслеживаемом файле"),
                    "where": where})
    return out


def env_ignored(root: Path) -> dict:
    gi = root / ".gitignore"
    if not (root / ".env").is_file():
        return {"level": OK, "what": ".env отсутствует", "where": "."}
    if gi.is_file() and re.search(r"^\s*\.env\b", _text(gi), re.M):
        return {"level": OK, "what": ".env закрыт от коммита", "where": ".gitignore"}
    return {"level": FAIL, "what": ".env есть, а в .gitignore не закрыт — "
            "секреты уедут в историю, и удалить их оттуда уже нельзя",
            "where": ".gitignore"}


def mutations_registered(root: Path) -> dict:
    """Зелёный прогон говорит, сколько тестов выполнилось. Мог ли хоть один
    упасть — не говорит никогда, и разница видна только при поломке."""
    f = root / ".superstack" / "mutations.json"
    if not f.is_file():
        f = root / "tests" / "mutations.json"
    if not f.is_file():
        return {"level": WARN, "what": "ни одной зарегистрированной поломки — "
                "«тесты держат» здесь никем не измерено",
                "where": ".superstack/mutations.json"}
    try:
        n = len(json.loads(_text(f)).get("mutations", []))
    except ValueError:
        return {"level": WARN, "what": "набор поломок не разобран",
                "where": ".superstack/mutations.json"}
    if n == 0:
        return {"level": WARN, "what": "набор поломок пуст — это «не проверяли», "
                "а не «тесты крепкие»", "where": ".superstack/mutations.json"}
    return {"level": OK, "what": f"зарегистрированных поломок: {n}",
            "where": ".superstack/mutations.json"}


def gate_is_code(root: Path, files: list) -> dict:
    """Кто решает «готово»: код возврата или человек глазами."""
    for p in files:
        rel = str(p.relative_to(root))
        if rel.startswith(".github/workflows/") or rel.endswith(("verify.sh", "gate.sh")) \
                or rel == ".claude/settings.json":
            t = _text(p)
            if re.search(r"\btest\b|pytest|npm (?:run )?test|exit \d", t):
                return {"level": OK, "what": "решение о готовности опирается на "
                        "код возврата", "where": rel}
    return {"level": WARN, "what": "проверка не привязана ни к CI, ни к хуку — "
            "«готово» решается глазами, и решение не переживёт усталости",
            "where": "."}


def run(root: Path) -> dict:
    files = walk(root)
    findings = [test_command(root), has_tests(files, root), env_ignored(root),
                mutations_registered(root), gate_is_code(root, files)]
    findings += empty_is_pass(files, root)
    findings += secrets(files, root)
    fails = [f for f in findings if f["level"] == FAIL]
    warns = [f for f in findings if f["level"] == WARN]
    return {"root": str(root), "checked_files": len(files),
            "findings": findings, "fails": len(fails), "warns": len(warns),
            "status": "fail" if fails else ("warn" if warns else "pass")}


def human(v: dict) -> str:
    head = {"pass": "ПРОЕКТ ЗДОРОВ", "warn": "ЕСТЬ ЧТО ПОДТЯНУТЬ",
            "fail": "ЗЕЛЁНОМУ ЗДЕСЬ ВЕРИТЬ НЕЛЬЗЯ"}
    lines = [f"{head[v['status']]}  (файлов осмотрено: {v['checked_files']})"]
    for f in v["findings"]:
        if f["level"] == OK:
            continue
        lines.append(f"  {f['level']}  {f['where']} — {f['what']}")
    if v["status"] == "pass":
        lines.append("  всё, что этот осмотр умеет проверить, в порядке")
    return "\n".join(lines)


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def _utf8_stdio() -> None:
    """Печать по-русски не должна зависеть от локали.

    В окружении без UTF-8 — минимальный контейнер, cron с урезанным env,
    `PYTHONCOERCECLOCALE=0` — кодировка вывода оказывается ascii, и первый же
    русский символ роняет инструмент целиком. Человек получает не «проверка не
    прошла», а трейсбек вместо любого ответа. На macOS по умолчанию это не
    воспроизводится: интерпретатор сам приводит локаль C к C.UTF-8.
    """
    for поток in (sys.stdout, sys.stderr):
        кодировка = (getattr(поток, "encoding", "") or "").lower().replace("-", "")
        if кодировка != "utf8" and hasattr(поток, "reconfigure"):
            поток.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    _utf8_stdio()
    halt_if_paused()
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(argv) != 1:
        print("вызов: project_doctor.py <корень проекта> [--json]", file=sys.stderr)
        return 3
    root = Path(argv[0]).expanduser().resolve()
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {root}", file=sys.stderr)
        return 2
    v = run(root)
    if "--json" not in sys.argv[1:]:
        print(human(v), file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return 1 if v["status"] == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
