# 快速开始：先用自然语言

你可以直接用日常语言请 Agent 使用 RDS，不必先记内部术语，也不必为了讨论研究问题先准备 JSON。说清研究目标、相关文件，以及你现在需要分析、拟定计划，还是在授权范围内执行。

## 选择一个提示词

### 理解已有结果

> 用 RDS 检查 `[日志或报告路径]` 中的结果。把实际测到的内容和可能的解释分开，并提出一个最小的后续检查，用来区分主要解释。不要运行实验或修改文件。

### 比较两个实验想法

> 用 RDS 比较 `[想法 A]` 和 `[想法 B]` 对 `[研究目标]` 的帮助。使用项目里已经确定的指标和资源限制，说明不同结果分别能回答什么，并推荐一个方案。如果缺少的信息会改变决定，请向我确认；不要自行补造，也不要启动运行。

### 接续已有项目

> 用 RDS 接续这个项目。先检查已记录的目标、完成的运行、当前证据和剩余预算，再总结已知内容并提出一个下一步。不要重新初始化项目或重复运行。

### 检查数学命题

> 用 RDS 检查这个命题：`[命题]`。保留它的定义域、假设和量词。告诉我现有检查器能够验证哪些部分、哪些仍未解决；不要把数值例子当成证明。

把方括号里的内容换成你的实际问题或路径。如果项目尚未说明指标、评估方法或资源上限，RDS 应明确指出缺失，并只询问可能改变计划的信息。这些提示词用于分析，不会授权未提及的实验或资源支出。

## 不手写契约，试用 CLI

讨论第一份计划时，Agent 可以在初始化项目之前使用 `project plan`。它根据已声明的目标、评估方式和资源整理下一步及缺失信息，不会补造执行预算。`--intent` 文件可提供你的声明；已有项目则提供原契约和回执位置。草案不会启动实验。示例以及暂停、调整已有项目的方法见[规划与人工指令](planning-and-steering.md)。

需要 Python 3.11 或更新版本。在仓库目录中运行，并选一个**全新空目录**。准备脚本会创建小型合成数据，以及与之匹配的代码、评估器、协议和文件哈希；目标目录非空时脚本会拒绝继续。

```powershell
python -B examples/project-runner/prepare.py --root ./my-project
python -B scripts/rds_cli.py --root ./my-project project init --contract ./my-project/contract.json
python -B scripts/rds_cli.py --root ./my-project project status --brief
```

这几条命令只准备并检查本地项目，不会运行两组实验。若要执行完整 CPU 演示，请看[项目执行器示例](../examples/project-runner/README.md)和[仓库的快速上手命令](../README.zh-CN.md#确定性的执行与验收内核)。演示使用合成数据，不能证明科学结论或泛化能力。

### 最小契约模板

如果无法运行 `prepare.py`，`project init` 也接受手写契约，但必须保持以下确切结构：每个 `sha256` 都是被绑定文件的真实十六进制 SHA-256，路径相对项目根目录，`min_useful_delta` 是精确有理数字符串；可选字段（`objective_sha256`、`execution_policy`、`stop_policy`、`maintenance_allowance`）见 [stop policy](stop-policy.md) 与[原生研究](native-research.md)。哈希是猜出来的契约会被拒绝。

```json
{
  "schema": 1,
  "description": "CPU demonstration, not scientific confirmation",
  "bindings": [
    {"role": "code",      "path": "experiment.py", "sha256": "<sha256>"},
    {"role": "config",    "path": "config.json",   "sha256": "<sha256>"},
    {"role": "data",      "path": "data.csv",      "sha256": "<sha256>"},
    {"role": "evaluator", "path": "evaluate.py",   "sha256": "<sha256>"},
    {"role": "protocol",  "path": "protocol.json", "sha256": "<sha256>"}
  ],
  "allowed_commands": [
    ["python", "-B", "experiment.py", "--arm", "control",   "--output", "outputs/control.json"],
    ["python", "-B", "experiment.py", "--arm", "treatment", "--output", "outputs/treatment.json"]
  ],
  "output_roots": ["outputs"],
  "budget": {"wall_seconds": 20},
  "primary_metric": {"name": "mse", "direction": "min", "min_useful_delta": "1/100"}
}
```

## 遇到 `[RDS-REJECT]` 时

它表示这次命令没有满足某个前置条件；单凭它不能说明研究想法好坏。不要猜字段后反复提交，也不要重新初始化。把准确命令、项目目录和完整输出交给 Agent，再要求：

> 请用通俗语言解释这条 RDS 拒绝信息，指出缺失或无效的输入以及最小安全修正。先检查当前项目状态。不要改文件、重新初始化、运行实验或花费资源。

有些命令会初始化不同的 RDS 账本。应按当前工作流和实际错误选择命令，不要猜着把 `init` 换成 `project init`，或反过来。如果修正会改变研究目标、方法、预算、数据访问或启动运行，先明确决定是否接受这项变化。

## 按需查看详情

- [Agent 入口：精简命令与输出](agent-entry.md)
- [项目与开发循环](development-loop.md)
- [科研工作流](research-workflow.zh-CN.md)
- [形式化验证](formal-verification.zh-CN.md)
