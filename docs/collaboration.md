# 协作约定（2026-10-20）

三个 session 共享同一工作区（`D:\Myfile\work space\RDS` 与 `C:\Users\XU\rds-357`）。

| session | 角色 | 写作用域 |
| --- | --- | --- |
| 本 session（lead） | 判断、评估、核对、汇总、与用户交互 | `docs/audit-verify-*.md`、`docs/*-plan.md`、`benchmark/` |
| `session-6132bb26` | 修复 | `scripts/`、`tests/` |
| `session-6ae93536` | 开发 | 按任务分配，优先 `docs/`、`benchmark/` |

## 规则

1. **写前先读**：改 `scripts/` 前先看 `docs/audit-verify-*.md` 与 `docs/pulse-review.md` 的核对结论；被证伪的条目不要修。
2. **不重叠**：本 session 不直接改 `scripts/`；修复方只改 `scripts/`/`tests/`，不动 `docs/` 的评估文件。
3. **commit 带 session 标识**：如 `fix(session-6132bb26): ...`、`docs(lead): ...`，方便对账。
4. **共享工作区无锁**：两边同时改同一文件时，后写者先读最新内容再写；冲突时以 `git diff` 为准，不互相覆盖。
5. **同步点**：本文件的「进度」表，每轮更新。

## 进度

| 日期 | 谁 | 事项 | 状态 |
| --- | --- | --- | --- |
| 10-20 | lead | 审计核对（结构 / 成本 / 表达力三组） | 在跑 |
| 10-20 | lead | P0：VoI 停机、fold 缓存 | 在跑 |
| 10-20 | lead | 问题 4：RSI 去 settrace、放宽双变量 | 在跑 |
| 10-20 | lead | 问题 2：21 条反向边清单 + 破 3 条 | 在跑（交给 audit-verify-structure） |
| 10-20 | session-6132bb26 | DSH 临时目录权限修复 | 在跑 |
