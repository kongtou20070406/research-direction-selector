# 红队独立复核报告：Stage 2 假设接入协议（commit 3864dfa）审查

- **被审文档**：`docs/stage2-hypothesis-protocol.md`（commit `3864dfa`）
- **复核员**：gem-review（红队）
- **日期**：2026-10-21
- **测试环境**：Python 3.13.5，测试脚本位于仓库外临时目录 `_gem_review/`，未修改仓库中任何代码或测试文件。

---

## 快速结论

- **主任务（六条验收判据审查）**：仅 1 条有效（✅ Check 5），其余 5 条均存在致命缺陷：2 条永远不可判/必定失败（❌ Check 1, ❌ Check 3），2 条存在严重 Fail-Open 假通过漏洞（⚠️ Check 2, ⚠️ Check 6），1 条因底层代码白名单限制无法执行（❌ Check 4）。
- **附加任务（`_fits` 接入点审查）**：协议 §4 提出的「在 `_fits` 顶端加一个 `if` 作为唯一接缝」在架构上完全错误。`_fits` 仅在 `generate_rivals`（行 915）与 `escape`（行 1190, 1196）被调用；在该处短路会彻底破坏竞争假设（Rivals）与证伪逃逸（Escape）的语义。

---

## 一、六条验收判据逐条审查

### Check 1: 比较两个 JSON 逐字段（Byte-identical）
- **协议声明**：
  > A campaign with no `hypotheses` block is byte-identical: Run `benchmark/newtonbench/devset_run.py` on two equations before and after; compare the two result JSONs' `runs` entries field by field.
  > （§4 Reversion trigger: Check 1 fails on any equation, even by one field, and the difference is not explainable by provenance alone.）
- **红队结论**：❌ **不可执行（必定 FAIL，等于摆设）**
- **证据与行号**：
  1. `benchmark/newtonbench/devset_run.py:342` 在每个 run 结果中强制写入浮点执行时间：`row['seconds'] = round(time.monotonic() - t0, 1)`。
  2. `benchmark/newtonbench/devset_run.py:345` 写入动态临时路径：`'campaign_root': str(base)`，其中 `base = Path(tempfile.mkdtemp(prefix='devset-run-'))`（行 329）。
  3. `benchmark/newtonbench/devset_run.py:352` 写入总耗时：`'elapsed_seconds': round(time.monotonic() - start, 1)`。
- **实测验证**（`_gem_review/test_check1_field_by_field.py`）：
  对同一方程 `feynman-I.12.1` 连续跑两次原版 `devset_run.main`：
  - `runs[0]` 的 `seconds` 字段为 `5.2` vs `4.4`；
  - `runs[1]` 的 `seconds` 字段为 `9.5` vs `9.7`；
  - `runs[2]` 的 `seconds` 字段为 `16.2` vs `16.1`；
  - 顶层 `campaign_root` 和 `elapsed_seconds` 全部不一致。
- **定性**：如果严格按协议要求对 `runs` 字段做逐字段比较，由于时间测量存在物理抖动，**Check 1 在任何真实运行下 100% 触发回滚（FAIL）**。必须明确将 `seconds`、`campaign_root`、`elapsed_seconds` 等不稳定字段排除在比对范围外。

---

### Check 2: Monkeypatch synthesize 让其抛出异常
- **协议声明**：
  > A campaign with the block never enumerates: Monkeypatch `scripts/rds_compass_model.py:synthesize` to raise; the run must complete without raising.
- **红队结论**：⚠️ **严重 Fail-Open（异常被静默吞掉，假通过）+ 无法直接 CLI 执行**
- **证据与行号**：
  1. **异常吞噬漏洞**：`benchmark/newtonbench/devset_run.py:339-341`：
     ```python
     try:
         row = run_one(name, budget, base)
     except Exception as exc:  # one failed declaration must not sink the sweep
         row = {'equation': name, 'B': budget, 'claim_exact': False,
                'error': f'{type(exc).__name__}: {exc}'}
     ```
     `devset_run.py` 外层将单格异常完全捕获，记录在 `row['error']` 中并返回 exit code 0。
  2. **缺少 CLI 注入机制**：`devset_run.py` 没有任何命令行参数支持在外部注入 monkeypatch，使用者只能手写外部包装脚本。
  3. **存活假说依然会触发 synthesize**：在当前代码下，即使声明了 hypothesis，`rds_compass.py:548` 在每个测量 step 依然会触发 `action == 'RIVALS'`，进而调用 `generate_rivals` -> `_fits` -> `synthesize`。
- **实测验证**（`_gem_review/test_check2_swallowed_exception.py` 与 `test_count_synthesize.py`）：
  - 当对 `model.synthesize` 注入异常时，`devset_run.main` 顺利执行完毕（exit code 0），外层完全没有 raise，生成的 JSON 中 `runs` 记录为 `'error': 'RuntimeError: SYNTHESIZE CRASHED!'`。
  - 按判据「the run must complete without raising」判定，**哪怕全盘崩溃也直接判为 PASS（严重 Fail-Open）**。
  - 在未封堵 `RIVALS` 时，带有声明假设的 campaign 在运行期间调用了整整 13 次 `synthesize`。
- **修复要求**：判据必须额外校验 `row['status'] == 'GOAL_IDENTIFIED'` 且 `row.get('error') is None`。

---

### Check 3: 渲染表达式与实验数量
- **协议声明**：
  > The declared hypothesis is actually used: The result's `law` renders to the declared expression, and `experiments` drops to the number needed to test it.
- **红队结论**：❌ **不可执行（规范化导致字符串比对必错，且后半句为无定义模糊口径）**
- **证据与行号**：
  1. **语法树规范化（Canonicalization）**：`scripts/rds_compass_model.py:657` 的 `render()` 对 AST 强制添加严格括号、空格，并对交换律运算（如乘法、加法）进行字母序重排。
  2. **预算耗尽策略**：`scripts/rds_compass_declaration.py:40` 中 `verification.spend_budget` 默认是 `True`。这意味着只要还有实验预算，系统就会持续生成 `CONFIRM` 动作，直至把预算耗尽。
- **实测验证**（`_gem_review/test_check3_render.py` 与 `test_drive_with_hypotheses.py`）：
  - 测试 devset 前 10 个方程，将原始声明表达式与 `model.render(model.parse(...))` 比对，**匹配率为 0%（10/10 全部不相等）**：
    - `feynman-I.12.1`：原声明 `mu*n`，渲染为 `(mu * n)`（空格与括号差异）；
    - `feynman-I.12.5`：原声明 `q2*Ef`，渲染为 `(Ef * q2)`（变量排序重排）；
    - `feynman-I.10.7`：科学计数法 `2.99792458e8**2` 渲染为 `(299792458 ** 2)`。
  - 在 `feynman-I.12.1` 声明真实假说运行下，`experiments` 耗尽了全部 8 次预算，并没有「drops to the number needed to test it」。
- **定性**：若判据使用字符串判等，将直接因 AST 规范化误判。后半句「the number needed to test it」属于未形式化的模糊语言，无法编写自动化门禁。

---

### Check 4: 来源标识（Provenance）进入账本
- **协议声明**：
  > Provenance reaches the ledger: `COMPASS_HYPOTHESIS` rows carry the declared `provenance` string verbatim.
- **红队结论**：❌ **当前基线下无法执行（模型层严格白名单直接报错）**
- **证据与行号**：
  `scripts/rds_compass_model.py:533-535`：
  ```python
  require(isinstance(value, dict) and {'id', 'predict'} <= set(value)
          and set(value) <= {'id', 'statement', 'predict', 'applies', 'premises', 'origin', 'parent'},
          'Hypothesis requires id and predict')
  ```
- **实测验证**（`_gem_review/test_fits_behavior.py`）：
  在 declaration 的 `hypotheses` 中传入 `{'id': 'h1', 'predict': {'y': 'x'}, 'provenance': 'human'}`，在 `decl.initialize` 阶段直接抛出 `ValueError: Hypothesis requires id and predict`。
- **定性**：协议 §4 宣称只需修改 `scripts/rds_compass.py`，但此判据在不改动 `scripts/rds_compass_model.py` 字段白名单的情况下甚至无法通过初始化解析。

---

### Check 5: 语法错误/未声明变量在摄入时拒绝
- **协议声明**：
  > An unparseable hypothesis fails closed: A block naming a variable the campaign did not declare raises at ingest, not at replay.
- **红队结论**：✅ **通过（有效且已具备 Fail-closed 特性）**
- **证据与行号**：
  `scripts/rds_compass_declaration.py:232` 在 `initialize()` 编译 plan 时，调用 `model.compile_hypothesis` 校验变量是否在 `inputs` 中声明。
- **实测验证**（`_gem_review/test_check5_undeclared_var.py`）：
  声明未定义的变量 `z`（`predict: {'y': 'mu * z'}`），在 `initialize()` 阶段直接抛出 `ValueError: Undeclared variable: z`，未延迟至 replay。

---

### Check 6: 假认证率检测运行
- **协议声明**：
  > The false-certification harness still runs: `false_certification_rate` is present in the result file, not inferred.
- **红队结论**：⚠️ **严重 Fail-Open（空跑与崩溃时默认存在键，假通过）**
- **证据与行号**：
  `benchmark/newtonbench/devset_run.py:295-306`（`summarize` 函数）：
  ```python
  def summarize(runs):
      ...
      return {'false_certification_rate': false_cert / len(known) if known else None, ...}
  ```
- **实测验证**（`_gem_review/test_check6_fail_open.py`）：
  当 `runs` 为空（例如全部方程因为错误跳过，或者没有生成任何 claim 时），`summarize([])` 生成的字典中 `'false_certification_rate'` 键依然存在（其值为 `None` / `null`）。
- **定性**：判据仅检查 `is present in the result file`。只要 JSON 文件写出，该键必然存在，即使没有任何数据被核验过。判据在无数据时默认通过（Fail-Open）。必须修改为：`checkable_claimed_count > 0 and false_certification_rate is not None`。

---

## 二、附加任务：`_fits` 调用点与架构接缝分析

### 1. `_fits` 在 `scripts/rds_compass.py` 中的全部调用位置

`_fits` 定义于 `scripts/rds_compass.py:772`：
`def _fits(state, *, rows=None, probes=None, bound=None, escalate=False):`

在 `scripts/rds_compass.py` 内部仅有以下 **3 处** 调用：
1. **`scripts/rds_compass.py:915`**（位于 `generate_rivals` 函数）：
   ```python
   def generate_rivals(store, state):
       ...
       found, notes = _fits(state)
   ```
2. **`scripts/rds_compass.py:1190`**（位于 `escape` 函数的主路径）：
   ```python
   def escape(store, state, diagnosis):
       ...
       found, notes = _fits(state, escalate=True)
   ```
3. **`scripts/rds_compass.py:1196`**（位于 `escape` 函数中的 RSI 升级路径）：
   ```python
   if rsi['qualified']:
       state = fold(store)
       found, notes = _fits(state, escalate=True)
   ```

### 2. 在 `_fits` 顶端插入声明式假设的架构冲突分析

协议 §4 断言：
> Implementing the block requires touching `scripts/rds_compass.py` more than once. The seam is one `if` at the top of `_fits`; if it is not, the design is wrong.

经代码追踪与实测，这一断言存在严重的架构性误判：

1. **`_fits` 的职责是观测拟合，不是假设声明摄入**：
   - 声明式假说（Declared Hypotheses）在 `scripts/rds_compass_declaration.py:229-236` 已经由 `initialize()` 编译并写入初始账本，存放在 `state['hypotheses']` 中。
   - `_fits` 的本职工作是：在拥有实际实验观测值 `rows` 的前提下，通过符号搜索（`synthesize`）寻找能够解释数据的候选假说。
2. **破坏竞争对手生成（`generate_rivals`，行 915）**：
   - 当初始假说存活且需要寻找竞争假说时，调度器会进入 `action == 'RIVALS'`。
   - 此时 `generate_rivals` 调用 `_fits(state)`。若在 `_fits` 顶端短路返回声明式假说本身，`generate_rivals` 拿到的不是竞争假说，而是当前正在检验的假说本身。这会导致后续 `_novel` 检查判其冗余丢弃，或者让假设库陷入自相矛盾。
3. **破坏被证伪后的逃逸机制（`escape`，行 1190/1196）**：
   - 当声明式假说被反例证伪，触发 `ALL_REFUTED` 诊断时，调度器进入 `action == 'ESCAPE'` 试图寻找替代假说。
   - 若 `_fits` 依然无条件返回声明式假说，系统将反复吐出已被打死的假说，导致无限死循环或非预期的停滞。
4. **单处修改无法成立**：
   - 要让声明式假说在不运行全量枚举（Check 2）的情况下顺利完成闭环，必须在调度层（`decide`，行 473）处理好「已有外部假说时无需生成 rivals」或「如何管理外部假说生命周期」，同时修改 `scripts/rds_compass_model.py:533` 的字段白名单。仅在 `_fits` 加一行 `if` 绝不可能完成 Stage 2。

---

## 三、复核结论摘要表

| 判据 / 任务 | 审查结果 | 致命问题根因 | 实测依据文件 |
| :--- | :---: | :--- | :--- |
| **Check 1** (JSON byte-identical) | ❌ 必定 FAIL | `seconds`、`campaign_root` 动态字段导致比较必不相等 | `devset_run.py:342, 345` |
| **Check 2** (Monkeypatch synthesize) | ⚠️ 严重 Fail-Open | 外层 `try...except` 吞掉异常，crash 也判通过；带假说时依然调 13 次 synthesize | `devset_run.py:339` |
| **Check 3** (Render to declared) | ❌ 必定 FAIL | AST `render` 强制规范化（括号、排序），与原文匹配率为 0% | `rds_compass_model.py:657` |
| **Check 4** (Provenance to ledger) | ❌ 无法执行 | `compile_hypothesis` 字段白名单拦截 `provenance` | `rds_compass_model.py:533` |
| **Check 5** (Ingest fail-closed) | ✅ 通过 | 未声明变量在 `initialize` 时被 `parse` 拦截并报错 | `rds_compass_declaration.py:232` |
| **Check 6** (False cert harness) | ⚠️ 严重 Fail-Open | `summarize([])` 默认产生值为 `null` 的键，空跑也能满足判据 | `devset_run.py:300` |
| **附加任务** (`_fits` 架构接缝) | ❌ 架构错误 | 仅在 `_fits` 加 `if` 会彻底破坏 `generate_rivals` 与 `escape` 语义 | `rds_compass.py:915, 1190` |
