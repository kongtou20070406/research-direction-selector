# 共享研究库

这里只放需要在合作者之间交换的知识：研究程序 `program.json`、决定记录 `DECISIONS.md`、已有定论的主张背后的小证据（卡片、结果 JSON、Lean 证明），以及自动生成的看板 `BOARD.md` 和变化记录 `CHANGES.md`。训练日志、检查点、PDF 和临时文件不在这里。

内容由 [research-direction-selector](https://github.com/kongtou20070406/research-direction-selector) 工作流里的 `share.py export` 生成，`tools/` 是同一版本的检查和渲染工具。

发布时可重复传入 `publish_shared.py --knowledge <登记文件.jsonl>`，将本次指定的登记文件同步到 `knowledge/`。`KNOWLEDGE.md` 按项目和类型汇总有效条目。

## 第一次使用

需要 Python 3.11 及以上，不需要第三方包。先请项目负责人把你加为这两个私有仓库的协作者。

```bash
git clone <your-shared-repo-url>
cd <shared-repo>
python tools/onboard.py
```

`onboard.py` 会先确认镜像完整，再告诉你：

- 项目的北极星和当前焦点；
- 最近一次同步改了什么；
- 现在可以认领的小任务（已经拆到一张卡、一个证明或一个封闭工程任务就能解决的叶子）；
- 每篇论文的负责人和它还缺的证据。

如果你也要用同一套工作流（论文写作门禁、预检、交接等技能），再装工作流仓库：

```bash
git clone https://github.com/kongtou20070406/research-direction-selector.git
cd research-direction-selector
python scripts/bootstrap.py
```

## 这个项目怎么推进

大问题拆成小问题，小的再拆，直到可以轻松解决；解决后逐层合回去。`BOARD.md` 是当前的拆分树和待办，GitHub 上可以直接看。

- **认领一个叶子**：在 `BOARD.md` 的“可以开工的叶子”里挑一个，开分支 `leaf/<命题编号>-<你的名字>`，在 PR 描述里写清你打算怎么证伪它。
- **交回结果**：不要改 `program.json`（它由导出脚本生成）。新建 `contrib/<命题编号>-<你的名字>/`，放一个 `CLAIM.json`（对应命题、一句话结论、强度、证伪线、署名、证据文件列表）和证据文件，提 PR。CI 会运行 `tools/contrib.py`、`tools/program.py check` 和 `tools/share.py check`，不通过就不能合并。负责人认可后把它并入项目，下一次同步时它会出现在 `program.json` 和 `evidence/` 里，`contrib/` 里的文件夹随之清掉。
- **论文**：每篇论文由一位负责人推进。除非你主动提出并经负责人同意，不参与论文写作；你的结果以主张和证据的形式进入程序，由负责人决定是否写进论文。
- **北极星**：只有项目负责人能改，改动会写进 `DECISIONS.md` 和 `CHANGES.md`。

## 同步

```bash
git pull                 # 拿到别人的进展
python tools/onboard.py  # 再看一遍现在该做什么
```

每次同步只需要读 `CHANGES.md` 最上面一段。
