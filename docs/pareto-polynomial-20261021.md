# pareto-polynomial 全量验证（2026-10-21）

预注册：`benchmark/newtonbench/polynomial-receipt-protocol.md`（在本次运行前写下）
对照基线：`benchmark/newtonbench/results/pareto-stage2-costfix.json`

## 0. 结论

修法在当前 `scripts/rds_compass.py` 上生效：主格 `polynomial.status` 从 `POLYNOMIAL_RIVAL_DISAGREES` 变成
`AGREES_WITH_UNIQUE_POLYNOMIAL_ON_DECLARED_EXPERIMENTS`。36 格全量重跑与基线**逐格零差异**，五条安全项全 PASS。
唯一没达成的协议 §2 门槛是「`status`: `JUDGMENT_REQUIRED` → `GOAL_IDENTIFIED`」，原因不是实现，
而是协议 §2 第 1 行与第 3 行互斥——详见 §5。

## 1. 运行

```
cd "D:/Myfile/work space/RDS/rds-357" && python -B benchmark/newtonbench/pareto.py benchmark/newtonbench/results/pareto-polynomial.json
```

- 输出文件：`benchmark/newtonbench/results/pareto-polynomial.json`，30128 字节，`json.load` 读通，`schema=2`，36 行。
- 构造：`benchmark/newtonbench/pareto.py:290-340` 原样未改（`hypotheses=[('lin','x+1')]`、
  `verification={'holdout':2,'confirmations':2,'max_gap':2}`、6 个 budget×3 world）。
- 主格单跑（仓库外脚本 `D:/Myfile/work space/RDS/poly_maincell_probe.py`，构造照抄同一文件的 rds 臂）：
  实测 22.7s，`status=JUDGMENT_REQUIRED`，与全量文件同格一致。
- 运行前工作区已有两处未提交改动：`M scripts/rds_compass.py`（即被验证的修法本身，`_polynomial_check`
  的 `continue` 分支）与 `M benchmark/newtonbench/devset.py`（与本任务无关）。本次没有写任何 `scripts/` 文件，
  也没有 commit/push/checkout/stash；新增文件只有本报告、判定脚本、单跑脚本和
  `results/pareto-polynomial.json`。

## 2. 基线文件未被覆盖（跑前 / 跑后 sha256）

| 文件 | sha256（跑前 = 跑后） |
|---|---|
| `results/pareto.json` | `cad299b801a3e5726890409fa5e57b229c502cd17b917caa72c425909637e9cc` |
| `results/pareto-stage2.json` | `12da476e8c9ca37e2bed03efb60206ac51d1fb56017670c140f2996b77784bc6` |
| `results/pareto-stage2-costfix.json` | `779662ffb905cedae185ff85e5738ad969b8668e8341e03ce2973a6fc69444f4` |
| `results/pareto-confirmations.json` | `a815377f09b0a8d4e215dca97dd0bd822e549b7fef7053caa5c02ac0c8e87dd3` |
| `results/pareto-holdoutfirst.json` | `c55753a5dde4c0d4f721475e543ab478f773508f16c4f1077c8f06923753de59` |

五个文件的 mtime 均早于本次运行（最晚 12:55:29，本次产物 13:02:29），sha256 一字不差。

另：`pareto-stage2-costfix.json` 的 rows 与 `pareto-stage2.json` 的 rows **完全相同**（`rows == rows2` → `True`），
costfix 只改了顶层聚合，与任务预期一致。

## 3. 五条安全项（`docs/pareto-polynomial-20261021-compare.py`）

| # | 门槛 | 判定 | 实测 |
|---|---|---|---|
| 1 | `correct_count` 不降 | **PASS** | rds 14 → 14；全文件 14 → 14 |
| 2 | `false_certification_count` 不增（协议原值 1 指 rds 臂） | **PASS** | rds 1 → 1；全文件 16 → 16 |
| 3 | 无 `GOAL_IDENTIFIED` 降级为 `JUDGMENT_REQUIRED`/`SEARCH_EXHAUSTED` | **PASS** | 全部 36 格逐个比对，`demotions=none` |
| 4 | 新增的 `claim_exact` 行必须 `false_certification=false` | **PASS** | 无新增 `claim_exact` 行（`newly claim_exact rows=none`），空集通过 |
| 5 | `experiments_per_correct` 不超 21.0 的 110%（≤23.10） | **PASS** | rds 21.0 → 21.0 |

## 4. 主格 `sparse-tail / rds / budget=32`

| 指标 | 实测值 |
|---|---|
| `status` | `JUDGMENT_REQUIRED` |
| `claim` | `CONSISTENT_BUT_UNCONFIRMED` |
| `claim_exact` | `0`（`exact_match` 32/32） |
| `experiments` | `32` |
| `law` | `ite((x < (61/2)), (1 + x), 1000)` —— 即 `x+1 if x <= 30 else 1000`，与 truth `x+1 if x != 31 else 1000` 在 x0..31 全等 |
| `polynomial.status` | `AGREES_WITH_UNIQUE_POLYNOMIAL_ON_DECLARED_EXPERIMENTS` |
| `polynomial.degree_bound` / `assurance` | `6` / `CERTIFICATE_CHECKED` |
| `polynomial.points` | `x1, x30, x16, x9, x23, x5, x27, x20`（x31 不在拟合集内，与协议 §0 一致） |
| `polynomial.coefficients` | `[1, 1, 0, 0, 0, 0, 0]` → `1 + x` |
| `novel_confirmations` | `0 / 2` |
| `distinct_live_classes` / `replay` | `1` / `CONSISTENT, tested=32, undecided=0` |

`polynomial` 字段不在 `pareto.py` 的行 schema 里（行只有 status/claim/claim_exact/exact_match/experiments/
claimed/false_certification），所以上表 `polynomial.*` 与 `law` 来自仓库外单跑脚本对同一构造的完整 `verify()`
value；`status`/`claim`/`claim_exact`/`experiments` 与全量文件同格完全一致，两条路径互证。

## 5. 协议 §2 第 1 行为什么不可能达成（实现无误，协议自相矛盾）

`scripts/rds_compass_drive.py:104-107`：

```python
rival = (verification.get('polynomial') or {}).get('status') == 'POLYNOMIAL_RIVAL_DISAGREES'
unique = (verification['claim'] == 'CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS' and not rival
          and verification['distinct_live_classes'] == 1 and verification['receipts_rechecked'])
return finish('GOAL_IDENTIFIED' if unique else 'JUDGMENT_REQUIRED', ...)
```

`verify()` 的 `claim` 只由 `novel_confirmations >= required_confirmations` 决定（`scripts/rds_compass.py:1549-1552`）。
主格 `novel_confirmations=0`、`required_confirmations=2` → `claim` 必然是 `CONSISTENT_BUT_UNCONFIRMED`，
于是 `unique=False`，`status` 必然 `JUDGMENT_REQUIRED`，**与 polynomial 状态无关**。

因此协议 §2 的
- 第 1 行「`status`: `JUDGMENT_REQUIRED` → `GOAL_IDENTIFIED`」与
- 第 3 行「`claim`: **不变**（`CONSISTENT_BUT_UNCONFIRMED`）」

不可能同时成立。协议 §2 第 4 行自己也写明 `novel_confirmations=0` 与 rival 判定无关、不应变化，
所以第 1 行是笔误：这次改动**本来就不会**改变主格 `status`。改前 `status` 也是 `JUDGMENT_REQUIRED`
（基线 `pareto-stage2-costfix.json` 同格即此值），改后不变属于无回归，不属于失败。

（改前 `polynomial.status` 按协议 §0 记为 `POLYNOMIAL_RIVAL_DISAGREES`；本次未重跑旧代码，
因为任务禁止改 `scripts/`、禁止 checkout/stash，故这一条只作背景，不作为本次测量值。）

## 6. `sparse-mid` / `regime-double` 的 rds 臂

两者 budget=32 逐字段无变化（36 格全同的一部分）：

| 格 | status | claim | claim_exact | experiments |
|---|---|---|---|---|
| `sparse-mid/rds/32` | `GOAL_IDENTIFIED`（=基线） | `CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS`（=基线） | 32（=基线） | 32（=基线） |
| `regime-double/rds/32` | `GOAL_IDENTIFIED`（=基线） | `CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS`（=基线） | 32（=基线） | 32（=基线） |

## 7. 36 格逐格差异表

无差异。判定脚本对所有 36 格的 `status`/`claim`/`claim_exact`/`exact_match`/`experiments`/`claimed`/
`false_certification` 逐字段比对，输出 `none`。汇总层同样全等：`rds` 臂与 `baseline-uniform` 臂的
`correct_count`/`false_certification_count`/`experiments_per_correct`/`claimed_count`/`experiments_total` 一字不差。

这是预期结果：改动的唯一可观察效果是 `verify()['polynomial']['status']`，而该字段没有进入 pareto 的行；
主格 `status` 又因 `claim` 未确认而不可能翻转，所以文件层面零差异。若要把这个改进变成文件里的可见证据，
需要 `pareto.py` 的行 schema 增加 `polynomial.status` —— 但那要改文件，不在本次任务范围。

## 8. 复现

```
cd "D:/Myfile/work space/RDS/rds-357" && python -B benchmark/newtonbench/pareto.py benchmark/newtonbench/results/pareto-polynomial.json
cd "D:/Myfile/work space/RDS/rds-357" && python -B docs/pareto-polynomial-20261021-compare.py
python -B "D:/Myfile/work space/RDS/poly_maincell_probe.py" "D:/Myfile/work space/RDS/poly_maincell_probe.json"
```

OVERALL: PASS
