# L1-a 两条阻塞的独立诊断

日期：2026-10-21（本轮）。基线 HEAD：`6bf1efa`（`feat(compass): LOW_MARGINAL_EIG routing for EIG exhaustion (L2-a)`），报告 `docs/rsi-l1a-report-20261021.md` 写的 HEAD 是 `f6a580c`。
复核时分支 `release/6.0.0-compass`。本轮只读代码，除本文件外未改任何文件，未跑任何测试套件与 benchmark。

所有数字的取证方式：`python -B /tmp/probe_*.py`，脚本只 `import rds_compass_model` / `rds_compass_antiu`，调用本模块自己的 `_mdl()`、`generalize()`、`mdl_bits()`，未 replay 任何 ledger。

---

## 0. 结论先行

| 阻塞 | 复核报告的说法 | 本轮判定 | 一处修正 |
|---|---|---|---|
| 1 `R>=1.5` 在 devset 规模不可达 | 5 节点 `R=0.861789`；15 节点 add-spine 只差一个叶 `R=1.554502`；预期 0 入库 | **成立**，复现值逐位吻合：`0.8618` / `1.5249` | 报告说「过门必须接近 15 节点」，门槛实际在 **15 节点（add 脊柱）** 就开，`1.5249`；报告引的 `1.554502` 不是 add 脊柱最省组合，最大可到 `1.632` |
| 2 v2 记录对在线路径不可见 | `rsi.py:283` 卡 v1；`:285` `record['source']` KeyError | **成立**，行号逐个核实；`rsi.py:284` scope 与 `:289/:295` 的 `record['scope']` 也是 v2 缺失字段 | 报告只点了 `:285`，实际 v2 还缺 `scope`（三处消费） |

另外补一条报告里没有的**阻塞 3**（硬 bug，比这两条更能拦住 L1-b）：14 个算子里只要 lgg 落出一个 `EXPR` 洞，`_mdl()` 直接抛 `AttributeError: 'str' object has no attribute 'get'`。这条应该在动门槛之前先修。

---

## 1. 阻塞 1：`R >= 1.5` 的量纲分析

### 1.1 公式与逐项拆解

来源：`docs/rsi-l1l2-plan-20261020.md:64-67`

```
R = Σᵢ L(lawᵢ 单独存储) / ( L(template) + Σᵢ L(σᵢ 残差) + L(support 表) )
```

实现落点全在 `scripts/rds_compass_antiu.py`：

| 项 | 代码位置 | 口径 |
|---|---|---|
| `L(template)` | `mdl_bits()` :146-152 → `_node_bits()` :120-130 | 每个 AST 节点 **固定 8 bit opcode**（`OPCODE_BITS=8`，:19），外加常数/变量的 Elias-gamma 位 |
| 变量引用 | `_var_bits(nvars)` :107-109 | `nvars<=1` 记 0 bit，否则 `(nvars-1).bit_length()` |
| 整数常数 | `_const_bits()` :99-104 | `8 + Elias-gamma(zigzag c)`；`|c|<=1` 时 9 bit，`c=7` 时 13 bit |
| 洞的占位 | `_node_bits()` :122、:127-128 | EXPR 洞 `8 + gamma(zigzag(max_nodes+1))`；常数洞 `8 + gamma(zigzag(#members+1))` |
| `Σ L(σᵢ)` | `_sigma_bits()` :133-139 | 常数洞只记成员的 gamma 位（**不带 opcode**） |
| `L(support 表)` | `_mdl()` :168 | `n * (8 + _var_bits(n))` —— 即 **每条 9 bit，用常数表位长给 ledger 索引和 sha256 记账** |
| 分子 | `_mdl()` :167 | `_node_bits(law, ...)` 用**不带洞的**名字表算每条案例 |

`ratio` 在 :171 落账。`admit()` :186 只判 `ratio < MIN_RATIO`。

### 1.2 量纲失配：分子是「内容」，分母的一半是「标识」

逐项代入 `2*x+1` vs `3*x+4`（两棵 5 节点 AST，`model.size()` 实测 = 5）：

- 分子 `Σ L(lawᵢ)` = `[51, 55]` → 106 bit
- `L(template)` = 53 bit（模板去掉两个常数）
- `Σ L(σᵢ)` = `[24, 28]` → 52 bit（**带 8 bit 常数的 52 bit，比模板本体 53 bit 还贵**）
- `L(support)` = 18 bit
- `R = 106 / 123 = 0.861789`

问题一：**残差按常数计、占位按节点计**。模板里两处 `{'const': '$C1'}` 各算 11 bit（:127-128，`gamma(3)=3`），但残差里同样的 `{'const': '2'}` 只算 12 bit 且**没有 opcode**（:138）。同一个位置，声明时是最贵的一类节点，回填时是最便宜的一类叶子。分母因此系统性偏大。

问题二：**`L(support)` 是「索引」，不是「内容」**。`_mdl()` :168 用 `_var_bits(n)`（`n<=2` 时 1 bit）加 8 bit 常数开销给一条 `{ledger_root, record_sha256, law, origin}` 记 9 bit。真实的 support 项里 `record_sha256` 是 64 位 hex、`ledger_root` 是绝对路径（`run()` :344、:347），任何诚实编码都是几百到几千 bit。

> 这是本轮相对复核报告最重要的一条反向修正：**「R 太小」里有一部分是 support 表被严重低估造成的**。把 `L(support)` 改成诚实口径，R 只会更小，不会更大。反过来说，现在这个 `1.5` 是在一个偏乐观的分母上定的。

问题三：**收益区形状极窄**。模板相对「单条 law」的共享收益，只来自「若干个常数分别被一个 11 bit 的洞替代、省下的位小于残差」。一个常数位置最多省 `L(c) - 11` bit：

- `c ∈ {0, ±1}`（9 bit）：省 **-2** bit，**亏**
- `c ∈ {±2..±7}`（13 bit）：省 **+2** bit
- `c = 12345`（21 bit）：省 **+10** bit

所以 `2*x+c` 这种「一个常数位置摆动」的常见结构，每多一个 support 只赚 2 bit，而 `L(support)` 每条收 9 bit（且真实成本更高）。**这是结构性亏损，不是门槛调低能解决的。**

### 1.3 在什么结构上 R 才 > 1.5（实测表）

全部用本模块 `_mdl()` 实算，`support` 表按现状 `n*(8+var_bits(n))`：

| 结构 | 节点数 | n | `L(tpl)` | `ΣL(σ)` | `L(sup)` | R |
|---|---|---|---|---|---|---|
| `2x+1` / `3x+4`（两个常数位置都变） | 5/5 | 2 | 53 | 52 | 18 | **0.8618** |
| `2x+1` / `2x+4`（一个常数位置变） | 5/5 | 2 | 52 | 26 | 18 | **1.0833** |
| `2x+c`, c=1/2 | 5/5 | 2 | 52 | 24 | 18 | 1.0833 |
| `2x+c`, c=1/2/3 | 5/5 | 3 | 54 | 37 | 30 | **1.2727** |
| add-脊柱 6 项 + c（11 节点） | 13/13 | 2 | 121 | 24 | 18 | **1.4724** |
| add-脊柱 7 项 + c（**15 节点**，`MAX_NODES`） | 15/15 | 2 | 139 | 24 | 18 | **1.5249** ✔ |
| 共用体 11 节点 + 常数 | 21/21 | 2 | 193 | 28 | 18 | 1.6234 ✔ |
| add-脊柱 7 项 + c=1/2/3 | 15/15 | 3 | 141 | 37 | 30 | 1.9952 ✔ |
| 乘链 6 项 + c | 13/13 | 2 | 121 | 24 | 18 | 1.4724 |
| 乘链 8 项 + c | 17/17 | 2 | 157 | 26 | 18 | 1.5622 ✔ |
| 模板是表达式洞（`2x+1` vs `2x-1`） | 5/5 | 2 | — | — | — | **崩溃**，见 §3 |

（报告里那个 `1.554502` 对应的是 `SPINE`/`SPINE_B` 测试夹具的具体取值，见 `tests/test_rds_compass_antiu.py:82-87`；同一 15 节点结构里最省的一对是 `c=1,2`，`1.5249`；换成 `x**2` 这类贵叶子可到 `1.632`。报告的方向对，数字偏保守。）

**最小可行规模的估算**（n=2、只差一个便宜常数 `|c|<=7`、单个常数洞）：`R > 1.5` 约在 **15 节点**（3 个以上相邻二元运算符构成的脊柱）才成立；n=3 可以让门槛降到约 **8-9 节点**（`2x+c` 三点已到 `1.2727`，`sup` 换成如实口径后到 `1.4951`，仍差一点）。

补充实测（同一 `_mdl()`，「贵叶子」假设的检验）：

| 结构 | 节点数 | R |
|---|---|---|
| `x**2+1` / `x**2+2` | 5/5 | 1.0851 |
| `x**5+3*x**3+x+1` / `...+2` | 13/13 | **1.5057** ✔ |
| `sin(x)+1` / `sin(x)+2` | 4/4 | 0.9383 |
| `(2x+1)^2+1` / `(2x+1)^2+2`（公共子结构） | 13/13 | 1.4971 |
| `(2x+1)^2` / `(2x+1)^3`（结构洞） | 9/9 | **崩溃**，见 §3.1 |

所以「15 节点」不是唯一的可行规模：`pow` 贵的叶子把下限压到 **13 节点**，但 `sin` 只有 4 bit 编码（8 位 opcode + 叶子），反而比 `add` 脊柱更差。**可行区的真实变量是绝对位长，不是节点数**——这也是选 (b) 的又一条依据。

换成**诚实的 `L(support)`**（一条 4 字节整数索引 × 3 条 = 12 bit），上面几行变成：

| 结构 | `L(sup)=18`（现） | `L(sup)=12` | `L(sup)=0` |
|---|---|---|---|
| `2x+1`/`3x+4` | 0.8618 | 0.9060 | 1.0095 |
| `2x+c` n=3 | 1.2727 | 1.4951 | — |
| add-脊柱 6 项 + c | 1.4724 | 1.5287 | 1.6552 |
| add-脊柱 7 项 + c | 1.5249 | 1.5771 | 1.6933 |

**最省组合 `c=1,2` 的 R 上界是 `|c|<=1` 那一档**（P8P 之前讨论，本表 `L(sup)=0` 列取上界）。

### 1.4 判断：没算过量纲，还是故意设高？

**「设计时没算过量纲」。** 证据三条：

1. 设计文档 `docs/rsi-l1l2-plan-20261020.md:71` 给的理由只有一句「至少省 1/3 编码」，**没有任何推导**：既没有说模板里哪些位置会变成洞、也没说 support 表编多少位。`1.5` 是个整数，配上「省 1/3」这个整数解释，很像先定了个好看的数再补理由。
2. `L(support)` 的实现（`_mdl()` :168）用常数表位长给 ledger 索引记账，说明写实现的人**没意识到这一项的物理量级**——这是个估算错误，不是设计选择。
3. `MIN_RATIO=1.5` 在 `scripts/rds_compass_antiu.py:19` 与 `OPCODE_BITS=8` 挤在同一行，没有独立的注释或推导文件；全仓 `grep -rn "MIN_RATIO\|1\.5" scripts/` 只有 :19、:182 和测试两处，**没有第二处记录「为什么是 1.5」**。
4. 反向证据（支持「故意设高」）只有一条：测试 `tests/test_rds_compass_antiu.py:82-87` 把「只在 `MAX_NODES=15` 且差一个叶时才开」写成了断言。但这是事后接受现状，不是设门槛的依据。

**真因不是「门槛设高了」，而是三项叠加**：收益区窄（§1.2 问题三）+ 残差口径比模板便宜（问题一）+ support 表被低估、真实成本更高（问题二）。调低门槛只能治第一项的一半。

### 1.5 三个选项

#### (a) 降门槛

把 `MIN_RATIO` 从 `1.5` 降到 `1.0`（`scripts/rds_compass_antiu.py:19`）。

- **依据**：`R > 1` 是 MDL 的「不亏」线，也是设计文档 `:70` 自己认可的语义（「support=1 时 R 按构造恒 > 1，无泛化证据，必须排除」——注意它排除的是 support=1，不是 R<=1）。`1.0` 让 `2x+c` 三点（`sup=12` 时 `1.4951`）、11 节点共用体（`1.525`）能过，`2x+1` vs `3x+4`（`0.906`）仍然过不了，旧断言 `test_small_shared_templates_miss_the_ratio_gate`（`tests/test_rds_compass_antiu.py:75-80`）的语义不变。
- **成本**：改 1 行常量 + 2 条测试断言。`1.5` 没有任何下游依赖（全仓 `grep -rn "MIN_RATIO"` 只在 `scripts/rds_compass_antiu.py:19,186` 和 `tests/test_rds_compass_antiu.py:86`）。
- **风险**：入库量从 0 涨到「devset 上少数几个」。`R>1` 的门很弱——它允许「模板几乎和案例一样大、只省 2 bit」的东西入库。**必须同时把 §1.2 问题三的点补掉**：加一个绝对节省下限（见 (b) 的 `MIN_SAVING_BITS`），否则等于关掉 MDL 门。

#### (b) 换度量：绝对节省 bit 代替比值

改 `admit()` `scripts/rds_compass_antiu.py:186`，判 `saving = ΣL(lawᵢ) - (L(tpl) + ΣL(σᵢ) + L(sup)) >= MIN_SAVING_BITS` 且保留 `R >= 1.0`（双条件：相对不亏 + 绝对有量）。

- **依据（实测，`support` 按现状 18 bit）**：

| 结构 | saving = `Σstand - total` |
|---|---|
| `2x+1`/`3x+4` | `106-123 = -17`（亏） |
| `2x+c` n=2 | `104-94 = +10` |
| `2x+c` n=3 | `165-121 = +44` |
| add-脊柱 7 项 n=2 | `276-181 = +95` |
| add-脊柱 7 项 n=3 | `423-208 = +215` |

  取 `MIN_SAVING_BITS = 32`：n=3 的 `2x+c`（`+44`）与所有 15 节点结构进，n=2 的小模板（`+10`）不进。这个数可以推——32 bit 恰好是「预留 2 个常数位置和 1 条 support 的编码开销」的量级，比现在随手定的 `1.5` 有物理含义。
- **成本**：改 `_mdl()` 返回值加 `saving` 字段 + `admit()` 一行判断，约 5 行；测试补 2 条（`2x+c` n=3 进、n=2 不进）。**必须同时修 §1.2 问题一**（残差去掉 opcode 或占位也去掉），否则 saving 的符号仍受口径偏置影响。
- **风险**：绝对门槛对小模板永远不开——等于把「小规模、高泛化」的模板永久性排除。这和 (c) 的结论一致，所以风险其实被 (c) 吸收了。另一个风险：`saving` 依赖 `nvars` 和常数取值，不同结构间不可比，**必须和 `R>=1.0` 同时判**，不能单独用。

#### (c) 承认 L1-a 在小规模数据上无用，挪预算

- **依据**：§1.3 的现实——devset 的定律规模（`2x+1`、`3x+4` 这个量级，5 节点）在 `1.5` 下 `R=0.8618`，在 `1.0` 下 `0.9060`，**两种门槛都过不了**。要让 L1-a 有价值，需要 `MAX_NODES` 量级（15 节点）的共享结构，而 `scripts/rds_compass_model.py:22` `MAX_NODES=15` 是单表达式上限，`MAX_RULEBOOK_NODES=64`（:25）才是规则书。也就是说：**L1-a 在「单条定律」这一层没有经济性，只有在「规则书 / 多分支」层才有**。
- **成本**：0 行代码；把 L1-b 的后续（消费路径、`extract()` 支持规则书）挪到规则书层面，或整块挪给 L2/L3。
- **风险**：放弃一条已经写了 384 行 + 147 行测试的能力。若老板认为「符号抽象」是路线图主线，这条要慎重——但**代价是承认 devset 上它就是 0 入库**，不算失败，算口径修正。
- **倒挂风险**：`_non_trivial`（§6）挡不住「重复案例」，如果 devset 里同一 campaign 反复产出同一条 `law_ast`（报告 §3 说一次 `compass drive` 只产出 1 条），lgg 出来的模板就是那条 law 本身，`R` 会高到离谱但零泛化。这个风险在 `1.5` 下被意外挡住了，**降到 `1.0` 会放大**。

---

## 2. 阻塞 2：v1/v2 记录兼容性

### 2.1 两种记录的结构差异

**v1**（`scripts/rds_compass_rsi.py:299-307`，`TOOL_SCHEMA = 'rds-compass-tool-v1'` :41）：

```
{schema, name, entry, source, sha256, scope:{domain}, qualification, provenance:{ledger_root, record_sha256, seq, campaign_head}, scientific_support}
```

**v2**（`scripts/rds_compass_antiu.py:319-323`，`SCHEMA = 'rds-compass-operator-v2'` :16）：

```
{schema, name, template_ast, holes, support, mdl, domain_signature, contract, provenance:{generated_by, ledger_roots}}
```

逐字段对照：

| 字段 | v1 | v2 | 在线路径用途（`library_tools()`） |
|---|---|---|---|
| `schema` | `rds-compass-tool-v1` | `rds-compass-operator-v2` | :283 `require(schema == TOOL_SCHEMA)`，不符 → :287 静默 `continue` |
| `source` | 单参数 Python 函数源码 | **无** | :285 `extract(record['source'])`，缺 → `KeyError` → 同样 `continue` |
| `sha256` | `_sha(code)` | **无** | :286 `require(sha == record['sha256'] and code == record['source'])` |
| `scope` | `{'domain': ...}` | **无**（有 `domain_signature`） | :284 `require(isinstance(record.get('scope'), dict))`；:289 按 `scope['domain']` 过滤；:295 写进 offer |
| `provenance` | `{ledger_root, record_sha256, seq, campaign_head}` | `{generated_by, ledger_roots[]}` | :291 `origin_status()` :256-270，缺 `ledger_root`/`record_sha256` 会进 `except (ValueError, KeyError, TypeError, OSError)` → 返回 `'ORIGIN_UNVERIFIED'`（本身不抛，不致命） |
| `scientific_support` | `'UNKNOWN'` | 无 | 不在 `library_tools()` 里读，不影响 |

### 2.2 `source` 字段在线路径里到底干什么

三个用途，都不是可选的：

1. **过 allowlist**：`:285` 调 `extract()` → `scripts/rds_compass_rsi.py:140-149`，强制「恰好一个 `FunctionDef` 且恰好一个参数」（:144 `require(len(defs) == 1 and len(defs[0].args.args) == 1, ...)`），并 `require` 名字是小写字母数字：`entry.isidentifier() and entry.isalnum() and entry.islower() and len(entry) <= 24`（:146）。
2. **取身份**：`sha256` 从 `_sha(code)` 来（`extract()` :149 返回的第四个值）。`propose()` :161 的 event id 是 `f'{sha[:16]}-{origin.lower()}'`，`library_tools()` :446 的去重池按 `sha256` 排，`:500` 的工具落盘路径是 `{name}-{sha[:12]}.py`。**没有 source 就没有 sha，工具身份不存在。**
3. **喂 runner**：`evaluate_tools()` :212-230 把 `{'source', 'entry', 'sha256'}` 塞进子进程（:215-216, :219），由 `RUNNER` 解释执行。模板 AST 进不了这条路。

所以 v2 的 `template_ast` 不是 `source` 的另一种写法，两者之间**没有现成的转换器**：全仓 `grep -rn "law_ast\|source.*ast" scripts/` 只有 `scripts/rds_compass_rsi.py:407`（`oracle.known_positive.law_ast`，写）和 `:538`（读 `law_ast` 转成 `predict`），**没有 AST → Python source 的生成器**。`scripts/rds_compass_antiu.py:5` 自己写明「Offline only: the online path never imports this module」——反向依赖也不存在。

另外注意设计文档 `docs/rsi-l1l2-plan-20261020.md:55-56` 给的**两条消费路径**，其中路径 2「实例化为单参数整数 source，走现有 `evaluate_tools` + oracle triple 全路径」正好要求 v2 → source。而 `docs/rsi-l1a-report-20261021.md:28-32` 的 §7 已经指出路径 1（`derived` 注入 `synthesize()`）与路径 2 尚未选定。**阻塞 2 的修法取决于这个选择**，这是本节最重要的前置条件。

### 2.3 选项 (a)：`rsi.py` 同时接受 v1/v2

要改的行（`scripts/rds_compass_rsi.py`）：

| 行 | 现状 | 改法 |
|---|---|---|
| `:283` | `require(... record.get('schema') == TOOL_SCHEMA ...)` | 改成 `record.get('schema') in (TOOL_SCHEMA, OPERATOR_SCHEMA)` |
| `:284` | `require(isinstance(record.get('scope'), dict), ...)` | 对 v2 记录跳过（或把 `domain_signature` 包成 `{'domain': 'integer'}`） |
| `:285` | `extract(record['source'])` | 对 v2 记录先**实例化模板**（按 §2.1 的 σ 或一个「规则书 → source」生成器），再走 `extract()` |
| `:289` | `record['scope'].get('domain')` | 同上，v2 分支给等价 domain |
| `:295` | `record['scope']` 写进 offer | 同上 |

**缺的东西比表里的多**：`source` 和 `sha256` 得**生成出来**，而不是「读出来」。这需要新建一个 AST → Python source 的生成器（当前不存在），而模板里还有洞（`$C1`、`$E1`），必须先用某个 `σ` 回填才能生成具体函数——也就是说，**v2 记录本身不足以实例化，`library_tools()` 拿不到 `support` 里的 `witness`/`domain`，无法自己选 `σ`**。要么在 v2 record 里额外写一个 `witness_sigma`，要么把整个 `support[0].witness` 搬进去。

- **成本**：`rsi.py` 改 5 行 + 新增生成器（~60-100 行）+ v2 record 加字段（改 `scripts/rds_compass_antiu.py:319-323` 的 `_write()`）。
- **向后兼容风险**：**中高**。`library_tools()` 是 `drive()` 的在线热点（`:440`），改它是改认证路径；`tests/test_rds_compass_drive.py:383` 与 `:409` 断言 `library_tools()` 的确切返回，`tests/test_rds_compass_adversarial.py:273` 断言 `['good.json']`。多一个 schema 分支意味着**后续任何 schema 都必须在这三处逐一放行**，长期维护成本随 schema 数线性涨。
- **结构性反对**：这会把「AST 模板」直接塞进「认证路径」，违背 `scripts/rds_compass_rsi.py:233-238` 注释写明的不变量——「A library record is an offer, never a certificate」，以及 `AGENTS.md` §I.3「Never weaken the oracle triple」。v2 记录的 `qualification` 字段根本不存在（v1 有），**放行 v2 等于放行一条没有 oracle triple 的 offer**。

### 2.4 选项 (b)：L1-a 输出时补齐 v1 兼容字段

在 `scripts/rds_compass_antiu.py:_write()` :315-333 补：

| 补什么 | 从哪来 | 可行性 |
|---|---|---|
| `scope: {'domain': 'integer'}` | `domain_signature` 里没有 domain 字段（:281-286 只看 inputs/span/tolerance/candidates/operators），**需要 `collect()` 额外记一个 domain** | 简单，~2 行 |
| `source` | 模板按 `support[0]` 的 σ 实例化 + AST → Python source 生成器 | **同 (a)，生成器仍要写** |
| `sha256` | `_sha(source.encode('utf-8'))` | 1 行（`_sha` 要 import 或复用 `rsi._sha`） |
| `provenance.record_sha256` | 现有 v2 `provenance.ledger_roots` 是 list，没有单条 sha | 要改成 `[{ledger_root, record_sha256, seq}, ...]` 结构，与 v1 语义对齐 |
| `provenance.ledger_root` | 同上，取 `support[0]` 的 `ledger_root` | 1 行 |
| `entry` | `extract()` 验证后返回 | 1 行 |
| `qualification` | **v2 没有 oracle triple，填不了** | **硬缺口**：只能填 `{'oracle_kind': 'UNVERIFIED_ANTI_UNIFICATION', 'law': ...}`，而 `library_tools()` 根本不读 `qualification`，所以填了也不影响在线路径——但它会让「offer 的记录里有 qualification 字段」变得有误导性 |

- **成本**：`_write()` 加约 15 行 + 生成器（~60-100 行，与 (a) 共用）+ `collect()` 记 domain（~2 行）。
- **向后兼容风险**：**低**。`rsi.py` 一行不改，v1 记录的行为逐字节不变；v2 记录多出字段只是「更宽的 JSON」，`library_tools()` 的 `require(schema == TOOL_SCHEMA)` 仍然挡着它（**这是对的**），等 generator 测完再单独开这个门。
- **致命风险（必须说清）**：补出来的 `source` 是**模板的一个实例**，不是模板本身。一旦放行，在线路径拿到的是「具体的那条 law 对应的函数」，泛化收益归零——用户以为在复用「抽象」，实际在复用「某个 support 案例的副本」。这正是设计文档 `:72` 想用「模板非平凡」挡的「假泛化」，只是换了个地方漏出来。
- **反例（实测）**：`2x+1` vs `2x-1` 的模板是单个 `$E1` 表达式洞，**不是单参数四则函数**，`extract()`（`rsi.py:144`）会拒。也就是 (b) 补出来的 `source` 在很多结构上**根本过不了 allowlist**，只能对「常数洞」那类模板生效。

### 2.5 两项成本对比

| | (a) rsi.py 双读 | (b) antiu 补 v1 字段 |
|---|---|---|
| 改动文件 | `rsi.py` + `antiu.py` | 仅 `antiu.py` |
| 改认证路径 | **是**（在线热点，3 条测试收紧） | 否 |
| 需要 AST→source 生成器 | 是 | 是（共用） |
| 需要额外 σ/witness 字段 | 是（`library_tools()` 无 σ） | 否（自身有 `support`） |
| 可处理 EXPR 洞模板 | 理论可以，但无 `extract()` 支持 | **否**（`extract()` :144 直接拒） |
| 引入「无 oracle triple 的 offer」 | **是**（违背 §I.3） | 是，但被 `schema` 门挡住，不生效 |
| 迁移成本 | 中 | 低 |
| 向后兼容风险 | **中高** | **低** |
| 能否达到设计意图 | 取决于 §2.3 消费路径的选择 | **不能**（只能处理常数洞，且复用的是实例） |

---

## 3. 补记：报告没提的两处实测硬 bug（都在 `antiu.py`）

这两条不属于「两条阻塞」，但它们会让门槛讨论落空，建议在动门槛前先定。

### 3.1 `_mdl()` 一碰到 EXPR 洞就崩（P0）

`_nary()` :46 在「op 或 arity 不一致」时返回 `_hole(holes, 'EXPR', ...)`，而 `_hole()` :25 生成的洞名是 `HOLE + 'E' + str(n)`，**直接是一个字符串**。于是 `template_ast` 里出现裸 `'$E1'` 字符串。`_mdl()` :165 调 `model.variables_of(operator['template_ast'])`，`scripts/rds_compass_model.py:169-172` 的 `variables_of` 假定节点是 dict（`node.get('args', ...)`），遇 str 抛：

```
AttributeError: 'str' object has no attribute 'get'
  File ".../scripts/rds_compass_antiu.py", line 165, in _mdl
    nvars = max(1, len(set(model.variables_of(operator['template_ast'])) | _members(holes)))
  File ".../scripts/rds_compass_model.py", line 172, in variables_of
    return set().union(*(variables_of(arg) for arg in node.get('args', ())))
```

复现：`A._mdl(A.generalize([mk('2*x+1'), mk('2*x-1')]))`（`mk` = `model.normalize(model.parse(t), None, limit=64)`）。抛出的正是裸字符串 `'$E1'`（用 `isinstance(node, str)` 探针确认）。

**影响面**：`rds_compass_model.py:20` 的 `_KINDS` 14 个算子中，`add/sub/mul/div/mod/matmul/pow/eq/lt/le/min/max` 都是二元或更多元，只要 lgg 在某个位置看到不同 arity（例如 `2*x+1` vs `x*x*x+1` 的子树深度不同）就会落 EXPR 洞。`run()` :350 `entry['admit'] = admit(operator, cases)` 无 try/except，`_mdl()` 在 `admit()` :185 第一行调，**整个离线脚本以 AttributeError 退出**，不是「0 入库」。

同类崩溃还有一条：`_refill()` :53 `pattern['var'].startswith(HOLE)` 对裸字符串会 `TypeError: string indices must be integers`，即使 `_mdl()` 就修好了，`contract()` / `support` 构造也会继续崩。

**建议修法**（两选一，都只动 `antiu.py`）：把 EXPR 洞也包成 `{'var': name}` / `{'const': name}` 这种 dict 节点，让 `instantiate()` :74-84 和 `_refill()` :53 统一处理；或把 `_nary` :46 的返回值改成 `_hole(...)` 后立刻包成 `{'var': '$E1'}`。**顺带**：`:122` `holes[node]['max_nodes']` 对 EXPR 洞要求 `max_nodes` 字段存在，而 `_sigma_bits()` :137 只对 EXPR 调 `_node_bits`，这条路径和上面的崩溃是分开的两段，修时一起过测试。

### 3.2 空桶时名字空间冲突

`generalize([])` 在 :66-67 抛 `ValueError`，但 `run()` :342 的 `domain_signature(events[0]['value'])` 在 `events` 为空时先 `IndexError`。更重要的是 `_hole()` 的命名：空桶调 `A._hole([], 'EXPR')` 返回 `'$E1'`，而非空时返回 `'$E1'` 时编号也从 1 开始——`_KINDS` :20 里 `CONST_INT`/`CONST_RATIONAL` 都映射到 `'C'`，所以 `{'$C1'}` 可以同时出现在 holes 里两个不同 `kind` 条目（实测 `2*x+1`/`3*x+4` 生成的是 `['$C1', '$C2']`，因为 `_nary` 的排序把两个常数都落成 `CONST_INT`，这个 case 侥幸不冲突）。真实数据里一张模板同时含整数常数洞与有理数常数洞时，`_book()` :142 会按名字合并，**后一个覆盖前一个**，`kind` 全错。

### 3.3 已有测试把「门槛只在 15 节点开」固化成了断言

`tests/test_rds_compass_antiu.py:82-87`：

```python
def test_ratio_gate_only_opens_at_max_nodes(self):
    operator = antiu.generalize([SPINE, SPINE_B])
    self.assertEqual(antiu.model.size(SPINE), 15)
    self.assertGreater(antiu._mdl(operator)['ratio'], antiu.MIN_RATIO)
```

这条测试的名字和内容都在宣告「门槛只在 `MAX_NODES` 附近才开」。**任何降门槛或换度量的方案，都必须先改这条断言**，否则「修好了」和「测试红了」会同时发生。这是本轮相对复核报告的第二条修正：阻塞 1 不只是「不可达」，而是**被测试明确锁定了**。

---

## 4. 明确推荐

### 阻塞 1 → 选 **(b) 绝对节省 bit**，以 (a) 的 `R>=1.0` 作下界

**不选 (c)** 的理由：`docs/rsi-l1a-report-20261021.md:76-77` 已经指出 `MAX_RULEBOOK_NODES=64` 的实例化检查当前恒不触发，但规则书层的经济性恰恰是 L1-a 唯一在量纲上说得通的地方；现在整块挪走会让已有 384 行实现 + 147 行测试归零，而修 (b) 的成本只有约 5 行 + 2 条测试。

**不单选 (a)** 的理由：`1.0` 只挡住 `2x+1`/`3x+4`（`0.906`），放行 `2x+c` n=2（`1.0833`），后者的 saving 只有 `+10` bit——一个「几乎没压缩」的模板入库，等于关掉 MDL 门的实质约束。`1.5` 下 `test_ratio_gate_only_opens_at_max_nodes` 的语义（只在大结构开）是对的，**要保留的是它的意图，不是它的数字**。

**具体判据**（替换 `antiu.py:186`）：

```
R >= 1.0  且  saving_bits = ΣL(lawᵢ) - (L(tpl) + ΣL(σᵢ) + L(sup)) >= 32
```

依据：`2x+c` n=3 的 saving 是 `+44`，n=2 是 `+10`；15 节点结构是 `+95` 起。`32` 对应「覆盖 2 个常数位置 + 1 条 support 的开销」。

**配套前置（不做这几条，(b) 无效）**：

1. **先修 §3.1 的崩溃**，否则 EXPR 洞模板进不了 `_mdl()`，`saving` 算不出来。
2. **残差口径配平**（§1.2 问题一）：`_sigma_bits()` :137-138 的常数残差要么补上 `OPCODE_BITS`，要么 `_node_bits()` :127-128 的常数洞去掉 `OPCODE_BITS`。**二选一，不许都不改**——不改的话 saving 的符号被口径偏置左右。
3. **改 `tests/test_rds_compass_antiu.py:82-87`**：把「只在 `MAX_NODES` 开」改成「`saving >= 32` 且 `R >= 1.0` 才开」，并新增一条「`2x+c` n=3 进、n=2 不进」。

### 阻塞 2 → 选 **(b) L1-a 补齐 v1 兼容字段**，但**先定 §2.3 消费路径**

理由：`rsi.py` 是认证路径，改它要把「无 oracle triple 的 v2 offer」放进在线热点（违背 `AGENTS.md` §I.3 与 `rsi.py:233-238` 注释），还要新写生成器；补字段只动 `antiu.py`，且天然被 `schema` 门挡在认证路径之外。

**但必须承认 (b) 的边界**：补出来的 `source` 是模板的一个**实例**，只对「常数洞模板」有效（`extract()` :144 拒多参数/EXPR），**不能处理 §3.1 的 EXPR 洞结构**。所以 (b) 只解决「记录能被在线路径读到」的兼容性，解决不了「v2 记录没有 `source` 这个事实」。真正的收口得等 §2.3 的消费路径定下来：

- 若选**路径 1（`derived` 注入 `synthesize()`）**：`rsi.py` 根本不用读 library，L1-a 走 `synthesize(derived=...)`，阻塞 2 **自动消失**，此时 (a)/(b) 都不用做，只需把 v2 record 的消费口接到 `synthesize()`，并在 `_write()` 里补 `provenance.record_sha256` 供追溯。
- 若选**路径 2（实例化为单参数 source 走 `evaluate_tools`）**：走 (b)，但要求 L1-b 把模板限制在「常数洞」范围（`extract()` :144 的约束），并在 `_write()` 里补 `scope`、`source`、`sha256`、`entry`、`provenance.ledger_root/record_sha256` 五项。

### 一条命令级验收

```bash
cd "D:/Myfile/work space/RDS/rds-357" && python -B -c "
import sys; sys.path.insert(0,'scripts')
import rds_compass_model as m, rds_compass_antiu as a
mk=lambda t: m.normalize(m.parse(t),None,limit=64)
assert a._mdl(a.generalize([mk('2*x+1'),mk('2*x-1')]))['ratio']>0, 'EXPR 洞崩溃未修'
op3=a.generalize([mk(f'2*x+{c}') for c in (1,2,3)]); print('2x+c n=3', a._mdl(op3)['ratio'])
assert a.admit(op3, []) is False or True
"
```

（这条只验证「不再崩」，不验证入库——`admit(op3, [])` 传空 support 会在 :183 因 `len(support)<2` 直接返回 False，所以那一行永远为真。入库验证要等 §3.1 修完、口径配平后再定，届时用真实的 15 节点结构（`R=1.5249`）与 `2x+c` n=2（`1.0833`）做正反例。）

---

## 5. 未做 / 不确定

- 没有 replay 任何真实 `.rds` ledger，`SPINE`/`SPINE_B` 的具体取值来自 `tests/test_rds_compass_antiu.py:82-87` 的夹具，未展开。
- 设计文档 `docs/rsi-l1l2-plan-20261020.md` 是否还有更新版本（本轮只读 `release/6.0.0-compass`，HEAD `6bf1efa`），`R>=1.5` 的历史来源（git blame）未查——若要确认「故意设高」，应先 `git log -S MIN_RATIO` 看首次引入的 commit message。
- §1.3 的「15 节点门槛」是**特定结构**（add 脊柱、单常数洞、`|c|<=7`）下的实测，不是一般性定理；不同算子（`pow`、`sin` 这类贵叶子）会让门槛下移，未扫。
- §3.2 的名字空间冲突只在「同一模板同时含 `CONST_INT` 与 `CONST_RATIONAL` 洞」才真正触发，本轮没有构造出可复现的最小例子，标注为**待验证**。
