[English](README.md) · 简体中文

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/assets/rds-hero-dark.svg">
  <img src=".github/assets/rds-hero-light.svg" alt="Research Direction Selector" width="100%">
</picture>

# Research Direction Selector (RDS)

[![license](https://img.shields.io/badge/license-Apache%202.0-blue.svg?style=flat-square)](LICENSE)
[![tests](https://github.com/kongtou20070406/research-direction-selector/actions/workflows/test.yml/badge.svg?branch=main&event=push)](https://github.com/kongtou20070406/research-direction-selector/actions/workflows/test.yml)
[![stars](https://img.shields.io/github/stars/kongtou20070406/research-direction-selector?style=flat-square)](https://github.com/kongtou20070406/research-direction-selector/stargazers)
[![v6-legacy](https://img.shields.io/badge/v6%20archive-legacy%2Fv6-lightgrey.svg?style=flat-square)](https://github.com/kongtou20070406/research-direction-selector/tree/legacy/v6)

**面向“一人即科研舰队”的轻量化科研工作流与确定性验收系统。**

</div>

RDS 让一位研究者与**主模型**（负责选题判断、机理推演、论文正文与视觉审美）并肩调度工程与独立审稿协作者（支持 Claude Code、Codex、Antigravity 等任意命令行代理），并用零 LLM token 的确定性脚本把住代码、理论与排版底线。

> **适用范围与劝退提示：** RDS v7 面向开放性科学探索、机器学习机制/理论迭代以及 LaTeX + Python 科研流。它**不是**全自动盲目调参或刷榜工具——每次实验前都需要先和模型一起把可证伪假说和基线讲清楚。

## 快速上手（无需后台钩子即可使用）

RDS 的核心技能与校验脚本都可以独立运行；后台路由与简报钩子**完全可选且默认温和低打扰**：

```bash
git clone https://github.com/kongtou20070406/research-direction-selector.git
cd research-direction-selector

# 在本地运行全套测试与仓库链接检查
python -m pytest -q tests skills/paper-write/tests
python scripts/check_routes.py --repo-only

# 一键初始化新项目（probe / paper / program）
python scripts/bootstrap.py --init-project path/to/your-project --name my_study --mode program --vault path/to/notes
```

日常科研中，你只需用自然语言和主模型对话，后台校验会在对应环节自动把关：

| 你在对话里说的需求 | 后台自动流转的工作 | 交付前的确定性验收 |
|---|---|---|
| “在卡上试试这个新机制” | `research-gate` 查历史禁区；`reference/` 写纯净 PyTorch，可选跑 Proofline 算下界，有空间再进 `engine/` | AST 纯净度 + 前向/反向数值等价 + 同配方多随机种子基线 |
| “让独立审稿人核一下这条证明” | 并发启动只读审稿代理，只收可核验原始证据（不收口头结论） | Lean 4 / SymPy / Python 反例或纯 CPU 探针复核 |
| “写第 X 节 / 切到顶会排版” | `paper-write` 默认按 arXiv 单栏写稿（待补数字用 `% gate-sorry` / `TBD` 登记） | 句式清晰与去 AI 味检查、`theory_gate`、`--layout-check --render-pages` |
| “画一张机制主图” | `paper-figure-craft` 由主模型亲手设计构图与配色（协作者只做只读挑刺） | 符号一致性核对 + 渲染图视觉审查 |

## 三档探索模式（`probe` / `paper` / `program`）

- **`probe`（灵感探针）：** 零配置、用纯 CPU 或原生 PyTorch 快速验证一个核心直觉，不铺重装备。
- **`paper`（专题成文）：** 仅需 `.agents/brief.json` 与 `paper_gate.json` 即可跑通单篇论文，日常产出 arXiv PDF 并用 `% gate-sorry` / `TBD` 追踪待补数字，定稿 `--no-sorry` 时统一清零。
- **`program`（长期科学探索）：** 围绕开放性核心难题推进，借助 `program.json` 命题树、双镜头焦点切换（全局模式 + `focus.note` 局部焦点）与跨项目知识库沉淀长期发现——论文只是探索途中的自然里程碑。

## 核心设计原则

1. **主模型主见与独立判断（反迎合）：** 主模型先基于物理/数学机理形成独立判断，再看协作者输出或回应引导性追问；协作者只交可核验原始证据（测试、反例、Z3/Lean 证书），不替主模型拍板；遇到与已证实禁区或简洁性冲突的方案，主模型坦诚反驳并给出反例或替代路径。
2. **代码纯净度与数值等价（`reference/` 与 `engine/` 分离）：** 核心模型用极简原生 PyTorch 留在 `reference/`（一分钟可逐行对应论文公式）；设备相关的 `Triton`/`CUDA`/`torch.compile` 提速算子隔离进 `engine/`，并强制通过前向与反向数值等价门禁。
3. **理论简洁性与符号绑定：** 正文主定理先给两句直觉，控制 `<=5` 个活跃符号与 `<=5` 步主证明，并通过 `theory_gate.py` 校验 `code_ref` / `code_symbols` 与参考代码一一对应。
4. **温和低打扰的可选钩子：** 可选的 `skill_router.py` 与 `experience_brief.py` 仅在启用时注入紧凑建议型上下文，不强行卡住日常问答，也不要求研究者手输内部命令。

完整设计纲领、研究宣言与脚本命令速查见 [CHARTER.md](CHARTER.md)。

## 推荐搭配的开源生态工具

RDS v7 专注于研究方向、工作流与确定性门禁，推荐与以下两个专精工具配合使用：

- **[Proofline](https://github.com/kongtou20070406/proofline)** — *零 GPU 占用、带 Z3 与 Lean 4 形式化证明的 PyTorch 硬件时延下界工具。* 恰好卡在 `reference/` 与 `engine/` 之间：写任何 Triton/CUDA 核之前，先在 CPU 上算出算子的 `compute`/`memory`/`serial` 理论极限，确认真有优化空间再动手，省下大量盲目优化的时间。
- **[Obelisk](https://github.com/tommy0103/obelisk)** — *跨会话、跨代理的本地 SQLite 原始证据检索引擎。* 与 RDS 的单轮轻量简报和 `handoff.py` 阶段封包互补，在上下文压缩或新开会话后迅速找回历史实验与讨论原文。

## 安装与部署

完整环境前提、可选钩子接入、后台监听、协作者上车与常见排障见 [DEPLOY.md](DEPLOY.md)：

```bash
python scripts/bootstrap.py --apply   # 可选：将技能链接到本地代理目录并安装仓库 git hook
```

## 从 v6 升级说明

如果你需要查阅早期的 RDS v6 代码（`v6.3.0`，包含 `native/`、`rds_models/` 与 `schemas/`），它完整保留在 [`legacy/v6`](https://github.com/kongtou20070406/research-direction-selector/tree/legacy/v6) 分支。RDS v7 用上述更轻、经过实战检验的技能与确定性门禁架构取代了早期厚重的外壳层。

## 参与贡献

欢迎提 Issue、PR 与工作流改进建议，详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 开源协议

本项目基于 [Apache License 2.0](LICENSE) 协议开源。
