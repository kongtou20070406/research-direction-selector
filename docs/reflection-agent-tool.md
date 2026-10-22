# 反思：评估 RDS 时我把它当成了对手，而不是 skill

日期：2026-10-20。对象：RDS 6.0.0 Compass（PR #357）。

## 事实

这一路我做的是：手工跑 Compass campaign（`project init/drive --compass`），让 agent 只负责写一条 law，然后在 NewtonBench / 自造 sparse 任务上比较「RDS 的输出」和「裸 agent 的输出」。

结果：
- NewtonBench 上 RDS 的合成器输给 6.1sol（搜索空间、方言指数分母限制）。
- sparse 任务上修了两个 `regime_split` 的边界条件，10 任务里 3 个正确、2 个假认证、5 个无 claim——修复是尾部特化的，不是通用能力。
- 老板两次指出：这是「针对任务的优化」和「RDS 不参与 agent 循环」。

## 根因

仓库本身的定位是 agent skill，不是自动科研器：

- `SKILL.md` 开头写明「Agent pseudocode; not an executable language or a new kernel contract」，普通交互是「问题 → 判别测试 → 结果解释 → 下一步」的科研对话；
- `agents/openai.yaml` 是 agent 的接口声明，`integrations/omp/rds-status.mjs` 是 OMP 的状态钩子，`rds_conversation_hook.py` 在对话里给状态提示；
- RDS 的 CLI 是给 agent 在对话里调用的（discover/bind/init/execute/advance/advisor/compass/rsi），不是给我在外面当黑盒跑。

我把它当成了「和 agent 竞争的 baseline」，于是：
1. agent 被降级成「写 law 的函数」，RDS 的 decide/execute/receipt/verify 全被绕过；
2. 评估口径变成「RDS vs agent」，而不是「agent+skill vs agent」；
3. 修 bug 时盯着 benchmark 任务，做了窄补丁。

## 正确的用法

agent 是主角，RDS 是脚手架：

```text
agent: 提问 / 提出假设
RDS:   discover → bind → init（声明实验、预算、观察量）
agent: 解释为什么要做这个判别测试
RDS:   decide → execute → receipt（冻结执行、保留原始输出）
agent: 读结果、解释、提出下一个假设
RDS:   replay / verify / holdout（独立复核、反例、覆盖）
agent: 决定 claim 还是保留 UNKNOWN
```

评估也应该是：**同一个 agent（6.1sol）、同一类科研任务、同样的 token/实验预算，加载 RDS skill vs 不加载**。指标仍然是独立复核正确率、假认证率、每正确解 token 成本。

## 下一步

1. 用 OMP 加载 SKILL.md，让 6.1sol 在真实科研对话里跑一遍：从 `project discover` 到 `project advance`，观察它卡在哪、哪个返回看不懂、哪个命令它不会用。
2. 修 skill 层摩擦：返回结构对 LLM 友好、错误恢复、命令选择提示。不做 benchmark 特调。
3. 跑「有 skill vs 无 skill」的对照，报告正确率/假认证/成本。
4. 之前的 `regime_split` 修复保留，但不再当作「RDS 超过基线」的证据，只作为通用缺陷修复记录。
