# Packaging plan: `scripts/` 平铺模块 → `rds/` 包（2026-10-20）

纯设计文档，不改任何 `.py`。目标：把 `scripts/` 下 173 个模块（170 个 `rds_*` + 3 个非前缀，51,442 行）拆进 `rds/{shared, kernel, graph, compass, adapters, cli}`，保住 git history、账本兼容和 AGENTS.md 不变量。

## 0. 数据基线（可复验）

本文所有数字来自同一快照，读者可自行复验：

```sh
cd rds-357
python -B tools/import_graph.py --root . > import-graph.json
```

- Source-set SHA-256：`43da286db0125bb5e149a9959242961b01c5f800e55add0fa194c3628241a051`
- 分支：`release/6.0.0-compass`
- 任务书口径「148 个 `rds_*.py` / 51,397 行」是早期快照；当前图为 **173 模块 / 642 边 / 897 import sites / 51,442 行**（B 系列重构持续在加模块，以图为准）。

| 指标 | 当前值 | 口径 |
|---|---:|---|
| 模块 | 173 | `scripts/**/*.py` |
| 依赖边 | 642 | static + provider_callback |
| import sites | 897 | 含函数内延迟 import |
| 向上边 | 18 对 / 21 sites | 层标签口径 |
| 双向互引 | 40 对 | mutual pairs |
| 循环 SCC | 4 个 | 49 / 4 / 3 / 2 模块 |
| 未解析动态点 | 1 | `rds_capabilities:38` |

最大 SCC（49 模块）的层构成：L1×25、L3×18、L2×3（`rds_discrimination`、`rds_hypergraph`、`rds_hypergraph_input`）、L4×3（`rds_quick`、`rds_dialogue`、`rds_autonomy_worker`）。其余三个 SCC：`{rds_affine_toolchain, rds_builtin_verifiers, rds_theory_progression, rds_verify}`、`{rds_meta, rds_rsi, rds_rsi_confirmation}`、`{rds_compass, rds_compass_rsi}`。

### 0.1 本文的支点实测：模块级 import 子图已经是 DAG

把 49 模块大 SCC 的 393 条内部边按 import 位置拆开：

| import 位置 | 边数 | 运行时是否构成加载死锁 |
|---|---:|---|
| 模块级（`scope == ""`） | 73 | 可能 |
| 函数内延迟 import | 317 | 否（调用时双方已在 `sys.modules`） |
| provider_callback（`lifecycle_provider('advisor')`） | 3 | 否（注册表查表） |

对这 73 条模块级边单独跑 Tarjan：**没有大小 >1 的 SCC，即模块级子图是无环图**。也就是说「49 模块巨型 SCC」是 import_graph 把函数内延迟 import 保守计入后的全口径结果，不是 Python 加载意义上的循环。

推论：**物理拆包现在就可行，不需要先重写 393 条边**。真正挡在包边界上的只有 4 条模块级向上边（§2.2）。317 条函数内边拆包后跨包照常合法（`import rds.adapters.x` 在函数内与在 `scripts/` 平铺时语义相同），它们是长期的「消环」对象，不是拆包前置条件。

## 1. 目标包结构

### 1.1 分包表

| 子包 | 模块数 | 装什么 | 为什么 |
|---|---:|---|---|
| `rds/shared/` | 20 | SHARED 层值契约：`rds_core`、`rds_project_values`、`rds_reference_values`、`rds_guard_values`、`rds_admission_values`、`rds_output_values`、`rds_verify_types`、`*_values`、`*_records`、`rds_interrupt`、`rds_path_identity` 等 | 纯值/解码/算术，无平台所有权，零账本接触。叶子先行，所有人的地基 |
| `rds/kernel/` | 71 | L0×3（`rds_bounded_io`、`rds_mutation`、`rds_sqlite`）+ L1×68：`rds_project`（ProjectStore）、`rds_state`、`rds_cas`、`rds_tms_store`、`rds_campaign`、`rds_workspace`、`rds_guard`、12 个 `*_verify`/验证器、`rds_quick_ledger`、`rds_checkpoints`、`rds_outbox` 等 | Single Authoritative Ledger 的全部所有者：账本读写、admission、结算、CAS、TMS、证据/回执校验、证书内核。一个包 = 一个信任边界 |
| `rds/graph/` | 14 | L2：`rds_hypergraph`、`rds_hypergraph_input`、`rds_discrimination`、`rds_accelerator`、`rds_decision_packet`、`rds_trajectory`、`rds_transition` 等 | 派生研究关系与状态投影，只读 kernel、不改账本 |
| `rds/compass/` | 8 | Compass 家族竖切：`rds_compass_model`（SHARED）、`rds_fit_trend`（SHARED）、`rds_compass_store`（L1）、`rds_compass_declaration`（L1）、`rds_compass`（L3）、`rds_compass_rsi`（L3）、`rds_compass_drive`（L3）、`rds_compass_cli`（L4） | 家族内部已是 `model → store → declaration → compass → drive → cli` 单向链；对外模块级出边只有 11 条且全部向下（`rds_project`、`rds_cas`、`rds_artifacts`、`rds_core`），没有外部模块 import 家族内部（入口经 `rds_cli` 函数内调 `rds_compass_cli`）。竖切一个领域包比把 4 层打散落进四个包更保值 |
| `rds/adapters/` | 32 | L3 非 Compass：`rds_advisor*`（5）、`rds_owned_advisor`、`rds_autonomy`、`rds_jump*`、`rds_frontier*`、`rds_meta`、`rds_rsi`、`rds_structure`、`rds_operators`、`rds_math_router`、`rds_search_*`、`rds_experiments`、`rds_feasibility` 等 | 研究策略层：建议、提案、搜索、预测。可以读 kernel/graph，绝不写账本 |
| `rds/cli/` | 28 | L4 非 Compass：`rds_cli`、`rds_quick`、`rds_app`、`rds_dialogue`、`rds_dashboard`、`rds_session`、`rds_project_worker`、`rds_autonomy_worker`、`rds_hypergraph_archive/view/render/readable`、`rds_wheel`、`rds_model_export`、`rds_obelisk`、3 个非前缀工具（`build_plugins`、`paid_query`、`update_lean_toolchain`）等 | 入口、展示、子进程 worker、打包工具。唯一允许「什么都 import」的包 |

包内模块命名去 `rds_` 前缀：`scripts/rds_project.py` → `rds/kernel/project.py`。前缀在包路径里已冗余；`build_plugins` 等 3 个非前缀工具保留原名进 `rds/cli/`（或移 `tools/`，见 §5 风险表 R8）。

### 1.2 包依赖方向（合法 DAG）

```
shared ← kernel ← graph ← adapters ─┐
                     ↑                ├← cli
                     └──── compass ───┘
```

- `compass` 与 `adapters` 平级，都坐在 kernel/graph 之上、cli 之下。实测跨包模块级边里 `compass → adapters` 为 0，只有 1 条函数内边（`rds_compass → rds_math_router`，§2.4 处理）。
- 实测模块级跨包边共 155 条，**违反此方向的只有 4 条**（§2.2），其余 151 条天然合法。
- `kernel` 不 import 任何上层包——这是 Single Authoritative Ledger 的结构性表达：账本所有者不依赖策略。

### 1.3 命名冲突与别名策略

`rds/kernel/math.py`、`rds/kernel/state.py` 与 stdlib 同名不冲突（Python 3 绝对导入，包内 `import math` 仍取 stdlib）。调用方统一用「保留原变量名」的改写规则，diff 最小：

```python
import rds_math                      →  from rds.kernel import math as rds_math
from rds_math import blob            →  from rds.kernel.math import blob
import rds_core as _core             →  from rds.shared import core as _core
```

函数体零改动。`from X import *` 全仓不存在（grep 实证），无需处理通配导出。

## 2. SCC 破环策略

### 2.1 总策略：先搬后消，两步分开

- **搬移（Step 1–6）**：利用 §0.1 的 DAG 实测，物理拆包不需要先消环。验收口径是「模块级跨包边方向合法 + 测试全绿」，不是「SCC 变小」。
- **消环（Step 7，长期）**：搬完后 import_graph 的全口径 SCC 不会自动变小（317 条函数内边还在）。这 317 条里有相当部分根本不需要断——它们是 B1 系列刻意引入的解耦缝（B1 文档原话：recovery 不再 import QUICK 入口，经 provider 加载服务）。消环只针对真实坏味道，见 §2.3。

### 2.2 拆包前置：4 条模块级向上边（Step 0，每条一个独立 PR）

| # | 边 | 位置 | 断法 | 依据 |
|---|---|---|---|---|
| 0a | `rds_tms_store → rds_hypergraph` | `rds_tms_store.py:10`，`from rds_hypergraph import ASSURANCE, _validate, review_hypergraph, audit_sources` | `ASSURANCE` 常量与 `_validate` 纯校验下放 `rds/shared/`（B 系列 `*_values` 同款做法）；`review_hypergraph`/`audit_sources` 改为 TMS 持有注入的 reviewer 回调 | 沿用 B1a/B1d 已验证的「纯值下沉 + 入口 re-export」模式 |
| 0b | `rds_rsi_confirmation → rds_meta` | `rds_rsi_confirmation.py:6`，`from rds_meta import _atomic_bytes, _graph_lock, _load_object, digest, require` | 这五个是文件 IO/锁 helper，本就不属于 L3 策略模块；下放 `rds/shared/`（或并入既有 `rds_bounded_io`/`rds_core`），`rds_meta` re-export 兼容 | helper 错放层级，归位即可 |
| 0c | `rds_discrimination → rds_advisor_search` | `rds_discrimination.py:13`，`from rds_advisor_search import evaluate_condition` | `evaluate_condition` 是纯条件求值，下沉 `rds/graph/` 或 `rds/shared/`；`rds_advisor_search` re-export | graph 层不该依赖策略层 |
| 0d | `rds_tools → rds_quick` | `rds_tools.py:21`，`from rds_quick import execute` | 最硬的一条。B1 文档已定性：execute 携带 QUICK 的嵌套 admission 闭包（锁、契约、review、materialization），抽函数名没用。方案：新增 execution-service 注册点（复用 `rds_project_hooks` 的 `register()`/`provider()` 模式），`rds_quick` 启动时注册，`rds_tools` 经注册点取 execute | B1 文档原文「splitting requires an explicit execution-service boundary and equivalence tests」 |

完成后向上边 18 → 14 对。每条断边 PR 独立验收（§3 Step 0），不捆绑搬移。

### 2.3 拆包后消环（Step 7，长期，不阻塞拆包）

| 优先级 | 环 | 现状 | 断法 |
|---|---|---|---|
| P1 | `rds_compass ↔ rds_compass_rsi` | 6 条函数内互调 | RSI 工具注册表：`rds_compass` 定义工具协议，`rds_compass_rsi` 启动时注册；或把共享查询函数下沉 `rds_compass_store` |
| P1 | `{rds_meta, rds_rsi, rds_rsi_confirmation}` | 0b 断掉后剩 `meta ↔ rsi` | 共享值下沉，`rsi` 对 `meta` 只留单向 |
| P1 | `{rds_affine_toolchain, rds_builtin_verifiers, rds_theory_progression, rds_verify}` | 验证器注册表环 | `rds_verify` 的 composition root 已是注册表模式；把 `theory_progression → verify` 的反向调用改为经 `VerifierSpec` 接口 |
| P2 | kernel 内 40 对 mutual pairs 的 kernel 部分（`rds_project ↔ rds_campaign/rds_workspace/rds_state/rds_quick_ledger/rds_math/rds_method_revision/rds_owned_history/rds_owned_tools/rds_steering`） | 账本内部的高耦合 | 不追求全断。`ProjectStore` 是单一账本所有者，campaign/workspace/state 本来就是它的内部协作件——它们同处 `rds/kernel/` 包内，互引不构成包级循环。只清理跨文件神隐耦合（如经全局变量握手），用显式构造参数 |

### 2.4 技术选型：什么边用什么断法

1. **纯值下沉**（首选）：被向上引用的符号本身无平台所有权，直接移到下层包，原模块 re-export。B1a–B1d 已用四次，测试模式成熟。适用 0a/0b/0c。
2. **Provider 注册表**（次选）：`rds_project_hooks.py` 的 21 行模式（`_modules` 名字白名单 + `register()`/`provider()` + 函数内 lazy import）已在生产跑了整个 B 系列。适用 0d、P1 三环。白名单是代码不是配置，fail-closed：未知名 `ValueError`。
3. **协议类/接口对象**：`rds_verify` 的 `VerifierSpec`/`ProofRule` 已是这个模式（`BuiltinVerifier` 委托 `builtin_module(name)` 加载）。继续用，不新建平行体系。
4. **事件/outbox**（最后手段）：`rds_outbox` 已存在（L1，同事务 v1 envelope）。只有调用双方真的不该知道彼此时才用事件解耦——不要为了消环把直接调用改成异步事件，那会动时序语义。

**明确不做**：不引入新的事件总线框架、不引入 DI 容器库、不用 `sys.meta_path` import hook 做透明别名（静态分析不可追踪，import_graph 会瞎）。AGENTS.md 的 Single Authoritative Ledger 约束在此的投影：拆包不新建任何注册表之外的运行状态，注册表本身只是进程内 import 别名，不持久化、不构成第二账本。

### 2.5 Compass 家族为什么能竖切

家族对外模块级出边共 11 条，全部向下：

| 源 | 目标 | 要的符号 |
|---|---|---|
| `rds_compass` | `rds_project` | `canonical, digest, require`（三个纯函数，`rds_project.py` 本地定义） |
| `rds_compass_store` | `rds_project` | `ProjectStore, canonical, digest, require, _shell_argument` |
| `rds_compass_declaration` | `rds_project` | `IDENTITY, ProjectStore, canonical, digest, file_sha, require` |
| `rds_compass_drive` / `rds_compass_rsi` | `rds_project` | 同上类 |
| `rds_compass_store` | `rds_cas` | `cas_json` |
| `rds_compass` / `rds_compass_declaration` | `rds_artifacts` | `strict_json` |
| `rds_compass_model` | `rds_core` | `canonical, object_digest` |
| `rds_compass_rsi` | `rds_tools` | 工具工作台 |

注意 `canonical`/`digest`/`require` 在仓库里至少有三份本地定义（`rds_project`、`rds_cli`、`rds_reference_values`/`rds_core`）。拆包时应顺手把它们统一到 `rds/shared/` 一份，`rds_project`/`rds_cli` re-export——这是 §1.1 shared 包的固有价值，不是可选美容。但 `ProjectStore`、`IDENTITY`、`file_sha`、`_shell_argument` 属于 kernel 本体，Compass 对它们的依赖是合法的 `compass → kernel` 向下边，保留。

## 3. 迁移顺序

每步独立 PR、独立验收、可独立回滚。所有步骤的公共验收门：

```sh
# (a) 任务硬约束：import_graph 测试全绿
python -B -m unittest discover -s tests -p 'test_import_graph.py'
# (b) 图指标对照（与上一步快照比，写在 PR 描述里）
python -B tools/import_graph.py --root . > import-graph.json
# (c) 本步迁移模块的 focused 测试（AGENTS.md：只跑直接覆盖的 1–2 个模块）
python -B -m unittest discover -s tests -p 'test_rds_<module>.py'
# (d) import 冒烟
python -B -c "import importlib; [importlib.import_module(m) for m in ('rds.shared.core',)]"
```

`tools/import_graph.py` 在 Step 1 先扩展：扫描根从 `scripts/**/*.py` 扩到 `rds/**/*.py` + `scripts/**/*.py` 并存（过渡期），`GROUPS` 映射增加新模块名。此工具改动是 Step 1 的一部分。

### Step 0（4 个 PR）：断 §2.2 四条模块级向上边

- 0a：`ASSURANCE`/`_validate` 下沉。改 `rds_tms_store.py`、新 `rds_hypergraph_values`（SHARED）、`rds_hypergraph.py` re-export。约 40 净行 + 测试。
- 0b：helper 归位。改 `rds_rsi_confirmation.py`、`rds_meta.py`、目标 shared 模块。约 50 净行 + 测试。
- 0c：`evaluate_condition` 下沉。约 30 净行 + 测试。
- 0d：execution-service 注册点。改 `rds_tools.py`、`rds_quick.py`、`rds_project_hooks.py`（或新注册模块）+ 等价性测试（同一 execute 请求经旧/新路径行为一致）。约 120 净行 + 测试。
- 验收追加：`import-graph.json` 的 `counts.upward_edges` 从 18 降到 ≤14，4 条目标边消失。

### Step 1：包骨架 + `rds/shared/`（20 模块）

- `git mv` 20 个 shared 模块 → `rds/shared/*.py`；建 6 个 `__init__.py`（空或只放包 docstring，**不做聚合 re-export**，避免 `__init__` 成为新的环源）。
- codemod 全仓引用（约 77 处下游 import sites + tests）。
- 验收追加：20 个模块 import 冒烟；focused 测试 `test_rds_core.py`、`test_rds_project_values.py` 等。

### Step 2：`rds/kernel/`（71 模块）

- `git mv` L0×3 + L1×68。kernel 文件内含 424 个 import sites（全仓最密），codemod 主体在这一步。
- 同步 codemod tests 中 kernel 模块的 import（`from rds.kernel.project import ProjectStore` 等）。
- 验收追加：`rds.kernel.project` 等 import 冒烟；`test_import_graph.py` 全绿；focused 测试 `test_rds_cas_service.py`、`test_rds_native_recovery.py`；`engine_id` 相关测试（`test_rds_core.py:238` 的 engine_id 用例）确认按 §5-R1 预期变化。

### Step 3：`rds/graph/`（14 模块）

- 14 个 `git mv` + 22 个 source sites。验收追加：`test_rds_hypergraph.py`、`test_rds_decision_packet.py`。

### Step 4：`rds/compass/`（8 模块）

- 8 个 `git mv` + 42 个 source sites。含 `rds_compass_model`、`rds_fit_trend` 两个 SHARED 层成员（竖切优先于层标签）。
- 验收追加：`test_rds_compass_drive.py`、`test_rds_compass_model.py`、`test_rds_compass_tools.py`。Compass 的 parent-digest hash chain 是账本数据，不受影响（无文件移动触及 `.rds/`）。

### Step 5：`rds/adapters/`（32 模块）

- 32 个 `git mv` + 202 个 source sites。验收追加：`test_rds_advisor.py`、`test_rds_jump_loop.py`、`test_rds_feasibility.py`。

### Step 6：`rds/cli/`（28 模块）+ 入口薄壳

- 28 个 `git mv` + 198 个 source sites。
- 保留 5 个 `scripts/` 薄壳（§4.2）：`rds_cli.py`、`rds_compass_cli.py`、`rds_project_worker.py`、`rds_autonomy_worker.py`、`rds_quick.py`。每个 ~10 行：解析 `__file__` 定位 repo root → `sys.path` 兜底 → `from rds.cli.cli import main; main()`（worker 同构）。
- 验收追加：端到端 `python -B scripts/rds_cli.py --help` 与拆包前输出逐字节一致；`python -B scripts/rds_cli.py project init/exec` 最小项目冒烟；worker 子进程启动冒烟。

### Step 7（长期，可拆多个 PR）：消 §2.3 的环

- 每消一环，验收口径是 import_graph 的 SCC 计数真实下降（49 → …）。这步没有截止压力，与功能开发穿插进行。

### codemod 工具

写一个一次性 `tools/migrate_imports.py`（约 200 行，进 `tools/` 保留证据）：基于 `tools/import_graph.py` 的输出 JSON 做精确 site 替换（不用正则全文扫——import_graph 已给出每条的行号、scope、kind，按行号替换零误伤），覆盖 897 个 import sites + `rds_project_hooks._modules` 等白名单字符串表。tests 的 `sys.path.insert(0, str(ROOT / 'scripts'))` → `sys.path.insert(0, str(ROOT))` 用同一工具的规则通道处理。

## 4. 兼容性

### 4.1 模块 import：直接断，不用模块级 shim

仓内所有 `import rds_*` 调用方都在视野内（`scripts/`、`tests/` 373 文件、`tools/`、`examples/`、`benchmark/`），codemod 一次改完，不留 148 个 re-export shim 文件。理由：shim 文件本身就是 `scripts/` 平铺的延续，留着它们等于没拆；且 import_graph 需要额外规则才能看穿 shim 的传递边。

仓库**外部**若有第三方脚本 `import rds_project`：本仓库不是已发布的库（无 `pyproject.toml` 包声明，`scripts/` 靠 `sys.path` 插入工作），不存在语义化版本意义上的公开模块 API。在 release note 里用一行写明迁移映射表即可。

### 4.2 CLI 入口：保留 5 个薄壳

`python -B scripts/rds_cli.py ...` 这种调用出现在至少 15 处仓内字符串（用户提示、`next_command` 字段、`[RDS-HINT]` 输出）和无法估量的用户脚本/文档里，直接断会制造坏体验且毫无收益。薄壳保留这些路径可用：

- `scripts/rds_cli.py` → `rds.cli.cli:main`
- `scripts/rds_compass_cli.py` → `rds.compass.cli:main`
- `scripts/rds_project_worker.py` → `rds.cli.project_worker:main`（被 `rds_project.py:2021` 的 `Path(__file__).with_name(...)` 启动，见 §5-R2）
- `scripts/rds_autonomy_worker.py` → `rds.cli.autonomy_worker:main`（被 `rds_autonomy_admission.py:33` 做 hash 绑定，见 §5-R2）
- `scripts/rds_quick.py` → `rds.cli.quick`（QUICK 入口模块，保留 import 兼容）

薄壳是**入口**不是 shim：只做 main 转发，不做模块符号 re-export。

### 4.3 持久化字符串名：逻辑名冻结

- `rds_verify` 的 ProofRule `module` 字段（`'rds_scalar_verify'` 等 12 个）进入 proof plan 与验证器清单输出（`rds_verify.py:188`、`:225`）。旧账本/旧 plan 重放时 `builtin_module(name)` 必须仍能解析。对策：`_load_builtin_module` 的 if/elif **白名单 key 保持旧字符串不变**，value 改指新包模块（`from rds.kernel import scalar_verify as adapter`）。未知的 name 依旧 `ValueError`——fail-closed 语义不变。
- `rds_project_hooks._modules = {"advisor": "rds_owned_advisor", "quick": "rds_quick_ledger"}`：hook 名 `advisor`/`quick` 冻结，模块路径值改新包路径。
- 账本事件、CAS 内容、TMS revisions、Compass hash chain：全部不触及。没有任何一步改 schema、事件名或数据文件。

### 4.4 tests

373 个测试文件中约 250 个有 `sys.path.insert(0, str(ROOT / 'scripts'))`。codemod 规则：

1. path 行统一改成 `sys.path.insert(0, str(ROOT))`（`rds` 包从 repo root 可导入）；同文件里 `import rds_foo` 改成 §1.3 的别名式。
2. 内嵌 subprocess code 字符串（如 `test_pr274_jump_plan_inputs.py:69` 的 `"import sys;sys.path.insert(0,sys.argv[1]);import rds_jump"`）——argv 传的路径语义从 scripts 目录改为 repo root，字符串同步改。
3. `import scripts.rds_x` 前缀模式（import_graph 支持解析说明存在）一并改。

不引入 `conftest.py` 魔法：项目跑 `unittest discover`，conftest 不生效；每个测试文件显式插 path 的现状符合 AGENTS.md 的显式风格，只改插的内容。

## 5. 风险

| # | 风险 | 证据位置 | 影响 | 对策 |
|---|---|---|---|---|
| R1 | `engine_id` 变化 | `rds_cli.py:108-113` 对 `rds_cli.py`/`rds_quick.py` 文件字节做 digest 拼入 engine identity | 拆包后代码行变了 → engine_id 变 → 旧账本可读但不复用旧执行身份 | **预期行为**，B1a 已有先例（「old data stays readable, but changed code does not reuse an old execution identity」）。release note 声明；`test_rds_core.py:238` 的 engine_id 测试同步更新。绝不为保 engine_id 不变而保留旧文件字节 |
| R2 | `Path(__file__).with_name(...)` 同目录假设 | `rds_project.py:2021`（worker 路径）、`rds_autonomy_admission.py:33`（worker 文件 hash 绑定！）、`rds_conversation_hook.py:15`（`CLI_PATH`）、`rds_wheel.py:30`、`rds_cli.py:108-113` | 移进子包后 `with_name` 解析到错误目录；`:33` 的 hash 绑定失败会 fail-closed 拒绝启动 worker | 5 处全改为显式路径解析：worker/CLI 路径经 §4.2 薄壳位置或 `importlib.resources` 定位；hash 绑定改对包内文件字节做。Step 6 必查项 |
| R3 | subprocess 字符串入口 | `python -B scripts/rds_cli.py` 出现于 `rds_advisor_search.py:767`、`rds_feasibility.py:260-261`、`rds_project.py:1894,1897`、`rds_project_lifecycle.py:28`、`rds_result_record.py:222,225`、`rds_tool_workbench.py:301`、`rds_workspace.py:817,829`、`rds_compass_store.py:36`、`rds_cli.py:1321,1679,1708,1967` | 这些是打印给用户/喂给 agent 的命令字符串 | §4.2 薄壳保证字符串继续有效，零改动 |
| R4 | 注册表路径字符串 | `rds_host_hook.py:28` `"worker": "scripts/rds_project_worker.py::main"` | 外部 host 按此路径启动 worker | 薄壳保留路径有效；字符串在 Step 6 顺手更新为新路径但薄壳兜底 |
| R5 | 打包工具读源文件 | `build_plugins.py:75,84` 校验 `scripts/rds_cli.py` 存在且正则抽 `VERSION` | 插件打包流程 | 薄壳文件保留 `VERSION = "..."` 行（re-export 自 `rds.cli.cli`），或 build_plugins 改读包内。Step 6 处理 |
| R6 | 动态 import 字符串 | `rds_project_hooks.py:20`（`_modules` 值）、`rds_builtin_verifiers.py:12-35`（12 个 if/elif）、`rds_capabilities.py:38`（真动态，import_graph 唯一 unresolved 点） | 字符串不跟随 `git mv` 自动改 | §4.3；`rds_capabilities` 的动态加载目标本就在图外，拆包后在白名单处显式列新包路径 |
| R7 | import_graph 自身 | `tools/import_graph.py` 的 `GROUPS` 表与扫描 glob 绑定 `scripts/` 树 | 不更新则新模块全部 UNKNOWN → `unclassified_edges` 爆炸，验收门自己先红 | Step 1 首批改动含工具更新；`test_import_graph.py` 同步 |
| R8 | 3 个非前缀工具 | `build_plugins`、`paid_query`、`update_lean_toolchain`（全 L4） | 名字不带 `rds_`，进 `rds/cli/` 后风格突兀 | 随 cli 包迁移；或论证后移 `tools/`（它们本来就是仓库维护脚本）。Step 6 定夺，二选一，不悬空 |
| R9 | git rename 检测 | 51,442 行、148+ 文件移动 | 大文件 rename 检测一般可靠，但 `rds_cli.py` 薄壳替换（同名新文件 + 旧文件移走）在同 commit 会干扰 similarity 匹配 | 薄壳与 `git mv` 分两个 commit：先 `git mv` 全部文件（纯移动 commit），再下一 commit 落薄壳。验收：`git log --follow -- rds/cli/cli.py` 能追过 rename |
| R10 | pickle | 全仓 grep 无 `pickle` | 无 | 无 |
| R11 | 中间态混住 | Step 1–6 之间 `rds/` 包与 `scripts/` 平铺共存 | 同一模块绝不允许两份拷贝同时可 import（双账本风险在 import 层的投影：同一模块两个身份） | codemod 保证每个模块只存在于一处；`sys.path` 里 `scripts/` 与 repo root 并存期間，被迁模块在 `scripts/` 立即消失（`git mv` 不是复制）。Step 内原子，Step 间不留同名文件 |
| R12 | 模块级 import 顺序 | 73 条模块级边虽无环，但 `from rds.kernel import math as rds_math` 这类包级导入会触发 `rds/kernel/__init__.py` 执行 | `__init__.py` 若做聚合 import 会人为造环 | §3 Step 1 已规定 `__init__.py` 为空。验收：冒烟脚本逐一 import 每个迁移模块 |

## 6. 工作量估算

| 步骤 | 文件数 | import sites | 净行数估算 | 风险 |
|---|---:|---:|---:|---|
| Step 0a–0d | 改 ~10 + 新 1–2 | — | ~250 + 测试 | 中（0d 最高，需等价测试） |
| Step 1 shared | 20 mv + 改 ~40 | 77 下游 + 9 内部 | ~150（含工具/GROUPS 更新） | 低 |
| Step 2 kernel | 71 mv + 改 ~150 | 424 | ~600 | 中（量大但机械；engine_id 预期变化在此步落地） |
| Step 3 graph | 14 mv + 改 ~20 | 22 | ~80 | 低 |
| Step 4 compass | 8 mv + 改 ~15 | 42 | ~120 | 低（家族内聚，出边全向下） |
| Step 5 adapters | 32 mv + 改 ~60 | 202 | ~300 | 低 |
| Step 6 cli + 薄壳 | 28 mv + 新 5 壳 + 改 ~60 | 198 | ~350 | 中（R2/R3/R5 集中在此步验收） |
| tests codemod | ~250 文件 | — | ~750（≈每文件 3 行：path 行 + 1–2 import 行） | 低（机械，含内嵌 subprocess 字符串） |
| codemod 工具 | 新 1 | — | ~200 | 一次性 |
| Step 7 消环 | 视范围 | — | 每环 50–200 | 长期，不阻塞 |

合计：~148 个 `git mv`、897 个 import site 重写、~250 个测试文件、总 diff 约 2,500–3,500 行，其中 >90% 是单行机械替换。人工审查集中在 Step 0（断边语义）和 Step 6（入口/子进程边界）。

### 完成定义（Definition of Done）

1. `python -B -m unittest discover -s tests -p 'test_import_graph.py'` 全绿（每步）。
2. 最终步后 `import-graph.json`：模块数 173 不变、边数 ±0（除 Step 0/7 有意消掉的）、`unclassified_edges` 为 0、模块级跨包边方向 100% 合法、全口径最大 SCC ≤ 拆包前（预期持平，Step 7 后下降）。
3. `python -B scripts/rds_cli.py --help` 与拆包前逐字节一致。
4. 旧 `.rds/` 项目目录打开、recovery、Compass state fold 各跑一个最小冒烟（复用既有 `test_rds_native_recovery.py` / `test_rds_compass_drive.py` 口径）。
5. `git log --follow` 对 `rds/kernel/project.py`、`rds/cli/cli.py`、`rds/compass/compass.py` 抽查可追到 `scripts/` 时代历史。
