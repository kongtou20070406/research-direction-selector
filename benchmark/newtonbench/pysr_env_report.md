# PySR 环境检查报告（rds-357 本机）

- 检查时间：2026-10-20（Asia/Shanghai）
- 工作区：`C:/Users/XU/rds-357`
- Python：3.13.5

## 1. 探测命令与结果

| 命令 | 退出码 | 输出 |
| --- | --- | --- |
| `python -c "import pysr; print(pysr.__version__)"` | 1 | `ModuleNotFoundError: No module named 'pysr'` |
| `python -m pip show pysr` | 1 | `WARNING: Package(s) not found: pysr` |
| `python -c "import sympy; print(sympy.__version__)"` | 0 | `1.14.0` |

补充探测（用于判断依赖缺口，非任务要求项）：

- `julia --version`：退出码 127，本机 PATH 中无 Julia。
- `pip list` 中相关包：numpy 2.2.6、scipy 1.18.1；无 pysr / gplearn / julia 相关包。

## 2. Smoke 测试

未执行。原因：pysr 未安装，任务约束为「不可用则明确写不可用原因，不要安装」，故拟合 y = 2*x**2 + 1 的最小 smoke 跳过，无命令、耗时与输出公式可记录。

## 3. 结论

- **PySR 本机不可用**：未安装，且其必需的后端 Julia 也不在 PATH 中。
- 版本信息：pysr 无（未安装）；sympy 1.14.0 可用；Python 3.13.5；numpy 2.2.6 / scipy 1.18.1。

## 4. 可否用于 RDS 适配

当前**不能**。NewtonBench 目录内尚无任何 pysr / symbolic-regression 引用（已 grep 确认），RDS 侧也没有现成适配代码；在 pysr 缺席的情况下，任何依赖符号回归的候选工具接入都无法进入 execute 阶段。

## 5. 下一步需要什么

1. 由有权限的环境维护者执行安装（本任务未授权安装，未动任何包）：
   - `pip install pysr`
   - 首次使用还需 `python -c "import pysr; pysr.install()"` 拉取 Julia 后端依赖（Julia 本身可由 pysr 的 installer 处理，或先单独安装 Julia 并加入 PATH）。
2. 安装后需验证 Python 3.13 兼容性（pysr 对高版本 Python 的支持以官方发布说明为准），再重跑本报告第 2 步的 smoke（10 点拟合 y = 2*x**2 + 1，iterations=5）。
3. 若短期内无法引入 Julia 依赖，可评估纯 Python 替代（如 gplearn），但那是另一个候选工具，需单独立项比较，不属于本次环境检查范围。
