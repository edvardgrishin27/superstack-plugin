#!/usr/bin/env python3
"""Подставной запуск для теста приёмки волны.

  fake_run.py exit <код>
  fake_run.py gate <имя ворот> <статус> <выжило> <выполнено> [ещё ворота...]
  fake_run.py full <done: 0|1>
  fake_run.py merge <статус: pass|fail|unknown> <папка>

Печатает то, что напечатала бы планка, и возвращает заданный код.
"""
import json
import sys


def main(a):
    kind = a[0]
    if kind == "exit":
        print("подставной прогон")
        return int(a[1])
    if kind == "gate":
        gate, status, survived, executed = a[1], a[2], int(a[3]), int(a[4])
        g = {"gate": gate, "status": status, "detail": "подставные ворота",
             "survived": [{"id": f"s{i}", "why": "выжила"}
                          for i in range(survived)],
             "executed": executed}
        print(json.dumps({"gates": [g] + [
            {"gate": n, "status": "pass", "detail": ""} for n in a[5:]]}))
        return 0
    if kind == "full":
        done = a[1] == "1"
        print(json.dumps({"done": done, "next": "подставной итог",
                          "gates": []}))
        return 0 if done else 2
    if kind == "merge":
        print(json.dumps({"status": a[1], "detail": "подставное сведение"}))
        return {"pass": 0, "fail": 1}.get(a[1], 2)
    return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
