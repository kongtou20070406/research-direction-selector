# source seam：让外部发现方法交候选进来

制定：2026-10-21，**在任何一次实验运行之前**写下。

## 0. 问题

Compass 的 law 只有一个生产者：`scripts/rds_compass_model.py:888` 的 `synthesize`
自底向上枚举。`docs/benchmarks/size-ceiling-20261021.md` 已经测死这条路的上限：

- 40 个真解里 10 个 AST size > 7（默认 `synthesis.max_nodes`）。
- 把这 10 个里能搜的 4 个拿出来，`max_nodes` 7→9→12、`max_candidates` 提到
  200000（`MAX_SYNTH_CANDIDATES`），**全部 `SEARCH_TRUNCATED`、fits=0**。
  `attempted` 恒 20000、`distinct` 一字不差——20000 候选在 size≤7 内就烧完了。
- `feynman-I.41.16` 真解 size 24，连 `MAX_NODES = 15`（单表达式上限）都超，
  永远不可能被枚举到。

这不是预算问题，是**生成器**的结构天花板。而 Stage 3 的方向就是把生成外包：
PySR、LLM、CAS 符号求解、手工推导都能产出 law，内核只保留判定权
（对应 `AGENTS.md` §I.2：任务真相 != 科学支持）。

## 1. 改动

### 1.1 `synthesize(..., seeds=())`

`scripts/rds_compass_model.py:888` 加 `seeds=()` 参数。在枚举开始之前，把每个
seed 表达式 `parse` 后**直接判定**：

```python
def consider_seed(expression):
    """A source-supplied law is judged, never enumerated."""
    node = parse(expression, set(names))
    if size(node) > MAX_RULEBOOK_NODES:
        return
    spans = spans_of(node)
    values = values_from(spans)
    if all(v is not None and matches(v, t, tol) for v, t in zip(values[:n], targets)):
        if node not in fits:
            fits.append(node)
```

**关键差别：seed 不进 `pools`。**`consider` 里有 `pools[size(normalized)].append(...)`，
而 `pools` 只有 `1..max_nodes` 键；size 24 的 law 会直接 `KeyError`。seed 走的是
「判定」而不是「成为进一步组合的材料」，所以不进池是正确的语义，不是绕过的补丁。

**seed 的 size 上限是 `MAX_RULEBOOK_NODES = 64`，不是 `max_nodes`。** 这是设计意图：
外部生成器负责复杂度，内核负责它是否复现全部测量。`compile_hypothesis` 本来就用
`MAX_RULEBOOK_NODES`，所以能通过 seed 判定的 law 一定能进规则书。

**`fits` 的顺序：seed 在前。**`keep=4` 的早停在 `consider` 里，seed 塞满 4 个之后
枚举不再开始——外部给的候选优先于枚举候选，这是有意的（枚举救不回这些方程）。

### 1.2 `plan['sources']`

`benchmark/newtonbench` 的 campaign 声明里新增：

```json
"sources": [{"id": "pysr-run-3", "laws": ["x + 1", "..."]}]
```

`scripts/rds_compass.py` 的 `_fits` 把全部 `laws` 摊平成 `seeds`，传给 **WHOLE_LAW 这一次**
`_fits_synthesize`；`_fits_memo_key` 也带上 seeds（否则不同 source 会命中同一缓存）。

只传 WHOLE_LAW 是充分的，不是漏传：GRAMMAR_ESCALATION 与 DERIVED 都排在
`not whole['fits']` 之后，seed 一旦命中就根本不进入；REGIME_SPLIT 排在 `not found`
之后同理。RESIDUAL_CORRECTION 是在被证伪的 law 上搜**残差**，seed 的语义是完整 law，
放进去是错的。

声明校验（`scripts/rds_compass_declaration.py`）：`sources` 必须是列表，每项
`{'id': str, 'laws': [str, ...]}`，`id` 不得带首尾空格且长度为 1–80 字符，`laws` 为非空字符串列表。
每条 law 必须能解析、只引用声明输入，且 AST 不超过 `MAX_RULEBOOK_NODES`；超限在 ingest
拒绝，不让 `synthesize` 静默略过。拟合成功的假设将来源 ID 编入既有 `origin` 字段，初始形式为
`rival-synthesis:SOURCE:["source-id", ...]`；后续 jump 会保留来源并前缀 jump ID，例如
`jump:j01:SOURCE:["preregistered-truth"]`。同一表达式由多个 source 提交时保留全部 ID。

### 1.3 为什么不是 subprocess

source 声明的是**一批已经算好的表达式**，不是要内核去执行的东西。调用 PySR / LLM
是调用方的事。这样不新增进程、不新增超时逻辑、不把外部工具的不确定性带进账本，
也不违反 `AGENTS.md` §II.3「不引入线程/子进程除非明确要求」。

## 2. 主格

`feynman-I.41.16`，B=8，`benchmark/newtonbench/devset_run.py` 的构造
（9 个声明点，grammar `['div','exp','mul','pi','pow','sub']`，constants `['0','1','2','3']`）。

| 项 | 门槛 |
|---|---|
| 不加 source | `SEARCH_EXHAUSTED`、`claim_exact=False`（基线，已实测 398.2 s） |
| 加 source（真解原文） | `GOAL_IDENTIFIED`、`claim_exact=True` |
| `law` | 与真解在 9 个声明点上逐点相等（不要求字符串相等，`normalize` 会改写形式） |
| `experiments` | ≤ 8 |

真解原文（`benchmark/newtonbench/results/truth-size.json` 的 `law` 字段）：

```
1.054571817e-34*omega**3/(pi**2*2.99792458e8**2*(exp(1.054571817e-34*omega/(1.380649e-23*T))-1))
```

`parse` 接受任意 int/float 字面量（`scripts/rds_compass_model.py:523`），所以常数
不是障碍；容差 `rel = 1/1000` 足以吸收 `text(float)` 的表示误差。

### 实测结果

三臂均用 `feynman-I.41.16 / B=8`、独立临时 campaign root，完整候选输入不共享缓存：

| 变体 | 用时 | 状态 / claim | claim_exact | experiments | 独立认证 |
|---|---:|---|---:|---:|---|
| 无 source | 398.2 s | `SEARCH_EXHAUSTED` / `NOT_ESTABLISHED` | false | 8 | 未声称 |
| 真解 source | 29.5 s | `GOAL_IDENTIFIED` / `CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS` | true | 8 | 32 点，零误差，`false_certification=false` |
| 错解 source（分母 `c²` 改成 1） | 399.4 s | `SEARCH_EXHAUSTED` / `NOT_ESTABLISHED` | false | 8 | 未声称，`NOT_CLAIMED` |

真解候选 AST size 24，大于默认枚举上限 7；内核仍逐条检查原始测量并独立认证。错解没通过测量拟合，未变成 claim。首个组合探针因三个变体共用同一 campaign root，后两行实际没有运行（`FileExistsError: [WinError 183]`）；表中时间来自后续独立目录的重跑。初次文件的两条 `0.0s` 不作证据。

`benchmark/newtonbench/results/source-seam-20261021.json` 保存三臂初测结果；从仓库根目录运行
`python -B benchmark/newtonbench/source_seam.py benchmark/newtonbench/results/source-seam-20261021.json`
可重跑全部三臂，`--variant truth-source` 可单独复验真解臂。实验只量内核消费预计算表达式后的成本；source 生成/LLM/PySR/CAS 的耗时与 token 不进入 ProjectStore，目前保持 **UNKNOWN**，不能据此声称端到端 Pareto 改善。

runner 另读账本回查 source origin，结果写在 `benchmark/newtonbench/results/source-seam-origin-check.json`。
它应包含 `jump:j01:SOURCE:["preregistered-truth"]`，说明规则书里仍能追到候选来源。

## 3. 反例门槛（比主格重要）

同一格塞一个**错** law（把真解里的 `2.99792458e8**2` 改成 `1`）：

| 项 | 门槛 |
|---|---|
| `claim_exact` | 不因错 law 变成 True |
| `false_certification` | 不得为 true |
| `fits` | 不得含那条错 law |

## 4. 安全项

- `test_rds_compass_model.py` 27 全绿；新增 source seam 模块覆盖空 seeds、错 seed、越界输入、provenance 与 ingest 上限。
- `test_rds_compass_drive.py -k LawTests` 13 全绿。
- 不带 `sources` 的 campaign 与改前逐字段相同：跑 `sparse-mid / rds / b8` 对比
  `results/pareto-polynomial.json`。已比 `status`、`claim`、`claim_exact`、`exact_match`、
  `experiments`，五项完全相同；结果文件不保存 `law`，所以 law 字段不作逐字段判据。
  `devset_run.py` 的无 source `hypotheses: []` 及 `pareto.py` 路径均保持无 source。

## 5. 最小反向测试

把 `synthesize` 里的 seed 循环注释掉，主格退回 `SEARCH_EXHAUSTED`。

## 6. 回滚触发

§2 或 §3 任一不满足 → 回滚 `rds_compass_model.py` 与 `rds_compass.py` 的改动。

## 7. 这条为什么是 Stage 3 的核心

它把「谁来找 law」和「谁说了算」拆开。找 law 的方法可以随便换（PySR、LLM、
手工、CAS），内核的判定路径、账本格式、假认证检查一个字不改。

这也是唯一能绕过 `max_nodes` 天花板的路——天花板在生成器里，不在判定器里。
`decompose` 接线测过 19 个 DERIVED case 零命中，`max_nodes` 和 `max_candidates`
两个旋钮都测过无效；再往上堆枚举没有出路，换生成器才是。

**当前边界**：source 只是声明里预计算的表达式列表，内核不会运行或计费生成器。上表只证
「真候选能增加搜索 reach、错候选未获确认」，不证端到端每正确解成本下降。要把 source
放到质量—成本 Pareto 前沿，必须把外部生成调用、费用、模型/版本及 source ID 绑定到现有
ProjectStore/CAS 事件，并与最终独立认证结果关联；不能另建一份 source ledger。
