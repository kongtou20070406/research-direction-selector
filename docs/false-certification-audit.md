# RDS 假认证率（False Certification Rate）安全指标审计报告

**审计日期**：2026-10-21  
**审计人员**：鲸鱼娘·大肥鱼（DeepSeek Harness Kernel Audit）  
**审计范围**：RDS 仓库内核代码、NewtonBench、devset、pareto 与 Compass benchmark 套件  
**报告状态**：FINAL / READ-ONLY AUDIT  

---

## Executive Summary（一句话审计结论）

> **当前现状**：RDS 的假认证率指标处于**「核心内核无定义、合成玩具脚本已自动化、真实科学 Benchmark 严重依赖人眼与事后手工推演」**的严重割裂状态；绝大多数主力 Benchmark 只报准确率（`claim_exact`）而不报假认证率，存在因过拟合伪拟合导致假认证率静默飙升的巨大安全盲区。

- **是否自动**：**半自动 / 局部自动**。仅在 Compass 合成世界（`benchmark/compass/run.py`、`yield_holdout.py`）中由脚本自动逐点比对；在 NewtonBench 432 trials、devset、pareto 评测中**完全没有自动化计算与聚合**，51 次假认证系事后在 PR 讨论中人眼核算。
- **最大漏检风险**：**当目标定律不可表达且采样点极少时，系统易将巧合命中的低阶过拟合多项式判定为 `GOAL_IDENTIFIED`；而现行 holdout 机制默认关闭（`holdout: 0`），即使设为 2 点亦因确定性首末采样（`seed=0`）导致中部异常漏检率高达 93.75%，形成致命安全破防。**

---

## 一、指标定义与代码路径定位

### 1.1 内核层认识论边界（内核不定义、不自证）
在 RDS 核心内核代码库（`rds-357/scripts/`）中，**完全不存在** `false_certif`、`false cert` 或 `假认证` 的计算或存储逻辑：
- `rds-357/scripts/rds_compass.py:1402` 明确将 `scientific_support` 锁定为 `'UNKNOWN'`。
- 系统内核只产生执行层与认识论状态：
  - `status`: `GOAL_IDENTIFIED`、`SEARCH_EXHAUSTED`、`JUDGMENT_REQUIRED`、`BUDGET_EXHAUSTED`（`rds-357/scripts/rds_compass.py:32`）；
  - `claim`: `CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS`、`CONSISTENT_BUT_UNCONFIRMED`、`NOT_ESTABLISHED`（`rds-357/scripts/rds_compass.py:1392-1395`）；
  - `best_effort`: 当搜索耗尽时选择反例最少的候选，并标明 `'note': 'PARTIAL_FIT: a refuted candidate, not a claim'`（`rds-357/scripts/rds_compass.py:1382`）。
- **依据**：符合 `AGENTS.md` 第一章核心公理——「任务完成（INPUT_REPORTED）不等于科学支持（Task Truth != Scientific Support）」，RDS 内核严禁自我宣告科学真理，假认证判定必须位于外部独立复核器（External Verifier）。

### 1.2 外部评估层真实计算路径
全仓仅在以下测试与 Benchmark 代码路径中对该指标进行了可执行的计算与断言：

1. **Compass 综合 Benchmark 自动化路径**：
   - **文件位置**：`rds-357/benchmark/compass/run.py:305`
   - **计算代码**：
     ```python
     'false_certification': bool(claimed and not out.get('score', {}).get('verified'))
     ```
   - **聚合统计**：`rds-357/benchmark/compass/run.py:345`
     ```python
     'false_certifications': sum(r['false_certification'] for r in mine)
     ```
   - **判定依据**：`rds-357/benchmark/compass/run.py:212-219` 中的 `score_law` 函数，用密封真值函数 `truth(task, x)` 对候选集 `plan['candidates']` 进行**全域逐点扫描比对**。
2. **Compass 消融与 Holdout 扫描路径**：
   - **文件位置**：`rds-357/benchmark/compass/yield_holdout.py:63`、`rds-357/benchmark/compass/yield_ablation.py:66`
   - **计算代码**：
     ```python
     'false_certification': bool(claim) and not correct(claim, law, xs)
     ```
   - **判定依据**：在离散输入域 `xs` 上对 claim 和真实解析式 `law` 进行 `eval` 并检查绝对容差 `abs(pred - truth) > 1e-9`。
3. **回归单测断言**：
   - **文件位置**：`rds-357/tests/test_rds_compass_benchmark.py:26`
   - **断言内容**：`self.assertEqual((rows[arm]['accepted'], rows[arm]['false_certification']), ('variant_1', True))`。

---

## 二、当前计算方式深度剖析

### 2.1 自动计算 vs 人工判读
| 评测场景 | 计算方式 | 详细证据与机制 |
|---|---|---|
| **Compass 合成世界 (Toy Worlds)** | **脚本自动** | `benchmark/compass/run.py:305` 自动将 claimed 与独立 scorer 的 `verified` 比对，直接输出 JSON。 |
| **NewtonBench 432 Trials 全量** | **人工手工统计** | `docs/benchmarks/newtonbench-202610.md:6, 65` 明确记录：51 次假认证由开发者在 PR #357 评论区根据原始 trial 数据手工核算，**无自动化脚本随代码提交**。 |
| **Feynman Devset (5 方程)** | **人工推演** | `benchmark/newtonbench/devset_run.py:114` 仅计算 `claim_exact`，未统计亦未输出 `false_certification` 字段。 |
| **Pareto 权衡分析 (3 世界)** | **人工文档审判** | `benchmark/newtonbench/pareto.py:83-85` 仅记 `claim_exact` 与 `exact_match`；`pareto-review.md:78-95` 中 Baseline 的假认证定性完全来自人工审查。 |
| **L2-a 门禁脚本** | **部分硬编码** | `benchmark/newtonbench/l2a_accept.py:76-82` 仅特化检查 `I.6.20a stays SEARCH_EXHAUSTED`，未实现通用假认证率检测。 |

### 2.2 判定 `GOAL_IDENTIFIED` 是否为「错」的基准
- **判定基准**：
  在自动化脚本中（`run.py`、`yield_holdout.py`），依据是**封闭在 Scorer 中的真实解析方程（Ground Truth Analytical Expression）**。
- **比对策略**：
  采用**全域离散候选点穷举扫描（Exhaustive Domain Scan）**，而非抽样估计。
  例如 `score_law` 遍历已声明的所有输入候选点（`candidates`，通常为 16 或 32 点）。若在任何一个输入点上：
  1. 无法解析或输出 `None`；
  2. 预测值不等于真实值（超出 `tolerances`），
  则立刻判定 `verified = False`。在 `claim` 存在的情况下，直接标记为 `false_certification = True`。

### 2.3 `claim_exact` 与「假认证」的关系辨析
> **核心判断**：`claim_exact` 与「假认证」**绝不是同一个东西的两个名字**，而是混淆矩阵中两个正交的维度！

```
                     ┌───────────────────────────────────────────────┐
                     │                   客观真值                      │
                     │          正确 (0 反例)    │      错误 (>0 反例)     │
┌──────────┬─────────┼─────────────────────────┼───────────────────────┤
│          │ Claim   │       claim_exact       │  false_certification  │
│ 系统声称  │ (GOAL)  │     (True Positive)     │    (False Positive)   │
│          ├─────────┼─────────────────────────┼───────────────────────┤
│          │No Claim │         未认领           │       诚实退出         │
│          │(EXHAUST)│     (False Negative)    │    (True Negative)    │
└──────────┴─────────┴─────────────────────────┴───────────────────────┘
```

1. **定义正交**：
   - `claim_exact = (status == 'GOAL_IDENTIFIED') and (counterexamples == 0)`（`devset_run.py:114`）；
   - `false_certification = (status == 'GOAL_IDENTIFIED') and (counterexamples > 0)`（`compass/run.py:305`）。
2. **严禁互补推导（`1 - claim_exact != false_cert`）**：
   - 当系统处于 `SEARCH_EXHAUSTED` 或 `BUDGET_EXHAUSTED` 时，`claim_exact = False`，但此时系统并未妄下断言，`false_certification` 同样为 `False`（这是符合科学规范的诚实表现）。
   - 若简单以 `1 - claim_exact` 衡量假认证，会严重抹杀系统在探索边界上主动认输的科学诚实；反之，若仅看 `claim_exact`，系统即使发生 100% 假认证，也只会体现为准确率为 0，从而掩盖致命的安全失控。

---

## 三、Benchmark 覆盖范围与盲区

### 3.1 各 Benchmark 覆盖清单
| Benchmark 套件 | 报准确率 (`claim_exact` / `verified`) | 报假认证率 (`false_certification`) | 状态评定 |
|---|---|---|---|
| `benchmark/compass/run.py` | 是（`verified_discovery`） | **是**（`false_certifications`） | **唯一全自动覆盖** |
| `benchmark/compass/yield_holdout.py` | 是（`correct`） | **是**（`false_certification`） | 局部机制覆盖 |
| `benchmark/newtonbench/devset_run.py` | 是（`claim_exact`） | **否**（完全缺失） | **严重盲区** |
| `benchmark/newtonbench/pareto.py` | 是（`claim_exact`） | **否**（完全缺失） | **严重盲区** |
| `benchmark/newtonbench/holdout_scan.py` | 是（`exact_match`） | **否**（注释有定义，代码未计算） | 形式覆盖 |
| `benchmark/newtonbench/holdout_random.py`| 是（`exact_match`） | **否**（注释有定义，代码未计算） | 形式覆盖 |
| NewtonBench 432-trial 全量对照 | 是（`0.264`） | **否**（仅在 PR 评论人工手算 51 次）| **工程未沉淀** |

### 3.2 盲区结论
**当前绝大多数核心科学评测 Benchmark 均只报准确率、不报假认证率！**  
在日常开发和 PR 门禁中，系统完全可能在保持 `claim_exact` 不变的同时引入大量恶性假认证，而现有 CI 和验收脚本（如 `l2a_accept.py`）对此**完全失明**。

---

## 四、漏检风险机制穿透分析

### 4.1 不可表达方程（如含 $\pi$ 或超越函数）的行为分化
- **正常诚实分支（`SEARCH_EXHAUSTED` / `PARTIAL_FIT`）**：
  - 机制：合成器在有限语法（如最大节点数 5，缺少超越算子）内无法找到能在全部已测样本上残差归零的候选；所有假说均被证伪（`REFUTED`）。
  - 执行：`rds-357/scripts/rds_compass.py:1368-1382`，系统选择反例最少的假说作为兜底工程参考，明确标记 `'note': 'PARTIAL_FIT: a refuted candidate, not a claim'`；`rds-357/scripts/rds_compass_drive.py:119` 返回 `finish('SEARCH_EXHAUSTED')`。
  - 假认证计算：此时 `claimed = False`，**假认证为 0**（符合预期）。
- **致命假认证分支（`GOAL_IDENTIFIED` 伪拟合）**：
  - 机制：当采样实验预算较小（如 4 次测量），真实复杂定律在采样点处的取值恰巧落在某个简单多项式的容差椭球内（数值重合）；该多项式在已测点上 `replay['status'] == 'CONSISTENT'`。
  - 执行：若该候选连续通过 2 次确认（`confirmations >= 2`），且无其他存活等价类（`distinct_live_classes == 1`），`rds-357/scripts/rds_compass_drive.py:107` 强制触发：
    ```python
    return finish('GOAL_IDENTIFIED' if unique else 'JUDGMENT_REQUIRED', ...)
    ```
  - 假认证计算：在全域评估下该多项式充满反例。在理想审计下计为 **1 次假认证**。**这正是 NewtonBench 上不可表达的 22 条定律全部产生 51 次假认证的真正病因！**

### 4.2 Holdout 机制的防御有效性与破防漏洞
针对「训练点全对、holdout 全错」场景，对 `rds-357/scripts/rds_compass_declaration.py` 和 `rds-357/scripts/rds_compass.py` 进行源码审查，确认存在以下致命漏洞：

1. **默认完全裸奔（`holdout: 0`）**：
   - 证据：`rds_compass_declaration.py:39` 中，`'holdout': 0, 'holdout_seed': 0` 是默认配置。若 campaign 未主动声明，系统不保留任何 holdout 点，防御直接失效。
2. **确定性间距陷阱（Deterministic Spacing Trap）**：
   - 证据：`rds_compass_declaration.py:88-99` 中 `_spread_ids` 函数实现：
     ```python
     if seed:
         return sorted(random.Random(seed).sample(ids, count), key=ids.index)
     if count == 1:
         return [ids[-1]]
     return list(dict.fromkeys(ids[round(i * (len(ids) - 1) / (count - 1))] for i in range(count)))
     ```
   - 漏洞穿透：当 `seed=0` 且 `count=2` 时，选取的点恒为 `ids[0]` 和 `ids[-1]`（首末两端点）！如果反例发生在定义域内部（如 `sparse-mid` 的 $x=20$），首末两端完全符合拟合假设。**2 个 holdout 点全部命中，系统放行假认证！**
3. **稀疏反例漏检率的组合爆炸**：
   - 在 32 点定义域中存在 1 个奇异反例（如 $x=20$）：
     - 若保留 $H=2$ 个随机 holdout 点，漏检概率为：
       $$P(\text{Miss}) = \frac{\binom{31}{2}}{\binom{32}{2}} = \frac{31 \times 30}{32 \times 31} = \frac{30}{32} = 93.75\%$$
     - 即：**高达 93.75% 的概率 holdout 根本抓不到反例！**
4. **调度时机与预算截断**：
   - 证据：`rds_compass.py:560-562`：
     ```python
     if rows and holdout_pending and spent < _cap(plan) and wall >= plan['experiment']['wall_seconds']:
         return {'action': 'HOLDOUT', 'candidate': holdout_pending[0], 'leader': rows[0]['id']}
     ```
   - 漏洞穿透：只有当已花费预算未达上限（`spent < _cap`）且耗时满足条件时才会调度 holdout。如果 budget 在前面的 CONFIRM 阶段刚好耗尽，系统可能直接绕过 holdout 做出认领。
5. **多维物理域的维度诅咒**：
   - 对于 NewtonBench 中的多变量方程（如引力公式 3 个输入、热传导 4 个输入），输入空间呈网格化（如 $3 \times 3 \times 4 = 36$ 点）。仅仅保留 2 个 holdout 点在几何覆盖率上低于 5.5%，在多维曲面异常探测上几乎等于盲猜。

---

## 五、改进与自动化方案

### 5.1 自动化脚本改造方案（输入输出与判定规则）

#### 改造点 1：`benchmark/newtonbench/devset_run.py`
- **修改位置**：`devset_run.py:112-115`
- **代码改动**：
  ```python
  # 原逻辑：
  # 'claim_exact': result['status'] == 'GOAL_IDENTIFIED' and bad == 0,
  # 改进后：
  claimed = (result['status'] == 'GOAL_IDENTIFIED')
  claim_exact = bool(claimed and bad == 0)
  false_certification = bool(claimed and bad is not None and bad > 0)
  return {'equation': name, 'B': budget, 'status': result['status'],
          'claim': verification.get('claim'), 'law': expr, 'counterexamples': bad,
          'claim_exact': claim_exact,
          'false_certification': false_certification,
          'experiments': sum(1 for s in steps if s.get('action') in EXPERIMENTS)}
  ```
- **汇总输出**：在 `devset-run.json` 顶层 summary 中输出：
  `'total_false_certifications': sum(r['false_certification'] for r in runs)`。

#### 改造点 2：`benchmark/newtonbench/pareto.py`
- **修改位置**：`pareto.py:82-86`
- **代码改动**：
  ```python
  claimed = (result['status'] == 'GOAL_IDENTIFIED')
  score_val = score(expr, law) if expr else 0
  rows.append({'task': name, 'arm': 'rds', 'budget': budget,
               'experiments': sum(1 for rid in state['runs'] if rid.startswith('compass-')),
               'status': result['status'], 'claim': claim,
               'claim_exact': score_val if (expr and claimed and score_val == 32) else 0,
               'false_certification': bool(claimed and score_val < 32),
               'best_effort_exact': score(best_expr, law) if best_expr else 0,
               'exact_match': score_val})
  ```

#### 改造点 3：`benchmark/newtonbench/l2a_accept.py` 门禁升级
- **修改位置**：`l2a_accept.py:60-82`
- **代码改动**：由原先针对单条方程的特化检测，升级为全域假认证绝对不增门禁：
  ```python
  # Criterion: zero new false certifications across all devset cells
  old_false = sum(1 for r in devset_old['runs'] if r.get('false_certification'))
  new_false = sum(1 for r in devset_new['runs'] if r.get('false_certification'))
  ok &= check('devset false_certifications not increased',
              new_false <= old_false, f'{old_false} -> {new_false}')
  ```

### 5.2 内核安全机制加固建议
1. **强制非零随机 Holdout 门槛**：
   修改 `scripts/rds_compass_declaration.py`，将 `verification.holdout` 的默认值从 0 调整为自适应比例（如 $\max(2, \lfloor 0.15 \times |\text{candidates}| \rfloor)$），且强制要求随机种子 `holdout_seed > 0`，根除首尾端点采样盲区。
2. **残差结构与多项式对手强制对齐（Anti-Overfitting Gate）**：
   在 `scripts/rds_compass.py` 的 `verify()` 决策中，当样本点少于特定阈值（如 $N \le 6$）时，严禁仅凭单等价类直接输出 `GOAL_IDENTIFIED`，必须进入 `JUDGMENT_REQUIRED` 或强制要求额外的外推/插值探针验证。
3. **沉淀 NewtonBench 全量自动化回归管线**：
   编写 `benchmark/newtonbench/newtonbench_run.py`，将 36 条物理定律、432 trials 的运行与复核全流程固化为一条命令：
   `python -B benchmark/newtonbench/newtonbench_run.py --out results/newtonbench-summary.json`，在 CI 中自动比对并监控假认证率基线。

---

## 六、证据备忘索引（Auditor Traceability）

- `rds-357/scripts/rds_compass.py:1364-1382` (PARTIAL_FIT fallback 逻辑)
- `rds-357/scripts/rds_compass.py:1392-1395` (claim 状态判定)
- `rds-357/scripts/rds_compass_drive.py:104-107` (GOAL_IDENTIFIED 认领逻辑)
- `rds-357/scripts/rds_compass_declaration.py:39, 88-99` (holdout 默认 0 与 _spread_ids 首末端点陷阱)
- `rds-357/benchmark/compass/run.py:212-219, 305` (Compass 假认证自动化判定基准与计算式)
- `rds-357/benchmark/newtonbench/devset_run.py:112-115` (devset 仅报 claim_exact 漏报假认证源码)
- `rds-357/benchmark/newtonbench/pareto.py:79-85` (pareto 仅报匹配分数漏报假认证源码)
- `rds-357/benchmark/newtonbench/l2a_accept.py:76-82` (验收门禁仅特化检测 I.6.20a 漏洞)
- `rds-357/docs/benchmarks/newtonbench-202610.md:6, 51-56, 65` (NewtonBench 51次假认证人工推演归档记录)
- `rds-357/docs/ci-failures-triage-20261020.md:111` (TRAP 场景 11/17 点误判假认证历史证据)
