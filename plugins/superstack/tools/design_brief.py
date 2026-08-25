#!/usr/bin/env python3
"""SUPERSTACK — промпт для внешнего дизайн-инструмента.

Зачем это отдельный шаг, а не «пусть строитель сверстает».

Живой случай, из которого инструмент и вырос. Направление выбрали одним словом
(«тёмная галерея»), перевели в восемь проверяемых величин и отдали строителю.
Он сверстал — аккуратно, по всем числам, — и человек увидел ГОТОВЫЙ САЙТ, ни
разу не увидев дизайна. Палитру `#0a0a0c / #f2f2f4 / #86868f / #6355d9` выбрал
строитель, ни с кем не согласовав. Она случайно вышла похожей на ориентир, и
это удача, а не работа.

Восемь величин — это КРИТЕРИИ ПРИЁМКИ, а не дизайн. Они говорят «не больше
четырёх цветов» и молчат о том, какие это цвета и почему.

Отсюда порядок, который этот инструмент обслуживает:

  1. агент опрашивает человека и СОБИРАЕТ ПРОМПТ — здесь;
  2. человек уходит с этим промптом во внешний дизайн-инструмент и работает
     там: смотрит варианты, спорит, выбирает;
  3. возвращается с результатом — палитрой, шрифтами, композицией;
  4. агент переводит результат в токены и критерии и только теперь строит,
     опираясь на дизайн-скиллы.

Почему промпт собирается кодом, а не пишется каждый раз заново. Он состоит из
семи обязательных частей, и пропуск любой стоит целого захода во внешний
инструмент: без ролей цветов вернётся палитра без ролей, без списка экранов —
красивый одинокий экран, без «чего не делать» — карусели и градиенты.

Что здесь принуждается:

  · ОРИЕНТИР ЧЕЛОВЕКА ИДЁТ ДОСЛОВНО. Пересказ «хочет чего-то строгого» теряет
    единственное, что известно о его вкусе.
  · ЭКРАНЫ ПЕРЕЧИСЛЯЮТСЯ ПОИМЁННО. Дизайн одного экрана не масштабируется на
    продукт: состояния и пустоты появляются только там, где их назвали.
  · ЧТО ПРИНЕСТИ ОБРАТНО — ЧАСТЬ ПРОМПТА. Иначе человек вернётся со
    скриншотом, из которого нельзя достать ни одного значения.

  python3 design_brief.py <файл.json> --product "..." --reference "..." \
      --direction "тёмная галерея" --screen "начало" --screen "работы" \
      --feeling "..." --avoid "..."
  python3 design_brief.py <файл.json> --source <url> --source <url>
                                                 отметить сверку с документацией
  python3 design_brief.py <файл.json> --show      готовый промпт для человека
  python3 design_brief.py <файл.json> --show --stage system|screens

  код 0 — промпт собран, 1 — неполон, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

#: Правила сборки дизайн-системы — из чек-листа Claude Design, а не из общих
#: соображений. Лежат данными рядом, потому что меняются вместе с инструментом,
#: а не вместе с кодом.
RULES_FILE = Path(__file__).resolve().parent.parent / "data" / "design-system-rules.json"


def rules() -> dict:
    try:
        return json.loads(RULES_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def evaluative_words(*texts: str) -> list:
    """Оценочные слова в описании. Их наличие — отказ, а не замечание.

    «Сделай стильнее», «премиальнее», «современнее» — неоперациональные
    указания: модель не знает, что с ними делать, и заполняет пробел
    усреднённым паттерном из обучения. Требование, сформулированное так,
    гарантированно возвращается однотипным — то есть ровно тем, против чего
    дизайн-систему и заводят.
    """
    bad = (rules().get("forbidden_words") or {}).get("words") or []
    hay = " ".join(t.lower() for t in texts if t)
    return sorted({w for w in bad if re.search(rf"\b{re.escape(w)}", hay)})


#: Части промпта, без которых заход во внешний инструмент вернёт не то.
#: Каждая закрывает конкретный способ вернуться с бесполезным результатом.
REQUIRED = {
    "product": "что это за продукт — одной фразой, словами человека",
    "reference": "ориентир человека ДОСЛОВНО (что он назвал сам)",
    "direction": "выбранное направление",
    "screens": "экраны поимённо — дизайн одного не масштабируется на продукт",
    "feeling": "что человек должен почувствовать, зайдя",
    "audience": "кто эти люди — формат промпта требует аудиторию наравне с целью",
}

EMPTY = {"schema": "superstack.design-brief.v1", "product": "", "reference": "",
         "direction": "", "screens": [], "feeling": "", "audience": "",
         "avoid": [], "constraints": [], "docs": {}, "updated": None}

#: Сколько дней сверка с документацией дизайн-инструмента считается свежей.
#: Тридцать — не осторожность, а наблюдение: сам инструмент вышел исследовательским
#: превью и меняется месяцами, а не годами. Промпт, собранный по памяти модели,
#: выглядит точно так же, как собранный по документации, — и это единственная
#: причина, по которой требование живёт в КОДЕ, а не в инструкции агенту.
DOCS_FRESH_DAYS = 30


def load(path: Path) -> dict:
    if not path.is_file():
        return json.loads(json.dumps(EMPTY))
    try:
        d = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return json.loads(json.dumps(EMPTY))
    for k, v in EMPTY.items():
        d.setdefault(k, json.loads(json.dumps(v)))
    return d


def save(path: Path, data: dict, now: str = None) -> None:
    from datetime import datetime, timezone
    data["updated"] = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def docs_age_days(data: dict, now=None) -> "int | None":
    """Сколько дней назад сверялись с документацией. None — не сверялись."""
    from datetime import datetime, timezone
    d = (data.get("docs") or {}).get("checked")
    if not d:
        return None
    try:
        when = datetime.fromisoformat(d)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - when).days


def docs_stale(data: dict, now=None) -> "str | None":
    """Причина, по которой промпту нельзя доверять. None — можно.

    Проверяется ДО показа промпта человеку: он уносит его во внешний
    инструмент, тратит там время и возвращается с результатом, собранным по
    устаревшему представлению. Обнаружить это можно только вернувшись.
    """
    age = docs_age_days(data, now)
    if age is None:
        return ("с документацией дизайн-инструмента не сверялись — промпт "
                "собран по памяти модели, а она устаревает молча")
    if age > DOCS_FRESH_DAYS:
        return (f"сверка с документацией была {age} дней назад при пределе "
                f"{DOCS_FRESH_DAYS} — инструмент меняется быстрее")
    if not (data.get("docs") or {}).get("sources"):
        return "сверка отмечена, но источники не названы — проверить нечем"
    return None


def missing(data: dict) -> list:
    """Чего не хватает, чтобы поход во внешний инструмент имел смысл."""
    out = []
    for key, why in REQUIRED.items():
        v = data.get(key)
        if not v or (isinstance(v, list) and not v):
            out.append(f"{key}: {why}")
    return out


def render_system(data: dict) -> str:
    """Промпт ПЕРВОГО этапа — создать дизайн-систему.

    Порядок «сначала система, потом экраны» взят из того, как устроен сам
    инструмент: он строит систему из загруженного контекста один раз, и все
    последующие проекты наследуют её цвета, шрифты и компоненты автоматически.
    Начав с экрана, человек получает красивый экран и никакой системы — второй
    экран приедет другим, и сшивать их придётся руками.

    Формат описания взят оттуда же: цель, макет, контент, аудитория. «Сделай
    страницу цен» работает хуже, чем «страница цен, три колонки, переключатель
    месяц/год, для SaaS управления проектами, аудитория — малые агентства».
    """
    avoid = "\n".join(f"- {a}" for a in data["avoid"]) or "- ничего дополнительно"
    limits = "\n".join(f"- {c}" for c in data["constraints"]) or "- нет"
    return f"""Собери мне дизайн-систему для проекта.

**Цель:** {data['product']}

**Аудитория:** {data['audience'] or 'опиши сам, исходя из продукта'}

**На что хочу быть похожим:** {data['reference']}

**Направление:** {data['direction']}

**Что человек должен почувствовать, зайдя:** {data['feeling']}

**Чего точно не надо:**
{avoid}

**Ограничения, которые уже приняты:**
{limits}

Собери из этого систему. Мне нужно, чтобы каждое правило было НАЗВАНО: то,
чего в правилах нет, потом будет придумано заново и по-разному.

1. **Имя у каждого компонента.** Дальше я буду ссылаться на них по именам в
   задачах — «кнопка primary», «карточка работы», — и без имён вместо готового
   компонента возьмётся произвольный.
2. **Палитра — фиксированная**, шестнадцатеричными значениями, с ролями: фон,
   основной текст, приглушённый текст, акцент. Акцент один и только на главном
   действии.
3. **Типографика:** семейство, начертания и правила их применения; шкала
   размеров — не больше пяти ступеней, в пикселях или rem.
4. **Layout:** базовый шаг отступов, сетка, структура страницы и правила
   расстояний. Отдельно: между блоками одного цвета — один отступ, а не два
   с обеих сторон.
5. **Кнопки — параметрами, числами.** Высота, внутренние отступы, радиус,
   толщина рамки. Без чисел они приедут произвольными.
6. **Набор компонентов:** кнопки, поля ввода, карточки, навигация, заголовки,
   состояния загрузки и скелетоны, alert, пустые состояния. Каждый — во всех
   состояниях, которые у него бывают: обычное, наведение, фокус, нажатие,
   недоступное.

Если что-то в моём описании противоречит само себе — скажи прямо, а не выбирай
молча."""


def render_screens(data: dict) -> str:
    """Промпт ВТОРОГО этапа — экраны поверх готовой системы."""
    screens = "\n".join(f"- {s}" for s in data["screens"])
    return f"""Теперь по этой системе собери экраны.

**Продукт:** {data['product']}
**Аудитория:** {data['audience'] or 'та же'}

**Экраны:**
{screens}

Для каждого экрана покажи: что где стоит, что крупное и что мелкое, и как он
выглядит на узком экране 320px — что переносится, что скрывается, что остаётся.

Отдельно — **пустые состояния с настоящим текстом**, который там будет написан.
У меня пока нет ни фотографий, ни цен, ни адреса: эти места должны выглядеть
намеренно, а не сломанно.

Верни всё одним куском — я принесу его целиком туда, где будут верстать.

Правки я буду делать кнопкой Remix, не командой."""


def render(data: dict) -> str:
    """Оба этапа подряд — для проверки и для тех, кто хочет видеть всё сразу."""
    return render_system(data) + "\n\n---\n\n" + render_screens(data)


def _flag_all(argv: list, name: str) -> list:
    out = []
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            out.append(argv[i + 1])
    return out


def _flag(argv: list, name: str, default: str = "") -> str:
    v = _flag_all(argv, name)
    return v[0] if v else default


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
    if not plain:
        print("вызов: design_brief.py <файл.json> [--product ...] | --show",
              file=sys.stderr)
        return 3
    path = Path(plain[0])
    data = load(path)

    for key in ("product", "reference", "direction", "feeling", "audience"):
        v = _flag(argv, f"--{key}")
        if v:
            data[key] = v
    for key, flag in (("screens", "--screen"), ("avoid", "--avoid"),
                      ("constraints", "--constraint")):
        vals = _flag_all(argv, flag)
        if vals:
            data[key] = vals

    src = _flag_all(argv, "--source")
    if src or "--docs-checked" in argv:
        from datetime import datetime, timezone
        data["docs"] = {"checked": _flag(argv, "--docs-checked") or
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "sources": src}

    gaps = missing(data)
    stale = docs_stale(data)
    vague = evaluative_words(data.get("feeling", ""), data.get("direction", ""),
                             data.get("product", ""))
    if "--show" in argv:
        if vague:
            print("ПРОМПТ НЕОПЕРАЦИОНАЛЕН: оценочные слова — "
                  + ", ".join(f"«{w}»" for w in vague), file=sys.stderr)
            print("  модель не знает, что с ними делать, и заполнит пробел "
                  "усреднённым паттерном; замени на "
                  + ((rules().get("forbidden_words") or {}).get("instead")
                     or "конкретные величины"), file=sys.stderr)
            return 1
        if stale:
            print(f"ПРОМПТ НЕ ПРОВЕРЕН: {stale}", file=sys.stderr)
            print("  сначала прочитай документацию дизайн-инструмента и запиши "
                  "сверку: --source <url> --source <url>", file=sys.stderr)
            return 1
        if gaps:
            print("ПРОМПТ НЕПОЛОН — заход во внешний инструмент вернёт не то:",
                  file=sys.stderr)
            for g in gaps:
                print(f"  ! {g}", file=sys.stderr)
            return 1
        stage = _flag(argv, "--stage", "system")
        if stage not in ("system", "screens", "both"):
            print(f"НЕ УДАЛОСЬ: нет этапа «{stage}» — есть system, screens, both",
                  file=sys.stderr)
            return 3
        # Умолчание — ПЕРВЫЙ этап, а не оба сразу. Человек, получивший оба
        # промпта разом, несёт во внешний инструмент второй вместе с первым и
        # получает экраны раньше системы: тогда система строится под уже
        # нарисованный экран, а не экраны под систему.
        print({"system": render_system, "screens": render_screens,
               "both": render}[stage](data))
        return 0

    save(path, data)
    if gaps:
        print("СОБИРАЮ: ещё нужно", file=sys.stderr)
        for g in gaps:
            print(f"  ! {g}", file=sys.stderr)
    else:
        print("ПРОМПТ ГОТОВ — покажи человеку командой --show", file=sys.stderr)
    print(json.dumps({"ready": not gaps and not stale and not vague,
                      "vague_words": vague, "missing": gaps,
                      "docs_stale": stale,
                      "docs_age_days": docs_age_days(data)},
                     ensure_ascii=False, indent=1))
    # Код записи отделён от вердикта о полноте: сборка идёт по частям, и
    # ненулевой код на каждом шаге приучил бы его не читать.
    return 0


if __name__ == "__main__":
    sys.exit(main())
