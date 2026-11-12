[🇷🇺 Русский](README.md) · 🇬🇧 English

# SUPERSTACK

**Describe a product in your own words. Get a version that was checked before anyone called it done.**

<p align="center"><img src="assets/superstack-hero.jpg" alt="SUPERSTACK, the build is checked before it is called done" width="100%"></p>

[![version](https://img.shields.io/badge/version-0.14.0-1f6feb)](.claude-plugin/marketplace.json)
[![tests](https://img.shields.io/badge/tests-2538-2ea043)](tests)
[![mutations](https://img.shields.io/badge/mutations-724-8957e5)](tests/mutations.json)
[![python](https://img.shields.io/badge/python-3.8%2B-3776ab)](#requirements-and-platforms)
[![dependencies](https://img.shields.io/badge/dependencies-none-555555)](#under-the-hood)
[![license](https://img.shields.io/badge/license-MIT-97ca00)](LICENSE)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-d97757)](#quick-start)

2538 tests · 724 mutations, all caught · 492 mechanisms.

These are project numbers. Tests check the code. A mutation breaks the code on
purpose, and the test suite has to notice. A mechanism is a separately checkable
part of the plugin.

## What you get

SUPERSTACK is a plugin for Claude Code. You write one sentence, for example
*"I want a site where people book a haircut"*, and you get a working version.
At the end comes a report with a **How to open it** section. It holds an
address, not a status.

The plugin takes six steps between the sentence and the address.

1. It writes your sentence down verbatim and asks only what you alone know.
   Who books, what happens on a double booking, what the client sees afterwards.
2. It turns the answers into a requirements list and a description of how the
   product should work. It draws the interface on a canvas and carries colors
   and sizes into the code as exact values.
3. It shows a plan. The first line of code appears after your yes.
4. It hands tasks to a coding agent one at a time. The agent does each one from
   a clean slate, without the tail of earlier conversations.
5. It runs the project's own checks. Until they pass, the work does not count as
   finished.
6. It asks a separate agent to read only your original request and the changes.
   The agent answers one question. Is this what was asked for?

The `/superstack` command checks how your Claude Code is set up, separately, and
changes nothing without your consent.

## Quick start

### Without opening a terminal

The plugin runs only in Claude Code. Paste this text into your Claude Code and
it does the rest itself.

```
Install SUPERSTACK for me. Run exactly these three commands in the terminal, one
at a time, and do not stop if the first one comes back red:

CLAUDECODE= claude plugin marketplace add edvardgrishin27/superstack-plugin
CLAUDECODE= claude plugin marketplace update superstack
CLAUDECODE= claude plugin install superstack@superstack

If the first command answers "Marketplace 'superstack' is already installed",
that is not an error, keep going. Always run the second command, it refreshes
the package list. The CLAUDECODE= prefix is required. Install nothing else.
When you are finished, say in one line that it is done and tell the person to
quit the whole application (Cmd+Q on a Mac) and open it again. A new chat in the
same window will not do, plugins are read at startup.
```

Tested on a clean computer and on top of an earlier install.

<details id="inside-this-text">
<summary>What is inside this text</summary>

The first command adds the plugin catalog. If it is already added, the command
exits with code 1, and that is normal. The second command refreshes the package
list. Without it, a computer with an earlier install takes the old list from its
cache and installs the previous version while everything looks successful. The
`CLAUDECODE=` prefix is needed because `claude` refuses to start from inside
another session. Both sequences were run against an isolated config
(`CLAUDE_CONFIG_DIR`).

</details>

### From a terminal

```bash
claude plugin marketplace add edvardgrishin27/superstack-plugin
claude plugin marketplace update superstack
claude plugin install superstack@superstack
```

No prefix is needed here, you are already outside a session. If the catalog is
already added, the first line exits with code 1. That is normal, and
the second line fixes the rest.

Quit Claude Code completely (Cmd+Q on a Mac) and open it again. After that,
write what you need in plain words, no special words to learn. A hint on where
to start arrives together with the answer to your first sentence.

## Why you can trust it

**An exit code closes the turn.** The end-of-turn check
(`hooks/verify-gate.py`) runs the project's own commands. While they return
non-zero the turn stays open, and no wording in the chat changes that. When a
turn is closed without a fresh check, the check says so aloud, once per session.

**"Proven" comes from a run record.** The check leaves a record with the exit
code, the test count, the time and a fingerprint of the project. Without a fresh
green record carrying the same fingerprint, the report says "on its word".

**Blind acceptance.** The agent in `agents/blind-acceptance.md` gets your
original request and the changes, with no description and no plan. It compares
the result with the request, and it has no plan to agree with.

**"Put it back" in one sentence.** `tools/checkpoint.py` snapshots the product
folder before and after every edit. `/oops` returns the state from before the
edit and shows what will come back first. No Claude Code setting is deleted.
Removals go to quarantine, and a copy is written before the first change.

**Answers are written without jargon.** The list of banned words lives in
`tools/plain_ru.py`, and a text containing one fails the check.

**The plugin holds itself to a bar.** `tools/gauntlet.py` runs nine gates. The
strictest are the mutation gates. Each of the **724 registered mutations**
breaks the code on purpose and must turn the test suite red. A check that cannot
fail does not count as a check.

**No third-party dependencies.** Python 3 and the standard library. Nothing to
install, nothing to audit downstream.

## Commands

There are seven. You do not have to type them, each also answers to plain words.

| Command | Say it in words | What it does |
|---|---|---|
| `/go` | "I want a booking site", "build a bot that…" | Carries a new product or a large part from a sentence to a checked version. Requirements list, spec, canvas design, a plan with your yes, builders at work, exit-code check, security, blind acceptance, project memory, a "How to open it" report |
| `/change` | "fix the button", "change the text", "add a field" | One small edit to something that already works. Snapshot before, edit by a builder, check, snapshot after. If a snapshot fails, it asks "this cannot be undone, continue?" and waits for a yes |
| `/what` | "where am I", "what next", "where did I stop" | One line of state and one next step, which a tool reads from the project files |
| `/stop` | "stop", "switch superstack off", and back with "switch it on" | A pause from one sentence, no terminal commands. While the brake file exists, no new step starts. It does not interrupt work already running |
| `/oops` | "undo", "put it back" | Returns the project folder to its state before the edit, or the Claude Code settings the system changed itself. It shows what will come back first and rolls back after your consent. If there is nothing to return to, it answers "nowhere to go back to" |
| `/fix` | "it does not work", "nothing happens", "error again" | Checks the links of the system itself in order and stops at the first broken one. Is Python there, are the plugin files in place, are the checks on in this folder, is a pause active, can the state folder be written. It answers with one link and one action |
| `/superstack` | "check my install", "what do I have installed", "why does the agent behave like this" | A machine profile. Probes read settings, count skills and checks, look at memory and connections. The verdict opens as a page where every finding carries its origin, measured, derived or ambiguous |

## How it works

`/go` runs in phases. Code is written in the fourth. Every phase before it works
out **what** to build, every phase after it proves that **this** is what got
built.

```mermaid
flowchart LR
    A["Your sentence"]
    B["0 Set up"]
    C["1 Understand · G1"]
    D["2 Spec · G2"]
    E["2.5 Design"]
    F["3 Plan · G3, you approve"]
    G["4 Build"]
    H["5 Verify · non-zero code holds the turn"]
    I["5.4 Security"]
    J["5.5 By eye"]
    K["6 Blind acceptance · G4"]
    L["7 Project memory"]
    M["8 Report · How to open it"]
    A --> B --> C --> D --> E --> F --> G --> H --> I --> J --> K --> L --> M
```

A phase may be skipped, never silently. When design or security is not needed,
one line says so.

<p align="center"><img src="assets/panel-en.jpg" alt="The SUPERSTACK progress page, the main number counts only what the machine verified" width="100%"></p>

The progress panel shows only what the machine verified as its main number. The
"on the assistant's word" figure stands apart and is never added to it. This is
a snapshot of the real page on a demo state.

## Under the hood

Python 3 and the standard library. 7 skills, 3 agents and three automatic checks
in a session. They fire at start, before context compaction and at the end of a
turn. Every mechanism sits in a file you can open.

| Mechanism | Where it lives | What it does |
|---|---|---|
| Verification closes the turn | `hooks/verify-gate.py`, `tools/verify.py` | "Done" means exit code zero, anything else keeps the turn open |
| Blind acceptance | `agents/blind-acceptance.md`, `tools/blind_accept.py` | A verdict on the original request and the diff, with no plan |
| Snapshots and rollback | `tools/checkpoint.py`, `tools/oops.py` | Every stage boundary is a return point |
| Plain-Russian dictionary | `tools/plain_ru.py` | Jargon in text for a person is caught by code |
| Progress panel | `tools/live_panel.py`, `tools/progress.py` | A page on `localhost:8787` with an estimate of how much is left |
| Turn notification | `tools/notify.py` | You are told when the turn is back with you |
| Builder watchdog | `tools/watchdog.py`, `tools/stall.py` | A frozen agent is noticed by the system |
| Design as values | `tools/design_pull.py` | Canvas values go into code as they are, contrast below `4.5:1` is refused |
| Security pack | `tools/security_pack.py`, `tools/data_rollback.py` | For products that reach people. The data rollback path is declared as commands |
| Builder contracts | `tools/contract.py` | "Done" with red tests is caught by code |
| Brake file | `tools/pause.py`, `~/.claude/superstack/PAUSE` | No new step starts while the file exists |
| Settings are not deleted | `tools/apply.py` | An edit is reversible, secrets are not copied even into the backup |

### The bar the plugin holds itself to

Nine gates, run by `tools/gauntlet.py`. The numbers at the top of this page are
measured. The release script (`tools/sync_public.py`) rewrites
them in both language versions.

| Gate | What it proves |
|---|---|
| **suite** | the whole test suite is green |
| **hermetic** | the same suite is green under any `HOME` |
| **mutations** | 724 registered mutations, each must turn the suite red |
| **wiring** | every tool is reachable from a skill or an automatic check |
| **rules** | the rule files are intact |
| **manifest** | marketplace entries match the tree, versions agree, schemas are valid |
| **plan** | every mechanism on the map is in place, and most are checked by a real break |
| **reconciled** | the map was read against the plan, and the plan has not changed since |
| **weight** | every skill fits under the size ceiling |

## How it compares to the sets next to it

People ask how it differs before they install. The SUPERSTACK cells point to a
file you can open. The data on the others comes from their code and descriptions
on 3 September 2026. Where a check was not possible, the cell says so.

| What is compared | SUPERSTACK | Superpowers | Skills Matt Pocock |
| --- | --- | --- | --- |
| Who decides "done" | **an exit code**, non-zero means the turn is not closed (`hooks/verify-gate.py`) | the model, from rule text | the model, from rule text |
| Breaks that must fail the suite | **yes**, 724 registered (`tests/mutations.json`) | no | no |
| Tests for the tool itself | **yes**, suite green under any `HOME` (`tools/gauntlet.py`) | 44 files | not found |
| Blind acceptance | **yes**, the checker gets only the request and the changes (`tools/blind_accept.py`) | no, it is given the spec | no, it is given the spec |
| Numbers on the panel | **from files**, "on its word" kept apart (`tools/progress.py`) | no panel | no panel |
| "Put it back" | **yes**, in one sentence, a snapshot at every stage (`tools/checkpoint.py`) | a branch, no command | no |
| Stop in one word | **yes** (`tools/pause.py`) | no | no |
| Your requirements list | **yes**, verbatim and numbered, dropped only with your own quote (`tools/manifest.py`) | plan tasks | numbered tickets |
| How it starts | one plugin install, then seven commands and plain words | a plugin, skills switch on by themselves | a plugin or files |

## What SUPERSTACK never does

Only what a file backs is listed here. What the lead (the main agent of the
session) follows as a working rule is called a rule.

- **Does not close a turn while the checks are red.** While the exit code is
  non-zero the turn stays open. If a turn is closed without a fresh check, the
  check says so aloud (`hooks/verify-gate.py`, `tools/verify.py`).
- **Does not write code without your yes on the plan.** This is a working rule
  the lead follows (`skills/go/phases/3-план.md`).
- **Does not drop your requirement without your words.** Code checks they are in
  the original request (`tools/manifest.py`).
- **Does not delete Claude Code settings.** Quarantine instead of deletion, a
  copy before the first edit (`tools/apply.py`). "Put it back" returns the
  product folder.
- **Does not copy your keys.** A backup keeps the file without the password
  value, and the value never reaches the log (`tools/apply.py`, `tools/log.py`).
- **Does not start new steps after "stop".** The brake is a file on disk.
  It does not interrupt work already running (`tools/pause.py`).
- **Does not switch on the forbidden-command list without your consent**
  (`tools/deny_list.py`).
- **Does not print a number it did not measure.** The progress page counts from
  files, and the figures on this page are rewritten at release time
  (`tools/progress.py`, `tools/sync_public.py`).

## Requirements and platforms

- **Claude Code**, desktop app or terminal. `claude plugin …` exists nowhere
  else.
- **Python 3.8 or newer**, whatever the system already has. The floor comes from
  the syntax in the code, which uses assignment expressions (`:=`) and nothing
  newer. No third-party packages.
- **pytest**, if you want to run the test suite.
- **macOS and Linux** are verified end to end.
- **Windows**. The code paths are there, but no live run has ever been done.
- **Language.** The product answers in the language you write in. Messages
  printed straight by the hooks and by several tools are in Russian.

## Status

Version 0.14.0. The release script checks the nine-gate bar on the same tree
this text ships from.

## Contributing

One order for everything new.

> **mechanism → map → test → mutation → code.**

A mechanism is done when it is recorded in `data/plan-coverage.json` with an
evidence file and a literal substring of the executable text, has a test in
`tests/`, and has a registered mutation in `tests/mutations.json` that
turns the suite red.

```bash
python3 -m pytest tests/ -q          # the suite
python3 tools/gauntlet.py --quick    # fast loop, no mutations or hermetic gate
python3 tools/gauntlet.py            # everything, about two hours
```

`--quick` returns code 2 on purpose. Two gates do not run in it, and "skipped"
must look different from "passed".

## License

MIT, see [LICENSE](LICENSE).

<details>
<summary>If you installed before version 0.3.0</summary>

Before version 0.3.0 the system shipped as seven packages (`superstack-core`,
`-install`, `-spec`, `-guard`, `-build`, `-brain`, `-control`). It is one package
now, and the old seven must be removed. Otherwise two copies of the same skills
sit side by side, and either one can be picked up by description.

In the desktop app open **settings → plugins**, remove each of the seven old
ones, then press **update** on the `superstack` marketplace and install
`superstack`. The order matters. Without the marketplace update the old list
comes from the cache, where there are seven packages, and the previous version
gets installed, looking the same as the new one.

**The `/plugin` slash command will not work.** It exists only in terminal Claude
Code. The desktop app answers "isn't available in this environment". Verified on
the desktop.

</details>
