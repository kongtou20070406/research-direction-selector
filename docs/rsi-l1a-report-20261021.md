# L1-a 实现报告：离线反统一脚本

日期：2026-10-21。HEAD：`f6a580c`。

## 取证结论（四条锚点）

| 锚点 | 实际位置 | 内容 |
|---|---|---|
| `extract()` allowlist | `rds_compass_rsi.py:140-149` | 强制 `len(defs)==1 and len(defs[0].args.args)==1`，即单参数函数 |
| `export()` library record | `rds_compass_rsi.py:299-320` | 写 `rds-compass-tool-v1` schema，含 `qualification.law` 文本，**无 `law_ast` 字段** |
| `MAX_NODES` / `MAX_RULEBOOK_NODES` | `rds_compass_model.py:22,25` | `MAX_NODES=15`（单表达式），`MAX_RULEBOOK_NODES=64`（规则书） |
| dict-AST 形状 | `rds_compass_model.py:128` `normalize()` | `{'op': str, 'args': list}` 或 `{'const': str}` 或 `{'var': str}` |

## 实现摘要

- `lgg(a, b)` → `_Holes` + `_nary`：一阶反统一，op/arity 相同递归，否则 hole
- `generalize(asts)` → `{'template_ast', 'holes', 'laws', 'sigmas', 'domain_signature'}`
- `mdl_bits(law)` / `_mdl(operator)`：Elias-gamma 编码，1 字节 opcode，变量 ⌈log2(nvars)⌉ bit
- `admit(template, support)`：R≥1.5 ∧ support≥2 ∧ 非平凡 ∧ refill≤64
- `_non_trivial(operator)`：任一 σ 回填后与其他 support 案例 AST 不等价
- `contract(template, support, runner)`：见证重放 + 边界探针 + 可判别性（mutants ≥1 区分）
- `run(roots, library)`：读 ledger → 分桶 → 反统一 → 判定 → 写 v2 record

## 测试结果

`python -B -m unittest tests.test_rds_compass_antiu -v` → **15/15 OK，0.004s**

## 设计缺陷（一条，不改设计）

**节点上限口径矛盾**：`admit()` 用 `MAX_RULEBOOK_NODES=64`（§2.2 写死），但 RSI `extract()` 只收单参数函数。若模板实例化后是多分支规则书（如 `ite` 嵌套），`extract()` 会拒绝。设计 §2.1 的两条消费路径——「注入 `synthesize()` 的 `derived` 参数」和「实例化为单参数 source 走 `evaluate_tools`」——需要明确选哪一条，或让 `extract()` 支持规则书。

建议：L1-b 验收时先走消费方式 1（derived 注入），方式 2 留到 `extract()` 支持规则书之后。

## 没做的事

- 没改任何既有文件（`decide()`、`synthesize`、RUNNER 均未动）；
- 没写报告文件（本文件由 lead 补写）；
- 没跑全量测试套件（只跑 `test_rds_compass_antiu`）；
- 没 commit（由 lead 完成）。

## 独立复核（第二个执行子 agent，HEAD `0a091b9`）

另一路执行独立跑了一遍同一任务，回来复核已提交版本。以下每条都有复现命令或源码位置。

1. **既有测试变红（先修这个）**。`tests/test_import_graph.py:983` `test_real_repository_is_covered`
   断言 `scripts/**/*.py` 里没有 `UNKNOWN` 层，而 `tools/import_graph.py:37-69` 的 `LAYERS` 表没有
   `rds_compass_antiu`。实测
   `python -B -m unittest tests.test_import_graph.ImportGraphTests.test_real_repository_is_covered`
   → `AssertionError: ['rds_compass_antiu'] is not false`。修复一行：`LAYERS` 里加
   `rds_compass_antiu="L4",`（离线 CLI 入口，只 import L1/SHARED/L3，和 `paid_query="L4"` 同理）。
   本任务禁改该文件，未动手。

2. **`R >= 1.5` 在 devset 规模上几乎不可达（比第 1 条更影响 L1-b）**。用本模块自己的 `_mdl` 算：
   典型 5 节点定律对（`2x+1` vs `3x+4`）`ratio = 0.861789` → 拒绝；15 节点 add-spine 只差一个
   常数叶 `ratio = 1.554502` → 通过。即模板要过 MDL 门，必须同时满足「接近 `MAX_NODES=15`」和
   「两案例只差一个叶」。按 devset 定律规模，预期 0 入库。

3. **`support >= 2` 在单 campaign 上不可达**。实测一次 `compass drive` 只产出 1 条带 `law_ast` 的
   `COMPASS_HYPOTHESIS`（`origin='jump:j01:WHOLE_LAW'`）。而 `domain_signature` 含
   `_decade(len(plan['candidates']))`，campaign 实验点数不同就落进不同桶 → 跨 campaign 聚合同样
   失效。第 2、3 条是两道并联门禁。

4. **v2 记录对在线路径完全不可见**（§2.5「零改动复用」不成立）。`scripts/rds_compass_rsi.py:283`
   硬 `require(record.get('schema') == TOOL_SCHEMA)`（v1），v2 在 `:287-288` 被当 damaged record
   静默 `continue`；即便放宽 schema，`:285` 的 `extract(record['source'])` 也会 KeyError —— v2 只有
   `template_ast`，没有单参数 `source`。接上需改 `rsi.py`，本任务禁改。

5. **非平凡判据的真实语义**。由 lgg 性质 `instantiate(T, σᵢ) == lawᵢ` 恒成立，`_non_trivial` 等价于
   「support 里至少两个案例不同」。它挡住「重复案例」，但测不到 §7 想挡的「模板零泛化」。更贴意图
   的判据：每个洞至少在两个 σ 中取不同值。

6. **2.2 的口径缺口**。Elias-gamma 对 0 与负数无定义（实现用 zigzag 补），`L(support 表)` 与每个洞
   绑定的编码 2.2 没写（实现取 `n * (8 + ceil(log2 n))` 与按洞类型分派）。两处需 lead 确认口径，
   否则跨实现会漂。

7. **`MAX_RULEBOOK_NODES=64` 的实例化检查当前路径恒不触发**：`generalize` 里
   `model.normalize(..., limit=MAX_NODES=64)` 已先拒掉 >64 节点 AST。保留作断言，无害。

8. **行数**：`f6a580c` 版本主文件 384 行 + 测试 147 行；非空非注释代码行 384。任务书限额 400
   （含测试）按代码行口径合规，按总行口径超；仓库 `AGENTS.md` 的 250 net LOC 更严。

9. **第二路的最终版**：结构更紧凑（净 -59 行，15 条测试同样全绿），与 `f6a580c` 的差异存在
   `D:/Myfile/work space/RDS/_antiu_working_diff.patch`（仓库外，未覆盖 commit）。复核用的一次性
   探针脚本已删，仓库无新增临时文件（`git status` 只剩本报告这一处修改）。
