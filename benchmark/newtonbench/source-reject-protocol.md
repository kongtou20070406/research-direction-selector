# source reject：内核会不会把一条错律收成 claim

制定：2026-10-21，**在跑表之前**写下。错律原文和判据先于数字。

## 0. 问题

`benchmark/newtonbench/source-reach-protocol.md` 量过 reach：外部给出**恰好正确**的 law 时，10 个 size>7 的方程里 6 个确凿救回（2 个对照超时记 UNKNOWN）。那证明内核**能收下**真候选。

没证明的是另一半：内核**会不会拒绝错候选**。`scripts/rds_compass_model.py:997` 的 `consider_seed` 只做一件事——过 `matches` 测量拟合就进 `fits`，跟枚举出来的树同一扇门。已经测过的错律只有一格（`feynman-I.41.16`，分母 `c**2` 改成 `1`，`source-seam-protocol.md` §3）。一格拒了推不出一般拒。

真实生成器不会只给一条正确律。它给一批，其中多数错。所以本协议量两件事：

1. **单错**：只给一条错律，内核不得声称它是解；
2. **混投**：真解和错律同时给，内核必须收真解、拒错律。

## 1. 错律怎么造（跑之前钉死）

每条错律由**真解原文做一次字符串替换**得到。替换规则写在协议里，不由结果挑选。每格必须先验证错律**在声明行上已有反例**；验证不过则该格记 `NOT_APPLICABLE`，**不换规则重试**。

| 方程 | 真解（`results/truth-size.json` 的 `law`） | 替换 | 错律 |
|---|---|---|---|
| `feynman-I.41.16` | `1.054571817e-34*omega**3/(pi**2*2.99792458e8**2*(exp(...)-1))` | `pi**2*2.99792458e8**2` → `pi*2.99792458e8**2` | 分母少一个 `pi` |
| `feynman-II.24.17` | `sqrt(omega**2/2.99792458e8**2-pi**2/d**2)` | `-pi**2/d**2` → `+pi**2/d**2` | 根号内减号改加号 |
| `feynman-I.10.7` | `m_0/sqrt(1-v**2/2.99792458e8**2)` | `m_0/sqrt(` → `m_0/(` | 去掉 `sqrt` |
| `feynman-I.6.20a` | `exp(-theta**2/2)/sqrt(2*pi)` | `/sqrt(2*pi)` → `*sqrt(2*pi)` | 除改乘 |
| `feynman-I.8.14` | `sqrt((x2-x1)**2+(y2-y1)**2)` | `+(y2-y1)**2` → `-(y2-y1)**2` | 加号改减号 |
| `feynman-II.34.11` | `g_factor*q*B/(2*m)` | `/(2*m)` → `/(2*m**2)` | 分母 `m` 改 `m**2` |

替换必须命中：脚本在替换前断言原串存在，否则该格记 `NOT_APPLICABLE`。

## 2. 三臂

B=8，每格独立临时 campaign root，`feynman-I.41.16` 之外不共享任何缓存。

| 臂 | 声明 | 期望 |
|---|---|---|
| `wrong-only` | `sources=[{'id':'preregistered-wrong','laws':[错律]}]` | 不得成为 claim |
| `mixed` | `sources=[{'id':'preregistered-wrong','laws':[错律, 真解]}]` | 收起真解，拒错律 |
| `truth-only` | 已入库 `source-reach-20261021.json` 的 truth-source 臂 | 不重跑，作对照 |

`mixed` 臂里错律排在真解**之前**：如果 `keep` 名额被先到的候选占满，错律会挤掉真解，本协议要能看见这件事。

## 3. 判据

主指标：**6 个方程的 `wrong-only` 臂里，错律未变成 `claim_exact=true` 且未触发 `false_certification=true` 的格数**。门槛 **6 / 6**——这是安全门，不是覆盖率，一格漏掉即整表 FAIL。

每个 `wrong-only` 格必须同时满足：

1. `claim_exact != true`；
2. `false_certification != true`；
3. `certification_check.points_checked > 0`（认证真的跑了，不是 `NOT_CLAIMED` 空转），**除非**该格没产生 claim——此时 `NOT_CLAIMED` 是正确结局，记 `PASS(no claim)`。

`mixed` 臂另判（不计入主指标，单独列）：

- `claim_exact == true` 且报告的 law 与真解在声明行上零反例 → `RECOVERED`；
- `claim_exact == true` 但 law 有反例 → `FALSE_RECOVERY`（整表 FAIL）；
- 未 claim → `NO_CLAIM`（不算失败，但记下来）。

安全项：

- 任一格 `false_certification == true` → 整表作废；
- `feynman-I.27.6` 不在本表（它是 reach 的阴性对照，与拒绝路径无关）；
- 每格 `experiments <= 8`。

## 4. UNKNOWN 规则

单格 900 秒未完成 → 记 `UNKNOWN (TIMEOUT)`，**不计入通过，也不计入失败**。若某格 UNKNOWN，主指标的分母随之减一，并在结论里写出实际分母。

## 5. 成本口径

只记内核墙钟。错律生成成本为 0（是本表按规则字符串替换的），**不得**当作真实生成器成本；本协议不产出任何每正确解成本数字。

## 6. 回滚触发

主指标未达 6/6 且存在非 UNKNOWN 的漏格 → seam 不能当裁判，先修拒绝路径，不接 PySR。本协议只测量，不改内核。

## 7. 这条和 reach 的关系

reach 证明「够不到时靠外部补」，本协议证明「外部补错了不会被收下」。两条都过，seam 才具备一个裁判该有的两面；只过 reach 不够，因为一个只认对的、不拒错的判定器，在真实生成器面前等于没有判定。
