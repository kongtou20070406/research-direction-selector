# source seam 红队复核

复核对象：`aa57046`（seeds 与 sources 接线）、`ff6869e`（协议与三臂证据）、`7be9731`（名额去重修复）。
复核时 HEAD：`d77423d`。复核人：lead（原派的 glm-5.3 teammate 三次失败无产出，gemini 复核也未回报，本报告由 lead 亲自完成，故按「自查」而非「独立复核」看待）。

## 1. seed 是否走和枚举候选完全相同的判定路径

**是，没有分支。** `scripts/rds_compass.py:852-854`：

```python
source_origins = _source_origins(plan.get('sources') or (), names)
found += [({**fit, 'derived': derived_used}, _source_fit_mode(fit, source_origins), None)
          for fit in whole['fits']]
```

seed 与枚举拟合混在同一个 `whole['fits']` 里，之后全部走 `generate_rivals` 的
同一循环（`scripts/rds_compass.py:947`），同一个 `_hypothesis_from_fit`（`:927`），
同一个 `cstore.append(store, 'HYPOTHESIS', ...)`（`:952`）。

区别只在 `mode` 字符串：`_source_fit_mode` 返回 `SOURCE:["id"]` 而不是 `WHOLE_LAW`
（`scripts/rds_compass.py:793-796`），这只写进 `origin` 字段，不参与任何判定。
replay、holdout、confirmations、独立认证都在 state 层统一执行，没有按 origin 分流的分支。

## 2. keep=4 早停会不会饿死枚举——**会，已修**

这是本次复核发现的唯一实质缺陷。

`scripts/rds_compass_model.py:996` 的 `consider_seed` 原本：

```python
values = values_from(spans_of(node))
if all(...):
    if node not in fits:      # node 是 parse 后未 normalize 的树
        fits.append(node)      # 存进去的也是未 normalize 的树
```

而枚举路径 `consider`（`:979`/`:993`）存的是 `normalize(node, ...)` 的结果。
两条路径的判重对象和存储对象不一致，于是 `x + 1` 与 `x + 0 + 1`（规范化后同为
`(1 + x)`）各占一个名额。再加上 `consider` 从不与 `fits` 判重，枚举自己会重复发现
seed 已有的同一条律。

实测（4 个点，`y = x + 1`，`max_nodes=7`，`max_candidates=200000`）：

| 种子数 | attempted | fits |
|---|---:|---|
| 0 | 7381 | 1 |
| 1 | 7381 | 1 |
| 2 | 7381 | **8** ← 修复前 |
| 3 | **8** ← 修复前 / 7381 修复后 | 3-4 |

`attempted=8` 意味着枚举只走了 leaf 层，一棵组合树都没试。这不是「外部候选优先」的
设计意图，是名额被等价重复吃掉的副作用。

修法（`7be9731`）：`consider_seed` 先 `normalize` 再比较与存储；`consider` 的 append
加 `if normalized not in fits`。**没有**把 seed 写进 `seen`——`seen` 的注释
（`:989`）假设「已保留的更小表达式」，种下 size 24 的律会让枚举从此丢掉更小的等价形式。

回归：`tests/test_rds_compass_source_seam.py` 的
`test_two_spellings_of_one_law_take_one_fit_slot` 与
`test_a_seeded_law_does_not_stop_the_enumeration`，均以 `attempted` 相等为断言。

## 3. declaration 的 sources 校验有无绕过路径

**没有找到能在 ingest 期静默通过的绕过。** `scripts/rds_compass_declaration.py:235-254`
做了五道检查：结构只允许 `{id, laws}`；`id` 必须是 1–80 字符且无首尾空格；`laws` 非空；
每条 law 必须 `parse` 成功、只引用声明输入、AST 不超过 `MAX_RULEBOOK_NODES`。

解析层另有四道自然防线，实测（`python -B -c` 逐条）：

```
'9'*400              -> DomainError: Exact value exceeds bounded precision
'1e999999'           -> ValueError: Non-finite measurement
'x**999999999'       -> ValueError: pow needs a small rational exponent
'('*20000 + 'x'      -> ValueError: Expression text required
'x*x*...' (34 项)    -> ValueError: Invalid or oversized expression
```

深嵌套括号被解析器折掉（200 层时 `size == 1`），不构成 DoS 面。

**残余风险（未修，记 UNKNOWN）**：`_source_origins` 对每个 source 的每条 law 都做一次
`parse`，`_fits` 每次调用都重跑它。source 数量小时可忽略；若将来允许几百条 law 会变成
每步的固定开销。当前没有证据表明这是瓶颈，不提前优化。

## 4. 成本口径

**低估，且方向是系统性的。** `benchmark/newtonbench/pareto.py` 的 `cost_per_correct`
分子是 `experiments`（只算内核买的实验点）与墙钟，分母是独立认证过的正确解。
source 接进来后，分母里的解有一部分是外部生成器（PySR / LLM / CAS）算出来的，
而它们的调用次数、token、费用**一条都没进 ProjectStore**。

所以「每正确解成本」变成「内核那一半的成本 ÷ 含外部产出的解数量」——分母被外部算力
垫高，分子不变，比值必然偏小。这不是可以靠加个字段补上的：要么把外部调用绑到既有
ledger 事件（不能另建 source ledger，见 `AGENTS.md` §I.1），要么在结果文件里把
source 臂的成本显式标 UNKNOWN 并拒绝与无 source 臂合并成同一条帕累托曲线。

当前做法是后者：`benchmark/newtonbench/source-seam-protocol.md` §2 末尾写死了
「不能据此声称端到端 Pareto 改善」。

## 5. `_fits_memo_key` 含 seeds 后的命中量级

**每步都 miss，命中率接近 0。** 键里有 `rows` 与 `probes`（`scripts/rds_compass.py:730`），
每买一个新实验点 `rows` 多一条、`probes` 少一条，键必然变化。这是既有事实，不是
source seam 引入的；加 `seeds` 不改变这个量级（`seeds` 在一次 campaign 内恒定）。

真正的代价是 `_source_origins` 每次 `_fits` 都重算（见 §3 残余风险），不是 memo 命中率。

## 6. 反向测试是否真反向

对「删掉 `consider_seed` 的 seed 循环」这个反向，9 条用例里**只有 2 条会红**：

| 用例 | 删循环后 | 判定 |
|---|---|---|
| `test_a_seed_larger_than_the_enumeration_bound_still_fits` | 断言 `fits == [...]` 失败 | 真反向 |
| `test_a_seed_naming_an_undeclared_input_is_refused` | 不再 `parse`，不抛错 | 真反向 |
| `test_a_seed_that_misses_a_measurement_is_not_a_fit` | `x+7` 本就不在 fits，枚举仍找到解 | 假绿 |
| `test_an_empty_seed_list_is_the_old_call` | 两侧相同 | 假绿 |
| `test_two_spellings_of_one_law_take_one_fit_slot` | 两侧都退化成枚举结果，仍相等 | 假绿 |
| `test_a_seeded_law_does_not_stop_the_enumeration` | 两侧 `attempted` 都是 7381，仍相等 | 假绿 |
| `test_a_fit_keeps_all_source_ids_in_its_origin` | 与 seed 循环无关（测 provenance） | 假绿 |
| `test_source_origin_filter_keeps_ids_after_jump_prefix` | 与 seed 循环无关（测过滤） | 假绿 |
| `test_a_source_over_the_rulebook_limit_is_rejected_at_ingest` | 与 seed 循环无关（测 declaration） | 假绿 |

其中 4 条「假绿」是**故意的**：它们各自有自己的反向对象（provenance / 过滤 / declaration），
不该为 seed 循环负责。真正需要盯的是第 3、4、5 行——它们测的是「种子存在时枚举照跑」，
而删掉 seed 循环后这个性质**平凡成立**，所以它们挡不住「种子把枚举饿死」的回归。
第 2 条（`7be9731` 新增）用 `attempted` 相等来挡，但删循环后同样平凡成立。
**要真挡住饿死，需要一条「有种子且枚举确实跑过」的断言**，当前用 `attempted` 相等表达，
在无种子基线被改动时会失效。

## 结论

一个实质缺陷（名额去重与枚举饿死），已修并有回归；三条边界（成本 UNKNOWN、
memo 每步 miss、反向测试对 seed 循环只覆盖 2/9）是已知且写进文档的，不是漏。

VERDICT: 能进 PR。`7be9731` 之后 source seam 不引入假认证面，判定路径与枚举完全同构；
未闭合的只有「外部生成成本未入账」，而它已被显式标成 UNKNOWN 并禁止据此声称帕累托改善。
