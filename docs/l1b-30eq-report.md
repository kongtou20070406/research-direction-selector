# L1-b 验收口径 15 → 90 cells（revision 7）

日期：2026-10-21
仓库：`D:\Myfile\work space\RDS\rds-357`，分支 `release/6.0.0-compass`，基线 HEAD `bce2218`（`git log --oneline -1` 已确认）
改动范围：**只改 `benchmark/newtonbench/l1b_accept.py` 一个文件**。未 commit、未 push。`scripts/`、`rds_compass.py`、其他测试全部未动（`git status --porcelain` 只有一行 ` M benchmark/newtonbench/l1b_accept.py`）。

## 1. 改了什么

| 项 | 改前 | 改后 |
|---|---|---|
| `EQUATIONS` | 硬编码 5 个方程 | `tuple(devset.list_equations())`，经 `sys.path[:0] = [ROOT/'scripts', HERE]` 导入注册表 |
| `CELLS` | `5 × 3 = 15` | `30 × 3 = 90`（实测 `len(CELLS) == 90`） |
| 缺失 cell 语义 | 不在文件里就 `SystemExit` | 分两种：**文件里完全没有这一格** → 仍 `SystemExit(MISSING CELLS)`；**这一格在但 `status` 为空或不认识** → 判 UNKNOWN，从所有 gate 分母剔除，单独列出 |
| 新增 gate 0 | 无 | `judged * 2 > len(CELLS)`，即判定格数必须过半；否则 FAIL，理由 `insufficient sample, cannot judge` |
| gate 1a/1b/2/3/4 | 分母 = 15 | 分母 = `judged`（67），阈值与措辞不变 |
| negative condition | `len(identical) == len(CELLS)` | `len(identical) == len(judged)`，且 judged 为空时不算零复用（fail closed 由 gate 0 兜） |
| `EXTRA_EQUATION`（I.6.20a） | 只对 15 格内的格子取行 | 保留「记录不判定」，另外把该方程里 UNKNOWN 的预算单独报出来 |
| docstring | 15 cells 口径 | 写明 revision 7 扩到 90、UNKNOWN 的两种来源与剔除方式、过半判定门槛 |

两处顺带修掉的**硬崩溃**（原代码在 30 方程文件上会直接 `SystemExit`，不带任何判定）：

- `experiments(run)` 遇到无 status 的异常行（没有 `experiments` 字段）原本 `MALFORMED experiments: None` 直接退出——现在这些行先被 `judged()` 挡掉，不会进分母。
- `false_certification(run)` 原本对「status 非 GOAL_IDENTIFIED 但 counterexamples 为 None」抛 `MALFORMED counterexamples: None`。现在只有真正的 claim 才查 counterexample，非 claim 一律 `False`——这与 `devset_run.certify_run()` 写入的 `false_certification=False / reason=NOT_CLAIMED` 一致。

`KNOWN_STATUSES` 取自 `scripts/rds_compass.py:32-34` 的 `HANDOFF_REASONS`，加上 `NO_MEASUREMENTS / RUN_FAILED / RUN_ERROR`。**这一条是本轮我自己定的口径，不是预注册文件里的原文**，需要老板确认（见第 5 节）。

## 2. 验收输出（原始）

### 2.1 语法与 import，期望 90

```
$ python -B -c "import sys; sys.path.insert(0,'benchmark/newtonbench'); import l1b_accept; print(len(l1b_accept.CELLS))"
90
```

### 2.2 fail-closed：文件不存在必须 FAIL

```
$ python -B benchmark/newtonbench/l1b_accept.py --off /nonexistent.json --on /nonexistent.json
[exit 1]
MISSING INPUT: C:\Users\XU\.dsh\profiles\web\node_modules\@dsh-ops\bash-win32-x64\nonexistent.json
L1-b cannot be judged without both result files. Run the library-off and library-on devset sweeps first.
```

（`/nonexistent.json` 在 Git-Bash→Windows 路径转换下被解析到 bash 运行时目录，结论不变：文件不存在，exit 1。）

### 2.3 基线文件自己对自己

```
$ python -B benchmark/newtonbench/l1b_accept.py --off benchmark/newtonbench/results/devset-run-30.json --on benchmark/newtonbench/results/devset-run-30.json
library off: benchmark/newtonbench/results/devset-run-30.json
library on:  benchmark/newtonbench/results/devset-run-30.json
equations:   30 (from the registry), budgets: [8, 16, 32]

UNKNOWN  23/90 cells carry no recognised status (declaration-time rejection); judged 67
         feynman-I.10.7 B=32 [off+on] ValueError: budget.experiments must be an integer from 0 to 18
         feynman-I.18.4 B=8 [off+on] ValueError: Declare 1..64 candidates
         feynman-I.18.4 B=16 [off+on] ValueError: Declare 1..64 candidates
         feynman-I.18.4 B=32 [off+on] ValueError: Declare 1..64 candidates
         feynman-I.26.2 B=32 [off+on] ValueError: budget.experiments must be an integer from 0 to 18
         feynman-I.40.1 B=8 [off+on] ValueError: Declare 1..64 candidates
         feynman-I.40.1 B=16 [off+on] ValueError: Declare 1..64 candidates
         feynman-I.40.1 B=32 [off+on] ValueError: Declare 1..64 candidates
         feynman-I.41.16 B=8 [off+on] KeyError: 'op'
         feynman-I.41.16 B=16 [off+on] KeyError: 'op'
         feynman-I.41.16 B=32 [off+on] ValueError: budget.experiments must be an integer from 0 to 18
         feynman-II.24.17 B=32 [off+on] ValueError: budget.experiments must be an integer from 0 to 18
         feynman-II.27.18 B=32 [off+on] ValueError: budget.experiments must be an integer from 0 to 16
         feynman-II.34.11 B=8 [off+on] ValueError: Undeclared variable: B_times_g_factor
         feynman-II.34.11 B=16 [off+on] ValueError: Undeclared variable: B_times_g_factor
         feynman-II.34.11 B=32 [off+on] ValueError: Undeclared variable: B_times_g_factor
         feynman-II.38.14 B=32 [off+on] ValueError: budget.experiments must be an integer from 0 to 18
         feynman-II.38.3 B=8 [off+on] ValueError: Declare 1..64 candidates
         feynman-II.38.3 B=16 [off+on] ValueError: Declare 1..64 candidates
         feynman-II.38.3 B=32 [off+on] ValueError: Declare 1..64 candidates
         feynman-III.10.19 B=8 [off+on] ValueError: Declare 1..64 candidates
         feynman-III.10.19 B=16 [off+on] ValueError: Declare 1..64 candidates
         feynman-III.10.19 B=32 [off+on] ValueError: Declare 1..64 candidates

PASS  gate 0: at least half of the pre-registered cells are judgeable  67/90 judged
PASS  gate 1a: claim_exact(on) >= claim_exact(off)  57 >= 57
PASS  gate 1b: no cell turns exact -> inexact  0 regressed
FAIL  gate 2: reuse evidence (claim_exact up, or total experiments down)  claim_exact 57->57, experiments 997->997
PASS  gate 3: total experiments <= 1.25x baseline  997 <= 1246.2
PASS  gate 4: no cell adds a false certification  0 added
NOTE  feynman-I.6.20a (recorded, not a gate): no change
FAIL  negative condition: operators were actually reused  67/67 judged cells byte-identical (23 UNKNOWN not evidence) -- L1 PATH DOES NOT EXIST

OVERALL: FAIL
[exit 1]
```

与任务书的期望**逐条吻合**：gate 1a/1b/3/4 PASS、gate 2 FAIL（on==off 无 reuse 证据）、negative condition FAIL（67/67 judged 全 identical）。

## 3. 额外负向测试（临时文件，未写入仓库）

都在 `%TEMP%` 下的副本上跑，不改仓库里的结果文件。

| 场景 | 构造 | 结果 |
|---|---|---|
| A 真缺失 | 从 90 格里删掉 `feynman-I.12.1 B=8` | `SystemExit: MISSING CELLS: [('feynman-I.12.1', 8)]`，覆盖 90 格的提示语 |
| B 只有 on 侧 UNKNOWN | 把 on 的 `I.12.1 B=8` 的 `status` 删掉 | `UNKNOWN 24/90, judged 66`；gate 1a 记 `56 >= 56`（该格 off 侧原本 exact，未被误判成 regression），gate 1b `0 regressed` |
| C 不认识的 status | 把一行 `status` 改成 `SOMETHING_NEW` | `UNKNOWN 24/90`，无异常退出 |
| D 文件不可解析 | 写入 `{not json` | `UNREADABLE INPUT: ...: Expecting property name enclosed in double quotes` |
| E 判定过少 | 抹掉 47 行的 status | `UNKNOWN 59/90, judged 31` → `FAIL gate 0 ... insufficient sample, cannot judge`，NOTEs 里 I.6.20a 三个预算都标 UNKNOWN |

关键证据是 **B 那一行**：UNKNOWN 格确实从分母里被摘掉了，而不是被当成 `claim_exact=False` 记了一笔回归。

## 4. diff stat

```
$ git diff --stat
 benchmark/newtonbench/l1b_accept.py | 117 +++++++++++++++++++++++++++---------
 1 file changed, 89 insertions(+), 28 deletions(-)
```

（改前先用 `git stash show -p stash@{0}` 看了 `l1b_accept 30eq recount` 那条 stash，只采纳了「`EQUATIONS` 从注册表读」这一条；`git stash pop` 没有执行，stash 仍在 `stash@{0}`。）

## 5. 拿不准的判断（标 UNKNOWN，别当既成事实）

1. **`KNOWN_STATUSES` 白名单是自定的**。`scripts/rds_compass.py:32-34` 只列了 8 个 handoff 理由，我额外加了 `NO_MEASUREMENTS / RUN_FAILED / RUN_ERROR`（devset 侧见过/可能见到的名字）。如果真实结果文件里出现别的新状态名，本脚本会判 UNKNOWN 并剔除——方向偏保守（不会把坏结果算成好结果），但可能低估样本量。要不要收紧到只用 `HANDOFF_REASONS` 八个，需要老板定。
2. **`false_certification()` 的放宽**改动了一处原有行为：旧代码对「status 非 GOAL_IDENTIFIED 且 counterexamples 为 `None`」抛 `SystemExit`，现在返回 `False`。理由是 `devset_run.certify_run()` 本来就给这类行写 `False`/`NOT_CLAIMED`，旧抛错在任何含 SEARCH_EXHAUSTED 行的文件上都会白死。但这条不是任务书要求的，属主动修正。
3. **gate 0 的「一半」取严格大于**：90 格时判定 45 格不够（`45*2 > 90` 为假），46 格才过。任务书写的是「少于一半 FAIL」，45 = 一半，按字面不该 FAIL；我按「过半」实现。要改成 `>=` 只需动一个比较符。
4. **negative condition 的分母换了**：原来 `15/15 identical` 才 FAIL，现在 `67/67 judged identical` 就 FAIL。语义上更严格（UNKNOWN 格既不能当复用证据也不能当反证据），但这是行为变化。
5. `docs/rsi-l1l2-plan-20261020.md:284` 里「4.1 的 15-cell 对照」这句**没改**，因为任务约束只许改 `l1b_accept.py`。口径已变，这行文字现在是过时的，需要主对话决定要不要在 plan 文档里补一句 revision 7。
6. 本轮只验证了「判定脚本读文件后怎么判」，**没有验证真实 l1off/l1on 两份结果文件是否存在**——`results/` 目录下没有 `devset-l1off.json` / `devset-l1on.json`，默认参数直接跑会 `MISSING INPUT` fail closed。L1-b 的真实对照还没跑过，任何「L1 有效/无效」的结论此时都不成立。

## 6. 复现

```bash
cd "D:/Myfile/work space/RDS/rds-357"
python -B -c "import sys; sys.path.insert(0,'benchmark/newtonbench'); import l1b_accept; print(len(l1b_accept.CELLS))"
python -B benchmark/newtonbench/l1b_accept.py --off /nonexistent.json --on /nonexistent.json; echo "exit=$?"
python -B benchmark/newtonbench/l1b_accept.py --off benchmark/newtonbench/results/devset-run-30.json --on benchmark/newtonbench/results/devset-run-30.json; echo "exit=$?"
```
