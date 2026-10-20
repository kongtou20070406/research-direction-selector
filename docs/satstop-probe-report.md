# SATSTOP 探针报告：regime-double 预算复跑与 COVERAGE_GAP 归因

- 运行时间：2026-10-20 18:29–18:41（+0800），本机 CPython 3.13，Windows
- HEAD 基线快照：`D:\Myfile\work space\RDS\.triage-head`（`git archive HEAD` 导出，**非 git 仓库**，本轮未在其中执行任何 git 命令）
- 工作区：`D:\Myfile\work space\RDS\rds-357`（分支 `release/6.0.0-compass`）
- 本轮性质：**只读 + 只跑探针**。未执行任何 git 写命令（`git log` / `git status` / `git show` / `git reflog` 均为只读查询）
- **未改动的文件**（运行前后 sha256 保持一致）：
  - `rds-357/scripts/rds_compass.py`、`rds-357/scripts/**` 全部
  - `rds-357/benchmark/newtonbench/pareto.py` → `db31482c01aec20809542cbb938bebf6a0aad4455400b5af5f3da57c71c2f77f`
  - `rds-357/benchmark/newtonbench/results/pareto.json` → `1ad333b380745b772cdb57f127a75fde58935afd2339ea66ef54ac16b5b19db3`
  - `rds-357/benchmark/newtonbench/pareto-review.md`（未触碰）
- **唯一被写的文件**：探针脚本 `rds-357/benchmark/newtonbench/probe_cell.py`（未跟踪新文件，在本轮明确授权范围内）。`scripts/` 下一个字节都没有改动。

## 0. 证据完整性披露：工作区 `scripts/rds_compass.py` 在本轮运行期间被外部 agent 改动

本轮**没有**对 `scripts/` 发起任何写操作，但工作区该文件在运行窗口内确实被并发 agent 改了一次。时间线（文件 mtime，+0800）：

| 时间 | 事件 | `scripts/rds_compass.py` sha256 |
| --- | --- | --- |
| 18:27:55 | 探针脚本改前版本写出（`b1734877…`） | 未测 |
| 18:34:34 | A 部分 work b12 写出 | 未测 |
| 18:35:34 | A 部分 work b32 写出 | 未测 |
| ≈18:36:00 | 本次会话读该文件 | `4333c595b51c339ed49c79e1a73f0ba167b1f8ea45d37a03bc94ced76e3f893f`（≠ HEAD） |
| 18:36:49 | B 部分 mg2 跑完 | 同上（推断） |
| 18:37:23 | B 部分 mg0 跑完 | 同上（推断） |
| **18:37:42** | **外部 agent 写入该文件** | 变为 `d62590bdc5fcbef0361980a57b5d0a8f68e9b948c01db74533b1ae0d63d7f6df` |
| 18:38:00 | B 部分 mg8 跑完 | `d62590bd…` |
| 18:38:40 | 附加对照 mgdefault 跑完 | `d62590bd…` |

现状（本轮结束时）：工作区 `rds-357/scripts/rds_compass.py` 与 `.triage-head/scripts/rds_compass.py` **逐字节相同**（`cmp` 无差异，双方 sha256 均为 `d62590bd…`），第 428 行为 HEAD 版规则 `return bool(gaps) and max(gaps) > min(gaps)`。

- `git status --porcelain` 无 tracked 修改项；`git log -S "len(plan['candidates']) // 10" --all` 全历史无命中。
- 结论：任务描述中的 G2 未提交改动**从未被提交**，且**已不在工作区**（18:37:42 被并发 agent 撤销回 HEAD 版本）。`4333c595…` 的字节已被覆盖，不可恢复。
- 影响评估：A 部分 work 侧三次跑与 B 部分 mg2/mg0 应发生在 G2 存在期间（**推断**，依据是 18:36 实测的 `4333c595…` 与任务描述）；mg8/mgdefault 发生在 G2 撤销之后（**实测**）。
- A 部分结论**不依赖**上述推断：它直接来自输出文件的字节比较（下表 sha256 成对相等）。G2 撤销前后在同一 (world, budget) 上的实验指标也完全一致（见第 3 节），交叉印证 G2 对这些单元无边际影响。

## 1. A 部分：regime-double 四档 × 两仓库

命令（在各自仓库根目录）：
`python -B benchmark/newtonbench/probe_cell.py regime-double <budget> <输出路径>`

| # | world | budget | arm | experiments | status | predict | actions | 字节 | sha256 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | regime-double | 12 | HEAD | 12 | GOAL_IDENTIFIED | `ite((x < (19/2)), (1 + x), (x + x))` | RIVALS 5, CONFIRM 4, EXPERIMENT 1, PROBE 7, ESCAPE 1 | 590 | `1805beefb3fd6fe5315993227902b8225baaa8b2e32d55d11021c17a3b80821a` |
| 2 | regime-double | 12 | 工作区 | 12 | GOAL_IDENTIFIED | 同上 | 同上 | 590 | `1805beefb3fd6fe5315993227902b8225baaa8b2e32d55d11021c17a3b80821a` |
| 3 | regime-double | 16 | HEAD | 16 | GOAL_IDENTIFIED | `ite((x < 10), (1 + x), (x + x))` | RIVALS 5, CONFIRM 4, EXPERIMENT 1, PROBE 11, ESCAPE 1 | 587 | `2e63faf1195db24bbf40e5d023fec1965f25cfbc6559d1752a57e6b3d6cbdae9` |
| 4 | regime-double | 16 | 工作区 | 16 | GOAL_IDENTIFIED | 同上 | 同上 | 587 | `2e63faf1195db24bbf40e5d023fec1965f25cfbc6559d1752a57e6b3d6cbdae9` |
| 5 | regime-double | 24 | HEAD | 21 | GOAL_IDENTIFIED | `ite((x < 10), (1 + x), (x + x))` | RIVALS 7, CONFIRM 3, EXPERIMENT 1, PROBE 15, ESCAPE 1, HOLDOUT 2 | 604 | `a8a1fd223432a7d50e64150fbeaf026d7dc730ecff31f32cdc273ae385f30955` |
| 6 | regime-double | 24 | 工作区 | 21 | GOAL_IDENTIFIED | 同上 | 同上 | 604 | `a8a1fd223432a7d50e64150fbeaf026d7dc730ecff31f32cdc273ae385f30955` |
| 7 | regime-double | 32 | HEAD | 21 | GOAL_IDENTIFIED | `ite((x < 10), (1 + x), (x + x))` | RIVALS 7, CONFIRM 3, EXPERIMENT 1, PROBE 15, ESCAPE 1, HOLDOUT 2 | 604 | `2d4068ff1aa2c709b81735f8c7cf5f50f6b72eb092221f881deb373b9f0fd41a` |
| 8 | regime-double | 32 | 工作区 | 21 | GOAL_IDENTIFIED | 同上 | 同上 | 604 | `2d4068ff1aa2c709b81735f8c7cf5f50f6b72eb092221f881deb373b9f0fd41a` |

输出文件路径：
- `D:\Myfile\work space\RDS\satstop-head-b12.json` / `satstop-work-b12.json`
- `D:\Myfile\work space\RDS\satstop-head-b16.json` / `satstop-work-b16.json`
- `D:\Myfile\work space\RDS\satstop-head-b24.json` / `satstop-work-b24.json`（**本会话开始前已存在，本轮未重跑、未覆盖**，sha256 由本轮重新计算）
- `D:\Myfile\work space\RDS\satstop-head-b32.json` / `satstop-work-b32.json`

对照冻结基线 `results/pareto.json` 的 rds 臂（B=12/16/24/32 → experiments 12/16/21/21）：本轮 6 次新跑全部吻合。四档中 HEAD 与工作区输出**逐字节相同**（`cmp` 无差异）。

## 2. B 部分：`--max-gap` 扫描（工作区，regime-double B=24）

探针脚本新增 `--max-gap`（`type=int`，**默认 2**，与 `pareto.py:67` 硬编码的 `'max_gap': 2` 一致），只覆盖声明里的 `verification.max_gap` 这一个值。**未改动** `scripts/` 下任何代码，未调整任何容差。

| max_gap | experiments | status | predict | actions | 输出文件 | 字节 | sha256 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2 | 21 | GOAL_IDENTIFIED | `ite((x < 10), (1 + x), (x + x))` | RIVALS 7, CONFIRM 3, EXPERIMENT 1, PROBE 15, ESCAPE 1, HOLDOUT 2 | `satstop-mg2-b24.json` | 604 | `a8a1fd223432a7d50e64150fbeaf026d7dc730ecff31f32cdc273ae385f30955` |
| 0 | 21 | GOAL_IDENTIFIED | 同上 | 同上 | `satstop-mg0-b24.json` | 604 | `8577e5737a07988c726bf4393639ee2a676bc9ce1c5e9f92b04b72340d5a6444` |
| 8 | 21 | GOAL_IDENTIFIED | 同上 | 同上 | `satstop-mg8-b24.json` | 604 | `6679124a1859ad24d93b7ec98ca596da1741b30a1ecea011c5aba7e9f60f4747` |
| （默认，未传 flag） | 21 | GOAL_IDENTIFIED | 同上 | 同上 | `satstop-mgdefault-b24.json` | 604 | `a8a1fd223432a7d50e64150fbeaf026d7dc730ecff31f32cdc273ae385f30955` |

补充事实：
- `--max-gap 2` 的输出与既有的 `satstop-work-b24.json` / `satstop-head-b24.json` **逐字节相同**（同一 sha256）：声明参数未调时行为不变。
- 未传 flag（默认 2）与显式 `--max-gap 2` 输出**逐字节相同**：默认值确实生效。
- 三个 mg 输出的**唯一**差异是回显的 `declaration.verification.max_gap`（`diff` 各只有第 25 行一处）；`experiments` / `status` / `predict` / `actions` 完全相同。

### 2.1 决策级 reason 直方图（额外只读诊断）

`--max-gap` 扫描出的差值是 0，单靠它分不清「规则从未命中」与「命中但被顶替」。为此给探针脚本再加了一个**只读**选项 `--dump-decisions <path>`（不改任何声明值、不改主 JSON 形状），用默认参数（max_gap=2）在**当前工作区**（已是 HEAD 版代码）跑一次 B=24：

- `satstop-reasons-b24.json`（604 字节，sha256 `a8a1fd223432a7d50e64150fbeaf026d7dc730ecff31f32cdc273ae385f30955`，与前四个 b24 文件逐字节相同）
- `satstop-reasons-b24.decisions.json`（1908 字节，sha256 `69e2dfecc1aadda12b97ef41a51a44f92d0214c9e7f6bfbb5ac74119cde15def`）

29 条决策的 `(action, reason)` 分布：

| action | reason | 次数 |
| --- | --- | --- |
| RIVALS | null | 7 |
| CONFIRM | `MINIMUM_CONFIRMATIONS` | 3 |
| EXPERIMENT | null | 1 |
| PROBE | **null** | **15** |
| ESCAPE | null | 1 |
| HOLDOUT | null | 2 |

`COVERAGE_GAP` 出现 **0 次**；`SPEND_BUDGET_ON_REFUTATION` 出现 **0 次**。

`scripts/rds_compass.py` 里 PROBE 有三个出口，只有一个是 `max_gap` 规则：

- `rds_compass.py:472` → 带 `reason: 'NO_RIVAL_WITHIN_BOUND_AT_CURRENT_EVIDENCE'`
- `rds_compass.py:490` → `{'action': 'PROBE', 'candidate': boundary or _spread(state, 1)[0], 'basin': diagnosis}`，**不带 reason 字段**
- `rds_compass.py:536` → 带 `reason: 'COVERAGE_GAP'`（即任务假设中的 `max_gap` 规则，调用 `_boundary_probe(state, min_gap=max_gap)`）

15 个 PROBE 的 reason 全为 `null` → 它们全部来自 **`rds_compass.py:490`**，没有一个是 `COVERAGE_GAP`。

机制（解释为什么 `--max-gap` 对该单元无效）：`:490` 用的是 `boundary = _boundary_probe(state)`，而 `_boundary_probe` 的签名是 `def _boundary_probe(state, min_gap=2)`（`rds_compass.py:390`），**默认 `min_gap=2` 是硬编码的**，不看声明里的 `max_gap`。声明里的 `max_gap` 只在 `:536` 那条从未命中的规则上生效。

## 3. 结论

**(a) G2 是否在任何一档改变了实验消耗？——没有。**

regime-double 的四档（12/16/24/32）上，HEAD 与工作区的探针输出**逐字节相同**（成对 sha256 相等，`cmp` 无差异）。`experiments` 四档依次为 12 / 16 / 21 / 21，与冻结基线 `results/pareto.json` 的 rds 臂一致。`status`、`predict`、`actions` 计数也全部相同。即：这处停止规则改动在 regime-double 的四档上都没有改变实验消耗。

**(b) 21 次消耗里 COVERAGE_GAP 探针占几次？——0 次（估计值），且该估计已由决策级 dump 直接证实。**

用 mg0 与 mg2 的差值估计：`21 − 21 = 0`。补强证据：`--dump-decisions` 显示 29 条决策里 `COVERAGE_GAP` 出现 0 次，15 个 PROBE 的 reason 全为 `null`（`rds_compass.py:490` 路径）。因此任务假设「15 个 PROBE 来自 `max_gap`/COVERAGE_GAP 规则」**被证伪**。

21 次消耗的实际构成：`PROBE 15 + CONFIRM 3 + EXPERIMENT 1 + HOLDOUT 2 = 21`（`RIVALS`、`ESCAPE` 不购买实验）；`CONFIRM` 的次数全部来自 reason `MINIMUM_CONFIRMATIONS`，而非 `SPEND_BUDGET_ON_REFUTATION`。所以 regime-double@24 的消耗主要由 **PROBE（15/21 ≈ 71%）** 驱动，而其中主导规则是 `rds_compass.py:490` 的跳转分支探针，不是 `max_gap` 规则，也不是 `spend_budget` 控制的 CONFIRM 分支。

**这个估计的边界**：

1. 差值 0 本身只能说明 `max_gap` 规则在该单元上没有**边际**贡献，无法单独区分「从未触发」与「触发但被其他规则顶替（同样买 PROBE，故 experiments 不变）」。本报告用 reason 直方图补上了这一刀，这一区分依赖 `decide()` 返回的 `reason` 字段被决策记录忠实保留——证据是 `CONFIRM` 的 `MINIMUM_CONFIRMATIONS` 被保留，而 `:490` 返回的字典本身就没有 `reason` 键。
2. `--max-gap 0/2/8` 只覆盖 regime-double 这一个 world、B=24 这一个预算；其他 world / 预算档位上 `max_gap` 规则是否触发**未测**，不能外推。
3. `--max-gap 0` 关闭的只是 `:536` 那一条规则；`:490` 路径的阈值来自 `_boundary_probe` 的硬编码默认值 2，本轮没有（也不应）改动它。
4. 结论 (b) 基于 G2 撤销后的 HEAD 版代码（第 0 节）；不过 `mg2`（G2 存在期间）与 `reasons`（G2 撤销后）逐字节相同，两者互证。

## 4. 复现命令

```bash
# A 部分（各自仓库根目录）
cd "D:/Myfile/work space/RDS/.triage-head" && python -B benchmark/newtonbench/probe_cell.py regime-double 12 "D:/Myfile/work space/RDS/satstop-head-b12.json"
cd "D:/Myfile/work space/RDS/.triage-head" && python -B benchmark/newtonbench/probe_cell.py regime-double 16 "D:/Myfile/work space/RDS/satstop-head-b16.json"
cd "D:/Myfile/work space/RDS/.triage-head" && python -B benchmark/newtonbench/probe_cell.py regime-double 32 "D:/Myfile/work space/RDS/satstop-head-b32.json"
cd "D:/Myfile/work space/RDS/rds-357"     && python -B benchmark/newtonbench/probe_cell.py regime-double 12 "D:/Myfile/work space/RDS/satstop-work-b12.json"
cd "D:/Myfile/work space/RDS/rds-357"     && python -B benchmark/newtonbench/probe_cell.py regime-double 16 "D:/Myfile/work space/RDS/satstop-work-b16.json"
cd "D:/Myfile/work space/RDS/rds-357"     && python -B benchmark/newtonbench/probe_cell.py regime-double 32 "D:/Myfile/work space/RDS/satstop-work-b32.json"

# B 部分
cd "D:/Myfile/work space/RDS/rds-357" && python -B benchmark/newtonbench/probe_cell.py regime-double 24 "D:/Myfile/work space/RDS/satstop-mg2-b24.json" --max-gap 2
cd "D:/Myfile/work space/RDS/rds-357" && python -B benchmark/newtonbench/probe_cell.py regime-double 24 "D:/Myfile/work space/RDS/satstop-mg0-b24.json" --max-gap 0
cd "D:/Myfile/work space/RDS/rds-357" && python -B benchmark/newtonbench/probe_cell.py regime-double 24 "D:/Myfile/work space/RDS/satstop-mg8-b24.json" --max-gap 8

# 决策级归因
cd "D:/Myfile/work space/RDS/rds-357" && python -B benchmark/newtonbench/probe_cell.py regime-double 24 "D:/Myfile/work space/RDS/satstop-reasons-b24.json" --dump-decisions "D:/Myfile/work space/RDS/satstop-reasons-b24.decisions.json"
```

单次运行约 22–35 秒。探针脚本版本 sha256：改前 `b1734877561c9e17584efda927489f26d723404029fdba27fd727727eceabe59`（`.triage-head` 副本仍是此版本，未改），加 `--max-gap` 后 `570531d246326e615dde218d37d485d7209b0d5c7639f89fbbb3f4e3584f1fac`，再加 `--dump-decisions` 后 `1d11e0fb26fdc2bf1d59e07002345b8b393982b604f5487f5a8f4c66e8d4ac38`（当前）。
