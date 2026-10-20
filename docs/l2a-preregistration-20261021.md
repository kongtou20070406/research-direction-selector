# L2-a 预注册协议：LOW_MARGINAL_EIG 路由

日期：2026-10-21。HEAD：`fd36bfc`。

## 三行

- **提升什么能力**：EIG 枯竭时唤醒 LLM 的路由能力。现状是 `ranking['ranked']` 为空时静默 fall through 到 RIVALS/CONFIRM，花钱买零信息。
- **可证伪信号**：`regime-double` B=24 和 B=32 的 bought 点中位数不增（±0）；`devset` 15 cells 中 `LOW_MARGINAL_EIG` 触发总次数 ≥ 1 且触发后 6 jump 预算内出现非 `NONE_ADOPTED` 的 jump ≥ 1 次。
- **绝对不允许**：任何 cell 新增假认证（GOAL_IDENTIFIED 但全 rows 重放有反例）；`claim_exact` 任何 cell 由 ✓ 变 ✗；路由触发后 5 步内无新 HYPOTHESIS 的比例 > 50%。

## 改动范围

只改 `scripts/rds_compass.py` 的 `decide()` 函数。不改 `rds_compass_drive.py`、不改 `escape()`、不改 `rank_experiments`。

## 改动内容

### 1. 触发点 A（rows >= 2，ranked 为空）

在 `rds_compass.py:492` 的 `if len(rows) >= 2 and can_buy:` 分支内，`ranking = model.rank_experiments(...)` 算完之后、`if ranking['ranked']:` 之前插入：

```python
        if ranking['ranked']:
            # 现状：EXPERIMENT，不动
            ...
        elif (can_buy and plan.get('agent') and state['budget'].get('agent_tokens') == 'AVAILABLE'
                and not _eig_routed_at(state, len(state['observations']))):
            return {'action': 'ESCAPE', 'basin': {**diagnosis, 'trigger': 'LOW_MARGINAL_EIG',
                    'ranked_total': len(ranking.get('excluded', [])),
                    'excluded': ranking.get('excluded', [])[:16],
                    'top_eig_per_cost': None,
                    'median_eig_per_cost': _median_eig(state)}}
```

`_eig_routed_at(state, obs_count)`：检查 `state['decisions']` 中是否已有 `action == 'ESCAPE'` 且 `basin.trigger == 'LOW_MARGINAL_EIG'` 且 `observations == obs_count` 的记录。有则返回 True，防同一证据重复触发。

### 2. rows == 1 入口放宽

在 `if len(rows) >= 2 and can_buy:` 之后新增：

```python
    if len(rows) == 1 and can_buy and not diagnosis['trigger']:
        # 单存活假设：EIG 恒为 0，无需调 rank_experiments
        if (plan.get('agent') and state['budget'].get('agent_tokens') == 'AVAILABLE'
                and not _eig_routed_at(state, len(state['observations']))):
            return {'action': 'ESCAPE', 'basin': {**diagnosis, 'trigger': 'LOW_MARGINAL_EIG',
                    'ranked_total': 0, 'excluded': [],
                    'top_eig_per_cost': None,
                    'median_eig_per_cost': _median_eig(state)}}
        # 不满足路由条件 → fall through 到现状 RIVALS/CONFIRM
```

`_median_eig(state)`：从 `state['decisions']` 中取 `action == 'EXPERIMENT'` 的 `eig_per_cost` 值，算中位数。无历史 EXPERIMENT 时返回 `None`。

## 验收命令

```bash
# 主场景：regime-double
python -B benchmark/newtonbench/pareto.py benchmark/newtonbench/results/pareto-l2a.json

# 回归场景：devset
python -B benchmark/newtonbench/devset_run.py benchmark/newtonbench/results/devset-l2a.json

# 最小反向测试
python -B -m unittest tests.test_rds_compass_drive.LawTests -v
```

## 判定（全部满足才通过）

1. `regime-double` B=24 和 B=32 的 `experiments` 与改前（HEAD `fd36bfc`）相同或更低；
2. `regime-double` 四档 `claim_exact` 保持 32/32；
3. `devset` 15 cells 中 `claim_exact` 保持 12/15；
4. `devset` 中 `LOW_MARGINAL_EIG` 触发总次数 ≥ 1；
5. 触发后 6 jump 预算内出现非 `NONE_ADOPTED` 的 jump ≥ 1 次；
6. 触发后 5 步内无新 `HYPOTHESIS` 的比例 ≤ 50%；
7. `LawTests` 13/13 全绿；
8. 最小反向测试：把路由条件改成恒 False，15 cells 结果与改前逐字节一致。

## 安全项

- 任何 cell 不得由 ✓ 变 ✗；
- 任何 cell 不得新增假认证（GOAL_IDENTIFIED 但全 rows 重放有反例）；
- `I.6.20a` 保持 SEARCH_EXHAUSTED（不奢望 L2-a 解决表达力问题）。

## 证据文件

- `benchmark/newtonbench/results/pareto-l2a.json`
- `benchmark/newtonbench/results/devset-l2a.json`
- `benchmark/newtonbench/results/pareto.json`（基线，不改）
- `benchmark/newtonbench/results/devset-run.json`（基线，不改）
