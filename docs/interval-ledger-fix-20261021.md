# 区间测量的账本往返修复（2026-10-21）

分支 `release/6.0.0-compass`，基点 `c319401`。缺陷：区间测量写进账本再读回时半径消失，判定从 `UNTESTED` 翻成 `CONSISTENT`。只改了 `scripts/rds_compass_model.py` 与 `scripts/rds_compass.py`，未 commit、未 push。

## 1. 改动清单（带行号）

### `scripts/rds_compass_model.py`

| 行 | 改动 | 作用 |
| --- | --- | --- |
| `72-74` | `Inexact.__reduce__` 改用 `_envelope(self)` 取中点与半径 | 深拷贝/pickle 不再经过 `self - radius`（那会掉进新算术，语义上无意义） |
| `78-83` | `__add__`，`__radd__ = __add__` | 加/减的半径相加 |
| `85-93` | `__sub__`、`__rsub__` | 同上，反序时把操作数调过来 |
| `95-100` | `__mul__`，`__rmul__ = __mul__` | 保守乘积包络 |
| `102-108` | `__truediv__`、`__rtruediv__` | 走 `_quotient`，分母含零抛错 |
| `111-114` | 新增 `_envelope(value)` | 统一取 `(中点, 半径)`：`Fraction` 取 `numerator/denominator` 与 `getattr(value,'radius',0)`，其余走 `exact`、半径为 0 |
| `117-119` | 新增 `_carry(mid, radius)` | 半径非零返 `Inexact(mid-radius, mid+radius)`，半径为零返普通 `Fraction`（与 `text` 的退化口径一致） |
| `122-135` | 新增 `_quotient(a, ra, b, rb)` | 分母区间含零抛 `DomainError`；否则用四角端点求包络（见 §3 的偏离说明） |
| `194-208` | 新增 `spell(value)` | 保形序列化，见 §2 |
| `486-487` | `_enclose` 的 `var` 分支先用 `point = Fraction(value.numerator, value.denominator)` 再加减半径 | 端点本身是点；否则 `value - radius` 会走新算术，端点被重新包成 `Inexact`，区间在表达式内部越算越宽 |

### `scripts/rds_compass.py`

| 行 | 改动 |
| --- | --- |
| `237` | 写账本：`outputs = {k: model.spell(v) for k, v in sorted(data['outputs'].items()) if k in plan['observables']}` |
| `728-729` | `_fits_memo_key` 的 `rows`：`inputs` 用 `canonical({k: model.spell(v) ...})`，观测值用 `model.spell(...)` |
| `949` | `packet()` 证据里的 `observable` 用 `model.spell(...)` |

源码净增：`scripts/rds_compass_model.py` +84 / −1，`scripts/rds_compass.py` +4 / −4，合计净增 84 行（上限 150）。新增测试文件 262 行不计入该预算。

## 2. `spell` 的语义

`text` 只写中点：`exact({lo:'7/10', hi:'11/10'})` 输出 `'9/10'`，读回只能是点值。`spell` 是把「测量」而不是「数值」写成文本的那一层：

- 先 `exact(value)`，与 `text` 同一入口（所以 `spell` 对 `{'lo','hi'}` 输入、`str`、`int`、`float` 的处理和 `text` 完全一致）；
- `radius = getattr(value, 'radius', 0)`，为零就直接返回 `text(value)`：点值路径**逐字符不变**，退化区间（`lo == hi`）也是点值文本而不是映射；
- 非零则返回 `{'lo': text(mid - radius), 'hi': text(mid + radius)}`。`exact` 已经能解析 `{'lo','hi'}`（`scripts/rds_compass_model.py:85` 起），所以读写闭合。

往返性质：`exact(spell(v)) == v` 且半径相同，对任意 `v` 成立。写出的都是精确有理数文本，没有浮点。

## 3. `Inexact` 四则运算的公式

记 `a, r_a` 为左操作数的中点与半径（普通 `Fraction`/`int` 视为 `r = 0`），`b, r_b` 同理：

- 加：`mid = a + b`，`radius = r_a + r_b`
- 减：`mid = a - b`，`radius = r_a + r_b`
- 乘：`mid = a * b`，`radius = |a|·r_b + |b|·r_a + r_a·r_b`
- 除（仅当 `|b| > r_b`，即分母区间不含零）：`mid = a / b`，包络取四个角点
  `{(a±r_a)/(b±r_b)}` 的 `min/max`，`radius = max(mid - min, max - mid)`

**这里偏离了任务给的除法公式，理由如下。** 任务规格是 `radius = (|a|·r_b + |b|·r_a) / (b² - r_b²)`，它是一阶相对误差传播，只在半径远小于数值时保守。反例（已写进测试用例，`('−2', '−1', '−3', '−2')`）：`a = [-2,-1]`（中点 `-3/2`，半径 `1/2`），`b = [-3,-2]`（中点 `-5/2`，半径 `1/2`）。规格公式给出 `radius = (3/4 + 5/4)/6 = 1/3`，上界 `3/5 + 1/3 = 14/15`；而 `a = -2, b = -2` 时 `a/b = 1`，超出 `1/15`。包络漏值的方向正是「把一个未定的格子判成已定」，与本 P0 要防的翻转同类，所以实现改用端点包络。`_quotient` 的 docstring 里写了同一个反例。若维护者坚持规格公式，需要接受这个反例，改回去只需替换一行。

半径为零时 `_carry` 返回普通 `Fraction`，与 `spell`/`text` 的退化口径统一；这也让 `a - b` 在两个点值上仍是点值，不额外引入 `Inexact`。

## 4. 五条测试与实际运行结果

新文件 `tests/test_rds_compass_interval_ledger.py`，17 个用例。

1. **账本往返不翻转判定**（`LedgerRoundTripTests`，2 条）
   `test_in_memory_replay_leaves_the_cell_undecided`：同一测量在内存里 replay 得 `UNTESTED`，`(tested, undecided) == (0, 1)`。
   `test_the_ledger_keeps_the_enclosure_the_verdict_kept`：真实 campaign 跑完 `manifest → register → execute → ingest → fold → measured → replay`。断言账本里 `state['observations']['x0']['outputs']['y'] == {'lo': '7/10', 'hi': '11/10'}`（不是 `'9/10'`），`measured()` 读回的是 `Inexact` 且 `radius == 1/5`，最终 `status == 'UNTESTED'`、`(tested, undecided) == (0, 1)`。**反向验证**：把 `scripts/rds_compass.py:237` 临时改回 `model.text`，该用例红：`AssertionError: '9/10' != {'lo': '7/10', 'hi': '11/10'}`，exit code 1；恢复后 sha256 `7992d2d93f363e0880bd6f6e69cfa1acbba5e6166a857812e05616dba52977f6` 与改前一致。
2. **缓存键不碰撞**（`FitsMemoKeyTests`，2 条）：`{'lo':'7/10','hi':'11/10'}` 与 `'9/10'` 的 `_fits_memo_key` 不同；同一区间两次算出的 key 相同。
3. **点值路径零变化**（`PointValueRegressionTests`，2 条）：照抄 `tests/test_rds_compass_interval.py:18-38` 的六个基线值，`exact` 的结果类型、值、无 `radius` 三项断言不变；`spell` 对点值返回 `str` 且 `exact` 可回归。
4. **`Inexact` 算术保守**（`EnclosureArithmeticTests`，5 条）：10 组区间（含负数、跨符号、真分数、大于 1、区间宽度大于数值），每组在 `[lo, hi]` 上取 5 个采样点（含两端点）做 25 个组合，验证减、加、乘、除的包络包含全部结果；另加「普通操作数退化半径不变」与「零半径返回 `Fraction`」。
5. **除以含零区间抛 `DomainError`**（`DivisionByAnEnclosureContainingZeroTests`，3 条）：跨零分母（`[-1/2, 1/2]`）在被除、自除两种方向都抛；只贴零的 `[0, 1/2]` 也抛；分母远离零时正常，且分子跨零不算错。

另有 `SpellingTests`（3 条）：区间往返闭合、退化区间返回点值文本、非法输入仍 `ValueError`。

运行结果（全部实跑）：

```
python -B -m unittest discover -s tests -p 'test_rds_compass_interval*.py' -v
Ran 33 tests in 0.625s — OK

python -B -m unittest discover -s tests -p 'test_rds_compass_model.py'
Ran 27 tests in 0.127s — OK

python -B -m unittest discover -s tests -p 'test_rds_compass_drive.py' -k LawTests
Ran 13 tests in 76.768s — OK
```

追加一次全量回归（不属于验收，只为确认账本里 `outputs` 的类型从 `str` 变成 `str | dict` 没波及别处）：

```
python -B -m unittest discover -s tests -p 'test_rds_compass*.py'
Ran 209 tests in 945.190s — OK
```

没有改动任何现有测试的断言；`tolerance()`、`_tolerances`、`decide`、`replay` 一个字没动。

## 5. 还没覆盖的漏径

1. **`packet()` 证据里的 `inputs` 仍用 `text`**（`scripts/rds_compass.py:948`）。本批只按要求改了同一行的 `observable`。带区间的输入在证据包里仍显示中点。
2. **`residuals`**（`scripts/rds_compass.py:347`）和 **verification 的 `left/right`**（`scripts/rds_compass.py:1301-1304`）用 `text` 输出，区间在报告层仍退化成中点。判定不受影响（replay 走的是 `measured`），但人读的报告会少一位。
3. **`scripts/rds_compass.py:857`**：`derived` 路径的取值拼装，仍是 `text`。
4. **`scripts/rds_compass_declaration.py:154/164/195/206/372`、`scripts/rds_compass_antiu.py:225/245/363`、`scripts/rds_fit_trend.py:64/70/71/79`**：声明与外部工具的序列化仍用 `text`。这几处不在本任务范围，未验证它们是否真的会收到 `Inexact`；如果会，同一条往返洞在那里仍然打开。
5. **`__neg__` 与 `__abs__` 没有重载**：`abs(Inexact)` 与 `-Inexact` 返回普通 `Fraction`，半径静默消失（实测 `abs(exact({'lo':'-1/10','hi':'1/10'})) == 0`，真值包络是 `1/10`）。目前没有消费点：`decide` 里 `abs(predicted-observed)` 的跨零误差由 `radius` 项补回，`_enclose` 已改成先取点再加减，`targets = [t - o]` 走的是 `__rsub__`。但只要有人写 `abs(interval)`，包络就掉。要修得同时让 `decide` 的比较感知半径，那是另一个 slice，不在本次范围。
6. **`<`、`<=`、`>`、`>=` 仍用中点比较**：`Inexact` 比较走 `Fraction` 的数值比较，半径不参与。因此在 `decide` 之外任何直接比较区间的地方都是「用中点下结论」。这是 `Inexact` 的既有设计，本次未动。
7. **账本里 `outputs` 的值类型从 `str` 变成 `str | dict`**：`measured()`、`confirmations()`、report、packet 都能吃，读数与全量 `test_rds_compass*.py` 均绿；但任何外部脚本若假设 `outputs[k]` 一定是字符串，会在带区间的测量上读到 dict。没有第二处账本，也没有 schema 版本号跟着升（`_observation` 的字节绑定没变，因为 `outputs` 本来就在声明侧序列化），这一点值得在 changelog 里提一句。
8. **`Fraction(Inexact)` 与 `ValueError` 的其它转换**：显式构造会掉半径。仓库里目前只有 `_enclose` 与 `_envelope` 两处显式构造，都是取点，属于预期行为；外部代码没有这层保护。
9. **`samples` 仍是 `text`**（`scripts/rds_compass.py:241`）：`samples = {str(k): model.text(v) for k, v in sorted(data['samples'].items())}`。一条 sample 若本身是区间，写进账本就只剩中点。它不进 `measured` 的判定，但会进拟合与展示，是同一条洞的采样支路。

## 6. 总判断

这个修复让区间测量在「测量 → 账本 → 读回 → 判定」这条链上可往返：写进去的是边界，读回来的是同半径的 `Inexact`，同一份数据在内存里和在账本上得出同一个 `UNTESTED`，缓存键也不再把它和它的中点混为一谈。边界在账本与判定之外：声明、外部工具、报告、证据包的展示层仍有若干 `text` 出口，`abs`/`-`/比较这三处仍是中点语义，区间一旦离开 `measured → decide` 这条路径，半径就还可能在别处掉；要彻底闭合得按 §5 逐处换 `spell` 并另做一个包络比较的 slice。
