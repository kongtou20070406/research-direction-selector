# B2 静态资产外置交付记录

分支：`refactor/assets-20261011`。基线：`9154f2fb2afc7147d8bdbb7de16458f84d826292`。仅本地提交；未 push、merge 或创建 PR。

## 改动与边界

- `scripts/rds_dashboard.py`：HTML 常量改为从 `__file__` 所在目录的 assets 读取；HTML 内原有 CSS/JS 一并保持原字符串。
- `scripts/rds_hypergraph_view.py`：保留公开常量和所有门面，外置 D3、HTML、CSS、JS、Pixi 许可及 Replica 模板/补丁字符串。27 个 renderer 补丁和 11 个 worker 补丁以有序 JSON 字符串对存储，原调用次序、匹配失败、替换次数和函数接口不变。
- `scripts/assets/.gitattributes`：禁用 Git 对该目录的文本换行转换，确保索引/检出字节一致。
- `scripts/build_plugins.py` 与 `examples/self-development/run.py`：明确递归 scripts 选择包含索引内 assets，以及 HTML/CSS/JS/JSON 作为 data 绑定。原有允许顶层目录已经包含 scripts，无须重复扩大选择范围。实测确认行为。
- `tests/hypergraph_archive_checks.cjs`：5 处 D3 fixture 读取从解析 Python 字面量改为读取独立 JS 文件；保留全部断言。新增 presentation 资源/隔离加载/QUICK 绑定测试，扩充插件搬移与快照资产用例。
- 未修改 `rds_cli.py`、`rds_project.py`、`rds_quick.py`、archive 实现、release/version 或主 checkout。

## 源码体积

| 模块 | 抽取前字节 | 抽取后字节 |
| --- | ---: | ---: |
| rds_dashboard.py | 54085 | 10120 |
| rds_hypergraph_view.py | 154151 | 50819 |

## 导出字节比较

先实际运行 before，保存原始导出文件与 SHA256，再修改源码并运行 after。12 项均做完整 `before_bytes == after_bytes` 比较，未修改或归一化任何导出字节。Dashboard 动态时间来自测试输入时钟，固定为 `2026-10-11T00:00:00+00:00`；路径输入在两次导出中相同。

| 导出 | 前字节 | 后字节 | 相等 | SHA256（前后相同） |
| --- | ---: | ---: | --- | --- |
| dashboard-demo | 45744 | 45744 | PASS | `bd6d59cab952f0e63761253fefd1a5827a11c8cc3b04662bc97f3fc890bb066c` |
| dashboard-hostile | 45791 | 45791 | PASS | `18ddf0c272a193914abdc311e0e40099e0142e5debe94d279b384b9d1c14e4ca` |
| dashboard-cli | 45744 | 45744 | PASS | `bd6d59cab952f0e63761253fefd1a5827a11c8cc3b04662bc97f3fc890bb066c` |
| hypergraph-small | 76311 | 76311 | PASS | `3b8216db4264fa3fd480a8c54663759960b1498855c8d9eb5f8424d5a14c6345` |
| replica-small | 579939 | 579939 | PASS | `9b498ddb925362af581471a01ad8ce1c118d66cc123f5b8193976059317fcaf0` |
| hypergraph-large | 1322099 | 1322099 | PASS | `229f7a1bc044beca06be1060315cb7eeb06496d8dd3d622d184b5f3f1bbd7e71` |
| replica-large | 3324835 | 3324835 | PASS | `807c447db8f71f44a93a415d53d283f589f05772ffb08c9d225a50a596954a85` |
| hypergraph-hostile | 76602 | 76602 | PASS | `543a7d2757e40a41171ff382b9e0d55164bdb482dc476ae698641d1f3675915a` |
| replica-hostile | 580354 | 580354 | PASS | `ad5d7a3128ea3ba519cbceefa5d64bb314c17722791273314fa08f7da06f54d7` |
| hypergraph-cli-demo | 76394 | 76394 | PASS | `d02b23434c45ceed04bb97ac9eb15f36a2af33f2af9c09d835338066b7f496a4` |
| hypergraph-cli-large | 1322110 | 1322110 | PASS | `5be07b4f209c1c192f3d569d1d64d12a547c8225f690851bffe4851aa77ccdab` |
| archive | 620806 | 620806 | PASS | `8c9e9921a08994ba1a501bfd765f445dcbc251460c57e43d262ddf9c7396d8b2` |

精确输出差异：无。JSON 补丁文件是字符串对的容器；解码后的匹配/替换字符串与原 AST 字面量 UTF-8 字节一致。9 个公开资源常量及 38 对补丁逐项与 Git 基线核对；原有 27 个函数在把资源引用替回原字面量后 AST 完全一致，见 `source-equivalence.json`。D3 版本/ISC 许可、Replica commit/file hash、Pixi 7.4.3/SHA256/MIT 许可均保留。Replica 上游源码未添加到交付；校验输入留在忽略的 `_local/upstream` 下。

## 实际验证

- 环境：Windows，Python 3.13.5，Node 可用。
- 首轮：114 tests，1 failure，0 errors；`viewer-tests.log` 保留原始失败。原因是 archive Node fixture 仍拆取原 Python 字面量；修复 fixture 读取路径后重跑。
- 扩展相关回归：159 tests，0 failures/errors，3 skips，`related-tests.log`。模块：test_rds_dashboard、test_rds_hypergraph_view、test_rds_hypergraph_archive、test_rds_hypergraph_readable、test_rds_presentation_assets、test_jump_ruby_archive_boundary、test_pr274_regular_inputs、test_pr274_repair_boundaries、test_rds_plugin_package、test_rds_self_development。
- 新增打包/快照用例及 legacy producer：13 tests，0 failures/errors/skips，`packaging-tests.log`。包括 test_rds_autonomy.AutonomyTests.test_legacy_paid_oversize_envelope_is_processed_unknown_once，确认原脚本拷贝 fixture 无需扩大资产范围。
- 自开发实际实例：选择 dashboard/hypergraph/archive/readable/presentation 的 5 个测试文件，114 cases，0 failures/skips；进程退出 0，run_status=SUCCEEDED。12 个 assets 路径（含 .gitattributes）逐项绑定 data 并比对复制字节；日志和原始结果见 self-development.log/json。临时实例执行了自身的独立规则演示/回滚；不表示科学收益或真实研究策略改进。
- `asset_binding_check.py before-add`：三种插件包及 freeze_public_source 都收录 0 个新资产。随后对资源执行 git add，再运行 after-add：全部 12 路径收录；索引 blob、SHA256、当前工作树/包/快照字节匹配。插件整体搬移后，从无关 cwd 用 `python -S -B` 分别运行两个 exporter，6 次均退出 0。
- `python -I -S -B` 从无关 cwd，显式引入模块所在 scripts 目录后导入两个模块并导出成功；资产定位依赖 __file__，无 cwd 依赖。
- QUICK 单测确认静态 Python 导入扫描不发现 assets，显式绑定后字节全部包含；MAX_FILES=128、MAX_TOTAL_BYTES=64 MiB 未变。实际 `rds_cli.py exec ... --bind data=<asset>` 冻结并运行 D3 exporter，11 个资源都绑定 data，run_status=SUCCEEDED，输出 76410 字节，见 quick-assets.json。
- QUICK 第一版辅助检查错误地读取不存在的 contract.json，出现 FileNotFoundError；该次 CLI 原始输出未持久化，不能认定其运行状态。修正辅助检查读取 ProjectStore 合约，在新的独立临时 fixture 验证成功；未改 QUICK 实现。
- git diff --check 与 git diff --cached --check 均通过，见 validation.json。

## 验证命令

```powershell
$env:PYTHONPATH='<worktree>\tests'
$env:RDS_REPLICA_ROOT='<worktree>\maintenance\refactor-20261011\_local\upstream'
$env:RDS_PIXI_JS="$env:RDS_REPLICA_ROOT\pixi.min.js"
python -B maintenance/refactor-20261011/asset_export_check.py before
python -B maintenance/refactor-20261011/asset_export_check.py after
python -B maintenance/refactor-20261011/asset_binding_check.py before-add
git add -- scripts/assets
python -B maintenance/refactor-20261011/asset_binding_check.py after-add
python -B -m unittest test_rds_dashboard test_rds_hypergraph_view test_rds_hypergraph_archive test_rds_hypergraph_readable test_rds_presentation_assets test_jump_ruby_archive_boundary test_pr274_regular_inputs test_pr274_repair_boundaries test_rds_plugin_package test_rds_self_development -v
python -B -m unittest test_rds_plugin_package test_rds_self_development test_rds_autonomy.AutonomyTests.test_legacy_paid_oversize_envelope_is_processed_unknown_once -v
python -B examples/self-development/run.py --workspace <new-external-workspace> --test-pattern test_rds_presentation_assets.py --test-pattern test_rds_dashboard.py --test-pattern test_rds_hypergraph_view.py --test-pattern test_rds_hypergraph_archive.py --test-pattern test_rds_hypergraph_readable.py
git diff --check
git diff --cached --check
```

before/before-add 为已执行的历史步骤；当前代码/索引状态不宜覆盖这些原始证据。完整原始导出位于忽略的 `_local/before` 和 `_local/after`；已提交的 JSON 保存每项完整 SHA256 与字节数。构建器要求外部输出目录，打包与自开发验证使用独立临时目录，测试后清理；当前主 checkout 未作为输出目标。

## UNKNOWN

- Ubuntu × Python 3.11/3.13 与 Windows Python 3.11：UNKNOWN，未运行跨平台 CI。
- 全仓回归：UNKNOWN，本批次运行相关回归与所选自开发 workload。
- POSIX FIFO 两项与实际 dangling symlink 一项：UNKNOWN，本机测试因平台/权限跳过；未绕过权限。
- 真人浏览器像素/交互视觉验收：UNKNOWN；已跑 Node VM 的实际产物/worker/geometry 边界和字节导出比较，但未执行真人浏览器视觉验收。
- 科研收益、task_gain、mechanism：UNKNOWN；自开发 assessment 原样保留 UNKNOWN。

## 新资产清单

- `scripts/assets/.gitattributes`
- `scripts/assets/dashboard/index.html`
- `scripts/assets/hypergraph/app.js`
- `scripts/assets/hypergraph/d3-bundle.js`
- `scripts/assets/hypergraph/index.html`
- `scripts/assets/hypergraph/pixi-LICENSE.txt`
- `scripts/assets/hypergraph/style.css`
- `scripts/assets/replica/app.js`
- `scripts/assets/replica/extra.css`
- `scripts/assets/replica/index.html`
- `scripts/assets/replica/renderer-patches.json`
- `scripts/assets/replica/worker-patches.json`

## 其他改动/证据文件

- `examples/self-development/run.py`
- `maintenance/refactor-20261011/.gitignore`
- `maintenance/refactor-20261011/B2-REPORT.md`
- `maintenance/refactor-20261011/asset_binding_check.py`
- `maintenance/refactor-20261011/asset_export_check.py`
- `maintenance/refactor-20261011/bindings-after-add.json`
- `maintenance/refactor-20261011/bindings-before-add.json`
- `maintenance/refactor-20261011/exports-after.json`
- `maintenance/refactor-20261011/exports-before.json`
- `maintenance/refactor-20261011/extracted-assets.json`
- `maintenance/refactor-20261011/packaging-tests.log`
- `maintenance/refactor-20261011/quick-assets.json`
- `maintenance/refactor-20261011/related-tests.log`
- `maintenance/refactor-20261011/self-development.json`
- `maintenance/refactor-20261011/self-development.log`
- `maintenance/refactor-20261011/source-equivalence.json`
- `maintenance/refactor-20261011/validation.json`
- `maintenance/refactor-20261011/viewer-tests.log`
- `scripts/build_plugins.py`
- `scripts/rds_dashboard.py`
- `scripts/rds_hypergraph_view.py`
- `tests/hypergraph_archive_checks.cjs`
- `tests/test_rds_plugin_package.py`
- `tests/test_rds_presentation_assets.py`
- `tests/test_rds_self_development.py`
