# RDS 上手：从 clone 到你的第一个 PR

[English](onboarding.md) · **简体中文**

这一页是第一次上手的路径。它假设你没读过源码，全程大约十五分钟。[CONTRIBUTING.md](../CONTRIBUTING.zh-CN.md) 是「长期」参考，说明改动如何被审查；本页只负责你的第一次改动。

RDS 没有发布到任何包索引。下面所有内容都在 Git checkout 里运行。

## 1. 三十秒判断

RDS（Research Direction Selector）是一个本地程序，用来保留**研究决定的历史**：目标、花掉的预算、真正执行过的运行、它们产生的证据，以及更早被否掉的路线的原因。

它适合这样的研究者：与 AI agent 一起工作几天或几周，已经记不清哪些运行发生过、花了多少、以及三次会话前为什么放弃了一个看起来不错的想法。

它不设计你的实验、不替你决定科学问题、不让 agent 变聪明，也不是给不可信代码用的沙箱。**如果你的问题不需要保留决定历史，你不需要 RDS。**

在这里退出是成功，不是失败。关掉这一页，继续用你现在的工具。

## 2. 环境自检

### 2.1 必需：Python 3.11 及以上，以及 Git

```powershell
python -V
git --version
```

仅 Windows：如果第一行失败，Windows 的 Python 启动器通常仍能找到已安装的解释器：

```powershell
py -3.13 -V
```

期望输出，来自写这一页的机器：

```text
Python 3.13.5
git version 2.51.0.windows.1
```

任何 `Python 3.11.x` 及以上都可以。`3.10` 及以下不行：部分代码使用了旧解释器无法解析的语法。

如果提示 `python` 无法识别，安装 CPython 3.11 或更高版本并重开终端，或改用上面的 `py -3.13` 写法。如果提示 `git` 无法识别，安装 Git 并重开终端。RDS 除 Python 标准库外不需要任何包，所以虚拟环境是可选的。

### 2.2 可选：Rust 工具链与 Lean

```powershell
cargo --version
lean --version
```

在没有它们的机器上，PowerShell 会给出本地化的「无法识别」提示，bash 会给出 `command not found`。两者都不影响本页。

缺少它们你会少跑什么：

| 缺少 | 你无法在本地运行 | 不受影响的部分 |
| --- | --- | --- |
| `cargo` | Rust 加速器构建，以及 CI 的 `native` job，其中包含一项缺了它就会拒绝运行的编译一致性测试 | 任何 Python 文档与命令行改动 |
| `lean` / `lake` | CI 的 `lean` job、Lean 验证测试（`tests/test_rds_lean*.py`）与统计义务测试（`tests/test_rds_statistical_obligations.py`） | 任何 Python 文档与命令行改动 |

**缺 Lean 或 cargo 不会阻塞文档 PR。** 你不需要安装任何一个就能走完本页。

`pytest` 同样不需要，本仓库也不使用它。这个仓库用 `python -m unittest` 跑测试。

### 2.3 拿到代码

```powershell
git clone https://github.com/kongtou20070406/research-direction-selector.git
cd research-direction-selector
```

期望：Git 打印进度并正常结束。本页后续都假设你的终端已经在这个目录里。

如果 clone 超时或连接被重置，通常是代理拦截了 Git。只为当前终端设置代理后重试：

```powershell
$env:HTTPS_PROXY = "http://127.0.0.1:7890"
git clone https://github.com/kongtou20070406/research-direction-selector.git
```

在 bash 中同样的写法是 `export HTTPS_PROXY=http://127.0.0.1:7890`。把端口换成你自己代理的端口。

## 3. 零风险观察

在 checkout 里运行以下命令。它们是只读视图：都不会建立项目，也不会启动任何工作。

```powershell
python -B scripts/rds_cli.py --version
```

期望输出，就是这一整行：

```text
5.9.0-rc.2
```

这个字符串是你 clone 的 checkout 的版本。开发中的 checkout 会报告这样一个候选版本，它和已发布的发行版不是一回事。

```powershell
python -B scripts/rds_cli.py status
```

当前目录及其上级都没有初始化过项目时，期望输出：

```text
[RDS-REJECT] RDS is not initialized
```

它退出码是 `1`。这是期望的答案，不是安装坏了：`status` 报告的是没有可报告的账本，要看的是这句话本身而不是退出码。这里的退出码 `1` 含义是「没有找到项目」。

```powershell
python -B scripts/rds_cli.py project discover
```

期望输出如下，因篇幅截断。真实输出是标准输出上的一个 JSON 对象；末尾那行 `[RDS] ...` 写在标准错误上。

```text
{
  "status": "NO_PROJECT_FOUND",
  "requested_root": "<你的目录>",
  "project_root": null,
  "workflow": {
    "mode": "UNINITIALIZED",
    "advisor": "NOT_PROGRAM_OWNED",
    "ledger": null,
    "default_new_project_mode": "FULL",
    "optional_capabilities_are_not_automatically_enabled": true,
    "continuity": {
      "status": "UNBOUND"
    }
  },
  "next_action": "python -B scripts/rds_cli.py --root '<你的目录>' project plan",
  "execution_started": false,
  "search_scope": "REQUESTED_ROOT_AND_ANCESTORS",
  "sibling_projects_searched": false
}
[RDS] mode=UNINITIALIZED advisor=NOT_PROGRAM_OWNED continuity=UNBOUND root=<你的目录>
```

`project_root` 是 `null`，因为什么都没找到；`execution_started` 是 `false`。

### 3.1 只读，但不是无副作用

**在你用上面三条命令检查「RDS 是不是开着」之前，先读这一段。**

`--version`、`status`、`project discover` 都只读账本——也就是项目对决定、运行与剩余预算的持久记录——不会建立项目，但它会在 usage 库里留一条调用计数。因此**当前你无法用这些命令验证「RDS 是不是关着的」**——本版本还没有无副作用的自查命令。要人工确认，只能直接检查 `.rds/` 是否存在、以及 usage 库里有没有条目。

usage 库在 Windows 上位于 `%LOCALAPPDATA%\ResearchDirectionSelector\cli-usage.sqlite3`，在其他系统上位于 `~/.local/state/ResearchDirectionSelector/cli-usage.sqlite3`，除非被 `RDS_USAGE_DB` 环境变量重定向。`python -B scripts/rds_cli.py usage` 会打印按天记录的调用数，而这一次调用同样会被计数，所以跑完本节命令后计数不为零是正常的。

## 4. 第一条真命令

挑一个不是 checkout、并且你不介意 RDS 去看一眼的目录。不存在就创建它：

```powershell
New-Item -ItemType Directory -Force -Path "$HOME\rds-first-look" | Out-Null
python -B scripts/rds_cli.py --root "$HOME\rds-first-look" project discover
```

bash 中：

```bash
mkdir -p ~/rds-first-look
python -B scripts/rds_cli.py --root ~/rds-first-look project discover
```

`--root` 是你想查询其项目的那个目录。它现在可以不存在，也不必是 Git 仓库。`--root` 写在子命令之前或之后都可以，两种写法行为一致。路径里有空格或非 ASCII 字符时，在 PowerShell 里要加引号；路径里有空格时，在 bash 里也要加引号。

期望输出与上一节是同一个 JSON 对象，其中 `"requested_root"` 是你传入的目录，`"status"` 仍是 `"NO_PROJECT_FOUND"`，因为那个目录里没有项目：

```text
  "status": "NO_PROJECT_FOUND",
  "project_root": null,
    "project_root": "<你的目录>",
  "execution_started": false,
  "search_scope": "REQUESTED_ROOT_AND_ANCESTORS",
```

这段输出里有两件事正是这一步的重点。第一，RDS **默认不启用**：没有任何东西替你打开，也没有任何可选能力被自动启用。第二，发现过程**不自动建账**：命令跑完后你的目录仍然是空的，也没有任何地方冒出一个 `.rds/` 目录。自己确认一下：PowerShell 用 `Get-ChildItem -Force`，bash 用 `ls -a`。

这一步是观察，不是授权。它告诉你现在有什么，没有启动任何工作。建立项目是你还没有被要求做的、第二个需要显式决定的步骤。

如果 `project discover` 报告了一个你没料到的项目，说明它在 `--root` 的某个上级目录里找到了项目：发现过程会沿该目录向上查父目录，不搜索兄弟目录。换一个 `--root` 重跑，或读它报告的 `project_root` 看匹配到了什么。

## 5. 跑一组聚焦测试

本节所有命令都在仓库根目录运行。

### 5.1 一条命令跑一组

```powershell
python -B -m unittest discover -s tests -p "test_rds_quick*.py"
```

期望输出，在写这一页的机器上实测：

```text
.....................................................................
----------------------------------------------------------------------
Ran 68 tests in 121.932s

OK
```

`68 tests` 与 `OK` 表示这一组测试全部通过。点号上方出现的 `[RDS-RESOLVE] proj -> project` 这类行，是命令行界面在标准错误上打印的缩写提示，不是失败。

在这台机器上这 68 个测试要跑约两分钟，而不是一秒，因为这一组包含进程级工作。如果你想在改动过程中拿到真正快的信号，点名一个更小的文件：

```powershell
python -B -m unittest discover -s tests -p "test_rds_costs.py"
```

期望输出，本机实测：

```text
Ran 18 tests in 0.593s

OK
```

### 5.2 按你改的文件选测试

`tests/` 下有 213 个测试文件。全量套件大约要一个小时——历史实测一次全量运行是 3555.7 秒，约 59 分钟，而那个数字绑定的是比你 clone 的更旧的提交——所以**不要一上来就跑全量**。跑与你改动对应的那个文件。

规则是机械的：名为 `scripts/rds_<name>.py` 的脚本，通常由 `tests/test_rds_<name>.py` 覆盖，再加上所有 `tests/test_rds_<name>_<detail>.py`。104 个 `scripts/rds_*.py` 里有 85 个至少有这样的一个测试。以下例子都在本 checkout 上用上面的 `discover` 形式实测过：

| 你改了 | 跑什么 | 本机实测 |
| --- | --- | --- |
| `scripts/rds_costs.py` | `python -B -m unittest discover -s tests -p "test_rds_costs.py"` | 18 个测试，0.6 秒 |
| `scripts/rds_artifacts.py` | `python -B -m unittest discover -s tests -p "test_rds_artifacts.py"` | 22 个测试，1.2 秒 |
| `scripts/rds_cli.py` | `python -B -m unittest discover -s tests -p "test_rds_cli_*.py"` | 43 个测试，56 秒 |
| `scripts/rds_project.py` | `python -B -m unittest discover -s tests -p "test_rds_project*.py"` | 163 个测试，4 分 13 秒，跳过 3 个 |
| `docs/` 下的文件 | `python -B -m unittest tests.test_rds_plugin_package` | 7 个测试，6.5 秒 |

这些时间是这一页作者实测到的，不是目标值。注意「聚焦」并不总是很快：只有前两行能给你秒级的信号，所以迭代过程中挑更窄的那一个。

用 `discover -s tests -p "<pattern>"` 形式，或 `tests.test_rds_x` 形式。不要写 `python -m unittest tests/test_rds_costs.py`；这套测试不支持那种形式。

最后一行对文档改动同样重要：`docs/` 会被打进插件包，而 `tests/test_rds_plugin_package.py` 检查打包结果。它大约跑七秒。

如果你以为存在的测试文件不存在，`discover` 会打印 `Ran 0 tests` 并退出 `5`。那说明你的 `-p` 模式没有匹配到任何文件，不是测试通过了。

## 6. 改一个字，走完整流程

这一节把你从读者变成贡献者。示例是这个 checkout 里真实存在的缺陷。

### 6.1 找到并修一个真实的一行缺陷

`docs/advisor-frontier.md` 用错误的锚点文本链接到 `docs/theory-reformulation.md` 的某一节，所以链接会落在目标页顶部，而不是它承诺的那一节。先找到它：

```powershell
Select-String -Path docs\advisor-frontier.md -Pattern "theory-reformulation.md#"
```

期望：一行匹配，其中包含

```text
[预载工具](theory-reformulation.md#按需查预载工具)
```

bash 中等价的查找是 `grep -n "theory-reformulation.md#" docs/advisor-frontier.md`。接着读目标文件的标题，拿到正确的锚点：

```powershell
Select-String -Path docs\theory-reformulation.md -Pattern "^#{1,6} "
```

期望：其中一个标题以 `按需查预载工具与可运行算子脚手架` 开头。锚点就是标题文本把空格换成 `-`，所以那个标题的锚点是 `按需查预载工具与可运行算子脚手架`，而这个链接缺了后半截。

用任意编辑器改那一行，在括号内锚点的末尾补上 `与可运行算子脚手架`。你的改动是一行：一次插入、一次删除。

这就是每一个首次贡献的形状——一个坏链接、一个错别字、一句更通顺的话。你不需要理解内核（真正执行工作并记录结果的那部分程序），也未必非得是这一个缺陷：任何一行的文档修正都走同样的步骤。

### 6.2 检查、提交、推送

```powershell
git diff --check
```

期望：完全没有输出，退出码 `0`。`git diff --check` 报告行尾空白和遗留的冲突标记，所以没有输出就说明空白是干净的。

如果它列出了某一行，先修掉那一行的行尾空白或冲突标记再重跑。只要它还有输出就不要提交；空白噪音会让一行改动变得难以审查。

```powershell
git switch -c docs/fix-broken-anchor
git add docs/advisor-frontier.md
git commit -m "docs: point the preloaded-tool link at its section"
```

期望：`git add` 没有输出，`git commit` 打印一行摘要，说明一个文件被改动、一次插入、一次删除。如果 `git commit` 打开了你不想要的编辑器，按上面写法加 `-m`。如果 `git` 因为没有配置身份而拒绝提交，就配置一个：

```powershell
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

现在把分支推到你自己 fork 的仓库。先在浏览器里 fork 仓库，然后：

```powershell
git push -u origin docs/fix-broken-anchor
```

期望：Git 报告新分支。如果推送因为缺少写权限被拒，说明你在往项目仓库推而不是你的 fork；用 `git remote -v` 看 `origin` 指向哪个 URL，改成推你 fork 的 URL。

### 6.3 开 PR

对 `main` 开 PR。仓库自带模板 `.github/pull_request_template.md`，它要求填五个字段。每个都填。你认为不相关的字段也不要删掉，改成写 `not applicable` 并说明原因。

| 模板中的字段 | 它要求什么 |
| --- | --- |
| Problem and component | 具体改变了什么问题，以及它涉及五个组件中的哪一个：科研规程 Skill、执行与验收内核、研究状态与记忆、Advisor，或 RSI（负责提出并评估对项目自身规则的改动的组件） |
| Claim and release scope | 确切的主张、它的假设，并把 base 分支上已有行为、本 PR 引入的行为与开发候选区分开 |
| Public reproduction and results | 最小输入、命令、版本或提交，以及实际报告的 status 与 assurance，两者要分开写；`assurance` 表示该检查所用的依据类型，不是置信度分数 |
| Validation | 你实际跑过的检查及其结果，包括失败和跳过 |
| Translations and publication | 受影响的翻译是否仍然一致，以及改动中是否含私有日志、`.rds/` 状态或凭据 |

对一行文档修正来说，模板的主要内容就是：写明文件、贴上命令与输出、勾上末尾两个复选框。保持这么短就够。文档 PR 不需要基准测试，也绝不能声称科研结果。

### 6.4 看 CI 结果

`.github/workflows/test.yml` 定义了三个 job，每个 PR 都会跑。`native` 在 Ubuntu 和 Windows 上运行并构建 Rust 加速器；`test` 在 Ubuntu 和 Windows 上、各用 Python 3.11 和 3.13 运行，因此会报告四条检查结果，它就是跑 Python 测试套件的那个 job；`lean` 在 Ubuntu 上运行，需要 Lean 工具链。对于只碰 `docs/` 的改动，它们都应当通过、不出现新的失败，几分钟内跑完。

在 PR 页面底部读 job 列表。某个 job 旁边的红叉表示它失败了；点进去读最后一个失败的步骤。黄点表示它被跳过或还在运行。要认的就是上面那三个 job 名字；因为 `native` 和 `test` 是矩阵，页面上可能显示多于三条条目。

CI 通过意味着程序检查通过了。它不意味着你的改动在科学上正确，也不证明任何科研收益——对一次链接修正，诚实的说法就只是这个链接现在能解析了。

如果某个 job 在你根本没碰过的测试上失败，在 PR 里说明，而不是一直重跑到它变绿。一个只有第二次才通过的检查，本身就是关于不稳定性的证据，值得报出来。

## 7. 失败对照表

| 症状 | 原因 | 动作 |
| --- | --- | --- |
| 提示 `python` 无法识别 | `PATH` 上没有 CPython | 运行 `py -3.13 -V`。能用的话，把本页所有 `python` 写成 `py -3.13`。否则安装 CPython 3.11 或更高版本并重开终端。 |
| `SyntaxError`，或报错提到 `match`、`TypeError: 'type' object is not subscriptable` | 解释器低于 3.11 | 运行 `python -V`。如果显示 3.10 或更低，说明 `PATH` 上更靠前的是另一个 `python`；改用 `py -3.13`，或激活一个用新版解释器的环境。 |
| 命令本来能跑，`cd` 到含空格或中文的目录后就失败 | 终端把未加引号的路径拆开了 | 把整条路径加引号：PowerShell 用 `--root "C:\Users\XU\My Folder"`，bash 用 `--root "/home/you/My Folder"`。RDS 本身能处理空格和非 ASCII 字符；常见故障是终端里未加引号的展开。 |
| `ModuleNotFoundError: No module named 'test_rds_quick'`，或报某个同级测试模块导入失败 | 你直接点名了单个文件而没用 discover，于是 `tests/` 从未进入导入路径 | 在仓库根目录用 `python -B -m unittest discover -s tests -p "test_rds_quick*.py"`。对会导入同级夹具的文件，`python -B -m unittest tests.test_rds_quick*.py` 这种写法会失败。 |
| `Ran 0 tests`，退出码 `5` | `-p` 模式没有匹配到任何文件 | 用 PowerShell 的 `Get-ChildItem tests\test_rds_*.py` 或 bash 的 `ls tests/test_rds_*.py` 列出真实文件名，并给模式加引号，别让终端展开它。 |
| `[RDS-REJECT] RDS is not initialized`，退出码 `1` | 那个目录还没有初始化任何项目 | 什么都没坏；这是还没有项目时 `status` 的正常输出。传 `--root` 指向你打算使用的目录，或者不管它、继续用只读命令。 |
| 写 `<checkout>/.rds/` 时权限不足，或插件打包器复制文件时权限不足 | 有命令被指向了 checkout、插件缓存，或别的账号的目录 | 不要在 checkout 或插件安装目录里初始化项目；用自己的根目录，比如 `$HOME/rds-first-look`。打包器也拒绝读取 `.rds/`，所以永远不要提交它。 |
| `git clone` 超时、`Recv failure` 或 `Failed to connect` | 代理或防火墙拦住了 Git | 只为当前终端设代理：PowerShell 里 `$env:HTTPS_PROXY = "http://127.0.0.1:7890"`，bash 里 `export HTTPS_PROXY=http://127.0.0.1:7890`，然后重试。仍然失败就在浏览器里确认 GitHub 是否可达，并报告确切报错。 |
| `No module named pytest`，或 `pytest: command not found` | 没装 `pytest`——而且本仓库不用它 | 不要安装它。运行 `python -B -m unittest discover -s tests -p "test_rds_costs.py"`，它除标准库外什么都不需要。 |
| `error: src refspec docs/... does not match any` | 分支没建出来，或提交没发生 | 运行 `git switch -c docs/fix-broken-anchor`，再重跑 `git add` 和 `git commit`。用 `git log --oneline -1` 确认存在一次提交。 |

## 8. 最小术语集

下面八个词会出现在前面的命令与输出里。每个只给一句话；全量列表见 [terminology.zh-CN.md](terminology.zh-CN.md)。

- **项目（project）** —— 一个被 RDS 记录了研究档案的目录，由其中的 `.rds/` 目录标识；它是显式建立的，从不自动创建。
- **账本（ledger）** —— 项目目标、决定、运行与剩余预算的持久记录，也就是 `project discover` 要找、`status` 要报告的那个东西。
- **预算（budget）** —— 项目在开工前声明的资源额度，执行要对照它检查，以免开销悄悄超支。
- **收据（receipt）** —— 程序为一次执行生成的记录：绑定了什么、跑了什么、报告了什么状态、写了哪些文件。
- **准入（admission）** —— 对「某次提议的运行当前是否满足适用条件」作出的判定；它说的是就绪，不是研究价值。
- **UNKNOWN** —— 一种一等的答案，表示证据不足以定论；它保持为 `UNKNOWN`，而不是靠猜测消解。
- **Advisor** —— 把原始目标、当前证据、约束与候选路线连接到下一个可执行动作或阻塞点的组件。
- **Skill** —— agent 加载的协作规程，用来决定该提议什么；它不同于本地程序实际强制执行的行为。

想知道怎样的改动容易审查，读 [CONTRIBUTING.zh-CN.md](../CONTRIBUTING.zh-CN.md)。想知道你正在动的子系统由哪份指南负责，从 [docs/README.zh-CN.md](README.zh-CN.md) 开始。
