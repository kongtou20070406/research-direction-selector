# RDS v6 Compass on NewtonBench

A pre-registered comparison of RDS v6 Compass against NewtonBench's own baseline agents on the public
NewtonBench law-discovery benchmark ([HKUST-KnowComp/NewtonBench](https://github.com/HKUST-KnowComp/NewtonBench),
MIT, pinned at `912a4ba`). Every arm uses the same model (Codex CLI `gpt-6.1-sol`, reasoning `xhigh`, default
service tier) and the same budget of 40 simulator queries per trial. Only NewtonBench's own evaluator decides
whether an answer is right. The design, the amendments, and every deviation are in
[PREREGISTRATION.md](PREREGISTRATION.md). NewtonBench's source is not part of this repository.

Raw numbers: [results/newtonbench-compass.json](results/newtonbench-compass.json) (NewtonBench commit, RDS
commits, model settings, every trial with its receipt hashes and Codex usage). Per task:
[results/per-task.md](results/per-task.md) (registered) and
[results/per-task-corrected.md](results/per-task-corrected.md) (post hoc, amendment 5).

## Result

**Compass is significantly worse than both NewtonBench baselines.** This holds in the pre-registered analysis and
in the post-hoc corrected analysis that removes a harness defect against Compass (amendment 5, below). In the
corrected analysis, on the 14 laws that Compass's exact-rational dialect can express, Compass shows no
significant difference from the baselines (0.964 vs 1.000, sign test 0 / 1 / 13, 95% CI [-0.107, 0.000]; no
equivalence test was run, and the baselines are at ceiling) and never certified a wrong law. In both analyses,
on the 22 laws it cannot express, Compass's own answer was never right: every time it certified one of them, the
certified law was wrong.

| 36 cells, both noise strata (mean over cells of NewtonBench's judged symbolic accuracy) | Registered (formal run `a341289`, defect included) | Corrected, POST HOC (amendment 5) |
|---|---|---|
| **Compass** accuracy | **0.264** | **0.375** |
| code_assisted accuracy | 0.938 | 0.938 |
| vanilla accuracy | 0.958 | 0.958 |
| **Primary: Compass - code_assisted** [paired bootstrap 95% CI] | **-0.674 [-0.812, -0.528]** | **-0.562 [-0.715, -0.410]** |
| Sign test over cells (+ / - / ties), exact two-sided p | 0 / 26 / 10, p = 3.0e-8 | 0 / 22 / 14, p = 4.8e-7 |
| **Compass false-certification rate** (certified and judged wrong / certified) [Wilson 95% CI] | **80/118 = 0.68 [0.59, 0.76]** | **51/105 = 0.49 [0.39, 0.58]** |
| FCR on expressible cells | 16/54 = 0.30 [0.19, 0.43] | 0/54 = 0.00 [0.00, 0.07] |
| FCR on not-expressible cells | 64/64 = 1.00 [0.94, 1.00] | 51/51 = 1.00 [0.93, 1.00] |
| Compass on the 14 expressible cells vs code_assisted | 0.679 vs 1.000, p = 0.0625 (n.s.) | 0.964 vs 1.000, 0 / 1 / 13, p = 1 (n.s.) |
| Compass on the 22 not-expressible cells vs code_assisted | 0.000 vs 0.898, p = 9.5e-7 | 0.000 vs 0.898, p = 9.5e-7 |
| compass_fallback (Compass answer, or the same model's fallback answer when Compass has no live rulebook) | 0.382 | 0.611 |

**GOAL_IDENTIFIED 只表示"在容差内与收据一致"，不表示科学上被支持。** A certification here says only that the
rulebook agrees with every receipted measurement within the declared tolerance; the false-certification rate
above is that statement's error rate when NewtonBench's judge is taken as the reference. `scientific_support`
stays UNKNOWN throughout.

The originally registered noise-free primary test (36 cells, noise 0 only): Compass 0.278 vs code_assisted 0.972,
-0.694 [-0.833, -0.556], 0 / 25 / 11, p = 6.0e-8 (registered); 0.389 vs 0.972, -0.583 [-0.750, -0.417],
0 / 21 / 15, p = 9.5e-7 (corrected). Noise 0.01 stratum: -0.653 [-0.806, -0.500], p = 1.2e-7 (registered);
-0.542 [-0.694, -0.389], p = 1.9e-6 (corrected). Every comparison is significant against Compass except those
restricted to expressible cells. RMSLE agrees: per-cell median RMSLE is worse for Compass than for code_assisted
in 33 of 36 cells (registered; 1 better, 2 ties) and 32 of 36 (corrected; 0 better, 4 ties).

## 中文摘要

**结论（负面）。** 在 NewtonBench（36 个 cell × 无噪声/1% 噪声两层，每格 2 次 trial，同一模型 gpt-6.1-sol
xhigh，每次 40 次模拟器查询）上，RDS v6 Compass 的符号准确率显著低于 NewtonBench 自带的两个基线。预注册主
分析：Compass 0.264，code_assisted 0.938，差值 -0.674（95% CI [-0.812, -0.528]），符号检验 0 胜 / 26 负 /
10 平，p = 3.0e-8。事后更正分析修复了一个对 Compass 不利的适配层缺陷（见 Amendment 5）后：Compass 0.375，
差值 -0.562（[-0.715, -0.410]），0 / 22 / 14，p = 4.8e-7。结论方向不变。

**为什么输。** 22/36 个规律含有 Compass 精确有理数方言表达不了的函数（exp、log、三角/反三角、非整数幂）。在
这些规律上，Compass 自己的答案正确率是 0；它只要认证了，认证的就是错的（假认证率 51/51 = 100%）：一个在
容差内拟合所有收据的有理函数近似。在 14 个可表达规律上，Compass 与基线无显著差异（更正后 0.964 vs 1.000，
0 胜 / 1 负 / 13 平，95% CI [-0.107, 0.000]；未做等价检验），并且没有发生假认证（0/54）。基线本身已接近
天花板，所以即便在可表达规律上也没有提升空间。

**Compass 真正贡献了什么。** 更正后，Compass 自己的答案正确 54 次（27 个 cell-层），全部在可表达规律上。所有
被认证规律的文本都是 jump agent（同一个 LLM）提出的；Compass 负责选实验、记收据、反驳竞争假设和认证。
NO_LIVE_RULEBOOK 时的回退答案（34/34 正确）是同一模型基于 Compass 收据写的，不算 Compass 效应。

**假认证率与准确率同等重要。** 登记分析 80/118 = 0.68；更正后 51/105 = 0.49；按可表达性拆分：可表达 0/54，
不可表达 51/51。GOAL_IDENTIFIED 只表示"在容差内与收据一致"，不表示科学上被支持。

**稳健性收益与科学推理收益要分开看（事后）。** NewtonBench 的 vanilla agent 不捕获模拟器异常，在 m8 hard v0
上 4 次 trial 因 OverflowError 失败；Compass 把非有限输出记作无观测的收据并继续（更正后 12 次 trial、122 个
非有限结果），没有失败。但这部分稳健性对主比较几乎没有贡献：去掉该 cell 后差值从 -0.562 变为 -0.564。
code_assisted 本身没有失败；在 m8 hard v0 上 Compass 自己的答案也没有一次正确。实验选择与验证带来的收益：
在可表达规律上与基线无显著差异（未做等价检验），整体为负。

**成本。** 更正后，Compass 每次 trial 平均输入 507k token（其中缓存 420k）、输出 35.7k；code_assisted 分别是
79k、6.1k，vanilla 是 42k、4.5k。按每个正确解计：Compass 输入 1.32M、输出 93k；code_assisted 85k、6.5k；
vanilla 44k、4.7k。Compass 平均耗时 1171 秒，基线是 117 到 151 秒。另有 3 个 Compass trial 的 jump 调用
在 540 秒超时时被杀、没有上报用量，记为 UNKNOWN，不做估算。

**局限。** 天花板效应；judge 与被测模型是同一个模型（RMSLE 不受影响）；更正分析是事后的；每格 N = 2；只跑了
vanilla_equation。**下一步：** 给方言加超越函数；认证前做残差结构检验；适配层做输入预检；换更难、基线不在
天花板的任务。

## Amendment 5: a harness defect against Compass, and the corrected analysis (POST HOC)

After the formal run, Compass was found to have certified wrong laws on expressible m6 and m8 cells. Every
noise-free receipt in those trials had the same value. The cause was in this harness, not in Compass: the
Compass experiment program passed each input to `run_experiment_for_module` under its `discovered_law` name.
The vanilla_equation simulators of four modules read other keyword names and silently fall back to defaults:

| Module | Signature name -> simulator keyword | Cells |
|---|---|---|
| `m4_snell_law` | n1 -> refractive_index_1, n2 -> refractive_index_2, angle1 -> incidence_angle | 3 |
| `m6_underdamped_harmonic` | k -> k_constant, m -> mass, b -> b_constant | 3 |
| `m8_sound_speed` | gamma -> adiabatic_index, T -> temperature, M -> molar_mass | 3 |
| `m10_be_distribution` | T -> temperature (omega was read) | 3 |

In those 12 cells (24 tasks, 48 Compass trials), Compass measured a constant or a one-input response. The
baselines were unaffected, because they call the simulator through NewtonBench's own agent with the names its
prompt gives. The pilot modules (m0, m1, m7) read the signature names, so the pilot did not catch the defect.
[results/input-audit.md](results/input-audit.md) (`audit_inputs.py`) lists, for every cell, which inputs the
simulator ignores under the old and the fixed harness. Under the fixed harness, the only ignored inputs are ones
the ground-truth law does not depend on (m0 hard v1 `mass2`, m2 medium v2 `current1`, m8 easy v2 `gamma`).

What was done:

- The registered analysis was left exactly as computed.
- The input names were fixed (`7e9fd63`) and nothing else was changed: same Compass code, design grid,
  tolerance, prompts, budget and model.
- The 48 Compass trials were rerun into `runs/formal-rerun`; all 48 completed.
- The corrected analysis applies the registered analysis to the formal trials with exactly those 48 replaced
  (`analyze.py --rerun-phase`).

Effect on the affected cells: the four expressible ones (m6 easy v1 and hard v1, m8 easy v2 and medium v0) went
from 0/16 to 16/16 correct, all certified. The eight not-expressible ones stayed at 0 for Compass. Across m4,
m8 hard and the noise-free m6 medium, 17 of 18 trials now end SEARCH_EXHAUSTED, and in all 17 the fallback
answer written from Compass's receipts is right. The remaining trials are certified rational approximations that
NewtonBench judges wrong: 10 of the 12 m10 trials (the other 2 stay uncertified), the noisy m6 medium, and one
noisy m8 hard trial. The corrected result is post hoc: it was produced after the registered result was known,
and the rerun ran a few hours after the formal run.

## Where Compass's answers came from (amendment 3 paths)

Corrected set, pooled over both strata (the registered set is in the JSON, `compass_paths`):

| Path | Trials | Compass's own answer correct | Same-model fallback answer correct | Rulebook origin |
|---|---|---|---|---|
| CERTIFIED (GOAL_IDENTIFIED) on expressible cells | 54 | 54 | (= Compass) | jump agent 54 |
| CERTIFIED (GOAL_IDENTIFIED) on not-expressible cells | 51 | 0 | (= Compass) | jump agent 51 |
| LIVE_UNCERTIFIED (JUDGMENT_REQUIRED) | 5 | 0 | 0 | jump agent 5 |
| NO_LIVE_RULEBOOK (SEARCH_EXHAUSTED) | 34 | 0 (no submission) | 34 | - |

In the registered set: CERTIFIED 118 (38 correct, all expressible; 117 from the jump agent, 1 from rival
synthesis), LIVE_UNCERTIFIED 9 (0 correct), NO_LIVE_RULEBOOK 17 (fallback 17/17 correct). Neither set had a trial
end UNTRANSLATABLE, EXCEPTION, TIMEOUT, CRASHED or MISSING.

**What Compass contributed.** In the corrected set, Compass's own answer was correct in 54 trials, which is 27
cell-strata: all 14 expressible cells noise-free and 13 of 14 with noise. Every one of those laws was written by
the jump agent, the same LLM the baselines use. Compass contributed experiment selection, receipts, refutation of
the rival rulebooks and the certification. The 34 correct fallback answers are the same model writing a law from
Compass's receipts after Compass found none. They are reported only as `compass_fallback` and are not a Compass
effect.

## Robustness vs experiment selection and verification (POST HOC sensitivity)

NewtonBench's vanilla agent does not catch simulator exceptions. It lost 4 trials on `m8_sound_speed/hard/v0`
(both strata, t1 and t2, `OverflowError` in `math.exp(gamma)`), which score 0 as registered. code_assisted and
Compass lost none. Compass records a non-finite simulator output as a receipt without an observable and keeps
going: in the corrected set, 12 trials hit 122 non-finite outputs, all on not-expressible cells (m10, m6
medium). No Compass experiment run raised.

| Primary, pooled (Compass - code_assisted) | All 36 cells | m8 hard v0 removed (35 cells) |
|---|---|---|
| Registered | -0.674 [-0.812, -0.528], p = 3.0e-8 | -0.679 [-0.821, -0.529], p = 6.0e-8 |
| Corrected | -0.562 [-0.715, -0.410], p = 4.8e-7 | -0.564 [-0.721, -0.400], p = 9.5e-7 |

The robustness gain ("Compass records a failed or non-finite experiment as a receipt and continues") is real
but worth about nothing in accuracy here. code_assisted, the primary comparator, never failed. In the one cell
where vanilla failed, Compass's own answer was never right (corrected: 3 of 4 trials SEARCH_EXHAUSTED, 1
certified a wrong law); only the fallback answer written from its receipts was right (3 of 4 trials). The
gain from experiment selection and verification, read off the cells where every arm ran cleanly, shows no
significant difference from zero on expressible laws (no equivalence test; the baselines are at ceiling) and is
negative overall.

## Cost: Codex token usage (descriptive)

Codex-reported usage, summed over every agent-side call of a trial: all retries; for Compass, every jump call of
every attempt; `compass_fallback` adds its fallback call. `output` includes reasoning tokens. A trial with any
call lacking usage is UNKNOWN and is excluded, never estimated. Judge calls are evaluation cost, reported
separately.

| Corrected set | Trials (usage known) | Mean input / trial (cached) | Mean output / trial | Correct | Input per correct solution | Output per correct solution | Mean wall s |
|---|---|---|---|---|---|---|---|
| vanilla | 144 (144) | 41,743 (26,087) | 4,546 | 138 | 43,558 | 4,743 | 117 |
| code_assisted | 144 (144) | 79,364 (37,338) | 6,133 | 135 | 84,655 | 6,542 | 151 |
| compass | 144 (141) | 507,211 (419,742) | 35,718 | 54 | 1,324,385 | 93,262 | 1,171 |
| compass_fallback | 144 (141) | 524,331 (431,733) | 35,989 | 85 | 869,772 | 59,700 | - |

"Correct" counts usage-known trials only (Compass's 3 UNKNOWN trials had correct fallback answers, so
compass_fallback has 88 correct in all). The three UNKNOWN trials are corrected-set Compass trials on m4
(noise 0); each had a jump call killed at its 540-second timeout, which reports no usage. By stratum, Compass averaged 578,705 input and 39,829 output tokens
per trial at noise 0, and 438,697 and 31,777 at noise 0.01. Per correct solution that is 1,426,094 / 98,150
(noise 0) and 1,214,853 / 87,999 (noise 0.01). code_assisted: 63,566 / 4,160 and 107,365 / 9,108. vanilla:
38,411 / 3,333 and 48,856 / 6,195. In the registered set, Compass averaged 357,354 input and 24,158 output
tokens per trial, or 1,354,184 / 91,545 per correct solution. The judge used 4.94M input and 0.12M output
tokens over 432 trials in the corrected set (4.76M / 0.11M registered). Every number by stratum and set is in
`descriptive.*.tokens`.

## False certification by stratum and expressibility

Wilson 95% intervals. A certification is a Compass trial that ended GOAL_IDENTIFIED; it is false when
NewtonBench's judge scores the certified law wrong.

| Stratum | Subset | Registered FCR | Corrected (post hoc) FCR |
|---|---|---|---|
| pooled | all | 80/118 = 0.68 [0.59, 0.76] | 51/105 = 0.49 [0.39, 0.58] |
| pooled | expressible | 16/54 = 0.30 [0.19, 0.43] | 0/54 = 0.00 [0.00, 0.07] |
| pooled | not_expressible | 64/64 = 1.00 [0.94, 1.00] | 51/51 = 1.00 [0.93, 1.00] |
| noise0 | all | 34/54 = 0.63 [0.50, 0.75] | 18/46 = 0.39 [0.26, 0.54] |
| noise0 | expressible | 8/28 = 0.29 [0.15, 0.47] | 0/28 = 0.00 [0.00, 0.12] |
| noise0 | not_expressible | 26/26 = 1.00 [0.87, 1.00] | 18/18 = 1.00 [0.82, 1.00] |
| noise0.01 | all | 46/64 = 0.72 [0.60, 0.81] | 33/59 = 0.56 [0.43, 0.68] |
| noise0.01 | expressible | 8/26 = 0.31 [0.17, 0.50] | 0/26 = 0.00 [0.00, 0.13] |
| noise0.01 | not_expressible | 38/38 = 1.00 [0.91, 1.00] | 33/33 = 1.00 [0.90, 1.00] |

## Every comparison

Accuracy is the mean over cells of each arm's mean judged accuracy; pooled views average both strata. Significant
means sign-test p < 0.05 and a bootstrap CI that excludes 0 (as registered).

<details><summary>Registered (formal run, defect included)</summary>

| View | Cells | Comparison | a | b | a - b [95% CI] | +/-/ties | p | Significant |
|---|---|---|---|---|---|---|---|---|
| pooled / all | 36 | compass vs code_assisted (primary) | 0.264 | 0.938 | -0.674 [-0.812, -0.528] | 0/26/10 | 3e-08 | yes |
| pooled / all | 36 | compass vs vanilla (secondary) | 0.264 | 0.958 | -0.694 [-0.833, -0.542] | 0/26/10 | 3e-08 | yes |
| pooled / all | 36 | compass_fallback vs code_assisted (secondary) | 0.382 | 0.938 | -0.556 [-0.688, -0.424] | 0/26/10 | 3e-08 | yes |
| pooled / all | 36 | compass_fallback vs vanilla (secondary) | 0.382 | 0.958 | -0.576 [-0.708, -0.438] | 0/26/10 | 3e-08 | yes |
| pooled / all | 36 | code_assisted vs vanilla (secondary) | 0.938 | 0.958 | -0.021 [-0.097, +0.042] | 3/3/30 | 1 | no |
| pooled / expressible | 14 | compass vs code_assisted (primary) | 0.679 | 1.000 | -0.321 [-0.571, -0.107] | 0/5/9 | 0.062 | no |
| pooled / expressible | 14 | compass vs vanilla (secondary) | 0.679 | 1.000 | -0.321 [-0.571, -0.107] | 0/5/9 | 0.062 | no |
| pooled / expressible | 14 | compass_fallback vs code_assisted (secondary) | 0.679 | 1.000 | -0.321 [-0.571, -0.107] | 0/5/9 | 0.062 | no |
| pooled / expressible | 14 | compass_fallback vs vanilla (secondary) | 0.679 | 1.000 | -0.321 [-0.571, -0.107] | 0/5/9 | 0.062 | no |
| pooled / expressible | 14 | code_assisted vs vanilla (secondary) | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | 0/0/14 | 1 | no |
| pooled / not_expressible | 22 | compass vs code_assisted (primary) | 0.000 | 0.898 | -0.898 [-0.989, -0.784] | 0/21/1 | 9.5e-07 | yes |
| pooled / not_expressible | 22 | compass vs vanilla (secondary) | 0.000 | 0.932 | -0.932 [-1.000, -0.830] | 0/21/1 | 9.5e-07 | yes |
| pooled / not_expressible | 22 | compass_fallback vs code_assisted (secondary) | 0.193 | 0.898 | -0.705 [-0.818, -0.580] | 0/21/1 | 9.5e-07 | yes |
| pooled / not_expressible | 22 | compass_fallback vs vanilla (secondary) | 0.193 | 0.932 | -0.739 [-0.852, -0.614] | 0/21/1 | 9.5e-07 | yes |
| pooled / not_expressible | 22 | code_assisted vs vanilla (secondary) | 0.898 | 0.932 | -0.034 [-0.159, +0.068] | 3/3/16 | 1 | no |
| noise0 / all | 36 | compass vs code_assisted (primary) | 0.278 | 0.972 | -0.694 [-0.833, -0.556] | 0/25/11 | 6e-08 | yes |
| noise0 / all | 36 | compass vs vanilla (secondary) | 0.278 | 0.972 | -0.694 [-0.833, -0.528] | 0/25/11 | 6e-08 | yes |
| noise0 / all | 36 | compass_fallback vs code_assisted (secondary) | 0.500 | 0.972 | -0.472 [-0.625, -0.319] | 0/18/18 | 7.6e-06 | yes |
| noise0 / all | 36 | compass_fallback vs vanilla (secondary) | 0.500 | 0.972 | -0.472 [-0.625, -0.319] | 0/18/18 | 7.6e-06 | yes |
| noise0 / all | 36 | code_assisted vs vanilla (secondary) | 0.972 | 0.972 | +0.000 [-0.083, +0.083] | 1/1/34 | 1 | no |
| noise0 / expressible | 14 | compass vs code_assisted (primary) | 0.714 | 1.000 | -0.286 [-0.500, -0.071] | 0/4/10 | 0.125 | no |
| noise0 / expressible | 14 | compass vs vanilla (secondary) | 0.714 | 1.000 | -0.286 [-0.500, -0.071] | 0/4/10 | 0.125 | no |
| noise0 / expressible | 14 | compass_fallback vs code_assisted (secondary) | 0.714 | 1.000 | -0.286 [-0.500, -0.071] | 0/4/10 | 0.125 | no |
| noise0 / expressible | 14 | compass_fallback vs vanilla (secondary) | 0.714 | 1.000 | -0.286 [-0.500, -0.071] | 0/4/10 | 0.125 | no |
| noise0 / expressible | 14 | code_assisted vs vanilla (secondary) | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | 0/0/14 | 1 | no |
| noise0 / not_expressible | 22 | compass vs code_assisted (primary) | 0.000 | 0.955 | -0.955 [-1.000, -0.864] | 0/21/1 | 9.5e-07 | yes |
| noise0 / not_expressible | 22 | compass vs vanilla (secondary) | 0.000 | 0.955 | -0.955 [-1.000, -0.864] | 0/21/1 | 9.5e-07 | yes |
| noise0 / not_expressible | 22 | compass_fallback vs code_assisted (secondary) | 0.364 | 0.955 | -0.591 [-0.773, -0.386] | 0/14/8 | 0.00012 | yes |
| noise0 / not_expressible | 22 | compass_fallback vs vanilla (secondary) | 0.364 | 0.955 | -0.591 [-0.773, -0.386] | 0/14/8 | 0.00012 | yes |
| noise0 / not_expressible | 22 | code_assisted vs vanilla (secondary) | 0.955 | 0.955 | +0.000 [-0.136, +0.136] | 1/1/20 | 1 | no |
| noise0.01 / all | 36 | compass vs code_assisted (primary) | 0.250 | 0.903 | -0.653 [-0.806, -0.500] | 0/24/12 | 1.2e-07 | yes |
| noise0.01 / all | 36 | compass vs vanilla (secondary) | 0.250 | 0.944 | -0.694 [-0.833, -0.542] | 0/26/10 | 3e-08 | yes |
| noise0.01 / all | 36 | compass_fallback vs code_assisted (secondary) | 0.264 | 0.903 | -0.639 [-0.792, -0.486] | 0/24/12 | 1.2e-07 | yes |
| noise0.01 / all | 36 | compass_fallback vs vanilla (secondary) | 0.264 | 0.944 | -0.681 [-0.819, -0.528] | 0/26/10 | 3e-08 | yes |
| noise0.01 / all | 36 | code_assisted vs vanilla (secondary) | 0.903 | 0.944 | -0.042 [-0.139, +0.042] | 2/3/31 | 1 | no |
| noise0.01 / expressible | 14 | compass vs code_assisted (primary) | 0.643 | 1.000 | -0.357 [-0.643, -0.143] | 0/5/9 | 0.062 | no |
| noise0.01 / expressible | 14 | compass vs vanilla (secondary) | 0.643 | 1.000 | -0.357 [-0.643, -0.143] | 0/5/9 | 0.062 | no |
| noise0.01 / expressible | 14 | compass_fallback vs code_assisted (secondary) | 0.643 | 1.000 | -0.357 [-0.643, -0.143] | 0/5/9 | 0.062 | no |
| noise0.01 / expressible | 14 | compass_fallback vs vanilla (secondary) | 0.643 | 1.000 | -0.357 [-0.643, -0.143] | 0/5/9 | 0.062 | no |
| noise0.01 / expressible | 14 | code_assisted vs vanilla (secondary) | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | 0/0/14 | 1 | no |
| noise0.01 / not_expressible | 22 | compass vs code_assisted (primary) | 0.000 | 0.841 | -0.841 [-0.977, -0.682] | 0/19/3 | 3.8e-06 | yes |
| noise0.01 / not_expressible | 22 | compass vs vanilla (secondary) | 0.000 | 0.909 | -0.909 [-1.000, -0.795] | 0/21/1 | 9.5e-07 | yes |
| noise0.01 / not_expressible | 22 | compass_fallback vs code_assisted (secondary) | 0.023 | 0.841 | -0.818 [-0.955, -0.659] | 0/19/3 | 3.8e-06 | yes |
| noise0.01 / not_expressible | 22 | compass_fallback vs vanilla (secondary) | 0.023 | 0.909 | -0.886 [-0.977, -0.773] | 0/21/1 | 9.5e-07 | yes |
| noise0.01 / not_expressible | 22 | code_assisted vs vanilla (secondary) | 0.841 | 0.909 | -0.068 [-0.227, +0.068] | 2/3/17 | 1 | no |

</details>

<details><summary>Corrected, POST HOC (amendment 5)</summary>

| View | Cells | Comparison | a | b | a - b [95% CI] | +/-/ties | p | Significant |
|---|---|---|---|---|---|---|---|---|
| pooled / all | 36 | compass vs code_assisted (primary) | 0.375 | 0.938 | -0.562 [-0.715, -0.410] | 0/22/14 | 4.8e-07 | yes |
| pooled / all | 36 | compass vs vanilla (secondary) | 0.375 | 0.958 | -0.583 [-0.736, -0.424] | 0/22/14 | 4.8e-07 | yes |
| pooled / all | 36 | compass_fallback vs code_assisted (secondary) | 0.611 | 0.938 | -0.326 [-0.479, -0.167] | 3/19/14 | 0.00086 | yes |
| pooled / all | 36 | compass_fallback vs vanilla (secondary) | 0.611 | 0.958 | -0.347 [-0.486, -0.208] | 1/19/16 | 4e-05 | yes |
| pooled / all | 36 | code_assisted vs vanilla (secondary) | 0.938 | 0.958 | -0.021 [-0.097, +0.042] | 3/3/30 | 1 | no |
| pooled / expressible | 14 | compass vs code_assisted (primary) | 0.964 | 1.000 | -0.036 [-0.107, +0.000] | 0/1/13 | 1 | no |
| pooled / expressible | 14 | compass vs vanilla (secondary) | 0.964 | 1.000 | -0.036 [-0.107, +0.000] | 0/1/13 | 1 | no |
| pooled / expressible | 14 | compass_fallback vs code_assisted (secondary) | 0.964 | 1.000 | -0.036 [-0.107, +0.000] | 0/1/13 | 1 | no |
| pooled / expressible | 14 | compass_fallback vs vanilla (secondary) | 0.964 | 1.000 | -0.036 [-0.107, +0.000] | 0/1/13 | 1 | no |
| pooled / expressible | 14 | code_assisted vs vanilla (secondary) | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | 0/0/14 | 1 | no |
| pooled / not_expressible | 22 | compass vs code_assisted (primary) | 0.000 | 0.898 | -0.898 [-0.989, -0.784] | 0/21/1 | 9.5e-07 | yes |
| pooled / not_expressible | 22 | compass vs vanilla (secondary) | 0.000 | 0.932 | -0.932 [-1.000, -0.830] | 0/21/1 | 9.5e-07 | yes |
| pooled / not_expressible | 22 | compass_fallback vs code_assisted (secondary) | 0.386 | 0.898 | -0.511 [-0.716, -0.273] | 3/18/1 | 0.001 | yes |
| pooled / not_expressible | 22 | compass_fallback vs vanilla (secondary) | 0.386 | 0.932 | -0.545 [-0.716, -0.352] | 1/18/3 | 7.6e-05 | yes |
| pooled / not_expressible | 22 | code_assisted vs vanilla (secondary) | 0.898 | 0.932 | -0.034 [-0.159, +0.068] | 3/3/16 | 1 | no |
| noise0 / all | 36 | compass vs code_assisted (primary) | 0.389 | 0.972 | -0.583 [-0.750, -0.417] | 0/21/15 | 9.5e-07 | yes |
| noise0 / all | 36 | compass vs vanilla (secondary) | 0.389 | 0.972 | -0.583 [-0.750, -0.417] | 0/21/15 | 9.5e-07 | yes |
| noise0 / all | 36 | compass_fallback vs code_assisted (secondary) | 0.750 | 0.972 | -0.222 [-0.375, -0.069] | 1/10/25 | 0.012 | yes |
| noise0 / all | 36 | compass_fallback vs vanilla (secondary) | 0.750 | 0.972 | -0.222 [-0.375, -0.069] | 1/10/25 | 0.012 | yes |
| noise0 / all | 36 | code_assisted vs vanilla (secondary) | 0.972 | 0.972 | +0.000 [-0.083, +0.083] | 1/1/34 | 1 | no |
| noise0 / expressible | 14 | compass vs code_assisted (primary) | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | 0/0/14 | 1 | no |
| noise0 / expressible | 14 | compass vs vanilla (secondary) | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | 0/0/14 | 1 | no |
| noise0 / expressible | 14 | compass_fallback vs code_assisted (secondary) | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | 0/0/14 | 1 | no |
| noise0 / expressible | 14 | compass_fallback vs vanilla (secondary) | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | 0/0/14 | 1 | no |
| noise0 / expressible | 14 | code_assisted vs vanilla (secondary) | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | 0/0/14 | 1 | no |
| noise0 / not_expressible | 22 | compass vs code_assisted (primary) | 0.000 | 0.955 | -0.955 [-1.000, -0.864] | 0/21/1 | 9.5e-07 | yes |
| noise0 / not_expressible | 22 | compass vs vanilla (secondary) | 0.000 | 0.955 | -0.955 [-1.000, -0.864] | 0/21/1 | 9.5e-07 | yes |
| noise0 / not_expressible | 22 | compass_fallback vs code_assisted (secondary) | 0.591 | 0.955 | -0.364 [-0.591, -0.136] | 1/10/11 | 0.012 | yes |
| noise0 / not_expressible | 22 | compass_fallback vs vanilla (secondary) | 0.591 | 0.955 | -0.364 [-0.591, -0.136] | 1/10/11 | 0.012 | yes |
| noise0 / not_expressible | 22 | code_assisted vs vanilla (secondary) | 0.955 | 0.955 | +0.000 [-0.136, +0.136] | 1/1/20 | 1 | no |
| noise0.01 / all | 36 | compass vs code_assisted (primary) | 0.361 | 0.903 | -0.542 [-0.694, -0.389] | 0/20/16 | 1.9e-06 | yes |
| noise0.01 / all | 36 | compass vs vanilla (secondary) | 0.361 | 0.944 | -0.583 [-0.736, -0.417] | 0/22/14 | 4.8e-07 | yes |
| noise0.01 / all | 36 | compass_fallback vs code_assisted (secondary) | 0.472 | 0.903 | -0.431 [-0.625, -0.222] | 3/19/14 | 0.00086 | yes |
| noise0.01 / all | 36 | compass_fallback vs vanilla (secondary) | 0.472 | 0.944 | -0.472 [-0.639, -0.306] | 1/19/16 | 4e-05 | yes |
| noise0.01 / all | 36 | code_assisted vs vanilla (secondary) | 0.903 | 0.944 | -0.042 [-0.139, +0.042] | 2/3/31 | 1 | no |
| noise0.01 / expressible | 14 | compass vs code_assisted (primary) | 0.929 | 1.000 | -0.071 [-0.214, +0.000] | 0/1/13 | 1 | no |
| noise0.01 / expressible | 14 | compass vs vanilla (secondary) | 0.929 | 1.000 | -0.071 [-0.214, +0.000] | 0/1/13 | 1 | no |
| noise0.01 / expressible | 14 | compass_fallback vs code_assisted (secondary) | 0.929 | 1.000 | -0.071 [-0.214, +0.000] | 0/1/13 | 1 | no |
| noise0.01 / expressible | 14 | compass_fallback vs vanilla (secondary) | 0.929 | 1.000 | -0.071 [-0.214, +0.000] | 0/1/13 | 1 | no |
| noise0.01 / expressible | 14 | code_assisted vs vanilla (secondary) | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | 0/0/14 | 1 | no |
| noise0.01 / not_expressible | 22 | compass vs code_assisted (primary) | 0.000 | 0.841 | -0.841 [-0.977, -0.682] | 0/19/3 | 3.8e-06 | yes |
| noise0.01 / not_expressible | 22 | compass vs vanilla (secondary) | 0.000 | 0.909 | -0.909 [-1.000, -0.795] | 0/21/1 | 9.5e-07 | yes |
| noise0.01 / not_expressible | 22 | compass_fallback vs code_assisted (secondary) | 0.182 | 0.841 | -0.659 [-0.909, -0.364] | 3/18/1 | 0.001 | yes |
| noise0.01 / not_expressible | 22 | compass_fallback vs vanilla (secondary) | 0.182 | 0.909 | -0.727 [-0.909, -0.523] | 1/18/3 | 7.6e-05 | yes |
| noise0.01 / not_expressible | 22 | code_assisted vs vanilla (secondary) | 0.841 | 0.909 | -0.068 [-0.227, +0.068] | 2/3/17 | 1 | no |

</details>

## Deviations and amendments

All five amendments are in [PREREGISTRATION.md](PREREGISTRATION.md) with their timestamps; 1-4 were committed
before the formal run started, 5 after it finished.

1. **Amendment 1 (ceiling).** A noise 0.01 stratum of the same 36 cells was added after the baseline pilot
   (12/12 correct) and NewtonBench's published accuracies showed the noise-free tasks near ceiling. It was
   decided from the baseline pilot and the paper only, never from Compass results. Every arm is told the noise
   model; Compass's tolerance in that stratum is 5 noise standard deviations (relative 1/20, absolute 5 x the
   module floor). The primary unit is the cell, pooled over both strata; the noise-free test is reported as the
   originally registered primary test.
2. **Amendment 2 (Compass crash).** A `KeyError: 'attempted'` in Compass's regime split, found in the pilot, was
   fixed (`6c8ad0d`, later upstream as `24745cc`). The crashed pilot runs are kept as `*.superseded-*`.
3. **Amendment 3 (paths).** Every Compass trial is labelled with its submission path (CERTIFIED,
   LIVE_UNCERTIFIED, NO_LIVE_RULEBOOK, ...). A fallback answer, written by the same model when Compass has no live
   rulebook, is scored only as the `compass_fallback` secondary submission and is not a Compass effect.
4. **Amendment 4 (merged release).** The formal run evaluates `release/6.0.0-compass` at `24745cc` merged into
   this branch (`73ab8b7`; formal runs at `a341289`). The pilot's noisy m7 false certification was recorded and
   the tolerance rule was not changed.
5. **Amendment 5 (harness defect, after the run).** See above. The registered analysis is unchanged; the rerun
   and the corrected analysis are post hoc.

Other deviations and facts a reader needs:

- **Judge is the same model.** NewtonBench's symbolic-equivalence judge ran on `gpt-6.1-sol` through the Codex
  CLI (no other model or API key was used). Symbolic accuracy therefore depends on the evaluated model's own
  family. RMSLE does not use the judge and is unaffected. Judge and RMSLE can disagree: code_assisted's m4 answers
  written with `atan2` equal the ground truth on the 0-90 degree domain (RMSLE about 1.6e-16) and were judged
  not equivalent (m4 medium v2 in both strata, m4 hard v2 noisy).
- **No temperature control.** The Codex CLI exposes no temperature; NewtonBench's requested temperatures are
  recorded per call but not applied.
- **Query cap.** All arms get 40 simulator queries per trial. Compass's 64-point design grid was fixed with
  knowledge of the Compass dialect (levels are squares of rationals so that `sqrt` is exact).
- **Only `vanilla_equation`.** NewtonBench's simple and complex systems were not run.
- **Not run:** the optional ablation arm (decided a priori, for cost).
- **Simulator exceptions.** NewtonBench's vanilla agent does not catch simulator exceptions: 4 vanilla trials
  (m8 hard v0, both strata, t1 and t2) ended FAILED on an `OverflowError` in `math.exp(gamma)` and score 0, as
  registered. See the post-hoc sensitivity analysis.
- **Contamination audit.** 0 trials in any arm touched sealed material.

## Limitations

- **Ceiling.** Both baselines are near ceiling even with 1% noise (code_assisted 0.903, vanilla 0.944 in the noisy
  stratum). The benchmark leaves little room for any method to beat them; it can only show where Compass loses.
- **Dialect.** Compass's exact-rational dialect has no transcendental functions (exp, log, sin, arcsin, ...) and
  no non-integer powers beyond exact square roots. 22 of 36 cells are not expressible in it; on those Compass
  cannot submit a correct law at all. This is a property of the dialect fixed a priori, not of the tasks chosen.
- **Tolerance certifies approximations.** On not-expressible cells every certification was wrong: Compass found a
  rational function that agrees with every receipt within the declared tolerance on the declared grid and
  certified it. **GOAL_IDENTIFIED 只表示"在容差内与收据一致"，不表示科学上被支持。** The noisy stratum's 5-sd
  tolerance makes this easier; the noise-free 1e-6 relative tolerance does not prevent it.
- **Harness defect (amendment 5).** The registered result includes a defect against Compass on 12 cells. The
  corrected result is post hoc: the rerun was done after seeing the registered result, though nothing but the
  input names changed.
- **Same-family judge**, **N = 2 trials per task**, **36 cells**, one model, one benchmark system.

## Next steps

1. **Dialect:** add transcendental functions (exp, log, sin/cos, arcsin) and real-valued powers with interval or
   tolerance semantics, so that the laws NewtonBench uses become expressible. This is a Compass change and is
   out of scope for this branch.
2. **Residual-structure test before certification:** a certified law should leave residuals without structure
   (sign runs, correlation with inputs, scale with the noise model), not only fit within tolerance; that would
   have refused most of the 64 approximate certifications.
3. **Harness:** a pre-flight check that every declared input changes the simulator's output somewhere on the
   design (what `audit_inputs.py` now does) should be part of any Compass adapter.
4. **Harder tasks:** NewtonBench's simple/complex systems, higher noise, or other benchmarks where the baselines
   are not at ceiling, so that a gain, if there is one, can show.

## How to reproduce

NewtonBench is cloned outside the repository and used from a venv that holds only numpy, scipy and pandas
(`common.NEWTONBENCH`, `common.EVAL`). No API key is used: every model call, including NewtonBench's judge, goes
through the Codex CLI.

```bash
python -B run.py --phase pilot --trials 1      # held-out pilot tasks, both noise strata (not counted)
python -B run.py --phase formal --trials 2 --parallel 12
python -B run.py --phase formal-rerun --trials 2 --parallel 12 --arms compass    # amendment 5, post hoc
python -B audit_inputs.py --out results/input-audit.md
python -B analyze.py --phase formal --rerun-phase formal-rerun --out results/newtonbench-compass.json \
    --table results/per-task.md --corrected-table results/per-task-corrected.md
```

`run.py` skips trials that already have a `trial.json`, so rerunning it resumes. A trial that times out or exits
without a result is written as TIMEOUT or CRASHED and scored as a failure.

| File | Role |
|---|---|
| `common.py` | Task sample, Codex CLI backend (pure chat for the baselines and the judge), contamination audit, Compass design grid, simulator keyword names |
| `trial.py` | One trial of one arm on one task, scored by NewtonBench's `evaluate_law` |
| `nb_experiment.py` | Compass experiment program: one NewtonBench simulator query per kernel run |
| `nb_jump.py` | Compass jump agent: the same Codex model on the sealed jump packet |
| `run.py` | Orchestrator with a 4-hour wall per trial |
| `audit_inputs.py` | Amendment 5 audit: which inputs the simulator ignores under each harness |
| `analyze.py` | The registered analysis, plus the descriptive and post-hoc parts marked as such |
