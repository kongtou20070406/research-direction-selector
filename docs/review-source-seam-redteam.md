# source seam 独立红队复核报告

复核对象：`aa57046`（source seam 实现）与 `ff6869e`（协议文档与三臂证据），基准 HEAD `ff6869e`。
复核原则：只读代码与实验证据，不依赖已有结论，逐项给出 `file:line` 证据。

## 1. 假认证风险：seed 判定路径与枚举是否完全相同
**完全相同，无任何跳过或特殊优待。**
- **拟合过滤**：`scripts/rds_compass.py:808, 816` 传入过滤掉 holdout 后的 `rows`，在 `scripts/rds_compass_model.py:1016` 经受完全一致的 `matches(v, t, tol)` 容差检验。
- **假设编译**：`scripts/rds_compass.py:935` 统一调用 `model.compile_hypothesis` 编译，不因来源降低 AST 校验。
- **Replay 评估**：`scripts/rds_compass.py:184-185`（`_derive`）对全部存活假设无差别执行 `_replay_cached` -> `model.replay`。
- **Novel Confirmations**：`scripts/rds_compass.py:314-323` 与 `293-303`（`_novel_for`）严格要求测量在假设进入账本之后启动并结算，seed 假设无任何预确认特权。
- **最终认证**：`scripts/rds_compass.py:1558-1586`（`verify`）全量测量重放，独立等价类划分，必须满足 `confirmations >= required_confirmations` 才给 claim。
唯一区别仅为 `scripts/rds_compass.py:793-796` 将 `origin` 标记为 `SOURCE:[...]` 记录来源谱系，不影响逻辑分支。

## 2. keep=4 早停：枚举是否会被饿死与「结构性削弱」证伪路径
**单次调用中若 4 条 seed 均拟合，枚举确实被完全跳过；但「结构性削弱内核独立找解」被以下三条代码路径证伪：**
- **饿死发生点**：`scripts/rds_compass_model.py:1020-1025`。若 4 条 seeds 全拟合当前观测，`len(fits) == 4` 导致 `go = len(fits) < keep and ...` 短路为 `False`，`1026-1028` 行直接 `break`，底层枚举 `attempted = 0`。
- **证伪路径 1（主动实验证伪反驳）**：seed 仅在当前不充分测量下拟合。进入 rulebook 后，`scripts/rds_compass.py:1158-1210`（`decide`）依 EIG 选点测量，错的 seed 被反驳标记 `REFUTED`（`:184`）。下一轮 `_fits` 观测集扩充（`:804`），失效 seed 在 `rds_compass_model.py:1016` 被拒，`len(fits) < keep`，枚举立刻重新启动。
- **证伪路径 2（残差修正独立性）**：`scripts/rds_compass.py:859-865` 在首选假设被反驳后执行 `RESIDUAL_CORRECTION` 模式调用 `synthesize`，形参默认 `seeds=()`，枚举在残差搜索中享有 100% 独立性，绝不被 seed 塞满。
- **证伪路径 3（语法升级与派生变量）**：`scripts/rds_compass.py:825-828, 842-845` 的 `GRAMMAR_ESCALATION` 与 `DERIVED` 分支同样默认 `seeds=()`。

## 3. 声明校验：是否存在绕过路径
**在语法与数值语义上无法构造出过校验但在下游崩溃的用例；但存在未设总条数上限的边界缺口。**
- **超长数值/溢出**：`scripts/rds_compass_model.py:514` 限制字符不超过 2048；`1e500`、`exp(1000)`、`1/0` 等均抛出 `DomainError`（`:320-330` 等），在 `spans_of`（`scripts/rds_compass_model.py:946`）被 `except (DomainError, Undecided)` 捕获为 `None`，并在 `matches` 处安全丢弃，不崩。
- **嵌套深度**：`scripts/rds_compass_declaration.py:250` 强制 `model.size(node) <= MAX_RULEBOOK_NODES (64)`，AST 总节点数上限 64 限制树深不超过 64，远低于 Python 递归上限，无法击穿调用栈。
- **三元 `ite` 算子**：`scripts/rds_compass_model.py:37, 519, 548, 491` 原生支持 `ite`，并在区间评估与分支求值中完整闭合。
- **输入冲突**：`scripts/rds_compass_declaration.py:247-249` 强制 `stray == set()`，未声明变量全部在声明期 fail-closed。
- **构造出的防御盲区**：`declaration.py:236-254` 相比 `hypotheses`（`:231` 有 `MAX_HYPOTHESES`）缺少对 `sources` 总数及单 source 下 `laws` 长度的上限限制。可注入上万条合法 seed，造成 `_fits_memo_key`（`rds_compass.py:737`）序列化巨量数据及多次求值性能退化。

## 4. 成本口径：对 `cost_per_correct` 的影响
**若把 source 接进 pareto，每正确解成本会被系统性严重低估（虚假改善）。**
- **计算规则**：`benchmark/newtonbench/pareto.py:184-188` 计算 `experiments_per_correct = experiments / len(solved)`。
- **分母虚假推高**：超大 size 方程（如 `feynman-I.41.16`，真解 size 24，超出默认枚举上限 7）原本 `claim_exact=False`，外部 source 提示后解出，使分母 `len(solved)` 增加。
- **分子成本偷渡**：外部生成器（PySR / LLM / CAS）消耗的计算时间、GPU 显存或 token 开销均不进入 ProjectStore 账本（`benchmark/newtonbench/source-seam-protocol.md:116, 157-160`），分子仅计入内核验证的几次实验（如 8 次）。
- 外部生成算力被无偿补贴，不可直接接入 pareto 曲线对比。

## 5. memo 穿透：Key 稳定性与命中率量级
**source 固定时 key 绝对稳定；跨实验步命中率为 0，单步内及全套测试命中率在 30% ~ 50% 量级。**
- **稳定保证**：`scripts/rds_compass.py:737` 将 `seeds` 转为列表，经 `canonical` 和 `object_digest`（`:722`）计算，输入完全确定。
- **跨步失效**：`scripts/rds_compass.py:724-726, 735` 将 `rows` 与 `probes` 纳入 digest。每次主动实验买入新点，`rows` 增多、`probes` 减少，key 必定变化，跨步命中率为 0。
- **单步与全套测试命中率**：单步内 RSI tool 资格认证前后、escape 评估或多次 fold 重算时，同一数据状态会连续调用 `_fits`。实测 `python -B -m unittest tests/test_rds_compass_drive.py -k LawTests`，统计输出 `_FITS_MEMO_STATS: {'hit': 24, 'miss': 25, 'reset': 0}`，命中率达 48.98%。

## 6. 反向测试是否真反向：7 个用例逐个判定
基于在 `scripts/rds_compass_model.py:1020` 将 `seeds` 遍历循环置空实测，`tests/test_rds_compass_source_seam.py` 原 7 个用例中 **5 个假绿，仅 2 个真反向**：
1. `test_a_seed_larger_than_the_enumeration_bound_still_fits`：**FAIL（真反向）**。断言期望 seed 产出 AST size 9 的解，删循环后为空，断言失败。
2. `test_a_seed_that_misses_a_measurement_is_not_a_fit`：**PASS（假绿）**。`x+7` 本就不在 fits，枚举独立找到 `x+1` 使 `assertTrue` 仍成立。
3. `test_an_empty_seed_list_is_the_old_call`：**PASS（假绿）**。退化用例，空列表无论有无循环均行为一致。
4. `test_a_seed_naming_an_undeclared_input_is_refused`：**FAIL（真反向）**。未执行循环则不会调用 `parse`，无法触发 `ValueError`。
5. `test_a_fit_keeps_all_source_ids_in_its_origin`：**PASS（假绿）**。mock 了 `_fits_synthesize`，完全脱离真实 seed 循环。
6. `test_source_origin_filter_keeps_ids_after_jump_prefix`：**PASS（假绿）**。纯字符串过滤辅助函数，不执行 `synthesize`。
7. `test_a_source_over_the_rulebook_limit_is_rejected_at_ingest`：**PASS（假绿）**。仅测试 `declaration.initialize` 的 ingest 门禁，不执行 `synthesize`。
（注：现有代码新增的 `test_two_spellings_of_one_law_take_one_fit_slot` 与 `test_a_seeded_law_does_not_stop_the_enumeration` 删循环后同样为 **PASS 假绿**，总计 9 个用例 7 个假绿）。

## 复核之后的处置（lead 补记，2026-10-21）

本复核的基准是 `ff6869e`。之后两处落地，逐条对应上面的发现。

**§2 的单次短路，修复前还有一层没被看见。** `consider_seed` 存的是 parse 后未规范化的
AST，判重也拿未规范化的树比，于是 `x + 1` 与 `x + 0 + 1` 各占一个 `keep` 名额；
再加上 `consider` 从不与 `fits` 判重，枚举自己还会重复发现 seed 已有的律。
实测 `attempted` 从 7381 塌到 **8**：枚举连一棵组合树都没试就被判「够了」。
`7be9731` 让两条路径共用规范化后的比较与存储，并给 `consider` 的 append 加了
`normalized not in fits`。回归用例 `test_two_spellings_of_one_law_take_one_fit_slot`
与 `test_a_seeded_law_does_not_stop_the_enumeration`。

本报告 §2 那三条「重新激活枚举」的路径不受影响：它们讲的是 seed 被反驳之后枚举怎么回来，
与名额怎么计数是两件事，两边都对。

**§3 的数组总量盲区**已在 `67ec2f0` 补上：`MAX_SOURCES = 8`、`MAX_SOURCE_LAWS = 64`，
与同文件的 `MAX_CANDIDATES` / `MAX_HYPOTHESES` 并列，ingest 期 fail-closed。
回归用例 `test_too_many_source_laws_are_refused_at_ingest`。

**仍未闭合的是 §6。** 那 7 个假绿用例没有重写；新增的两个回归用例对「删掉 seed 循环」
同样假绿，因为它们挡的是「名额被等价式吃掉」，不是「循环消失」。这两件事要用不同的断言
分别挡，混在一起会两头都挡不住。留作下一轮。

VERDICT: 能进 PR。内核判定与枚举完全同构、假认证风险为 0，协议已显式禁止将 source seam 算作 pareto 改善；后续需在 declaration 补充 sources 总量门禁并补强真反向单测。
