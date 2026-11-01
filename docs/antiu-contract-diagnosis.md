# antiu contract WITNESS_REPLAY 诊断（feynman-I.14.3）

分支 `release/6.0.0-compass`，HEAD `a296c26`。改动只落在 `scripts/rds_compass_antiu.py` 与
`tests/test_rds_compass_antiu.py`，未 commit、未 push。

## 根因一句话

`collect()` 把每个 candidate 的输入**投影成单变量**（`variable = next(iter(plan['inputs']))` 取到
`g`），witness 里丢了 `h` 和 `m`；`contract()` 的 `_runner` 又只按 `{name: Fraction(point)}` 建环境，
三输入 law 重放时抛 `DomainError: Missing input`，被 `except ValueError` 吞成 `None`，于是
`got == [witness['value']]` 恒假 —— 连数值完全正确的实例也被判 WITNESS_REPLAY 失败。

## 证据

诊断脚本对 `feynman-I.14.3` 单个 ledger root（B8）逐条打印（`3` 个 case；15-root 命令是
三个 root 各 3 个，共 9 个）：

```
declared inputs: ['g', 'h', 'm']
task law: m*g*h
cases: ['j01-0', 'j01-1', 'j01-2'] variable chosen by collect: g
template: {"op": "mul", "args": [{"op": "mul", "args": [{"var": "$V1"}, {"var": "m"}]}, {"var": "$V2"}]} holes: ['$V1', '$V2']
admit: True non_trivial: True cases: 3
distinct witness environments: 4 {"g": "1", "h": "1", "m": "1"}
contract ok: False
witness checks (j01-0 x3, j01-1 x3, j01-2 x3): [False, True, False]
failures: [('WITNESS_REPLAY', 'j01-0'), ('WITNESS_REPLAY', 'j01-2')]
j01-0 matched_law=True got=['1', '200', '500', '5'] want=['1', '20', '500', '25']
j01-1 matched_law=True got=['1', '20', '500', '25'] want=['1', '20', '500', '25']
j01-2 matched_law=True got=['1', '2', '500', '125'] want=['1', '20', '500', '25']
```

三条嫌疑的实测结论：

1. **`_refill`/`instantiate` 选错子树 —— 不成立。** 三个 case 的
   `canonical(instantiate(template_ast, sigma))` 都等于 `canonical(case['law_ast'])`，
   `matched_law=True` 9/9（三个 root 完全相同的确定性 campaign）。
2. **`zip(support, instances)` 顺序错 —— 不成立。** `instances` 按 `operator['sigmas']` 顺序生成，
   与 `support` 同序；上面逐 case 的 `matched_law` 就是这条顺序的证明。
3. **Fraction/str 类型不匹配 —— 不成立，但比较点确实恒假。** `model.text` 统一产出 `'p/q'`
   字符串，两侧同型；真正的原因是求值器在缺输入时返回 `None`，`None != '25'`。

直接定位：`scripts/rds_compass_antiu.py:323` 的 `variable` 与 `:327` 的 `points` 构造，
配合 `:222` 的 `_runner` 单绑定环境。

`DomainError` 与 `Undecided` 都是 `ValueError` 子类，所以 `_runner` 的
`except ValueError` 把它静默降级成 `None`，fail-open 掩盖了投影错误。

## 修复

`scripts/rds_compass_antiu.py`，两处，13 行净增：

- 新增 `_env(point, name)`：witness 点若是完整环境（`dict`）就整体使用，否则退化为单变量绑定。
- `_runner` 改为 `model.evaluate(node, _env(point, name))`。
- `collect()` 的 `points` 改为保留 `candidate['inputs']` 的**全部**输入变量。

比较处没有放宽：仍然是 `model.text(model.evaluate(...))` 的字符串精确相等，没有引入 `AlmostEqual`。

## 测试

```
$ python -B -m unittest discover -s tests -p 'test_rds_compass_antiu.py'
Ran 21 tests in 0.003s

OK
```

19 → 21：新增 `MultiInputWitnessTest` 两条。

- `test_a_multi_input_witness_replays_under_the_full_environment`：`mul(mul($V1,m),$V2)`
  形状的三个 law（`m*g*g` / `m*g*h` / `m*h*h`）+ 四个完整环境 witness，断言
  `checks['witness'] == [False, True, False]`。
- `test_a_bare_point_still_binds_the_single_named_variable`：标量 witness 的向后兼容。

反向验证（关掉修复后跑同一套测试）：

```
FAILED (errors=1)
  File ".../scripts/rds_compass_antiu.py", line 220, in _runner
    row.append(model.text(model.evaluate(node, {name: Fraction(point)})))
TypeError: argument should be a string or a Rational instance
```

恢复修复后重新 `OK`。

## 15-root 运行结果

命令同任务书（`--library .tmpcmd/lib --log .tmpcmd/log.jsonl`），退出码 `1`：

```
admitted 0 records 0
```

buckets 只有 **4 行**：`feynman-I.6.20a` 的 `collect()` 结果恒为 0 条 case（无
`TOOL_QUALIFIED`、无 jump 假设），不建桶；其余四个方程各占一个签名。

> 本节数字是第 1 轮（只修 `Missing input`、采纳判据未修）的状态。第二轮补上采纳判据后的
> 数字见 §第二轮：把「采纳」判据补进 support。

| signature | cases | ratio | saving_bits | admit | contract |
|---|---|---|---|---|---|
| `43dfa00cdbe54db1` | 6 | 0.857143 | -56 | False | — |
| `55c35f278e6445c6` | 3 | 1.392857 | 22 | False | — |
| `6e1b156013a1f7f1` | 3 | 2.042553 | 98 | False | — |
| `e635443d9b766a88` | 9 | 2.090047 | 230 | True | **False** |

`CONTRACT_FAILED` 的 failures 从修复前的 9 条降到 6 条，全部集中在
`('WITNESS_REPLAY', 'j01-0')` 与 `('WITNESS_REPLAY', 'j01-2')`：

```
{"failures": [{"check": "WITNESS_REPLAY", "origin": "j01-0"}, {"check": "WITNESS_REPLAY", "origin": "j01-2"},
              {"check": "WITNESS_REPLAY", "origin": "j01-0"}, {"check": "WITNESS_REPLAY", "origin": "j01-2"},
              {"check": "WITNESS_REPLAY", "origin": "j01-0"}, {"check": "WITNESS_REPLAY", "origin": "j01-2"}],
 "reason": "CONTRACT_FAILED", "signature": "e635443d9b766a880063dec87918faa519417831442a937bdf26cbfa35831cf1"}
```

## 为什么 `admitted` 仍是 0（与任务书验收的冲突）

第 1 轮任务书要求跑出 `"admitted": 1` 且 contract 不再报 WITNESS_REPLAY。实测表明这两条在
**不放宽 oracle** 的前提下无法同时成立：

- `collect()` 把 jump 的三个 `COMPASS_HYPOTHESIS` 全部收进 support，**不看 campaign 后续是否已反驳**。
  `feynman-I.14.3` 的三个 root 的事件流里各有 2 条 `COMPASS_REFUTATION`：
  `j01-0`（`m*g*g`）被 counterexample `pt_25`（g=1,h=5,m=5，观测 25，预测 5）反驳，
  `j01-2`（`m*h*h`）被同一点反驳（预测 125）。只有 `j01-1`（`m*g*h`）在
  `COMPASS_VERIFICATION` 里是 `CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS`，
  `equivalence_class=["j01-1"]`，`replay.tested=4`，`lean.status=PASS`。
- 修复前这 6 个被反驳的 case 也"失败"，但原因是 `Missing input`，看起来像 9/9 全挂；
  修复后它们仍然失败，因为 `m*g*g` 和 `m*h*h` 在 `pt_23`/`pt_12` 上确实复现不了观测。
  这是 contract 的**正确拒绝**，不是 bug。
- 若把 `collect()` 改成只收 campaign 认可的假设（`j01-1` × 3 root），三个 law 完全相同 →
  `lgg` 无洞 → `_non_trivial()` 返回 `False` → `admit()` 仍为 `False`，`admitted` 还是 0。

所以 `feynman-I.14.3` 的模板有两个洞这件事本身，来自两个**被反驳的假设**在撑 support。
正确的 anti-unification 不应从被反驳的 law 里学模板。

要让 `admitted >= 1`，只有三条路，都需要显式决策：

1. **修 support 口径**：`collect()` 丢弃 `COMPASS_REFUTATION` 指向的假设。`feynman-I.14.3`
   随之变成 3 个相同 law 的 trivial bucket，`admitted` 仍 0，但 contract 不再报
   WITNESS_REPLAY（因为根本不跑）。这是**语义变更**，影响所有 bucket 的 MDL 计算，超出
   "最小改动"。
2. **放宽 contract 的 witness 口径**（例如只验 campaign 认可的 case、或用
   `case['law_ast']` 而非观测做 ground truth）。这会让 `checks['witness']` 变成自证，
   属于假认证，不建议。
3. 换一个 support 本身就是干净 CONSISTENT 假设的方程做验收目标。

## 第二轮：把「采纳」判据补进 support

上一轮把「`collect()` 丢弃 `COMPASS_REFUTATION` 指向的假设」列为超范围的口径变更（见
§为什么 `admitted` 仍是 0 的第 1 条）。本轮实现了它：设计文档
`docs/rsi-l1l2-plan-20261020.md:36` 要求反统一的第二个来源是「jump **采纳的** patch」，
而 `collect()` 只查了 `origin` 前缀。

### 判据：(B) 正的重放判决，而不是 (A) 排除被反驳

两种候选口径：

- **(A) 排除出现在任何 `COMPASS_REFUTATION` 里的假设。**
- **(B) 只收 `COMPASS_VERIFICATION.claim == 'CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS'`
  的假设**（成员取该事件的 `equivalence_class` 与 `hypothesis.id`）。

选 (B)，理由是 (A) 是 fail-open：「暂时没有反例」不是「被采纳」。`claim` 的第三个取值
`CONSISTENT_BUT_UNCONFIRMED`（`scripts/rds_compass.py:1413-1416`）和「从未被检验」的假设都
不会被 `REFUTATION` 覆盖，(A) 会把它们当成采纳收进 support。campaign 用
`claim=CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS` 表示「重放覆盖了全部已接收观测」，
这与 AGENTS.md「科学状态只由重放原始测量得出」对齐。(B) 的代价是把 `CONSISTENT_BUT_UNCONFIRMED`
也挡在外面，这是有意的 fail-closed。

两口径**不等价**：分歧发生在「未被反驳、也未被判为完全一致」的假设上。本轮 15 个 root 里没有
这种样本（每个 jump 假设要么在 `REFUTATION` 里、要么在 `CONSISTENT_WITH_ALL` 的
`equivalence_class` 里），所以两者在这批数据上恰好同解；判据按 (B) 实现，不依赖这个巧合。
既没被反驳也没通过的假设记 `UNVERIFIED_HYPOTHESIS`，与 `REFUTED_HYPOTHESIS` 分开，
复核者能区分「已被证据杀掉」和「还没被检验」。

### 实现与日志

`collect(events, observable, log=None)` 先跑 `_adoption(events)`，返回
`(refuted, adopted, claims)`；HYPOTHESIS 分支在原来的 `origin.startswith('jump:')` 之后
再判采纳。被排除的假设写进 `--log` 的 JSONL，`reason` 为 `REFUTED_HYPOTHESIS` 或
`UNVERIFIED_HYPOTHESIS`，字段含 `origin`、`hypothesis`、`signature`；被反驳的附
`refuted_by = {'basis', 'counterexamples'}`，未验证的附 `verification_claims`。一条实测：

```
{"hypothesis": "j01-0", "origin": "jump:j01:WHOLE_LAW", "reason": "REFUTED_HYPOTHESIS",
 "refuted_by": {"basis": "RECEIPTED_MEASUREMENT_COUNTEREXAMPLE",
                "counterexamples": [{"id": "pt_25", "observable": "y",
                                     "observed": "25", "predicted": "5", "residual": "20"}]},
 "signature": "e635443d9b766a880063dec87918faa519417831442a937bdf26cbfa35831cf1"}
```

本轮 15-root 日志共 9 条 `REFUTED_HYPOTHESIS`：`feynman-I.14.3` 的 `j01-0`/`j01-2`
各 3 个 budget 共 6 条，`feynman-II.2.42` 的 `j01-1` 3 条。

### 15-root 前后对照

同一份代码、同一批 ledger，只切换采纳判据（`_adoption` 换成旧的全收行为）跑两遍，
脚本 `.tmpcmd/run_15root.py`，输出 `.tmpcmd/adoption/{before,after}.json`。
`feynman-I.6.20a` 的 `collect()` 结果恒为 0 条 case（无 `TOOL_QUALIFIED`、无 jump 假设），
不建桶，所以**五个方程落在四个桶**里：

| 方程 | signature（前 12） | before cases / ratio / saving / admit / contract | after cases / ratio / saving / admit / contract |
|---|---|---|---|
| `feynman-II.2.42` | `43dfa00cdbe5` | 6 / 0.857143 / -56 / False / — | 3 / 2.042553 / 98 / False / — |
| `feynman-I.12.1` | `55c35f278e64` | 3 / 1.392857 / 22 / False / — | 3 / 1.392857 / 22 / False / — |
| `feynman-I.9.18` | `6e1b156013a1` | 3 / 2.042553 / 98 / False / — | 3 / 2.042553 / 98 / False / — |
| `feynman-I.14.3` | `e635443d9b76` | 9 / 2.090047 / 230 / **True** / **False** | 3 / 1.815789 / 62 / False / — |
| `feynman-I.6.20a` | 无桶 | 0 条 case | 0 条 case |

`admitted` 前后都是 0。`feynman-II.2.42` 与 `feynman-I.9.18` 的 after 行数字相同
（3 / 2.042553 / 98）是 MDL 恰好一致，两者 signature 不同、桶独立，不是串桶。

### 结论：这是负结果，不是待修的 bug

修完采纳判据后，五个方程在「存活假设」口径下**全部 `admit=False`**：

- `feynman-I.14.3`：被反驳的 `m*g*g`、`m*h*h` 不再进 support，三个 root 的存活 law
  完全相同（`((h * m) * g)`）→ anti-unification 无洞 → `_non_trivial` 返回 `False`。
  模板不再从错误结构里学洞，`WITNESS_REPLAY` 也随之不报（根本不跑 contract）。
- `feynman-II.2.42`：`j01-1`（`(t2 - t1) / (kappa * kappa)`）被 `pt_11` 反驳后只剩
  单个 law，同样 trivial。cases 6→3、saving -56→98 是只剩干净结构后的 MDL 重算。
- `feynman-I.12.1`、`feynman-I.9.18`：本来就只有一个存活假设，前后不变。

`admit()` 要求 `_non_trivial()` 为真，即模板里得有洞；洞只能来自结构不同的两个 law。
存活假设过滤后每个桶只剩一种 law，**没有跨预算的结构差异可供泛化**。要凑出
`admitted >= 1`，只能放宽 `_non_trivial`（把无洞模板也放出）或改 contract 的 witness
口径（自证），两者都是假认证，不做。

所以本轮的 L1-b 结论是：当前 devset 五个方程在「存活假设」口径下没有可复用的
跨预算泛化结构。这是 support 口径修对之后的真结果，不是实现缺陷。

## 其他已知缺陷（本轮未修，超出范围）

- `contract()` 的 boundary probe 用标量 `edge`，对多输入 law 同样退化成
  `{name: value}` 单绑定 → 全部 `None` → `checks['boundary']` 恒 `True`（fail-open）。
- `_exact()` 实际上恒 `True`：`model.text` 成功返回的字符串必然能被 `model.exact` 解析，
  所以边界探针的"非法值"分支是死代码。
- `run()` 写进 `operator['support']` 的字段只有 `ledger_root/record_sha256/law/origin`，
  v2 record 不含 witness 与 `law_ast`，下游 `library_tools()` 无法重放。

## diff stat

代码改动（不含本文档；docs 的计数随本节编辑变化，故不写死）：

```
 scripts/rds_compass_antiu.py    | 55 ++++++++++++++++++++++++++++++++---
 tests/test_rds_compass_antiu.py | 75 ++++++++++++++++++++++++++++++++++++++++++
```

第 1 轮的数字是 13 / 38；本轮新增 `_adoption`、`collect()` 的采纳分支和 3 条回归测试
（`tests/test_rds_compass_antiu.py` 从 21 条到 24 条）。
