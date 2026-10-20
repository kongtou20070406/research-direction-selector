# 外部审计核对：表达力与先验

核对对象：`kongtou20070406/research-direction-selector`，分支 `release/6.0.0-compass`，HEAD `cf8964b`（工作区干净）。
审计原文：`docs/rds-development-outline.md:52`（"表达力硬编码锁死在'一维玩具定律'"）、`:61`（"Agent 先验与符号枚举的本末倒置"）、`:87`（L2 行"枚举耗尽才叫 LLM"）。原文为外部审计报告，已随 `cf8964b` 收录进 outline。
方法：只读检索 + 读源码 + 本机实测（Python 3.13.5 / Windows），未改动任何代码。
判定口径：一条 claim 拆成可核查子句，**每个子句都成立**才算「正确」；有关键子句不成立或口径错位算「部分正确」；与代码相反算「错误」。

| # | 审计 claim | 判定 | 性质 |
|---|---|---|---|
| 1 | 表达力锁死（15 节点 / 200k 候选 / 单变量整数工具 / 硬编码数论原语 / `sys.settrace` 计步 20–50×） | **部分正确** | 混合：单变量整数是**真限制**；有界方言与计步是**设计选择** |
| 2 | Agent 先验倒置（Hard Tier `compass_codex` token 为 0，枚举耗尽才叫 LLM） | **部分正确** | 不是表达力限制，是**策略接线缺陷（已修）** |
| 3 | 方言限制（幂指数分母 ≤8、AST 节点上限等） | **正确** | **设计选择** |

---

## 1. 表达力锁死 —— 部分正确

### 1.1 逐子句核对

| 子句 | 代码事实 | 判定 |
|---|---|---|
| `MAX_NODES=15` | `scripts/rds_compass_model.py:22` 确实 `MAX_NODES = 15`。但这是**硬上界**，不是实际搜索宽度：plan 默认 `max_nodes=7`（`scripts/rds_compass_declaration.py:33`），`synthesize()` 默认 `max_nodes=7`（`rds_compass_model.py:783`），RSI 资格判定用 `min(5, plan['synthesis']['max_nodes'])`（`scripts/rds_compass_rsi.py:373`）；上界只在 `BOUNDS` 里 clamp（`rds_compass_declaration.py:43-44`）和 `synthesize` 的 require 里生效（`rds_compass_model.py:794`） | 数值对，口径错 |
| `MAX_CANDIDATES=200000` | 常量名是 `MAX_SYNTH_CANDIDATES = 200000`（`rds_compass_model.py:32`），同样只是上界；默认 `max_candidates=20000`（`rds_compass_declaration.py:33`、`rds_compass_model.py:784`），字段扩散搜索用 `min(200000, bound*10)`（`rds_compass.py:633`） | 数值对，名字/口径错 |
| 只能枚举"标量代数式" | "标量"成立：所有算子作用在标量精确值上（`rds_compass_model.py:33-39`），没有向量/张量/微分算子。"代数式"偏窄：默认 grammar 只给四则（`rds_compass_model.py:769-779`，默认 `constants=[0,1,2,-1]`、`operators=['add','sub','mul','div']`），但引擎支持 `neg/abs/floor/ceil/sqrt/exp/log/sin/cos/pi/mod/pow/min/max/lt/le/eq/ite` 共 22 个算子（`:33-38`）：升级方言 `EXTENDED_OPERATORS` 含 `sqrt/exp/log/sin/cos/pi`（`rds_compass.py:579`，在默认四则无 fit 时启用 `:611-618`），结构层还有 regime split（`ite` 分段，`rds_compass_model.py:924-954`）、残差修正（`:783-784, 903`）与单点脉冲补丁（`rds_compass.py:659` 起） | 部分成立 |
| `rds_compass_rsi.py` 只收单变量 | `rds_compass_rsi.py:144`：`require(len(defs) == 1 and len(defs[0].args.args) == 1, ...)`。入口函数只能一个位置参数 | **正确** |
| 只对整数输入触发 | `rds_compass_rsi.py:424` 只从 `plan['inputs']` 里挑 `spec.get('integer')` 的变量；`:427-428` 没有整数输入直接返回 `NO_TOOL_FAMILY_FOR_DOMAIN`；`:453` 把候选输入 `int(model.exact(...))` 后送子进程；工具返回值必须是 `int`/`Fraction`（`:73`） | **正确** |
| 硬编码数论原语"30 个" | `rds_compass_rsi.py:104-131` 的 `families()` 实测 30 个唯一函数名（本机 `len(families())==30`，`set` 去重后仍 30），与 `docs/releases/6.0.0.md:195` "base catalogue grew from 19 to 30 tools" 一致 | **正确** |
| `sys.settrace` 计步 | `rds_compass_rsi.py:44` `STEP_LIMIT = 200000`；runner 源码在 `:46-82`（字符串常量 `RUNNER`），tracer 计事件、超限抛 `Budget`；`sys.settrace(tracer)` 在 `:69`，清理在 `:72/:78`；runner 以 `python -I -B -c` 子进程执行（`:219-220`），不是同进程内 trace | **正确**（但"逐字节码"措辞不准：`settrace` 是行/调用事件级，不是字节码级） |
| 20–50× 解释器开销 | 代码里没有这个数字，需实测。本机复现（每个函数取 `families()` 里的源码模板，60 个整数输入 400–459，5 次中位数）：`issquare` 46.6×、`val2` 39.4×、`isprime` 13.8×、`fib` 10.0×、`collatz` 7.5×、`divisors` 5.9× | 短工具落在 20–50×，长循环工具低于该区间 |
| 附带断言"禁止调用 NumPy/PyTorch" | 成立，但原因不是 `settrace`：`scripts/rds_tools.py:64-66` 的源码 allowlist 只允许 `import math`、`fractions` 与 `from json import loads`，其他 import 直接拒绝 | 正确，归因错 |

### 1.2 结论

审计把"硬上界"当成"实际搜索规模"，把"默认四则方言"当成"引擎全部能力"，两处口径偏；`settrace`/单变量/30 个原语三项事实准确。

- **真限制**：`rds_compass_rsi.py:144`（单参数）+ `:424/:453`（整数域）是硬编码能力边界，Compass 无法在实数/多变量输入上合成特征工具。这是审计里唯一站得住的"表达力锁死"。
- **设计选择**：`MAX_NODES=15` / `MAX_SYNTH_CANDIDATES=200000` / `MAX_ROOT=8` / `MAX_BITS=512` / `MAX_POINTS=256` 这一整套有界方言，是为了让 replay 在精确有理数上可判定、可复现；`settrace` 计步是为了在子进程里给出确定的步数预算（`STEP_LIMIT`）和 60 秒超时（`rds_compass_rsi.py:45`），代价是数量级开销，不是缺陷。

---

## 2. Agent 先验倒置 —— 部分正确

### 2.1 0 token：核到，且只能核到 release notes

`docs/releases/6.0.0.md:223` 原文：

> The pre-registered gate did not pass on any task: the real bare agent made no false certification, so these traps did not bite the frontier model. **`compass_codex` used 0 Codex tokens in all 30 rows. That arm never called the model, so it measured deterministic Compass.**

同一事实在 `docs/compass.md:273` 有一句概述（"the jump agent was never asked when synthesis had nothing, so the Compass-with-Codex arm never called the model"）。

Hard Tier 的原始行级结果**未核到**：本分支没有 `bench/compass-hard-tier`，`benchmark/compass/results/` 只有 default tier 的 `compass-benchmark-codex.json` 等文件，里面没有 hard-* 任务的 0-token 行。所以这条只能引用 release notes，没有做二次数据核算。另注：按 `6.0.0.md:214/220` 的口径（4 任务、N=3、`hard-belief-drift` 2 sessions）推算，30 rows 是两 arm 合计，`compass_codex` 自身约 15 行；不影响 0 token 的结论，但"30 次运行"若指单 arm 会多算一倍。

### 2.2 "枚举耗尽才叫 LLM"：时点与方向都对不上

- **记录时点（6.0.0 hard tier）**：`docs/releases/6.0.0.md:228` 把病因写成已修缺陷——"The jump agent was called only to dispose of machine fits. When synthesis found nothing, which is exactly when a model could help, the drive ended without asking. The packet now requests `PROPOSALS` in that case." 即当时是**枚举成功才叫 agent 处置，枚举失败直接收工**。这正是 30 行 0 token 的原因：不是"枚举耗尽才叫"，而是"枚举耗尽**不叫**"。
- **当前代码（修复后）**：`rds_compass.py:745` 的 `request` 由 `items` 决定——有机器 rival 时 `DISPOSITIONS`，没有时 `PROPOSALS`；`escape()` 在无 rival 且无 agent 时直接记 `NO_RIVAL_WITHIN_BOUND`（`:998`），有 agent 时两条路都会 `_call_agent()`（`:1007-1008`，实现 `:853`）。回归测试 `tests/test_rds_compass_hard_followup.py:98-106`（`test_agent_is_asked_for_proposals_when_synthesis_finds_no_rival`）钉住了"枚举失败也要问 agent"。
- 所以修复后"LLM 排在机械枚举之后"成立，"只在枚举耗尽时才叫"仍不成立：有机器 fit 时也会调它做处置，只是不用它提候选。

### 2.3 结论

"0 token"是事实（release notes 明写），"Agent 先验倒置"的**方向叙述与 6.0.0 时点的实现相反**；审计把修复后的 `PROPOSALS` 行为当成旧缺陷，又把旧缺陷的后果（0 调用）当成"排在枚举后面所以没用上"。两种病因的修法不同：前者是路由时机，后者是 arm 接线。这条不是表达力限制，是策略/接线缺陷，且已在 HEAD 修复。

---

## 3. 方言限制 —— 正确

| 断言 | 证据 |
|---|---|
| 幂指数分母 ≤8 | `MAX_ROOT = 8`（`rds_compass_model.py:30`）；`pow` 要求指数是常量、`Fraction(指数).denominator <= MAX_ROOT` 且 `abs(指数) <= MAX_POWER`（`:146-148`，`MAX_POWER=8` 在 `:29`）；合成枚举里指数候选同样过滤（`:879`） |
| AST 节点上限 | 单表达式 `MAX_NODES=15`（`:22`）；规则手册 `MAX_RULEBOOK_NODES=64`（`:25`，用于 `:410/:448` 的 rulebook 解析）；复合表达式（regime split / residual）按 `2*15+3=33` 归一化（`:903`、`:950`） |
| "等"（同源限制） | `MAX_BITS=512` 精确有理数位宽（`:26`，校验 `:107`）；`MAX_TEXT`（`:28`）；`MAX_POINTS=256`（`:31`、`:798`）；grammar 常数至多 24 个（`:776`）；`synthesize` 的 operator 集合不含三元 `ite`（`:778`，`ite` 只在 regime split 内构造） |
| 与 agent 侧一致 | `benchmark/compass/codex_jump.py:40-45` 给 Codex 的方言说明直接写 "an exponent is a rational constant with denominator at most 8, for example `x ** 1.5` or `x ** (2/3)`"，并列出 `+ - * / % **`、比较、`abs/floor/ceil/sqrt/exp/log/sin/cos/pi/min/max` 与条件表达式 |

结论：**正确**。这条是纯**设计选择**：有界、精确、可判定的方言换取 replay 的确定性；审计自己也把它列为"方言限制"，没有把它当 bug。

---

## 真限制 vs 设计选择

| 类别 | 条目 | 依据 |
|---|---|---|
| **真限制** | RSI 工具只能单参数、只在整数输入上评估，表达不了多变量/连续域特征 | `rds_compass_rsi.py:144`、`:424`、`:427-428`、`:453` |
| **设计选择** | 15 节点 / 200k 候选 / 分母 ≤8 / 64 节点 rulebook / 512-bit / 256 点 / 30 个默认原语：有界方言换精确 replay | `rds_compass_model.py:22-32`、`:147-148`、`:776-778`；`rds_compass_declaration.py:33,43-44` |
| **设计选择** | `sys.settrace` 计步 + 子进程隔离 + 60 s 超时：确定步数预算的代价是 6–47× 开销 | `rds_compass_rsi.py:44-46,64-82,212-230` |
| **已修缺陷** | `compass_codex` 0 token：hard tier 时 jump agent 只在有机器 fit 时被叫，枚举失败不问 | `docs/releases/6.0.0.md:223,228`；`rds_compass.py:745,998,1007`；`tests/test_rds_compass_hard_followup.py:98-106` |

## 复核命令

```bash
cd C:/Users/XU/rds-357
grep -n "MAX_NODES\|MAX_ROOT\|MAX_SYNTH_CANDIDATES\|MAX_RULEBOOK_NODES" scripts/rds_compass_model.py
grep -n "STEP_LIMIT\|settrace\|def families\|args.args" scripts/rds_compass_rsi.py
grep -n "max_nodes.*max_candidates" scripts/rds_compass_declaration.py
grep -n "0 Codex tokens\|never called the model\|never asked" docs/releases/6.0.0.md
python -c "import sys; sys.path.insert(0,'scripts'); import rds_compass_rsi as r; print(len(r.families()))"   # 30
```

`settrace` 开销实测：脚本放在系统临时目录（未入库），对 `families()` 的 `issquare/val2/isprime/fib/collatz/divisors` 源码用 `exec` 装载，60 个整数输入、5 次中位数、`sys.settrace` 前后各一次，得到上文倍率区间。

## 未核到

- Hard Tier 30 行的原始记录（0-token 的逐行账本）：本分支无 `bench/compass-hard-tier`，`benchmark/compass/results/` 无 hard-* 结果文件，只有 `docs/releases/6.0.0.md:223` 的发布说明。
- 审计"20–50×"的原始测量环境与函数集合：本机只复现出短工具 39–47×、长循环 6–14×，无法确认其取样口径。
