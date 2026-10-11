# RDS onboarding: from clone to your first pull request

**English** · [简体中文](onboarding.zh-CN.md)

This page is the first-run path. It assumes you have never read the source code, and it takes about fifteen minutes. [CONTRIBUTING.md](../CONTRIBUTING.md) is the long-running reference for how changes are reviewed; this page covers your first change only.

RDS is not published on any package index. Everything below runs from a Git checkout.

## 1. Decide in thirty seconds

RDS (Research Direction Selector) is a local program that preserves the **history of your research decisions**: the goal, the budget you spent, the runs that actually executed, the evidence they produced, and the reasons earlier routes were rejected.

It is for a researcher who works with an AI agent over days or weeks and has lost track of which runs happened, what they cost, and why a promising idea was dropped three sessions ago.

It does not design your experiment, decide your science, or make an agent smarter, and it is not a sandbox for untrusted code. **If your problem does not need a preserved decision history, you do not need RDS.**

Stopping here is a successful outcome, not a failure. Close the tab and keep your current tools.

## 2. Check your environment

### 2.1 Required: Python 3.11 or newer, and Git

```powershell
python -V
git --version
```

Windows only: if the first line fails, the Windows Python launcher usually still finds an installed interpreter:

```powershell
py -3.13 -V
```

Expected output, from the machine this page was written on:

```text
Python 3.13.5
git version 2.51.0.windows.1
```

Any `Python 3.11.x` or newer works. `3.10` and below do not: parts of the code use syntax that older interpreters cannot parse.

If `python` is not recognized, install CPython 3.11 or newer and reopen the shell, or use the `py -3.13` form above. If `git` is not recognized, install Git and reopen the shell. RDS needs no packages beyond the Python standard library, so a virtual environment is optional.

### 2.2 Optional: Rust toolchain and Lean

```powershell
cargo --version
lean --version
```

On a machine without them, PowerShell answers with a localized "not recognized" message and bash answers with `command not found`. Neither is a problem for this page.

What their absence costs you:

| Missing tool | What you cannot run locally | What is unaffected |
| --- | --- | --- |
| `cargo` | The Rust accelerator build and the `native` continuous-integration job, including a compiled-parity test that refuses to run without it | Every Python documentation and command-line change |
| `lean` / `lake` | The `lean` job, the Lean verification tests (`tests/test_rds_lean*.py`) and the statistical-obligation test (`tests/test_rds_statistical_obligations.py`) | Every Python documentation and command-line change |

**Missing Lean or cargo never blocks a documentation pull request.** You do not need to install either one to finish this page.

`pytest` is also not required, and not used. This repository runs its tests with `python -m unittest`.

### 2.3 Get the code

```powershell
git clone https://github.com/kongtou20070406/research-direction-selector.git
cd research-direction-selector
```

Expected: Git prints progress and finishes with a clean exit. The rest of this page assumes your shell is inside that directory.

If the clone fails with a timeout or a reset connection, a proxy is usually intercepting Git. Set it for this shell only, then retry:

```powershell
$env:HTTPS_PROXY = "http://127.0.0.1:7890"
git clone https://github.com/kongtou20070406/research-direction-selector.git
```

In bash the same idea is `export HTTPS_PROXY=http://127.0.0.1:7890`. Replace the port with your own proxy's port.

## 3. Look around without risk

Run these from inside the checkout. They are read-only views: none of them creates a project or starts work.

```powershell
python -B scripts/rds_cli.py --version
```

Expected output, the whole thing:

```text
5.9.0-rc.2
```

That string is the version of the checkout you cloned. A development checkout reports a release candidate such as this one, which is not the same thing as a published release.

```powershell
python -B scripts/rds_cli.py status
```

Expected output when no project has been initialized anywhere above the current directory:

```text
[RDS-REJECT] RDS is not initialized
```

This exits with status `1`. That is the expected answer, not a broken installation: `status` reports that there is no ledger to report on, and the message, not the exit code, is what to read. Exit code `1` here means "no project found".

```powershell
python -B scripts/rds_cli.py project discover
```

Expected output, truncated here for length. The real output is one JSON object on standard output; the closing `[RDS] ...` line is written to standard error.

```text
{
  "status": "NO_PROJECT_FOUND",
  "requested_root": "<your directory>",
  "project_root": null,
  "workflow": {
    "mode": "UNINITIALIZED",
    "advisor": "NOT_PROGRAM_OWNED",
    "ledger": null,
    "default_new_project_mode": "FULL",
    "optional_capabilities_are_not_automatically_enabled": true,
    "continuity": {
      "status": "UNBOUND"
    }
  },
  "next_action": "python -B scripts/rds_cli.py --root '<your directory>' project plan",
  "execution_started": false,
  "search_scope": "REQUESTED_ROOT_AND_ANCESTORS",
  "sibling_projects_searched": false
}
[RDS] mode=UNINITIALIZED advisor=NOT_PROGRAM_OWNED continuity=UNBOUND root=<your directory>
```

`project_root` is `null` because nothing was found, and `execution_started` is `false`.

### 3.1 Read-only, but not side-effect free

**Read this before you use the three commands above to check whether RDS is switched on.**

Each of `--version`, `status` and `project discover` only reads the ledger — the project's durable record of decisions, runs and remaining budget — and does not create a project, but it leaves one invocation count in the usage database. Therefore **you cannot currently use these commands to verify "RDS is off"** — this version has no side-effect-free self-check command. To confirm by hand, inspect whether `.rds/` exists and whether the usage database contains entries.

The usage database lives at `%LOCALAPPDATA%\ResearchDirectionSelector\cli-usage.sqlite3` on Windows, and at `~/.local/state/ResearchDirectionSelector/cli-usage.sqlite3` elsewhere, unless the `RDS_USAGE_DB` environment variable redirects it. `python -B scripts/rds_cli.py usage` prints the recorded daily counts, and that call is counted too, so expect the counters to be non-zero after you have run the commands in this section.

## 4. Run your first real command

Pick a directory that is not the checkout, and that you do not mind having RDS look at. Create it if it does not exist:

```powershell
New-Item -ItemType Directory -Force -Path "$HOME\rds-first-look" | Out-Null
python -B scripts/rds_cli.py --root "$HOME\rds-first-look" project discover
```

In bash:

```bash
mkdir -p ~/rds-first-look
python -B scripts/rds_cli.py --root ~/rds-first-look project discover
```

`--root` is the directory whose project you are asking about. It does not have to exist yet, and it does not have to be a Git repository. `--root` may be written before or after the subcommand; both forms behave identically. Quote it in PowerShell whenever the path contains a space or a non-ASCII character, and quote it in bash whenever it contains a space.

Expected output is the same JSON object as in the previous section, with `"requested_root"` set to the directory you passed and `"status"` still `"NO_PROJECT_FOUND"`, because that directory has no project in it:

```text
  "status": "NO_PROJECT_FOUND",
  "project_root": null,
    "project_root": "<your directory>",
  "execution_started": false,
  "search_scope": "REQUESTED_ROOT_AND_ANCESTORS",
```

Two things about that output are the point of this step. First, RDS is **not enabled by default**: nothing is switched on for you, and no optional capability is enabled automatically. Second, discovery **does not create a ledger**: after the command your directory is still empty, and no `.rds/` directory has appeared anywhere. Check that yourself with `Get-ChildItem -Force` in PowerShell or `ls -a` in bash.

This step is observation, not authorization. It told you what exists; it did not start any work. Creating a project is a second, deliberate step you have not been asked to take.

If `project discover` reports a project you did not expect, it found one in an ancestor of `--root`: discovery walks that directory and its parents, and searches no siblings. Re-run it with a different `--root`, or read the reported `project_root` to see what it matched.

## 5. Run one focused group of tests

Run every command in this section from the repository root.

### 5.1 One group, one command

```powershell
python -B -m unittest discover -s tests -p "test_rds_quick*.py"
```

Expected output, measured on the machine this page was written on:

```text
.....................................................................
----------------------------------------------------------------------
Ran 68 tests in 121.932s

OK
```

`68 tests` and `OK` mean every test in that group passed. The `[RDS-RESOLVE] proj -> project` style lines that appear above the dots are the command-line interface printing its abbreviation hints to standard error; they are not failures.

Those 68 tests take about two minutes, not one second, on this machine, because the group includes process-level work. For a genuinely fast signal while you work, name a smaller file:

```powershell
python -B -m unittest discover -s tests -p "test_rds_costs.py"
```

Expected, measured here:

```text
Ran 18 tests in 0.593s

OK
```

### 5.2 Pick the tests that match what you changed

There are 213 test files in `tests/`. Running the whole suite takes roughly an hour — a historical full run measured 3555.7 seconds, about 59 minutes, and that figure is bound to a commit older than the one you cloned — so **do not start with the full suite.** Run the file that matches your change.

The rule is mechanical: a script named `scripts/rds_<name>.py` is normally covered by `tests/test_rds_<name>.py`, plus any `tests/test_rds_<name>_<detail>.py`. 85 of the 104 `scripts/rds_*.py` files have at least one such test. Examples, each verified on this checkout with the `discover` form above:

| You changed | Run | Measured on this machine |
| --- | --- | --- |
| `scripts/rds_costs.py` | `python -B -m unittest discover -s tests -p "test_rds_costs.py"` | 18 tests, 0.6s |
| `scripts/rds_artifacts.py` | `python -B -m unittest discover -s tests -p "test_rds_artifacts.py"` | 22 tests, 1.2s |
| `scripts/rds_cli.py` | `python -B -m unittest discover -s tests -p "test_rds_cli_*.py"` | 43 tests, 56s |
| `scripts/rds_project.py` | `python -B -m unittest discover -s tests -p "test_rds_project*.py"` | 163 tests, 4m13s, 3 skipped |
| a file under `docs/` | `python -B -m unittest tests.test_rds_plugin_package` | 7 tests, 6.5s |

The times are what this page's author measured, not a target. Notice that a "focused" group is not always quick: only the first two rows give you a signal in seconds, so run the narrower one while you iterate.

Use the `discover -s tests -p "<pattern>"` form, or the `tests.test_rds_x` form. Do not write `python -m unittest tests/test_rds_costs.py`; that form is not supported by this test suite.

The last row matters even for a documentation change: `docs/` is copied into the packaged plugin, and `tests/test_rds_plugin_package.py` checks the packaged result. It runs in about seven seconds.

If a test file you expected does not exist, `discover` prints `Ran 0 tests` and exits `5`. That means your `-p` pattern matched no file, not that the tests passed.

## 6. Change one character and land it

This section is what turns you from a reader into a contributor. The worked example is a real defect in this checkout.

### 6.1 Find and fix a real one-line defect

`docs/advisor-frontier.md` links to a section of `docs/theory-reformulation.md` using the wrong anchor text, so the link lands at the top of the target page instead of the section it promises. Find it:

```powershell
Select-String -Path docs\advisor-frontier.md -Pattern "theory-reformulation.md#"
```

Expected: one matching line, containing

```text
[预载工具](theory-reformulation.md#按需查预载工具)
```

In bash the equivalent search is `grep -n "theory-reformulation.md#" docs/advisor-frontier.md`. Now read the headings of the target file to get the correct anchor:

```powershell
Select-String -Path docs\theory-reformulation.md -Pattern "^#{1,6} "
```

Expected: one heading begins with `按需查预载工具与可运行算子脚手架`. An anchor is the heading text with spaces replaced by `-`, so that heading's anchor is `按需查预载工具与可运行算子脚手架`, and the link is missing its second half.

Edit that single line with any editor, appending `与可运行算子脚手架` to the anchor inside the parentheses. Your change is one line: one insertion, one deletion.

This is the shape of every first contribution — a broken link, a misspelled word, one sentence that reads better. You are not expected to understand the kernel, the part of the program that actually executes work and records the results, and the defect does not have to be this one: any one-line documentation correction follows the same steps.

### 6.2 Check, commit, push

```powershell
git diff --check
```

Expected: no output at all, and exit status `0`. `git diff --check` reports trailing whitespace and leftover conflict markers, so empty output means your whitespace is clean.

If it lists a line, fix that line's trailing whitespace or conflict marker and run it again. Do not commit while it reports anything; whitespace noise makes a one-line change hard to review.

```powershell
git switch -c docs/fix-broken-anchor
git add docs/advisor-frontier.md
git commit -m "docs: point the preloaded-tool link at its section"
```

Expected: `git add` is silent, and `git commit` prints a summary line naming one file changed, one insertion and one deletion. If `git commit` opens an editor you did not want, use `-m` as shown. If `git` refuses to commit because it has no identity configured, set one:

```powershell
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

Now publish the branch to your own fork. Fork the repository in the browser first, then:

```powershell
git push -u origin docs/fix-broken-anchor
```

Expected: Git reports the new branch. If the push is rejected because you lack write access, you are pushing at the project repository instead of your fork; run `git remote -v` to see which URL `origin` points at, and push to your fork's URL.

### 6.3 Open the pull request

Open the pull request against `main`. The repository supplies a template, `.github/pull_request_template.md`, and it asks for five fields. Fill each one in. Do not delete a field you think is irrelevant; write `not applicable` with a reason instead.

| Field in the template | What it asks for |
| --- | --- |
| Problem and component | The concrete problem that changes, and which of the five components it touches: the research-protocol Skill, the execution-and-acceptance kernel, research state and memory, the Advisor, or RSI, the component that proposes and evaluates changes to the project's own rules |
| Claim and release scope | The exact claim, its assumptions, and a separation of behavior already on the base branch, behavior this pull request introduces, and development candidates |
| Public reproduction and results | Minimal inputs, the commands, the version or commit, and the actual status and assurance reported, as two separate values; `assurance` is the type of basis the check used, not a confidence score |
| Validation | The checks you actually ran and their outcomes, including failures and skips |
| Translations and publication | Whether the affected translations still agree, and whether the change contains private logs, `.rds/` state, or credentials |

For a one-line documentation fix the template is mostly satisfied by naming the file, pasting the command and its output, and ticking the two checkboxes at the end. Keep it that short. A documentation pull request needs no benchmark, and it must not claim a scientific result.

### 6.4 Read the continuous-integration result

Three jobs are defined in `.github/workflows/test.yml` and run on every pull request. `native` runs on Ubuntu and Windows and builds the Rust accelerator; `test` runs on Ubuntu and Windows with Python 3.11 and 3.13, so it reports four check results, and it is the job that runs the Python test suite; `lean` runs on Ubuntu and needs a Lean toolchain. For a change that only touches `docs/`, all of them are expected to pass with no new failures, in a few minutes.

Read the job list at the bottom of the pull request page. A red mark next to a job means that job failed; open it and read the last failing step. A yellow mark means it was skipped or is still running. The three job names above are what to look for; because `native` and `test` are matrices, the page can show more than three entries.

Continuous integration passing means the program's checks passed. It does not mean your change is scientifically correct, and it does not establish any research benefit — for a link fix, the honest claim is exactly that the link now resolves.

If a job fails on a test you never touched, say so in the pull request rather than rerunning until it turns green. A check that passes only on the second attempt is evidence about flakiness, and that is worth reporting.

## 7. When something fails

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| `python` is not recognized | No CPython on `PATH` | Run `py -3.13 -V`. If that works, use `py -3.13` everywhere this page writes `python`. Otherwise install CPython 3.11 or newer and reopen the shell. |
| `SyntaxError`, or an error naming `match` or `TypeError: 'type' object is not subscriptable` | The interpreter is older than 3.11 | Run `python -V`. If it prints 3.10 or lower, a different `python` is earlier on `PATH`; use the `py -3.13` form, or activate an environment with a newer interpreter. |
| A command works, then fails after you `cd` into a directory containing a space or a Chinese character | The shell split an unquoted path | Quote the whole path: `--root "C:\Users\XU\My Folder"` in PowerShell, `--root "/home/you/My Folder"` in bash. RDS handles spaces and non-ASCII characters; the usual breakage is unquoted expansion in the shell. |
| `ModuleNotFoundError: No module named 'test_rds_quick'`, or an import error for a sibling test module | You named one file directly instead of using discovery, so `tests/` never went on the import path | Use `python -B -m unittest discover -s tests -p "test_rds_quick*.py"` from the repository root. The `python -B -m unittest tests.test_rds_quick*.py` form fails for files that import a sibling fixture. |
| `Ran 0 tests`, exit status `5` | The `-p` pattern matched no file | List the real names with `Get-ChildItem tests\test_rds_*.py` in PowerShell or `ls tests/test_rds_*.py` in bash, and quote the pattern so the shell does not expand it. |
| `[RDS-REJECT] RDS is not initialized`, exit status `1` | No project has been initialized for that directory | Nothing is wrong; this is what `status` prints before any project exists. Pass `--root` pointing at a directory you intend to use, or leave it and continue with read-only commands. |
| Permission denied while writing `<checkout>/.rds/`, or while the plugin builder copies files | A command was pointed at the checkout, the plugin cache, or another account's directory | Never initialize a project inside the checkout or a plugin installation; use your own root, such as `$HOME/rds-first-look`. The packager also refuses to read `.rds/`, so never commit it. |
| `git clone` fails with a timeout, `Recv failure`, or `Failed to connect` | A proxy or firewall is blocking Git | Set the proxy for this shell only: `$env:HTTPS_PROXY = "http://127.0.0.1:7890"` in PowerShell, `export HTTPS_PROXY=http://127.0.0.1:7890` in bash, then retry. If it still fails, check GitHub in a browser and report the exact error. |
| `No module named pytest`, or `pytest: command not found` | `pytest` is not installed, and this repository does not use it | Do not install it. Run `python -B -m unittest discover -s tests -p "test_rds_costs.py"`, which needs nothing outside the standard library. |
| `error: src refspec docs/... does not match any` | The branch was never created, or the commit did not happen | Run `git switch -c docs/fix-broken-anchor`, then re-run `git add` and `git commit`. Confirm with `git log --oneline -1` that a commit exists. |

## 8. Eight words you will meet

These eight appear in the commands and output above. Each is one sentence here; [terminology.md](terminology.md) has the full list.

- **project** — a directory RDS has been told to keep records for, identified by a `.rds/` directory inside it, created deliberately and never automatically.
- **ledger** — the durable record of a project's goal, decisions, runs and remaining budget, which is what `project discover` looks for and what `status` reports on.
- **budget** — the resource allowance a project declared before work started, which executions are checked against so spending cannot quietly exceed it.
- **receipt** — the engine-produced record of one execution: what was bound, what ran, what status it reported, and which files it wrote.
- **admission** — the decision that a proposed run currently satisfies the applicable conditions, which speaks to readiness rather than to the value of the research.
- **UNKNOWN** — a first-class answer meaning the evidence does not settle the question, kept as `UNKNOWN` rather than resolved by guessing.
- **Advisor** — the component that connects the original goal, current evidence, constraints and candidate routes to a next executable action or a blocker.
- **Skill** — the collaboration protocol an agent loads to decide what to propose, distinct from behavior the local program actually enforces.

For what makes a change reviewable, read [CONTRIBUTING.md](../CONTRIBUTING.md). For the guide covering the subsystem you are touching, start from [docs/README.md](README.md).
