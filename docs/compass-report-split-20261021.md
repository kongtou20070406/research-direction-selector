# Compass report / TMS 拆分：从 `dd4506e` 重做到 `1ffbe80`

## 为什么要重做而不是直接合

`dd4506e` 在侧分支 `ralph-compass-report-split` 上，父提交是 `c92be71`。主线从 `c92be71` 走到 `1ffbe80` **共 35 个 commit**，`scripts/rds_compass.py` 被改了多轮（区间账本、Stage 2 接缝、undecided 报告、多项式回执检查、source seam）。直接 merge 会把 35 个 commit 的改动冲掉。

重做前先验了一件事：**被搬的 8 个顶层节点在新旧两版里逐字节相同**。

```text
_improve_verdicts  old=2483 new=2483 identical=True
_rechecked         old=1152 new=1152 identical=True
_replicates        old= 385 new= 385 identical=True
_run_verdict       old=1684 new=1684 identical=True
_tolerances        old= 128 new= 128 identical=True
publish_tms        old=5203 new=5203 identical=True
stale_bindings     old=1016 new=1016 identical=True
```

（`c92be71` 与 `1ffbe80` 各取一次函数体文本比较；`UNSETTLED` 另核：`c92be71` 在 `scripts/rds_compass.py`，`1ffbe80` 在 `:1462`，内容 `('STALE_EVIDENCE','UNMEASURED_OR_SAMPLE_MISMATCH','JUDGMENT_REQUIRED')` 一致。）

搬迁边界的依赖用 AST 扫过：这 8 个节点引用的 core 顶层符号只有 `canonical`、`cstore`、`model` 三个模块级 import，正好是新模块头部已有的三行。**没有隐藏的 core 依赖**，所以纯搬运不会改变语义。

## 拆分范围

新增 `scripts/rds_compass_report.py`（203 行）：

- `_tolerances(plan)`、`_replicates(plan)`、`_run_verdict(state, ident, base, stale)`、`_improve_verdicts(state, stale=())`、`stale_bindings(store, state)`、`publish_tms(store, state, stale=None)`、`_rechecked(state, now)`、常量 `UNSETTLED`。

`scripts/rds_compass.py` 显式重导出这 8 个名称（插在 `from rds_project import ...` 之后）：

```python
from rds_compass_report import (UNSETTLED, _improve_verdicts, _replicates, _rechecked, _run_verdict,
                                _tolerances, publish_tms, stale_bindings)
```

旧导入路径继续有效，`tools/import_graph.py` 把新模块标成 `rds_compass_report="L3"`（紧跟 `rds_compass="L3"`）。

## 故意不搬的部分

`report`、`workset`、`handoff` 留在 core。它们依赖 `fold`、`_compact_hyp`、`agent_tokens`、`fit_trend`、`trend_stale` 等 core 实现；搬过去就要反向 import core，而 core 又要 import 新模块做兼容导出 → **循环依赖**。

这一判断与 `dd4506e` 一致，重做时未改。

## 验证（全部在 `1ffbe80` + 本次改动的工作区上跑）

| 检查 | 命令 | 结果 |
|---|---|---|
| 8 个导出同一性 | `getattr(core, n) is getattr(report, n)` | 通过 |
| 分层与无环 | `import_graph.inventory('.')` | `layer == 'L3'`；`cycles` 里无 `rds_compass_report` |
| 分层总表 | `test_import_graph.py` | **97 tests OK / 16.611s** |
| 模型层 | `test_rds_compass_model.py` | 27 tests OK / 0.154s |
| 路由 | `test_rds_compass_routing.py` | 5 tests OK / 2.738s |
| Stage 2 | `test_stage2_hypotheses.py` | 4 tests OK / 6.966s |
| source seam | `test_rds_compass_source_seam.py` | 10 tests OK / 0.033s |
| 区间往返 | `test_rds_compass_interval*.py` | 34 tests OK / 4.237s |
| 对抗 | `test_rds_compass_adversarial.py` | 46 tests OK / 125.061s |
| 驱动全量 | `test_rds_compass_drive.py` | **31 tests OK / 304.074s** |
| 硬跟进 | `test_rds_compass_hard_followup.py` | **12 tests OK / 396.343s** |

`dd4506e` 自带的文档记的是它在 `c92be71` 上跑的 `31 tests / 279.476s` 与 `test_rds_compass_hard_followup.py` `12 tests / 367.113s`。本次重做把两者都在新树重跑了一遍，**没有留下 UNKNOWN**。

## Diff stat

```text
scripts/rds_compass.py        | 9 +, 199 -   (1569 行)
scripts/rds_compass_report.py | 203 +         (新文件)
tools/import_graph.py         | 1 +
docs/compass-report-split-20261021.md | 本文
```

## 边界与风险

- 未改 receipts、TMS 语义、科学判定、预算或报告 schema。
- 未新增测试文件——这是纯搬运，既有测试就是回归网。
- 净增行数主要是新文件 203 行；core 净减 190 行。
- 这一步只拆了 report / TMS 一块。`rds_compass.py` 仍是 1569 行，`rds_project.py` 2081 行、`rds_cli.py` 2049 行没动。下一步如果继续拆，按 CRG 的 39 个 community 边界走，别按直觉切。
