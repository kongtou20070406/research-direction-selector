# 开放任务：先回答卡，再用外面的工具

这是普通科研的唯一入口。RDS 仍是插件。工具和 MCP 留在外面，内核只给一张卡，并记下会改变下一步的事实。

## 每轮

1. 读 `compass status`。不要读账本，不要读 `docs/compass.md`。
2. 先用普通话回答卡上的 `question`：当前判断、下一步、什么观察会改变判断。
3. 回答之后，自由使用外面的工具或 MCP。内核不替你选工具。
4. 只记一条：`compass record --note note.json`。一条是一次试验，或一个冲突。
5. 卡上的 `next` 是 `STOP` 时停止。不要改判据来继续。

## 记录

试验：

```json
{"kind": "trial", "id": "t1", "route": "python checker.py", "metric": "0.2", "receipt_sha256": "<64 hex>", "failed": false}
```

冲突：

```json
{"kind": "conflict", "id": "c1", "question": "Which record should the next trial trust?"}
```

`record` 不运行命令，也不调用模型。相同 `route` 再记一次，状态是 `REUSE`：用旧回执，不要重跑。

## 卡

不超过 4096 字节。字段只有目标、判据哈希、最多 5 条试验、最多 1 条冲突、一个问题、未知项和 `next`。
`next` 只能是 `ANSWER`、`RECORD`、`REUSE`、`STOP`。`scientific_support` 恒为 `UNKNOWN`。

没有容差或没有停止规则时，卡是 `STOP`。不要补一个默认值。

## 不做什么

不跑语法合成，不跑 EIG 选点，不跑盆地逃逸，不跑 RSI。那些只属于显式的 `IDENTIFY_LAW` 或 `IMPROVE` 声明。
这张卡不证明科学发现，也不证明比不读卡更准。
