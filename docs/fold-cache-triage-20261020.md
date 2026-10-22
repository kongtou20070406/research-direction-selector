# fold 缓存收尾 — 归因与验收

日期：2026-10-20 ｜ 执行：`ws-triage` ｜ 基线：`5c3666d`

## 0. 结论（先说最重要的）

**工作区里不存在「未提交的 fold 缓存代码」，所以本任务没有可提交的对象。**

fold 缓存（`_REPLAY_CACHE`）**已经落地并提交**了：由 `97e133c`（`feat(compass): stop buying when the widest gap matches the closest pair`，Tue Oct 20 17:19:20 2026 +0800）引入 `scripts/rds_compass.py`，而 `97e133c` 是 `c873e9d` 的**祖先** —— 它不但已提交，还已经过了 CI。

handoff §3 说「缓存的**未提交代码**在 `scripts/rds_compass.py` 之外（见 §5）」，但 §5 的表格里没有 fold 缓存相关的任何文件（只有 `rds_compass.py` 的 `_gap_wide_enough`，以及 8 个 upward-edge 文件）。**这句是陈旧的**：写 handoff 的时候缓存确实未提交，之后被 `97e133c` **搭车**提交了 —— 那条的 message 只讲 VoI-bounded confirmation（`spend_budget` 不再填满剩余槽位，regime-double B=24/32 从 24/32 降到 21）与 budget-economy 文档，**一个字没提缓存**，所以 `git log --oneline -- '*fold*' '*cache*'` 也搜不到它，得用 `git log -S '_REPLAY_CACHE'` 才找得着。任务书第 2 条的「如果代码存在」前提因此不成立，走第 6 条的报备路径。

## 1. 证据链

| 问题 | 命令 | 结果 |
|---|---|---|
| 工作区有无未提交改动 | `git status --short` | 5 个未跟踪文件（`docs/ci-failures-triage-20261020.md` 与 4 个 `pareto-after-832a11f-b{12,16,24,32}.json`）；**没有任何 ` M` 行** |
| 有无 stash | `git stash list` | **空** |
| 缓存代码在不在 | `grep -c '_REPLAY_CACHE' scripts/rds_compass.py` | **13** |
| 谁引入的 | `git log --oneline -S '_REPLAY_CACHE' -- scripts/rds_compass.py` | `97e133c` |
| 是否已过 CI | `git merge-base --is-ancestor 97e133c c873e9d` | **YES**（在 `c873e9d` 之前） |
| 该文件是否还有改动 | `git status --porcelain -- scripts/rds_compass.py` | **空** |
| 任务书另两个候选文件 | `find . \( -iname '*fold*' -o -iname '*cache*' \)` | `benchmark/newtonbench/fold_profile.py`（**已跟踪**）、`scripts/rds_proof_cache.py`（proof obligation 的缓存，与 fold 无关）、`tests/test_rds_proof_cache.py`、`tests/test_pr311_proof_cache_workspace_admission.py`；**没有 `fold_cache.py` / `rds_compass_cache.py`** |
| store 层有没有 | `grep -Ei 'fold\|cache\|stale' scripts/rds_compass_store.py` | **无命中** |
| 全历史提交名含 fold/cache | `git log --oneline --all -- '*fold*' '*cache*'` | `e55f3e3`（devset runner + fold profile）、`d48b9c3`、`995e8eb` —— 都是别的 cache；**`97e133c` 不在其中**（提交信息与文件名都没提缓存） |
| **`c873e9d` 快照里也有缓存** | `grep -c '_REPLAY_CACHE' .triage-head/scripts/rds_compass.py` | **13** —— 与工作区同数。这是「缓存不属于任何未提交改动」的铁证 |
| 快照与工作区的 `rds_compass.py` 差多少 | `diff .triage-head/scripts/rds_compass.py scripts/rds_compass.py` | 只有 8 行，全部是 `832a11f` 删闸门那一段，**与缓存无关** |

`97e133c` 的改动面：`docs/budget-economy.md` +51、`scripts/rds_compass.py` +46/−2（`95 insertions(+), 2 deletions(-)`）。

## 2. 已提交实现的审查

实现位于 `scripts/rds_compass.py:149-172`：

```python
# --- Replay cache ---------------------------------------------------------------
# ``ingest`` folds three or four times per call and ``drive`` folds again every
# step, so the same (hypothesis, measured rows, tolerances) replay is recomputed
# many times. Rows are identified by their receipt SHA256s (a receipt binds its
# output bytes). RDS_NO_REPLAY_CACHE=1 disables the cache (A/B profiling).
_REPLAY_CACHE = {}
_REPLAY_CACHE_LIMIT = 4096
_REPLAY_CACHE_DISABLED = os.environ.get('RDS_NO_REPLAY_CACHE') == '1'
_REPLAY_CACHE_STATS = {'hit': 0, 'miss': 0}


def _replay_cached(hypothesis, rows, tolerances):
    """Memoized ``model.replay``; cache and caller never share a replay object."""
    if _REPLAY_CACHE_DISABLED:
        return model.replay(hypothesis, rows, tolerances)
    key = (hypothesis['sha256'],
           tuple(sorted(r['receipt_sha256'] for r in rows)),
           digest({name: (str(tol[0]), str(tol[1])) for name, tol in tolerances.items()}))
    if key in _REPLAY_CACHE:
        _REPLAY_CACHE_STATS['hit'] += 1
        return deepcopy(_REPLAY_CACHE[key])
    result = model.replay(hypothesis, rows, tolerances)
    _REPLAY_CACHE_STATS['miss'] += 1
    if len(_REPLAY_CACHE) >= _REPLAY_CACHE_LIMIT:
        del _REPLAY_CACHE[next(iter(_REPLAY_CACHE))]  # insertion-order eviction
    _REPLAY_CACHE[key] = deepcopy(result)
    return result
```

### 2.1 缓存键是否充分

key 三项：`hypothesis['sha256']`（假设内容哈希）、`sorted(receipt_sha256)`（被测行的 receipt 集合）、`digest(tolerances)`。

- `model.replay` 只读 `row['inputs']`、`row['outputs']`、`row['id']`（`scripts/rds_compass_model.py:550-580`）。`inputs` 由 `env(state, candidate(state, ident))` 派生、`outputs` 由 receipt 绑定的输出字节派生 —— 两者都随之进 key。
- **`row['id']` 不在 key 里，但被 counterexamples 引用**，所以必须问：同一个 receipt 会不会属于两个不同的 `id`？receipt 由 `scripts/rds_cli.py:482` 构造，字段含 `run_id` 与 `plan_id`，而一次 run 只服务一个 candidate → `receipt_sha256 → id` 是单射，`sorted()` 抹掉的顺序信息可由 receipt 集合唯一还原。
- **注意这是审查推理，不是我跑出来的不变量测试**（见 §4 的反向测试与 §6 的 UNKNOWN）。

### 2.2 缓存对象隔离（这条是正确性的关键）

`_replay_cached` 进出都 `deepcopy`（「cache and caller never share a replay object」）。这不是洁癖：`_derive` 拿到结果后会**原地改**它 ——

```python
receipts = {row['id']: row['receipt_sha256'] for row in rows}
for hyp in state['hypotheses'].values():
    for item in (hyp['replay'] or {}).get('counterexamples', []):
        item['receipt_sha256'] = receipts.get(item['id'])
```

(`scripts/rds_compass.py:185-188`) 没有 deepcopy 的话，第二次命中会拿到已被上一次回填过的对象，`receipt_sha256` 会带着前一个 state 的绑定漏出来。**实现做对了。**

### 2.3 缓存是否触碰事件溯源不变量

- 缓存只 memoize `model.replay`（纯计算：`predict` + 比较，无 IO、无时钟、无随机）→ 不写 ledger、不改 `row_sha256`、不改 hash chain。
- `_REPLAY_CACHE_STATS` 只计数，不参与任何判定。
- 淘汰是插入序 FIFO（`del _REPLAY_CACHE[next(iter(_REPLAY_CACHE))]`），有硬上限 4096，无锁、无线程。
- **`scripts/rds_compass.py` 的 `decide` / `verify` 逻辑没有被缓存触碰**：缓存只服务于 `_derive` 的重放，调用点唯一。

## 3. `fold_profile.py` 实测

```
$ python -B benchmark/newtonbench/fold_profile.py
[fold profile] 20 hypotheses x 60 measured rows, 20 _derive rounds each
[fold profile] RDS_NO_REPLAY_CACHE env at import: <unset>
cache off : total   0.439s   per _derive avg    22.0ms
cache on  : total   0.078s   first (cold miss)    25.4ms, warm avg    2.8ms
speedup   :   5.6x total
replays   : hit 380 / miss 20 -> hit rate 95.0% (cache entries 20)
derived states identical, cache on vs off: True
rc=0
```

- **加速比 5.6×**（handoff 记的是 5.8×，同一量级；本轮实测 `0.439s → 0.078s`）。
- **命中率 95.0%**（380/400），与 handoff 一致。
- **`derived states identical, cache on vs off: True`** —— 脚本内置的等价性断言，且 `rc=0` 说明它自己的两条门槛（`hit > 0` 与 `sum(on) < sum(off)`）都过了。
- 冷启动首轮 `25.4ms` 略高于关闭缓存的均值 `22.0ms`，热轮 `2.8ms` —— 加速全部来自命中，不来自「少算」。

## 4. 测试（compass 家族）

命令（任务书指定）：

```
python -B -m unittest discover -s tests -p 'test_rds_compass*.py' -v
```

匹配到 6 个文件：`test_rds_compass_adversarial.py`、`test_rds_compass_benchmark.py`、`test_rds_compass_drive.py`、`test_rds_compass_hard_followup.py`、`test_rds_compass_model.py`、`test_rds_compass_tools.py`。

（结果见下方「测试结果」节，运行中。）

## 5. 结论与动作

- **没有可提交的对象**：fold 缓存的代码已在 `97e133c` 里，工作区干净，`git status` 无 ` M` 行。**本任务不产生 commit**，也不做任何回滚（没有东西可回滚）。
- 任务书第 4 条的验收门槛（`git diff` 确认不碰 `rds_compass.py` 的 `decide`/`verify`）**无对象可验**：工作区对 `scripts/rds_compass.py` 的 diff 是空的。
- 交付改为 §2 的实现审查 + §3 的实测 + §4 的家族测试。

## 6. UNKNOWN

1. **`receipt_sha256 → id` 单射性**只有静态推理（receipt 含 `run_id`/`plan_id`），没有写成断言测试；若将来有「同一 run 服务多个 candidate」的路径，这条会失效。
2. **`_REPLAY_CACHE_LIMIT = 4096` 是否够**：`fold_profile.py` 的规模（20 条目）远未逼近上限，淘汰路径**未在实测中触发过**。
3. **profiling 场景与真实 campaign 的差距**：`fold_profile.py` 是 20 陈述 × 60 行的内存重放，5.6× 是这一规模下的数字，不是端到端 campaign 的加速比。
