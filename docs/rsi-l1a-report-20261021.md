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
