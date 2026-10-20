# L1-a P0 崩溃修复：EXPR 洞的裸字符串节点

- 仓库：`D:\Myfile\work space\RDS\rds-357`
- 分支 / HEAD：`release/6.0.0-compass` @ `6bf1efa`
- 改动文件：`scripts/rds_compass_antiu.py`、`tests/test_rds_compass_antiu.py`（未 commit、未 push）
- 未触碰：`scripts/rds_compass.py`、`MIN_RATIO`、`test_ratio_gate_only_opens_at_max_nodes`、`_sigma_bits` / `_node_bits` 量纲配平

## 1. 复现

修前（`python -B -c "..."`，HEAD `6bf1efa`）：

```python
import sys; sys.path[:0] = ['scripts', 'tests']
import rds_compass_antiu as A
import rds_compass_model as model
mk = lambda t: model.normalize(model.parse(t), None, limit=64)
A._mdl(A.generalize([mk('2*x+1'), mk('2*x-1')]))
```

```
template: "$E1" holes: [{'name': '$E1', 'kind': 'EXPR', 'max_nodes': 5}]
Traceback (most recent call last):
  File "<string>", line 8, in <module>
    print(A._mdl(op))
  File "rds-357\scripts\rds_compass_antiu.py", line 165, in _mdl
    nvars = max(1, len(set(model.variables_of(operator['template_ast'])) | _members(holes)))
                          ~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "rds-357\scripts\rds_compass_model.py", line 172, in variables_of
    return set().union(*(variables_of(arg) for arg in node.get('args', ())))
AttributeError: 'str' object has no attribute 'get'
```

诊断报告对根因的判断完全成立：`_nary()` 的 EXPR 分支返回 `_hole()` 产出的裸字符串，`_mdl()` → `variables_of()` 撞上 `str.get`。`run()` 的 `admit()`（`:185` 首行调 `_mdl()`）无 try/except，整脚本以 AttributeError 退出。

修后，同一命令：

```
template: {"expr":"$E1"}
holes: [{'name': '$E1', 'kind': 'EXPR', 'max_nodes': 5}]
sigmas: [{'$E1': <2*x+1 的 AST>}, {'$E1': <2*x-1 的 AST>}]
mdl: {'template_bits': 15, 'residual_bits': [48, 48], 'support_bits': 18,
      'standalone_bits': [48, 48], 'ratio': 0.744186}
```

`admit()` 对该样例返回 `False`（ratio 0.744 < MIN_RATIO 1.5），属门限拒绝，不再是崩溃。

## 2. 修法：方向 A（EXPR 洞包成 dict 节点），键名用 `expr` 而非 `var`

`rds_compass_antiu.py:48`：

```python
return {'expr': _hole(holes, 'EXPR', max_nodes=max(model.size(node) for node in nodes))}
```

理由：

1. `_refill()` / `_book()` / `instantiate()` / `_node_bits()` 全都按 dict 走，包成 dict 后整条链口径统一；方向 B 只在 `_nary()` 出口补丁，`_node_bits` 对 `{'var': '$E1'}` 会走 `_var_bits` 分支、算出错误的位宽。
2. **不用 `{'var': '$E1'}` 这个形状**：`model.variables_of()` 见到 `'var'` 就把名字计入变量表，`$E1` 会被当成一个变量名，把 `_mdl()` 的 `nvars` 抬高、进而改变 `_var_bits` 与 `_sigma_bits` 的配平。实测修复后 `model.variables_of({"expr":"$E1"})` 返回 `set()`，`nvars` 只由 `_members(holes)` 里的真实成员贡献。
3. EXPR 洞在语义上就是「整个子树」，和 VAR/CONST 那种「叶子名字」不是同一类，独立键比借用 `'var'` 更不容易在下一次改动里被误读。

顺带修掉的连带点（同一链上的第三、四、五处）：

- `_refill()`（`rds_compass_antiu.py:56-57`）：加 `elif 'expr' in pattern` 分支，绑 `sigma[hole] = node`。原代码对裸字符串走 `isinstance(pattern, str)` 分支能跑，改 dict 后必须补这条，否则会落到 `for slot, arg in zip(...)` 空转、`sigma` 缺键。
- `instantiate()`（`:82-83`）：同上，`'expr'` 洞直接返回 `sigma[hole]`（整个子树）。
- `_node_bits()`（`:129-130`）：`'expr'` 节点按 `OPCODE_BITS + gamma(zigzag(max_nodes+1))` 计价，与原来裸字符串分支的数值完全一致（回归测试里 ratio 0.744186 可与手算对上：15 + (48+48) + 18 = 129，96/129 = 0.744186）。
- `_mutants()`（`:223`）：`walk()` 的叶节点判据补了 `or 'expr' in current`。不改这里的话，含洞模板会往下走 `current['args']` 抛 KeyError —— 这是修成 dict 之后**新暴露**的路径，不是旧 bug。

`_hole()`（`:23-27`）本身没动：VAR/CONST 洞是嵌在 `{'var': ...}` / `{'const': ...}` 里的名字字符串，语义正确，改动它会连带改 `_refill` 的后缀判断。

## 3. `git diff` 摘要

```
 scripts/rds_compass_antiu.py    | 12 ++++++++++--
 tests/test_rds_compass_antiu.py | 20 +++++++++++++++++++-
 2 files changed, 29 insertions(+), 3 deletions(-)
```

`scripts/rds_compass_antiu.py`：5 处（`_nary` 返回值、`_refill` 新分支、`instantiate` 新分支、`_node_bits` 新分支、`_mutants` 判据），净 +9 行。

`tests/test_rds_compass_antiu.py`：

- `:48` `test_arity_mismatch_becomes_an_expression_hole` 的断言从 `'"$E1"'` 改为 `'{"expr":"$E1"}'`。这条断言的**唯一内容**就是那个裸字符串形态本身，属于 bug 的一部分；hole 声明那行（`[{'name': '$E1', 'kind': 'EXPR', 'max_nodes': 5}]`）原样保留，洞的种类和 `max_nodes` 口径没变。
- 新增 `AdmitTest.test_a_root_expression_hole_does_not_crash_the_mdl`（19 行）：跑 `A._mdl(A.generalize([mk('2*x+1'), mk('2*x-1')]))`，断言模板为 `{"expr":"$E1"}`、`variables_of` 为空、`_refill` 绑到整个子树、返回 dict 含 `ratio` 且 `ratio == 0.744186`、`instantiate` 能精确还原每个 case、`admit()` 不抛。

## 4. 测试结果

```
python -B -m unittest discover -s tests -p 'test_rds_compass_antiu.py' -v
----------------------------------------------------------------------
Ran 16 tests in 0.003s
OK
```

修前 15 条全绿；修后 16 条（原 15 + 新增 1）全绿。中间曾红过 1 条，是上面那条钉住裸字符串形态的断言，按第 3 节说明更新。`test_ratio_gate_only_opens_at_max_nodes` 未改、仍通过。

## 5. 第三处同类裸字符串路径：没有

`grep -n "isinstance(.*str)"` 在 `scripts/rds_compass_antiu.py` 里只有 5 处（`:51` `_refill`、`:80` `instantiate`、`:127` `_node_bits`、`:223` `_mutants`，加新增的 `'expr'` 分支）。这四处现在都只是**向后兼容的防御分支**，模板里已不存在裸字符串产出点。

`model.variables_of` 的另两个调用点（`scripts/rds_compass.py:781`、`:1036`）喂的是 `model.normalize` 产出的真 AST，也碰不到洞。全仓 `grep '\$E1|EXPR'` 除本文件与测试外，命中的都是 `MAX_EXPRESSION_BYTES`、`NO_EXPRESSION` 这类无关标识符。

## 6. 遗留（不在本次范围）

- `rds_compass_antiu.py:114` `_name_count()` 里有一行 `set(...) if False else set()` 的死代码，`nvars` 实际上只由 `_members(holes)` 决定，真实 AST 里的变量名没被计进去。这会直接影响 `_var_bits`，和下一步的量纲配平是同一件事，本次未动。
- `_sigma_bits` 对 EXPR 洞按 `max_nodes` 计价，但 `_node_bits` 对同一个洞的模板侧也按 `max_nodes` 计价，两侧口径是否互补留给量纲配平那一步。
- `admit()` 的 `_mdl()` 调用仍无 try/except。修完这处后主路径不再抛，但若将来模板里再出现非法节点，仍是崩溃而非 fail-closed 的 `False`。按约束本次未改。
