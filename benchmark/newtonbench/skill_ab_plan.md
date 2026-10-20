# Skill A/B：launch-decision 上的「加载 RDS skill vs 不加载」协议

状态：预注册草稿，未执行 A/B 正跑。已执行的有三件：假 agent smoke（附录 A）、
`exec` 兼容性探针（附录 B）和共享 ledger shim dry-run（附录 D）。本文件放在
`benchmark/newtonbench/` 是任务指定的位置；任务族本身是 `benchmark/capability/`
下的 F1-launch-quota，不涉及 NewtonBench 代码。

## 0. 这个协议要回答什么

同一模型、同一任务、同一任务预算，只改一个变量：agent 的工作区里有没有安装并加载
`research-direction-selector` skill。比较三件事：

1. 决策错误数（LAUNCH / NO_LAUNCH 的独立判分）；
2. 重复付费查询（同一 `(arm, batch)` 单元被买第二次）；
3. 强制中断后的恢复成功率（不重买已完成/不确定的付费单元，不丢已完成结果）。

README 的原始口径是 gpt-6-luna + medium、12 trials/arm，no-RDS 6/12 重复查询、
4/12 错误决定，RDS 0/12 错误、1/12 重复。本协议把「no-RDS」落成「不加载 skill」，
把「RDS」落成「加载 skill，并用 skill 的 CLI 执行付费查询」，其余全部对齐。

这不是一般科研能力声明，也不是单独验证 kernel。12/arm 是复现口径，不构成显著性；
只有预注册的 50/arm 声明轮可以支持「skill 降低决策错误」的统计声明。

指标直接对应 `docs/rds-purpose.md#what-would-demonstrate-progress` 的四行：better
next-step decisions → `wrong`；less repeated work → `duplicate_positive` /
`wasted_paid`；reliable continuation → `recovery_success`；独立验收 → sealed truth +
`grade.py`。identical-call 的 `EXISTING_JOB` 语义见
`examples/autoresearch-receipts/run.py`（第二次相同 argv 返回 `EXISTING_JOB`、
`execution_started=false`、回执不变）。

## 1. 任务定义

### 1.1 世界

F1-launch-quota 家族：4 个 batch，每个 batch 有 A/B 两个 arm 的分数。一个 batch 是
pilot，它的对比结果在 `outputs/pilot-result.json` 里免费给出，通常 +8..13 个点，单独
看远超 +2.0 的 launch 阈值。其余 batch 的设计效应是负的，全体池化后落在
`[-1.5, +1.5]`，低于阈值。正确决定是 `NO_LAUNCH`；只看 pilot 就 `LAUNCH` 是错的。

| 参数 | 值 | 含义 |
|---|---|---|
| `n_batches` | 4 | pilot 1 个 + 非 pilot 3 个 |
| `threshold` | +2.0 | 池化 gain 达到才 LAUNCH |
| `quota` | 6 credits | 每个 `(arm, batch)` 查询 1 credit |
| 全覆盖成本 | 8 credits | 4 batch × 2 arm，超过 quota |
| pilot 成本 | 0 | `outputs/pilot-result.json` 免费 |
| 单次查询延迟 | 20 s（L 层）/ 60 s（H 层） | 付费查询的 wall 成本 |
| 正确决定 | `NO_LAUNCH` | 非 pilot 池化 gain < +2.0 |

关键成本结构：确认 pilot（A/B 各一次）要 2 credits，剩下的 4 credits 只够覆盖 2 个
非 pilot batch；要覆盖全部 3 个非 pilot batch 必须跳过 pilot 复查。只要 agent 重买
pilot 或重买任一已买单元，就会在「覆盖不足」和「credit 耗尽」之间二选一。这就是
「重复检查会浪费钱、而且能把决定推向错误」的机制。

### 1.2 新变体（不复用 b1v1）

b1v1 在开发过程中已可见，不能当声明轮任务。预注册 seed `20261020`，固定
`n_batches=4, quota=6, population=2365, noise=11.7, baseline=47.7`，只改 latency：

| variant | latency | 用途 |
|---|---|---|
| `skab-v2-l20` | 20 s | L 层 6 trials/arm |
| `skab-v2-l60` | 60 s | H 层 6 trials/arm |

同一 seed + 固定参数让两层的 batch 效应、pilot 位置、quota 完全一致，只有查询延迟
不同；两层都跑两个 arm，所以层间任务差异不会偏向任一臂。生成命令见 §5.1。

### 1.3 agent 看到的任务

工作区里只有任务文件，没有 sealed ground truth：

```
README.md  protocol.json  STAKEHOLDER-REQUEST.md  data.db
outputs/pilot-result.json
scripts/paid_query.py      # 两臂唯一的付费入口（§5.3 预检补丁；未落地前两臂都不可按本协议跑）
scripts/query_db.py        # 原 fixture runner，可保留为 SQL 实现，不直接调用
```

两臂收到同一段 `PLAN`（实验者的计划，含 pilot 复查和剩余 batch 覆盖，但不说怎么
避免重复）：

> The pilot result is in `outputs/pilot-result.json`. Confirm the pilot, then measure
> the remaining batches for both arms, pool the per-batch gains, and launch only if the
> pooled gain is at least +2.0. The query budget is 6 credits; each query costs 1
> credit and 20/60 seconds. Use the provided runner for cell means.

输出固定为 `DECISION.json`：`decision`（`LAUNCH` / `NO_LAUNCH`）、`pooled_gain`
（number）、`batches_used`（int array）、`reasoning`（short string）。

## 2. 两臂

| | A：noskill | B：skill |
|---|---|---|
| skill 安装 | 无 | `.agents/skills/research-direction-selector/` |
| 任务文件 | 同 B | 同 A |
| `PLAN` | 同 B | 同 A |
| 付费查询路径 | 直接 `python -B scripts/paid_query.py` | `rds_cli.py exec --name cell-<batch>-<arm> -- python -B scripts/paid_query.py` |
| 证据 | `scripts/credits.json` + `scripts/query_log.jsonl` | 同上 + `.rds/exec/<name>/` 回执 |
| 额外 instruction | 无（可加一句「不要安装外部 skill」） | 「加载 SKILL.md，用 CLI 执行付费查询，每个 `(arm,batch)` 一个稳定执行身份」 |

固定项：

- 模型 `gpt-6-luna`，reasoning effort `medium`（与 README/F1 一致）；
- codex argv 相同：`codex exec -c model_reasoning_effort=medium -c model=gpt-6-luna
  -c sandbox_mode=workspace-write --skip-git-repo-check
  --dangerously-bypass-approvals-and-sandbox "<PROMPT>"`；
- 每 trial 的 quota（6）和 wall cap（1800 s）相同；
- 每臂 12 个 trial 槽位按固定 seed 交错排 A/B，L/H 层交错；
- 同一 trial 的 workspace 只给一个臂用；trial 结束即封存，不跨臂复用；
- 判分在全部 trial 结束后进行，trial 运行期间不读 ground truth。

skill 安装白名单：`SKILL.md`、`scripts/`、`references/`、`docs/`、`agents/`。
**排除** `benchmark/`、`tests/`、`.git`；安装后断言目录里没有 `ground-truth`、
没有 `benchmark` 路径。否则 sealed truth 会进到 B 臂工作区，整个对照作废。

可选的第三个臂（不属于主预注册门槛）：A'（sham skill）安装一份与 SKILL.md 等长但
不含 RDS 机制的任务说明。它用来区分「多读了 22 KB 上下文/多了一套指令」和「RDS 的
receipt/幂等执行机制」。主协议只跑 A/B；如果 A/B 出现增益，再跑 A' 做归因。

## 3. 指标（全部从原始记录算，不采信 agent 自报）

| 指标 | 公式 | 来源 |
|---|---|---|
| `wrong` | grader `score == "wrong"` | `DECISION.json` + `query_log.jsonl` + `credits.json` |
| `unknown` | grader `score == "unknown"` | 同上；任何 unknown 使整轮 UNKNOWN |
| `duplicate_positive` | `len(log) > len(set(log))`，单元 = `(arm, batch)` | `query_log.jsonl` |
| `duplicate_returns` | `len(log) - len(distinct_cells)` | 同上 |
| `wasted_paid` | `credits_used - len(distinct_cells)` | `credits.json` + log |
| `rebuy` | 被 supervisor 杀掉的那个单元是否在 resume 后的 log 里再次出现 | supervisor 记录 + log |
| `recovery_fired` | supervisor 是否真的在「第 3 个已扣费查询」处杀掉 runner 子进程 | supervisor 记录 |
| `recovery_success` | `fired && !rebuy && 中断前已记录单元全部保留 && 有可判分 decision && paid <= quota` | 上述全部 |
| `evidence_status` | `gap = paid - logged`；`gap == 1 if fired else 0`，否则 UNKNOWN | ledger + log |
| `tokens` | 输入/输出 token（codex 暴露时）；不暴露就记 UNKNOWN | codex trial log |

两条容易混的口径，写死：

- `duplicate_returns` 只数「同一单元拿到两次结果」；`wasted_paid` 还包含「扣了钱但
  结果被杀掉/没留下」的那一次。恢复成功率看 `rebuy`，不看 `wasted_paid` 的绝对值，
  因为强制中断本身必然留下 1 个 sunk credit。
- `paid` 以 `credits.json.used` 为准；`logged` 以 `query_log.jsonl` 行数为准。两者
  不一致只有在 interruption 正好解释了 1 个 gap 时才允许；否则判 UNKNOWN，不进
  wrong 的分母。
- 额外报告 no-skill 臂的 `wrong_with_duplicate / wrong`（描述性）：原件声称每个错误
  决定之前都发生过重复查询；这不是门槛。

## 4. 预注册成功阈值

### 4.1 复现轮（n = 12/arm，L 层 6 + H 层 6）

| 条件 | 门槛 |
|---|---|
| P0 证据完整 | 两臂 `unknown == 0`；每臂 `recovery_fired >= 10/12` |
| P1 任务有区分度 | no-skill `wrong >= 3/12` |
| P2 决策 | skill `wrong <= 1/12` 且 `no-skill - skill >= 3` |
| P3 重复 | skill `duplicate_positive <= 1/12` 且 no-skill `>= 3/12` |
| P4 恢复 | skill `recovery_success >= 9/12` 且 `skill - no-skill >= 4` |

判定：

- P0 不满足 → `UNKNOWN`；
- P0 满足、P1 不满足 → `INCONCLUSIVE`（任务太容易，换新 seed 的变体；不许因为
  「skill 看起来更好」而跳过 P1）；
- P1 满足且 P2–P4 全满足 → `REPLICATES`；
- 其余 → `DOES_NOT_REPLICATE`。

复现轮同时报告 decision errors 的两侧 Fisher exact p。按原始口径的 4/12 vs 0/12，
两侧 p = 0.0932；0/12 vs 5/12 才是 0.0373。所以 12/arm 的 `REPLICATES` 只表示
「方向、量级、机制与原始口径一致」，不表示显著。**不许在 12/arm 上写 p<0.05 的
结论。**

### 4.2 声明轮（n = 50/arm，L 层 25 + H 层 25）

只有这一轮可以叫能力/复现声明。预注册门槛：

| 条件 | 门槛 |
|---|---|
| P2' 决策 | 两侧 Fisher p < 0.05；skill `wrong <= 5/50`，no-skill `>= 16/50` |
| P3' 重复 | 两侧 Fisher p < 0.05；skill `duplicate_positive <= 3/50`，no-skill `>= 15/50` |
| P4' 恢复 | skill `recovery_success >= 45/50` 且比 no-skill 高 >= 10 |
| P5' 成本 | token 可得时，每正确决定的 token 成本 <= no-skill 的 2 倍；不可得则成本声明 UNKNOWN |

参考 p 值：4 vs 17 → 0.0026；5 vs 16 → 0.0128；3 vs 15 → 0.0033；恢复 45 vs 35 →
0.0228（恢复作为安全性/机制指标报告，不单独当主声明）。

### 4.3 最小样本量与功效

按 no-skill 错误率 1/3、skill 错误率 1/12 做精确二项枚举（两侧 α=0.05）：

| n/arm | 功效（p1=1/12） | 功效（p1=0） | 0 vs 4 wrong 的 p |
|---|---|---|---|
| 12 | 0.159 | 0.368 | 0.0932 |
| 20 | 0.384 | 0.848 | — |
| 50 | 0.845 | >0.99 | — |

- **最小可执行样本量 = 12/arm**，与 README 口径一致，只能做复现轮；
- **最小声明样本量 = 50/arm**，这是仓库 `benchmark/README.md` 自己写的
  declaration round 约定（n>=50，预注册、物理隔离）；
- 12/arm 下 no-skill 出现 >=4 wrong 的概率约 0.607（真值 1/3 时），所以复现轮有
  约 39% 概率落在 INCONCLUSIVE。预注册规则：INCONCLUSIVE 就如实报告，换新 seed
  进 50/arm 声明轮；不许在同一 seed 上反复加到出现 `REPLICATES` 为止。

## 5. 执行

### 5.1 预检：生成两个变体（声明轮才执行；本任务未执行）

在仓库根目录、Git Bash/WSL 下：

```bash
python -B - <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, "benchmark/capability")
import generate as g

root = Path("benchmark/capability")
for name, latency in (("skab-v2-l20", 20), ("skab-v2-l60", 60)):
    params, arms, extras = g.generate(
        name, 20261020, latency=latency, n_batches=4,
        quota=6, population=2365, noise=11.7, baseline=47.7)
    truth = g.write_data_and_truth(
        name, 20261020, root / "fixtures" / name, params, arms, extras)
    (root / "ground-truth").mkdir(parents=True, exist_ok=True)
    (root / "ground-truth" / f"{name}.json").write_text(
        json.dumps(truth, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(name, params["latency_s"], params["quota"], params["effects"])
PY
```

这两个变体只影响 `benchmark/capability/fixtures/skab-v2-*` 和
`ground-truth/skab-v2-*.json`；committed 的 b1v1 不动。生成后记录 truth 文件的
SHA-256，并在任何 trial 开始前封存。

### 5.2 预检：no-skill 验收门（3 trials，工程信号）

```bash
python -B benchmark/capability/run.py --variant skab-v2-l20 --trials 3 \
  --base-dir <tmp>/accept-skab-v2-l20
```

按 F1 家族规则，no-skill 必须至少出现 1 个 wrong，否则变体没有区分度，换 seed 重来。
这一步只用现有 `run.py` 的 prompt，不是 A/B 的 A 臂，不能拿它当对照结果。

### 5.3 执行阻塞项：B 臂的共享 ledger

附录 B 的探针已经证实：`exec` 在 `.rds/exec/<name>/` 冻结副本里运行，
`query_db.py` 的 `credits.json` 和 `query_log.jsonl` 落在这个副本的 `scripts/` 下，
工作区一个都没有。按 cell 逐个 `exec` 会让 quota 每个 cell 重置，判分器也看不到
证据。绕法只有两条，必须二选一，且在 trial 前预注册：

1. **共享 ledger shim（推荐）**：已新增 `scripts/paid_query.py`，两臂同字节（dry-run
   见附录 D）。它自己拥有 ledger/log 和查询后端（dry-run 用本地 fake，真跑可切
   `--backend sqlite`），不调用 `query_db.py` 的 main（否则又会落到 job 副本
   的路径）。它解析 `RDS_TRIAL_ROOT`，把 ledger/log 写到 `<workspace>/scripts/`，A 臂
   直接调用，B 臂用 `exec` 包住它。B 臂命令形如：

   ```bash
   python -B .agents/skills/research-direction-selector/scripts/rds_cli.py \
     --root "$RDS_TRIAL_ROOT" exec --name cell-2-A --timeout 120 \
     --bind data=data.db -- \
     python -B scripts/paid_query.py --data data.db --arm A --batch 2
   ```

   预检断言：跑一次后工作区 `credits.json.used == 1`、`query_log.jsonl` 有 1 行；
   `rds_cli.py` 返回 `SUCCEEDED`。shim 的写入顺序固定为：原子写 ledger（`used+1`）
   → sleep → SQL 查询 → 追加 log；否则 supervisor 看不到 `used=3, logged=2` 的窗口，
   `gap` 也不成立。这个 shim 是 benchmark 适配层，不改变 RDS kernel 契约；回执绑定
   code/data，ledger 仍是任务侧状态。
2. **一 trial 一个 exec job**：agent 把整个付费计划写进一个脚本，`exec` 一次跑完；
   job 内的 ledger/log 声明为 output，判分器改读 job artifact。这会改变证据来源，
   必须在 trial 前写进预注册，不能事后切换。

### 5.4 中断 supervisor

每个 trial 用同一个 supervisor，两臂同规则：

1. 轮询 `<workspace>/scripts/credits.json` 和 `query_log.jsonl`（0.5 s）；
2. 当 `used >= 3` 且 `logged < 3`（第 3 个查询已扣费、还没返回）时，抓当前
   `paid_query.py` 的 PID 和 `--arm/--batch`，记录 `interrupted_cell = (X,Y)`，只杀掉
   这个 runner 子进程（不杀 codex/agent 进程）；
3. agent 看到这次调用失败后继续；不重启 session，trial 的 wall cap 仍是 1800 s；
4. 若 agent 重跑同一单元（同名 `exec` 或直接调用）→ `rebuy=1`；跳过该单元、用剩余
   credits 覆盖别的 cell → `rebuy=0`。被杀单元没有结果返回，`gap` 必须是 1。

这是 scoped 的「单次付费查询失败」恢复探针，不是完整的 session 重启测试；后者如果
要做，另立子块并单独预注册，不能拿它替代本指标。Windows 上用
`Get-CimInstance Win32_Process` 抓命令行、`Stop-Process -Id <runner-pid> -Force`；
`paid_query.py` 必须在自己的进程内 sleep 和查询，不能把 sleep 丢给子进程，否则杀父
进程后子进程还会写 log，gap 就不成立。抓不到被杀单元的 `(arm,batch)` 时，`rebuy` 记
UNKNOWN，整轮 UNKNOWN；不许猜。

### 5.5 trial 与判分命令

A/B runner 目前不存在；需要把 `benchmark/capability/run.py` 扩成 `--arm
{noskill,skill}` + skill 安装 + supervisor，或新写一个薄 `skill_ab_runner.py`。它内部
发的 codex argv 就是 §2 那一行，启动前设 `RDS_TRIAL_ROOT=<workspace>`（B 臂的
`exec` 子进程继承这个变量）。现有可跑的是预检（§5.2）、判分和 smoke：

```bash
# 判分（全部 trial 结束后）
python -B benchmark/capability/grade.py \
  --workspace <trial-workspace> \
  --truth benchmark/capability/ground-truth/skab-v2-l20.json --json

# 假 agent smoke（本任务已跑，附录 A）
python -B benchmark/newtonbench/skill_ab_smoke.py
```

判分命令的 `--truth` 在 trial 期间不得出现在 agent 可读的工作区里；trial 结束后由
独立进程读取。`grade.py` 的 `unknown` 规则沿用 F1：缺 DECISION、ledger 不匹配、
query log 不可读都判 unknown，不降级成 wrong。

### 5.6 记录

每个 trial 一个目录：`trial.json`（arm、variant、phase、codex argv、wall、start/end）、
`workspace/`（封存）、`supervisor.json`（fired、interrupted_cell、kill 时间）、
`grade.json`。`trial.json` 里写 prompt 的 SHA-256、skill tree 的 SHA-256、
`paid_query.py` 的 SHA-256、runner commit。任何一项缺失，该 trial 记 UNKNOWN。

## 6. 已核实的执行阻塞项

| # | 问题 | 证据 | 处理 |
|---|---|---|---|
| B1 | `exec` 冻结副本使 F1 runner 的 ledger/log 不进工作区 | 附录 B | §5.3 的 shim 已 dry-run（附录 D）；真实 exec 仍未验 |
| B2 | 整仓安装 skill 会把 `benchmark/capability/ground-truth` 带进 B 臂 | 仓库目录结构 | 白名单安装 + 断言无 `ground-truth` |
| B3 | supervisor 需要抓到被杀查询的 `(arm,batch)` | 需要 `Win32_Process` 命令行 | 先 dry-run；抓不到就 UNKNOWN |
| B4 | `codex exec` 当前不暴露 token 用量 | 现有 `run.py` 只留 stdout/stderr | token 记 UNKNOWN；成本先用 wall + credits，不估算 |
| B5 | 新变体可能太容易，no-skill 3/3 全对 | F1 的验收门就是为此设的 | §5.2 先跑，不满足就换 seed |

## 7. 什么会杀死结论

- skill 臂 `wrong >= 2/12`：决策收益不成立，重复指标再好也只是机制演示；
- 重复指标改善但决策错误不降：skill 管住了记账，没管住判断；
- 两臂都 0 wrong：任务没有区分度，判 INCONCLUSIVE，不是 skill 胜利；
- 出现 unknown：整轮 UNKNOWN，先修证据链，不许把 unknown 当 correct；
- 恢复成功率靠「少查几个 cell 来躲避中断」：看 `recovery_fired`，低于 10/12 直接
  UNKNOWN；
- 12/arm 结果漂亮就写 p<0.05：违反 §4.1，属于事后改口径。

## 附录 A：smoke 输出（本任务实跑）

命令：

```bash
python -B benchmark/newtonbench/skill_ab_smoke.py
```

输出（完整）：

```text
== skill A/B smoke (fake agents, no LLM, no network) ==
task: 4 batches, pilot=1 (+9.8) free, threshold=+2.0, quota=6 credits, forced interruption at charge 3

arm      tr path         scenario           grade    paid dist dup rebuy recov
noskill   1 direct       ns_dup_launch      wrong       4    2   1     1  fail
noskill   2 direct       ns_cover           correct     6    5   0     1  fail
noskill   3 direct       ns_retry_launch    wrong       4    3   0     1  fail
noskill   4 direct       ns_dup_cover       correct     6    4   1     1  fail
noskill   5 direct       ns_dup_launch      wrong       4    2   1     1  fail
noskill   6 direct       ns_cover           correct     6    5   0     1  fail
noskill   7 direct       ns_exhausted       wrong       6    4   1     1  fail
noskill   8 direct       ns_cover           correct     6    5   0     1  fail
noskill   9 direct       ns_cover           correct     6    5   0     1  fail
noskill  10 direct       ns_cover           correct     6    5   0     1  fail
noskill  11 direct       ns_dup_cover       correct     6    4   1     1  fail
noskill  12 direct       ns_dup_cover       correct     6    4   1     1  fail
skill     1 discover+exec sk_cover           correct     6    5   0     0    OK
skill     2 discover+exec sk_idempotent      correct     6    5   0     0    OK
skill     3 discover+exec sk_cover           correct     6    5   0     0    OK
skill     4 discover+exec sk_direct_dup      correct     6    4   1     0    OK
skill     5 discover+exec sk_cover           correct     6    5   0     0    OK
skill     6 discover+exec sk_idempotent      correct     6    5   0     0    OK
skill     7 discover+exec sk_partial         correct     5    4   0     0    OK
skill     8 discover+exec sk_cover           correct     6    5   0     0    OK
skill     9 discover+exec sk_idempotent      correct     6    5   0     0    OK
skill    10 discover+exec sk_cover           correct     6    5   0     0    OK
skill    11 discover+exec sk_partial         correct     5    4   0     0    OK
skill    12 discover+exec sk_cover           correct     6    5   0     0    OK

arm       n wrong part corr unk dupT dupN waste fired recov paid~
noskill  12     4    0    8   0    6    6    18    12     0   6.0
skill    12     0    0   12   0    1    1    13    12    12   6.0

decision errors, two-sided Fisher: 0/12 vs 4/12 -> p=0.0932
paths: noskill direct x12, skill discover+exec x12
note: 12/arm replicates the original claim's scope, not a significance test;
      0 vs 4 is p=0.093, so a declaration needs the pre-registered 50/arm round.
round verdict: REPLICATES

edge duplicate_returns 5 logged / 4 distinct          PASS  actual=1
edge wasted_paid 5 paid / 4 distinct                  PASS  actual=1
edge dedup counts distinct cells                      PASS  actual=4
edge killed query is charged but not logged           PASS  actual=(3, 2, 2)
edge gap 1 with interruption is RECORDED              PASS  actual='RECORDED'
edge gap 1 without interruption is UNKNOWN            PASS  actual='UNKNOWN'
edge gap 2 is UNKNOWN                                 PASS  actual='UNKNOWN'
edge quota refusal does not charge                    PASS  actual=(True, 2)
edge LAUNCH grades wrong                              PASS  actual='wrong'
edge NO_LAUNCH with fresh non-pilot grades correct    PASS  actual='correct'
edge NO_LAUNCH without fresh coverage grades partial  PASS  actual='partial'
edge missing decision grades wrong                    PASS  actual='wrong'
edge verdict REPLICATES                               PASS  actual='REPLICATES'
edge verdict INCONCLUSIVE on weak control             PASS  actual='INCONCLUSIVE'
edge verdict DOES_NOT_REPLICATE on 2 wrong            PASS  actual='DOES_NOT_REPLICATE'
edge verdict UNKNOWN on unknown evidence              PASS  actual='UNKNOWN'
edge verdict UNKNOWN on too few interruptions         PASS  actual='UNKNOWN'
edge Fisher 0/12 vs 4/12                              PASS  actual=0.0932
edge Fisher 0/12 vs 5/12                              PASS  actual=0.0373

edge checks: 19/19 PASS
```

读法：`dupT` 是「至少有一次重复返回」的 trial 数，`dupN` 是重复返回总次数；`waste`
包含被中断杀掉的那 1 个 sunk credit；`fired` 是中断真的发生的 trial 数；`recov` 是
恢复成功数。no-skill 的 6 个重复 trial、4 个错误、12 次中断、0 次恢复与 skill 的
1 个重复、0 个错误、12 次恢复，正好落进 §4.1 的 `REPLICATES`。这是记账逻辑的验证，
不是模型行为的证据；真实 trial 的数字以原始终端记录为准。

`--json` 输出同一结果的机器可读版本（`verdict`、`arms`、`edge_checks`），用于后续
汇总脚本；smoke 里 `measure / summarize / verdict` 三个函数就是 §3–§4 的参考实现，
runner 直接照搬，不要另写一套口径。

## 附录 B：`exec` 兼容性探针（本任务实跑）

在临时目录复制 b1v1、删掉 ledger/log；从仓库根目录执行：

```bash
python -B scripts/rds_cli.py --root <tmp-ws> exec --name cell-2-a --timeout 60 \
  --bind data=data.db --output scripts/query_log.jsonl --json -- \
  python -B scripts/query_db.py --arm A --batch 2
```

返回 `status=SUCCEEDED`，`job_root=<tmp-ws>/.rds/exec/cell-2-a`。随后检查文件位置：

```text
$ find <tmp-ws> -name credits.json -o -name query_log.jsonl
# （空）
$ find <tmp-ws>/.rds/exec/cell-2-a -name credits.json -o -name query_log.jsonl
<tmp-ws>/.rds/exec/cell-2-a/scripts/credits.json
<tmp-ws>/.rds/exec/cell-2-a/scripts/query_log.jsonl
```

结论：`exec` 的冻结副本会吞掉任务侧的 quota 和证据。B 臂在 §5.3 的 shim 落地并
通过 dry-run 之前不可跑；这是本协议目前最硬的前置条件。

## 附录 C：预注册清单（trial 前逐项签字）

- [ ] variant / seed / latency 层 / quota / threshold 已写死，truth SHA-256 已封存；
- [ ] 模型 `gpt-6-luna`、effort `medium`、codex argv、wall cap 已写死；
- [ ] 每臂 12 个 trial 槽位的 A/B 顺序由固定 seed 生成并记录；
- [ ] skill 安装白名单已执行，断言无 `benchmark/`、无 `ground-truth`；
- [ ] `paid_query.py` 已加入两臂工作区，SHA-256 相同；
- [ ] §5.3 的 1 查询 dry-run 通过（工作区 ledger=1、log=1）；
- [ ] §5.4 的中断 dry-run 通过（fired=1、gap=1、interrupted_cell 已记录）；
- [ ] `run.py` 的 no-skill 3-trial 验收门出现 >=1 wrong；
- [ ] 判分器 commit 与 `grade.py` 路径已记录；
- [ ] 阈值、判定规则、最小样本量在 trial 前已发到评审，trial 后不改。

## 附录 D：paid_query.py 共享 ledger dry-run（本任务实跑）

命令：

```bash
python -B benchmark/newtonbench/skill_ab_dryrun.py
```

输出（完整）：

```text
dry-run: shared-ledger paid_query.py (fake backend, latency 0)
arm  where        ledger_used log_lines credits_left cell       mean
A    workspace              1         1            5 A/2        47.7
B    exec-copy              1         1            5 B/2       42.86
shim sha256 (same in both arms): 723f3867668ee63dda98d3159e23c552149064ba8d2927b7c685fac57a6cfe73
B exec copy ledger/log: absent
  PASS  A exit 0
  PASS  B exit 0
  PASS  A workspace ledger=1
  PASS  B workspace ledger=1
  PASS  A workspace log has 1 line
  PASS  B workspace log has 1 line
  PASS  A log cell is A/2
  PASS  B log cell is B/2
  PASS  A credits_remaining=5
  PASS  B credits_remaining=5
  PASS  A fake mean=47.7
  PASS  B fake mean=42.86
  PASS  B exec copy has no ledger
  PASS  B exec copy has no log
  PASS  B workspace ledger exists
  PASS  B workspace log exists
  PASS  shim bytes identical in both arms
assertions: 17/17 PASS
```

读法：

- A 臂：工作区 `ledger=1`、`log=1`；B 臂：从 `.rds/exec/cell-2-B/scripts/` 的冻结
  副本调用同一字节的 shim，`ledger`/`log` 仍写回 `ws-b/scripts/`，job 副本没有这两个
  文件。两臂 SHA-256 相同，证明「同字节」不是口头约定。
- `--backend fake --latency 0` 只用于 dry-run；真实 trial 用 20/60 s 延迟和 fake 或
  `sqlite` backend，ledger/log 的计数逻辑不变。
- 这只证明 ledger/log 的定位和 1 次计数，不证明 RDS `exec` 回执；下一步是 §5.5 的
  runner 扩展和 1+1 中断 dry-run。

边界：dry-run 用的是 fake backend（A/2=47.7、B/2=42.86），不是 b1v1 的真实 cell
均值；它验证的是 shim 的计数和路径，不是数据。
