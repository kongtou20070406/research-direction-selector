# RDS 本地科研工作台 / Local research dashboard

这个界面将 RDS 的实际项目记录导出成一个可离线打开的 HTML 文件。主线是自动化辅助人类科研：目标 → 调查证据 → 选择实验 → 执行与验收 → 复盘与接续。它提供中文/英文切换、浅色/深色主题和当前页记录搜索。

## 快速开始

在仓库目录运行，`--root` 指向已经初始化 RDS 的项目目录：

```powershell
python -B scripts/rds_dashboard.py --root C:\research\project --output dist\dashboard.html
```

用浏览器打开生成的 HTML。无需服务器、网络连接、CDN 或前端依赖。项目记录改变后重新执行导出，页面本身不会轮询或运行实验。

没有状态库时，生成的页面显示空状态，不会初始化 `.rds`、填入假数字或展示模拟曲线。用下面的命令查看明确标记的介绍示例；示例只有未检验假设，没有运行结果或虚构预算：

```powershell
python -B scripts/rds_dashboard.py --demo --output dist\dashboard-demo.html
```

## 可展示的记录

| 页面 | 实际来源 |
| --- | --- |
| 项目总览 | `state.contract`、`state.budget`、当前分支与记录数量 |
| 假设与证据 | `state.hypotheses`，分别显示 `task_gain`、`mechanism`、`search_policy` |
| 实验计划 | `state.plans`、运行状态、用途、数据分区、预算与验收 |
| 执行收据 | `receipts` 表中的运行状态、实际收益字段、计费、复用与绑定 |
| 研究分支 | `state.branches` 中的理由、干预维度、父分支和停滞计数 |
| 诊断与建议 | `--advisor` 导入的结构化 JSON；作为建议保留原字段 |
| 复盘记录 | `events` 表最近 100 条事件及事件总数 |
| 快速开始 | 科研流程、CLI 导航与能力边界 |

`project init/create/execute` 项目读取 `.rds/project.sqlite3` 的现有 `ProjectStore.snapshot`：展示原 `runs`、资源预算向量和实际收据；参考 runner 项目读取 `.rds/state.sqlite3`。两种账本不会混合，页面与导出 JSON 标明 `PROJECT` / `REFERENCE`；同时存在时优先项目账本。没有假设或研究分支的项目保持这些页面为空。

项目预算分别展示已记录测量、预估记账、预留和剩余，保留原单位，并标明该资源尚未测量的运行数；已记录测量为 0 不表示所有运行的总用量为 0。项目收据直接展示各资源的测量值、未知状态和预估记账，墙钟时长使用收据中的秒数；参考收据保留毫秒时长与原计费分配。未知的 CPU/GPU 用量保持未知，墙钟不会换算成 CPU 时间。收据原件定位指向实际数据库的 `receipts` 表与 `run_id`；项目产物列表显示原路径，参考产物的哈希映射标为产物身份，不生成额外收据镜像。

运行成功、任务收益和机制支持是不同判断。收据里的 `gain` 仅按原记录展示，不会自动升级成确认结论。未记录字段显示 `—` 或“未记录”；缺少预算时没有资源进度。Lean / mathlib 是适用于数学子任务的协作能力。

## 研究超图

超图使用独立的全画布页面，只显示网络和按需打开的节点/超边详情。深灰背景、细线、按连接数调整大小的圆点与力导向布局适合浏览大图；没有项目总览或其他工作台面板。导出已保存的 TMS 图，或直接查看依赖 JSON：

```powershell
python -B scripts/rds_hypergraph_view.py --root C:\research\project --output dist\hypergraph.html
python -B scripts/rds_hypergraph_view.py --hypergraph dependency-map.json --output dist\hypergraph.html
```

输入复用 `hypergraph` 的声明适配器，接受原生依赖表、`claims` / `rules` 简写和程序生成的 `dependency_map`。修复及警告保留在原始详情中。只读取现有 TMS 快照及其绑定 CAS，不需要执行契约；不读取或混入工作台收据，不发布新快照。显式文件只决定此次展示的图，不能与 `--demo` 同用。

Canvas 以圆点表示主张，以小菱形表示超边汇合点：同一个汇合点的前提是 **AND**，通向同一结论的不同汇合点保留 **OR** 路线。支持整图平移、缩放、适应全图、小地图定位、搜索及上游依赖聚焦。从节点上开始拖动也平移整图；单击才打开详查。节点名称按缩放级别显示并避免重叠；悬停查看名称和状态，点击打开详情。搜索结果也可通过键盘选择；画布获得焦点后用方向键平移、`+` / `-` 缩放、`0` 适应全图、Esc 关闭详情。

右上角设置可切换统一细线、按声明状态或按关系类型区分线条。按状态时，实线表示 `SUPPORTED`、虚线表示 `PROPOSED`、点线表示 `CONTRADICTED`。按类型时，读取已有的 `relation` / `kind` / `type` 元数据，没有该字段则显示“依赖关系”；每种类型可选实线、虚线、点线、点划线，并单独调整浅色线条颜色、粗细，以及吸引 / 排斥 / 无作用、力度与作用距离。名称包含竞争、冲突、反驳等词的关系默认设为排斥；这只是可修改的显示偏好，不是推导出的科学关系。排斥只在设定距离内生效，吸引使用带平衡长度的弹簧。节点默认采用蓝白、暖白、金橙的星系配色，也可自选统一颜色或按声明状态着色；可调节点大小、轮廓粗细、标签阈值及全局力学参数。所有设置只影响当前页面，不改写输入。

声明的 `UNKNOWN` 不会因为进入依赖闭包而改写为 `SUPPORTED`。分析保持 `INPUT_REPORTED_DEPENDENCY_ANALYSIS_NOT_PROOF`；本页不审计外部证据文件或回执绑定。显示保存图时只验证该快照及其绑定 CAS 的完整性，不扫描项目证据目录。几何布局、聚团和圆点大小不表示科学重要性或证明顺序。

显示上限为 4,096 个主张节点、8,192 条超边及 32,768 条关联连接，输入最多 8 MiB，并仍需满足输入中声明的 schema 限额。超限显示提示，不绘制部分图。布局在离线 Web Worker 中用固定版本 D3 执行。四叉树近似全局排斥，碰撞力保持间距，吸引和定向关系排斥共同作用于主张节点；超边汇合点随关联节点取几何中心，不增加物理粒子。初始预热最多 180 步，每步检查 4.5 秒停止条件（单步可越过阈值），随后衰减至稳定并停止 Worker；初始模拟最多 291 步，重新调参约 248 步。没有持续旋转或随机摇晃，坐标更新经逐帧插值，静止时停止重绘。可暂停 / 继续；平移和缩放不重新激发物理模拟。布局不改写声明，浏览器不可用 Worker 时保留初始位置。绘制按样式批处理和视口裁剪，节点命中采用空间网格，标签避免重叠；Canvas 不为每个节点创建 DOM 元素。

组合阻断分析与大图显示分开：超过 200 节点、400 超边或 2,000 连接时，仍显示完整图，但明确标记 `NOT_RUN_LARGE_GRAPH`，不声称已求得闭包或最小阻断集合。较小图的分析最多 50,000 次组合、128 个阻断集（尊重输入更小的限额），截断保留“不完整”。保存的快照损坏时显示不可用；显式输入错误时 CLI 失败。

以下预览都是合成数据，没有真实实验结论。大图包含 1,200 个主张、1,426 条超边和 3,104 条连接；生成器固定，不使用外部数据。实际浏览器性能取决于硬件、图结构、缩放与显示面积；上限不是每种图结构的帧率保证。

```powershell
python -B scripts/rds_hypergraph_view.py --demo --output dist\hypergraph-small.html
python -B scripts/rds_hypergraph_view.py --demo --large --output dist\hypergraph-preview.html
```

页面只有内联代码和数据，Worker 通过本地 Blob 创建，无 CDN 或网络连接；可直接打开 HTML。`scripts/rds_hypergraph_view.py` 内嵌官方 npm UMD 发行文件：`d3-force@3.0.0`、`d3-quadtree@3.0.1`、`d3-dispatch@3.0.1`、`d3-timer@3.0.1`，保留各自完整 ISC 许可、版本及包 SHA-1。导出时无需 Node.js 或 npm；浏览器运行时不下载依赖。输出必须在项目 `.rds` 之外，且不能覆盖输入图。分享 HTML 会一并分享图中的节点、来源及路径。

## 载入诊断

可以传入 advisor 返回的 JSON 对象或对象数组：

```powershell
python -B scripts/rds_dashboard.py --root C:\research\project --advisor advice.json --output dist\dashboard.html
```

例如：

```json
{
  "diagnosis": "竞争解释尚未被区分",
  "minimal_experiment": {"intervention": "固定预算，只改变目标机制", "stop_condition": "干预没有改变被测属性"},
  "evidence_refs": [{"source": "run-log.json", "observation": "当前两组属性相同"}],
  "next_if_positive": "保留机制分支",
  "next_if_negative": "修订机制解释"
}
```

正常 `advise` 输出中的 `recommendations` 会分别展示理由。有界搜索与有限实验候选按原字段展示状态、行动或干预、竞争解释、待补证据、所需观测、结果对应的下一决策、成本与单位、来源和停止条件；未知成本保持 `UNKNOWN`。完整原 JSON 保留在折叠详情中，其他诊断类型仍保留原字段。

原生搜索、受阻及有限实验的候选必须是对象，候选中的 `steps` 和 `derivation` 必须是对象数组。导出器会拒绝损坏的候选结构，并在 CLI 错误中指出具体字段；原 Advisor 文件保留，其他诊断格式继续按原字段展示。

这些内容不会写回账本，不会生成计划或执行收据。界面保留来源和不确定性；没有已有 seed 不稳定性的证据时，不主动要求多 seed 实验。

## 只读和验证

导出器用 Python 标准库。项目状态复用 `ProjectStore.snapshot(check_bindings=False)` 的只读接口；参考状态与近期事件通过 SQLite `mode=ro`、`query_only` 和短读事务读取。它不调用会写入或迁移状态的 `cmd_status`，不为显示重新 hash 输入文件，不读取执行 artifact 的二进制内容。输出必须是 `.rds` 目录以外的 `.html` 文件。HTML 和 CLI JSON 输出均为 UTF8，包括 Windows 中文路径。

所有数据以转义 JSON 嵌入，界面使用 `textContent` 渲染用户字段；没有将用户文本作为 HTML 的路径。页面 CSP 禁用网络连接。浏览器本地存储只保存语言和主题偏好。HTML 包含项目中的契约、收据和路径，分享该文件就会分享这些内容。

```powershell
python -B -m unittest discover -s tests -p test_rds_dashboard.py
python -B -m unittest discover -s tests -p test_rds_hypergraph_view.py
```

测试覆盖真实 CPU 项目的成功与失败收据、未知资源与预估记账、两类账本选择、导出不 hash 输入或改变账本、UTF8 输出、无数据库时不创建状态、独立证据轴、脚本注入转义、示例不伪造结果，以及错误数据库与危险输出路径的拒绝。

超图回归另外覆盖真实 TMS/CAS 的只读导出、参考账本保护、显式中文路径与输入修复、原声明和派生闭包分离、损坏与缺失图、输入和画布上限、分析不完整、恶意图字段转义、输入文件保护，以及千节点图完整保留和大图分析跳过状态。安装 Node.js 时还会执行实际导出的 D3 Worker，验证吸引 / 排斥 / 无作用的方向、零力度不施力，以及衰减结束后停止；缺少 Node.js 时明确跳过此项。
