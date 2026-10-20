# PySR smoke：本机真实运行与 RDS 重放链（#357）

- 运行时间：2026-10-20（Asia/Shanghai）
- 工作区：`C:/Users/XU/rds-357`
- 本文只记录真实执行的输出；脚本放在系统临时目录（`%TEMP%/pysr_smoke/`），未入库，未改 `scripts/`、`pysr_adapter.py` 或测试文件。

## 0. 一句话结论

PySR 2.7.2 + Julia 1.13.2 在本机可用。10 点、5 次迭代的 `y = 2*x**2 + 1` 在 2.35 s 内找到 loss≈0 的等价式，经 `pysr_adapter` 转成 RDS 方言后 replay 为 `CONSISTENT`；同一规律加 1% 相对噪声、30 点后，前沿全部候选在 1% 相对容差下被 `REFUTED`（真值公式通过），高复杂度候选开始长出虚假线性项和等价因子拆分。

## 1. 环境

| 项 | 值 |
| --- | --- |
| Python | 3.13.5（`C:\Users\XU\miniconda3\python.exe`） |
| PySR | 2.7.2 |
| juliacall | 0.9.36 |
| SymbolicRegression.jl | 2.7.2 |
| Julia | 1.13.2，不在 PATH；由 juliacall 装到 `C:\Users\XU\.julia\environments\pyjuliapkg\pyjuliapkg\install\bin\julia.exe` |
| Julia depot | `C:\Users\XU\.julia`，1.4 GB（packages 21 MB / artifacts 99 MB / compiled 165 MB） |
| numpy / pandas | 2.2.6 / 3.0.6 |

安装观测：

- 首次 `python -c "import pysr"` 超过 60 s 未返回；当时 `~/.julia` 还是空目录、Manifest 尚未生成。
- `~/.julia/environments/pyjuliapkg/Manifest.toml` 时间戳 14:01，`pyjuliapkg/install` 14:02，`~/.julia/environments` 14:04，对应首次 import 触发的 Julia 拉取与预编译。
- 装好后 `import pysr` 3.85 s（含启动 Julia），同一进程内后续 fit 只需 0.05–2.55 s。
- 目录里原有的 `pysr_env_report.md` 记录的是安装前状态（“pysr 未安装”），已被本次运行推翻；那份文件没动，以本文为准。

## 2. 最小 smoke：`y = 2*x**2 + 1`

命令（`/tmp` 即 `C:\Users\XU\AppData\Local\Temp`）：

```bash
cd C:/Users/XU/rds-357
PYTHONDONTWRITEBYTECODE=1 python -B "C:/Users/XU/AppData/Local/Temp/pysr_smoke/smoke.py"
```

搜索设置：`niterations=5, populations=8, maxsize=12, binary_operators=['+','-','*','/'], unary_operators=[], procs=1, random_state=0`；数据 10 点，`x = linspace(-2, 2)`。主跑没开确定性模式，所以这次的前沿只是其中一次抽样；要逐字复现用下面片段里的设置。

前沿（全部 3 行）：

| complexity | loss | equation |
| --- | --- | --- |
| 1 | 8.24069 | `4.259262` |
| 5 | 0.43686 | `x * (x * 2.345563)` |
| 7 | 0（浮点显示） | `(x * (x * 2.0)) + 0.9999999` |

- fit 耗时：**2.35 s**；本次运行总墙钟 5.96 s（两次 fit + 全部 RDS 检查，Julia 已热）。
- RDS 链路：`adapt_pysr_expression` → `(((2 * x) * x) + (9999999/10000000))`；`compile_hypothesis` + `replay`，容差 `(0, 1/1000)`（绝对 1e-3，给浮点回算用），`CONSISTENT`，tested=10，counterexamples=0。
- 另外两行都被 replay 判 `REFUTED`：常数式与系数 2.3456 过不了 1e-3。也就是说这条链不是“什么都放行”。

最小可复现片段（已验证；cwd 必须是仓库根，`python -B - <<'PY'` 直接粘贴即可）：

```python
import sys

import numpy as np
from pysr import PySRRegressor

sys.path[:0] = ['scripts', 'benchmark/newtonbench']
import rds_compass_model as model
from pysr_adapter import adapt_pysr_expression

x = np.linspace(-2, 2, 10)
y = 2 * x ** 2 + 1
search = PySRRegressor(
    niterations=5, populations=8, maxsize=12,
    binary_operators=['+', '-', '*', '/'], unary_operators=[],
    procs=1, random_state=0, verbosity=0,
    deterministic=True, parallelism='serial',
)
search.fit(x.reshape(-1, 1), y)  # numpy 输入 -> 变量名 x0
best = search.get_best()
equation = str(best['equation'])
print('pysr:', equation, 'loss=', float(best['loss']))

adapted = adapt_pysr_expression(equation.replace('x0', 'x'), ['x'])[0]
print('rds :', adapted['expression'], 'supported=', adapted['supported'])

rows = [{'id': f'p{i}', 'inputs': {'x': model.exact(repr(float(a)))},
         'outputs': {'y': model.exact(repr(float(b)))}}
        for i, (a, b) in enumerate(zip(x, y))]
hyp = model.compile_hypothesis({'id': 'h', 'predict': {'y': adapted['expression']}},
                               ['x'], ['y'])
result = model.replay(hyp, rows, {'y': (model.exact(0), model.exact('1/1000'))})
print('replay:', result['status'], 'tested=', result['tested'])
```

实际输出（2026-10-20，两次运行一致）：

```
pysr: ((x0 + x0) * x0) + 0.9999999 loss= 0.0
rds : (((x + x) * x) + (9999999/10000000)) supported= True
replay: CONSISTENT tested= 10
```

注意 `deterministic=True, parallelism='serial'` 不是可选项：不写它，同参数两次前沿不同（见第 5 节）。

## 3. 噪声数据：1% 噪声、30 点

数据：`x ~ U(-3, 3)` 排序 30 点，`y = 2*x**2 + 1`，加 `uniform(-1%, +1%) * y` 的相对噪声。实测 `max|noise| = 0.1271`、`max relative = 0.00993`，落在 1% 界内。

### 3.1 主跑（niterations=10，maxsize=12）

| complexity | loss | equation |
| --- | --- | --- |
| 1 | 27.925 | `6.8407683` |
| 3 | 22.2244 | `x * x` |
| 5 | 0.4573 | `(x * x) / 0.4586226` |
| 7 | 0.00171223 | `((x * x) + 0.5048182) * 1.9919089` |
| 9 | 0.00157036 | `((x + -0.0035021522) * (x * 1.9916887)) + 1.0056372` |
| 11 | 0.00157035 | `(((x + -0.0035021522) * 3.2870023) * (x * 0.6059286)) + 1.0056372` |

RDS replay（容差 `(0, 1/100)`，相对 1%，按噪声生成机制预先定）：

| 候选 | replay | 反例/测试点 |
| --- | --- | --- |
| 真值 `2*x*x + 1`（对照） | **CONSISTENT** | 0/30 |
| C=1 常数 | REFUTED | 29/30 |
| C=3 `x*x` | REFUTED | 30/30 |
| C=5 `(x*x)/0.4586` | REFUTED | 26/30 |
| C=7 `((x*x)+0.5)*1.99` | REFUTED | 3/30 |
| C=9 及更高 | REFUTED | 4/30 |

真值能过、全部 PySR 候选过不了，说明容差没有被人为放宽；同时说明 1% 是噪声带上界，拟合残差必然略有越界。**在噪声数据上 replay 只是证伪器**，`CONSISTENT` 才有讨论价值，而这里一条候选都没拿到。

### 3.2 加长搜索（niterations=30，populations=15，maxsize=15，2.55 s）

loss 在 C=7 为 1.7122e-3，C=9 跌到 1.5703e-3（改善 8.3%），此后 C=9→C=15 只再降 1.2e-8（0.0008%），进入平台。也就是说，C=9 之后多出来的复杂度没有换来可分辨的拟合改善，全部花在不可识别的改写上：

- C=9 `((x - 0.0035) * (x * 1.9917)) + 1.0056`：凭空引入 `x` 的平移，展开后线性项约 `-0.00698x`，真值里这一项是 0；
- C=11 显式写出 `- 0.00698x`，同一个噪声过拟合项；
- C=13/C=15 把系数拆成 `1.487 * 1.339`（乘积 1.9917）、把常数拆成 `0.369 + 0.636`，C=15 相对 C=9 的 loss 只再降 1.2e-8。

这正是“前沿里出现错误结构”的实际形态：**误差平台上的等价改写和虚假低阶项**，不是更复杂的真实机制。

### 3.3 容差敏感性（只作敏感性带，不作主判据）

同一批前沿改用相对 2% replay：C=7 起变为 `CONSISTENT`（反例从 3/4 降到 0），C=1/3/5 仍 `REFUTED`；真值在 1% 与 2% 下都 `CONSISTENT`。噪声是按 ±1% 生成的，所以 2% 只能说明“这些结构落在一倍噪声余量内”，不能反过来当作它们被数据支持的证据。

## 4. 方言与适配边界（实测 `pysr_adapter.py`，sha256 `23c3c43b…`，提交 `27e6f71`）

| 输入 | 结果 |
| --- | --- |
| `c**1.3` | 拒绝：`exponent 13/10 denominator 10 exceeds 8`（与文档第 5 节的实际兼容问题一致） |
| `c**1.3` + `suggest_equivalent=True` | 追加等价改写 `exp((log(c) * (13/10)))`，带定义域 notes，不是静默替换 |
| `x**0.5` | 支持，转成 `(x ** (1/2))` |
| `x**2` / `x**3` | 支持，原样进 RDS AST |
| `exp(c)` | 支持 |
| `x % 2` / `floor(x)` | adapter 放行，且 `compile_hypothesis` 能编译——`%` 确实在 RDS 方言内，不是漏网算子 |

## 5. 风险与边界

- PySR 的 loss 是 L2（MSE）最小化；RDS replay 是逐点相对/绝对容差判定。两者不是同一指标，不能拿 loss 排名冒充 replay 结论。
- `replay` 返回的 `scientific_support` 恒为 `UNKNOWN`；`CONSISTENT` 只表示这些数据行没有反驳该式。
- 本次容差都是先生成机制、后看结果：clean 用绝对 1e-3（浮点回算），噪声用相对 1%（噪声生成界），中途没有因为候选被反驳而放宽。
- 常数经有符号有理数精确重算，7–8 位浮点常数的精度在 1% 噪声下不是瓶颈；瓶颈是结构不可识别。
- 全程无外部 API、无模型调用；唯一的网络访问是 juliacall 首次安装 Julia 包。
- **搜索结果默认不可复现。** `random_state=0` 单独设置时，同数据同参数两次跑出的前沿不同（一次 4 行、一次 3 行，常数也不同）；PySR 自己会警告要同时开 `deterministic=True` 和 `parallelism='serial'`。实测这两个开关都打开后，两次前沿逐字相同。接入 RDS 回执时若不开确定性模式，绑定的“前沿”本身每次都在变。

## 6. 下一步

1. 把 smoke 脚本从临时目录收进 `benchmark/newtonbench/`（本次写权限只给了这一个 md，脚本入库需 lead 决定）；入库版本固定 `deterministic=True, parallelism='serial'`。
2. 噪声数据改用预注册统计判据（残差 vs 1% 噪声模型、参数置信区间）作为主评价，replay 负责证伪和产反例；把 C=7/C=9/C=15 明确标成“loss 平台、不可区分”。
3. 全链路验证还差 rank：本实验 1% 容差下候选全被反驳，rank 若要在这批候选上给区分点，需要一个不依赖 replay 通过与否的口径（例如按噪声模型的后验预测差异）。
4. `pysr_env_report.md` 已过时（记录“未安装”），建议由 lead 决定更新或删除；本次未动它。
