# 测试清单与契约分层：contract / decision / regression / smoke

> 只读分析，未运行 `unittest` 全量套件（3000+ 用例太慢，且分类只依赖静态断言证据）。
> 每一条 `依据` 都指向 `tests/` 里具体的断言行，不靠文件名推断。
> 证据根：`research-direction-selector` @ `8991d3b`（`release/6.0.0-compass`）。

## 0. 数字先摆出来

| 类别 | 文件数 |
| --- | ---: |
| contract | 171 |
| decision | 30 |
| regression | 126 |
| smoke | 2 |
| UNKNOWN | 0 |
| **合计** | **329** |

三个最重要的发现：

1. `scripts/rds_cas.py`（L1 认证资产）只有 **1 个** contract 测试：`tests/test_rds_cas_service.py`。改 CAS 提取逻辑时，套件里没有第二个文件会拦住你。
2. `scripts/rds_contract_read.py`（L1 契约血缘读取）和 `scripts/rds_sqlite.py`（L0 事务原语）**没有 contract 测试**：各只有 1 个直接测试，都落在 regression。
3. `scripts/rds_cas_read.py`（CAS 读端口，三条 R0 读路径共用）在 `tests/` 里**一次都没被提到**；同类还有 `rds_advisor_choice.py`（L3 选路验证）等共 9 个模块。

## 1. 口径：336 是估算，实际 `tests/test_*.py` = 329

任务写的是 336。我在 `8991d3b` 上逐个核对，各种口径都对不上 336，能凑出 336 的只有一种组合：

| 口径 | 数量 | 命令 |
| --- | ---: | --- |
| `tests/test_*.py`（本表基数） | 329 | `find tests -maxdepth 1 -name 'test_*.py'` |
| `tests/*.py`（含 5 个 fixture） | 334 | `find tests -maxdepth 1 -name '*.py'` |
| 全仓 `test_*.py` | 331 | `find . -name 'test_*.py' -not -path './.git/*'` |
| `tests/` 全条目（334 py + `AGENTS.md` + `.cjs` + `.mjs`） | 337 | `git ls-files tests` |

334（`tests/*.py`）+ 2（仓内其它目录的 `test_*.py`）= 336。本表按任务要求只做 `tests/` 内的 **329** 个，合计行对得上；
5 个 fixture、2 个 Node 测试、2 个仓外测试在 §9 单独交代，不进四分类。

## 2. 分类判据

优先级严格 **contract > decision > regression > smoke**；命中的必须是断言行（`assert*` / `self.fail`），不是任意一行。

| 类别 | 判据（断言行的条件） | 证据样例 |
| --- | --- | --- |
| **contract** | `ast`：`frozen_ast`/`ast.dump` 且同行有 `hexdigest`/64-hex；`hex`：断言行含 64 位十六进制常量；`bytes`：`assertEqual` 把结果与非空 `b"..."` 字面量比较；`shape`：`assertEqual(set/sorted/tuple(...), <纯字面集合>)`（右侧有推导式/函数调用不算）；`assurance`：`assurance` 字段与非平凡常量比较；`ident`：`assertEqual/assertNotEqual` 的被测字段 ∈ {`sha256`,`digest`,`kind`,`schema`,`type`,`class`,`role`,`pin`,`identity`,`contract_sha256`,`receipt_sha256`,`packet_sha256`,`code_sha256`,`ledger_id`,`receipt_id`} 且期望值是字面常量 | `test_rds_cas_service.py:50` |
| **decision** | 断言或测试方法名含 `decide`/`decision`/`selection`/`advisor_{choice,review,search,coverage,workset,discriminat}`/`threshold`/`holdout`/`arms`/`comparison`/`rank`/`acceptance`/`benefit`/`harm` | `test_rds_search_allocation.py:57` |
| **regression** | 非 smoke 断言里含边界/缺陷/不变性词：`invalid`/`refuse`/`reject`/`blocked`/`guard`/`boundary`/`raise`/`idempot`/`repeat`/`resume`/`recover`/`retry`/`crash`/`partial`/`truncat`/`missing`/`duplicate`/`conflict`/`tamper`/`damage`/`corrupt`/`unchanged`/`assertRaises` | `test_rds_launch_refusal.py:38` |
| **smoke** | 全部断言只含 `returncode==0` / `--help` / `usage` / `sys.modules` / `self.fail(...)`，无业务字段断言 | `test_rds_project_hooks.py:29` |

两条否定式说明，避免误读：

- `sha256`/`receipt` 出现在**构造**代码里（fixture、断言右侧的动态 `digest(...)`）**不算** contract。动态自洽断言（`assertEqual(receipt['sha256'], digest(...))`）验证的是「哈希和内容一致」，实现换了它照样过，所以归 regression；contract 只认「期望值是字面常量」或「钉死账本行身份字段」。
- 一个文件常有多个类别的断言，表里记的是**文件级主类别**（按优先级取最高的那个）。

## 3. contract（171 个文件）

一个文件可以同时命中多个 tier，所以下表各 tier 之和大于 171。

| tier | 含义 | 文件数 |
| --- | --- | ---: |
| `ast` | AST/源码哈希钉死 | 3 |
| `hex` | 硬编码摘要常量 | 1 |
| `bytes` | 字节级钉死 | 3 |
| `shape` | 输出形状/字段集合钉死 | 19 |
| `assurance` | 认证级别 assurance 钉死 | 52 |
| `ident` | 账本行身份字段字面量 | 125 |

### 3.1 硬资产级：AST 哈希 / 摘要常量 / 字节 / 输出形状（26 个文件）

这一组是「改一个字节就会红」的测试：它们把源码 AST 摘要、内容哈希、原始字节或字段集合钉死成字面量。

| 测试文件 | 依据（file:line） | 覆盖 scripts 模块 |
| --- | --- | --- |
| [`test_jump_loop.py`](../tests/test_jump_loop.py) | `test_jump_loop.py:33 self.assertEqual(set(first_input['jump_packet']['generation_results']), {'probe', 'refresh', 'syn` | `rds_autonomy`, `rds_math`, `rds_project` |
| [`test_pr274_repair_boundaries.py`](../tests/test_pr274_repair_boundaries.py) | `test_pr274_repair_boundaries.py:329 self.assertEqual(set(targets), {workspace})` | `rds_hypergraph_archive`, `rds_quick`, `rds_campaign` |
| [`test_pr311_cas_workspace_admission.py`](../tests/test_pr311_cas_workspace_admission.py) | `test_pr311_cas_workspace_admission.py:60 self.assertEqual(Path(cas_bytes(self.child.root, b'unbound asset')['path']).r` | `rds_workspace`, `rds_quick`, `rds_math` |
| [`test_rds_advisor_coverage.py`](../tests/test_rds_advisor_coverage.py) | `test_rds_advisor_coverage.py:81 self.assertEqual(set(reports), {"root", "disconnected"})` | `rds_advisor_search`, `rds_quick`, `rds_advisor` |
| [`test_rds_advisor_discrimination.py`](../tests/test_rds_advisor_discrimination.py) | `test_rds_advisor_discrimination.py:71 self.assertEqual(set(report["ranking"]["pareto_front"]), {"update:update-check",` | `rds_advisor_search` |
| [`test_rds_advisor_search.py`](../tests/test_rds_advisor_search.py) | `test_rds_advisor_search.py:222 self.assertEqual(set(result["ranking"]["pareto_front"]), {"cheap:cheap-test", "importan` | `rds_advisor_search`, `rds_advisor`, `rds_meta` |
| [`test_rds_app.py`](../tests/test_rds_app.py) | `test_rds_app.py:33 self.assertEqual(set(bound), {"advisor", "quick"})` | `rds_app` |
| [`test_rds_archive_compression.py`](../tests/test_rds_archive_compression.py) | `test_rds_archive_compression.py:31 self.assertEqual(packed[4:8], b"\x00" * 4)` | `rds_hypergraph_archive` |
| [`test_rds_artifacts.py`](../tests/test_rds_artifacts.py) | `test_rds_artifacts.py:158 self.assertEqual(binding["code_sha256"], "9c10b08356db6478cbcc90a1e2ef920a731e8968533b268389` | `rds_verify_types`, `rds_advisor_search`, `rds_cli` |
| [`test_rds_cas_service.py`](../tests/test_rds_cas_service.py) | `test_rds_cas_service.py:50 self.assertEqual(hashlib.sha256(json.dumps(frozen_ast(node), ensure_ascii=True, separators=` | `rds_cas`, `rds_cli` |
| [`test_rds_compass_drive.py`](../tests/test_rds_compass_drive.py) | `test_rds_compass_drive.py:112 self.assertEqual(sorted(verification['equivalence_class']), ['far', 'near'])` | `rds_compass`, `rds_compass_store`, `rds_compass_drive` |
| [`test_rds_conversation_hook.py`](../tests/test_rds_conversation_hook.py) | `test_rds_conversation_hook.py:44 self.assertEqual(set(result), {"hookSpecificOutput"})` | `rds_cli` |
| [`test_rds_dynamics_verify.py`](../tests/test_rds_dynamics_verify.py) | `test_rds_dynamics_verify.py:87 self.assertEqual(set(certificate["witness"]), {"contraction", "fixed_point"})` | `rds_dynamics_verify` |
| [`test_rds_experiments.py`](../tests/test_rds_experiments.py) | `test_rds_experiments.py:98 self.assertEqual(set(report["candidates"][0]["template_ids"]), {first["id"], alias["id"]})` | `rds_experiments`, `rds_advisor_search`, `rds_artifacts` |
| [`test_rds_host_broker.py`](../tests/test_rds_host_broker.py) | `test_rds_host_broker.py:319 self.assertEqual(set(preparations), {'baseline'})` | `rds_host_broker`, `rds_project`, `rds_campaign` |
| [`test_rds_hypergraph_readable.py`](../tests/test_rds_hypergraph_readable.py) | `test_rds_hypergraph_readable.py:32 self.assertEqual(set(bundle), {"raw", "readable"})` | `rds_hypergraph_readable`, `rds_hypergraph_view` |
| [`test_rds_l3.py`](../tests/test_rds_l3.py) | `test_rds_l3.py:115 self.assertEqual(sorted(codes), [0, 1])` | `rds_meta`, `rds_probe`, `rds_obelisk` |
| [`test_rds_lean_progression_adapter.py`](../tests/test_rds_lean_progression_adapter.py) | `test_rds_lean_progression_adapter.py:41 self.assertEqual(set(rule.support_files), {"rds_operators.py", "rds_lean_verif` | `rds_verify`, `rds_verify_types`, `rds_lean_verify` |
| [`test_rds_plugin_package.py`](../tests/test_rds_plugin_package.py) | `test_rds_plugin_package.py:116 self.assertEqual(set(hooks["hooks"]), {"SessionStart", "PreToolUse", "PostToolUse"})` | `rds_cli` |
| [`test_rds_project_lifecycle.py`](../tests/test_rds_project_lifecycle.py) | `test_rds_project_lifecycle.py:320 self.assertEqual(sorted(r['status'] for r in results), ['ADVISOR_ENABLED', 'ALREADY_` | `rds_project_lifecycle`, `rds_project`, `rds_cli` |
| [`test_rds_project_read_port.py`](../tests/test_rds_project_read_port.py) | `test_rds_project_read_port.py:180 self.assertEqual(run.body, b"\xff\xfe{not-json")` | `rds_project`, `rds_project_read_port` |
| [`test_rds_quick_ledger_service.py`](../tests/test_rds_quick_ledger_service.py) | `test_rds_quick_ledger_service.py:74 self.assertEqual(hashlib.sha256(json.dumps(frozen_ast(node), ensure_ascii=True, se` | `rds_cli`, `rds_quick_ledger`, `rds_project_hooks` |
| [`test_rds_result_record.py`](../tests/test_rds_result_record.py) | `test_rds_result_record.py:56 self.assertEqual(hashlib.sha256(json.dumps(frozen_ast(node), ensure_ascii=True, separator` | `rds_cli`, `rds_output_values`, `rds_result_record` |
| [`test_rds_result_tools.py`](../tests/test_rds_result_tools.py) | `test_rds_result_tools.py:198 self.assertEqual(set(result['matched_fields']), {'data_sha256', 'data_split', 'evaluator_` | `rds_result_tools`, `rds_tools` |
| [`test_rds_statistical_obligations.py`](../tests/test_rds_statistical_obligations.py) | `test_rds_statistical_obligations.py:246 self.assertEqual(set(snapshot["plans"]), {"P1"})` | `rds_statistical_verify`, `rds_cli`, `rds_probe` |
| [`test_rds_verify_registry.py`](../tests/test_rds_verify_registry.py) | `test_rds_verify_registry.py:261 self.assertEqual(set(modules), {"abc", "re", "rds_core"})` | `rds_verify_registry`, `rds_core` |

### 3.2 认证级别钉死：assurance（48 个文件）

`assurance` 是仓库声明「这个结论被验证到什么程度」的唯一字段。改 verifier 路由、证书装配、降级逻辑会最先打红这一批。

| 测试文件 | 依据（file:line） | 覆盖 scripts 模块 |
| --- | --- | --- |
| [`test_hypergraph_evidence.py`](../tests/test_hypergraph_evidence.py) | `test_hypergraph_evidence.py:206 self.assertEqual(result["assurance"], "INPUT_REPORTED_DEPENDENCY_ANALYSIS_NOT_PROOF")` | `rds_hypergraph`, `rds_project`, `rds_cli` |
| [`test_rds_accelerator.py`](../tests/test_rds_accelerator.py) | `test_rds_accelerator.py:25 self.assertIn("NOT_SCIENTIFIC", status["assurance"])` | `rds_accelerator` |
| [`test_rds_advancement.py`](../tests/test_rds_advancement.py) | `test_rds_advancement.py:80 self.assertEqual(report["assurance"], "INPUT_REPORTED")` | `rds_advancement` |
| [`test_rds_advisor.py`](../tests/test_rds_advisor.py) | `test_rds_advisor.py:31 self.assertEqual(advice["assurance"], "HEURISTIC_ONLY")` | `rds_advisor`, `rds_checkpoints`, `rds_cli` |
| [`test_rds_advisor_cli.py`](../tests/test_rds_advisor_cli.py) | `test_rds_advisor_cli.py:71 self.assertEqual(answer['assurance'], 'HEURISTIC_ONLY')` | `rds_cli`, `rds_tms_store`, `rds_verify_types` |
| [`test_rds_advisor_lean_twostep.py`](../tests/test_rds_advisor_lean_twostep.py) | `test_rds_advisor_lean_twostep.py:478 self.assertEqual(result["formal_gate"]["assurance"], "CERTIFICATE_CHECKED")` | `rds_project`, `rds_advisor`, `rds_checkpoints` |
| [`test_rds_affine_toolchain.py`](../tests/test_rds_affine_toolchain.py) | `test_rds_affine_toolchain.py:103 self.assertEqual(vector_step["certificate"]["assurance"], "LEAN_KERNEL_CHECKED")` | `rds_verify`, `rds_affine_toolchain`, `rds_lean_verify` |
| [`test_rds_builtin_verifiers.py`](../tests/test_rds_builtin_verifiers.py) | `test_rds_builtin_verifiers.py:226 self.assertEqual(result["assurance"], "NONE")` | `rds_verify`, `rds_verify_registry` |
| [`test_rds_capabilities.py`](../tests/test_rds_capabilities.py) | `test_rds_capabilities.py:18 self.assertIn('NOT_SCIENTIFIC_VERIFICATION', report['assurance'])` | `rds_capabilities` |
| [`test_rds_capability_requirement.py`](../tests/test_rds_capability_requirement.py) | `test_rds_capability_requirement.py:103 self.assertEqual(entry['assurance'], 'INPUT_REPORTED_OBSTRUCTION_NOT_DIAGNOSIS'` | `rds_cli`, `rds_advisor`, `rds_quick` |
| [`test_rds_cli_performance.py`](../tests/test_rds_cli_performance.py) | `test_rds_cli_performance.py:134 self.assertEqual(second["binding"]["admission_probe"]["assurance"], "CERTIFICATE_CHECK` | `rds_cli` |
| [`test_rds_context_compact.py`](../tests/test_rds_context_compact.py) | `test_rds_context_compact.py:127 self.assertEqual(context["compaction"]["assurance"], "DECLARED_MANIFEST_IDENTITY_ONLY"` | `rds_context_compact` |
| [`test_rds_continuous_confirmation.py`](../tests/test_rds_continuous_confirmation.py) | `test_rds_continuous_confirmation.py:370 self.assertEqual(result['assurance'], 'RECOMPUTED_FINITE_FLOAT_EXPRESSION')` | `rds_continuous_confirmation`, `rds_domain_confirmation`, `rds_project` |
| [`test_rds_decision_requirements.py`](../tests/test_rds_decision_requirements.py) | `test_rds_decision_requirements.py:36 self.assertEqual(result['assurance'], 'FINITE_SAMPLE_REQUIREMENTS')` | `rds_result_tools`, `rds_tools` |
| [`test_rds_disk_cover_verify.py`](../tests/test_rds_disk_cover_verify.py) | `test_rds_disk_cover_verify.py:159 self.assertEqual(result["assurance"], "CERTIFICATE_CHECKED")` | `rds_verify`, `rds_disk_cover_verify`, `rds_verify_types` |
| [`test_rds_domain_confirmation.py`](../tests/test_rds_domain_confirmation.py) | `test_rds_domain_confirmation.py:147 self.assertEqual(result['assurance'], 'CERTIFICATE_CHECKED')` | `rds_domain_confirmation`, `rds_project`, `rds_verify` |
| [`test_rds_formal_kernel.py`](../tests/test_rds_formal_kernel.py) | `test_rds_formal_kernel.py:31 self.assertEqual(probe["assurance"], "CERTIFICATE_CHECKED")` | `rds_probe`, `rds_formal_kernel` |
| [`test_rds_goal_dependencies.py`](../tests/test_rds_goal_dependencies.py) | `test_rds_goal_dependencies.py:440 self.assertEqual(review['assurance'], 'INPUT_REPORTED_NOT_SCIENTIFIC_VERIFICATION')` | `rds_advisor_search`, `rds_quick`, `rds_advisor` |
| [`test_rds_goal_observation_link.py`](../tests/test_rds_goal_observation_link.py) | `test_rds_goal_observation_link.py:89 self.assertEqual(report['assurance'], 'DECLARED_LINK_NOT_SCIENTIFIC_PROOF')` | `rds_cli` |
| [`test_rds_guardrails.py`](../tests/test_rds_guardrails.py) | `test_rds_guardrails.py:124 self.assertEqual(result['checks'][0]['actual']['assurance'], 'CERTIFICATE_CHECKED')` | `rds_cli`, `rds_guard`, `rds_project` |
| [`test_rds_hypergraph_view.py`](../tests/test_rds_hypergraph_view.py) | `test_rds_hypergraph_view.py:227 self.assertEqual(result["assurance"], "INPUT_REPORTED_DEPENDENCY_ANALYSIS_NOT_PROOF")` | `rds_hypergraph_view`, `rds_tms_store`, `rds_hypergraph_readable` |
| [`test_rds_lean4_native.py`](../tests/test_rds_lean4_native.py) | `test_rds_lean4_native.py:64 self.assertEqual(result["assurance"], "CERTIFICATE_CHECKED")` | `rds_lean_verify`, `rds_verify`, `rds_cli` |
| [`test_rds_lean_feedback.py`](../tests/test_rds_lean_feedback.py) | `test_rds_lean_feedback.py:40 self.assertEqual(value["assurance"], "NONE")` | `rds_lean_feedback` |
| [`test_rds_lean_formal_plan_cli.py`](../tests/test_rds_lean_formal_plan_cli.py) | `test_rds_lean_formal_plan_cli.py:38 self.assertEqual(result["assurance"], "NONE", result)` | `rds_cli`, `rds_verify`, `rds_affine_toolchain` |
| [`test_rds_lean_obligation.py`](../tests/test_rds_lean_obligation.py) | `test_rds_lean_obligation.py:38 self.assertEqual(out['assurance'], 'NONE')` | `rds_lean_obligation` |
| [`test_rds_lean_verify.py`](../tests/test_rds_lean_verify.py) | `test_rds_lean_verify.py:31 self.assertEqual(result["assurance"], "LEAN_KERNEL_CHECKED")` | `rds_lean_verify` |
| [`test_rds_math_router.py`](../tests/test_rds_math_router.py) | `test_rds_math_router.py:60 self.assertEqual(result["assurance"], "CERTIFICATE_CHECKED")` | `rds_math_router` |
| [`test_rds_methods.py`](../tests/test_rds_methods.py) | `test_rds_methods.py:62 self.assertEqual(report['assurance'], 'INPUT_REPORTED')` | `rds_methods`, `rds_advisor_search`, `rds_quick` |
| [`test_rds_native_research.py`](../tests/test_rds_native_research.py) | `test_rds_native_research.py:150 self.assertEqual(registered['assurance'], 'LOCAL_CASES_ONLY')` | `rds_math`, `rds_tools`, `rds_project` |
| [`test_rds_nn_verify.py`](../tests/test_rds_nn_verify.py) | `test_rds_nn_verify.py:32 self.assertEqual(result["assurance"], "CERTIFICATE_CHECKED")` | `rds_nn_verify` |
| [`test_rds_obstruction_receipts.py`](../tests/test_rds_obstruction_receipts.py) | `test_rds_obstruction_receipts.py:129 self.assertEqual(audit['assurance'], 'RECEIPT_EXECUTION_NOT_STATEMENT_VERIFICATIO` | `rds_project`, `rds_cli` |
| [`test_rds_operators.py`](../tests/test_rds_operators.py) | `test_rds_operators.py:40 self.assertEqual(report["assurance"], "NUMERICAL_DIAGNOSTIC")` | `rds_operators` |
| [`test_rds_polynomial_confirmation.py`](../tests/test_rds_polynomial_confirmation.py) | `test_rds_polynomial_confirmation.py:229 self.assertEqual(result['assurance'], 'FINITE_EXACT_QQ_POLYNOMIAL_EVALUATION')` | `rds_polynomial_confirmation` |
| [`test_rds_probe_adversarial.py`](../tests/test_rds_probe_adversarial.py) | `test_rds_probe_adversarial.py:49 self.assertEqual(admission["assurance"], "NONE")` | `rds_probe`, `rds_cli`, `rds_formal_kernel` |
| [`test_rds_progress_policy.py`](../tests/test_rds_progress_policy.py) | `test_rds_progress_policy.py:92 self.assertEqual(review["assurance"], "INPUT_REPORTED_NOT_SCIENTIFIC_VERIFICATION")` | `rds_advisor`, `rds_checkpoints` |
| [`test_rds_proof_cache.py`](../tests/test_rds_proof_cache.py) | `test_rds_proof_cache.py:267 self.assertEqual(second["assurance"], "CERTIFICATE_CHECKED")` | `rds_verify_types`, `rds_proof_cache` |
| [`test_rds_resources.py`](../tests/test_rds_resources.py) | `test_rds_resources.py:47 self.assertEqual(result["assurance"], "INPUT_ESTIMATE")` | `rds_resources`, `rds_cli` |
| [`test_rds_result_methods.py`](../tests/test_rds_result_methods.py) | `test_rds_result_methods.py:213 self.assertEqual(result['assurance'], 'FINITE_NUMERICAL_OBSERVATION')` | `rds_result_tools`, `rds_tools` |
| [`test_rds_selection_review.py`](../tests/test_rds_selection_review.py) | `test_rds_selection_review.py:84 self.assertEqual(review['goal']['assurance'], 'INPUT_REPORTED')` | `rds_advisor_search`, `rds_quick`, `rds_advisor` |
| [`test_rds_support.py`](../tests/test_rds_support.py) | `test_rds_support.py:47 self.assertEqual(report["assurance"], "HEURISTIC_ONLY")` | `rds_compress`, `rds_adversary`, `rds_advisor` |
| [`test_rds_tensor_verify.py`](../tests/test_rds_tensor_verify.py) | `test_rds_tensor_verify.py:27 self.assertEqual(result["assurance"], "CERTIFICATE_CHECKED")` | `rds_tensor_verify` |
| [`test_rds_theory_directions.py`](../tests/test_rds_theory_directions.py) | `test_rds_theory_directions.py:102 self.assertEqual(recommendations[0]['assurance'], 'HEURISTIC_ONLY')` | `rds_advisor_search`, `rds_advisor` |
| [`test_rds_theory_progression.py`](../tests/test_rds_theory_progression.py) | `test_rds_theory_progression.py:58 self.assertEqual(result["assurance"], "FINITE_DOMAIN_EXHAUSTIVE_PLUS_NATIVE_LEAVES")` | `rds_theory_progression`, `rds_lean_verify`, `rds_theory_tools` |
| [`test_rds_theory_tools.py`](../tests/test_rds_theory_tools.py) | `test_rds_theory_tools.py:248 self.assertEqual(report['positive']['assurance'], 'INPUT_REPORTED_AXIOM_AUDIT')` | `rds_theory_tools` |
| [`test_rds_tms.py`](../tests/test_rds_tms.py) | `test_rds_tms.py:215 self.assertEqual(json.loads(run.stdout)['assurance'], 'INPUT_REPORTED_DEPENDENCY_ANALYSIS_NOT_PROO` | `rds_hypergraph`, `rds_hypergraph_input`, `rds_advisor_search` |
| [`test_rds_tool_compare.py`](../tests/test_rds_tool_compare.py) | `test_rds_tool_compare.py:73 self.assertEqual(tools.register(self.root, 'after', result['candidate']['validation_id'])[` | `rds_tool_compare`, `rds_tools`, `rds_math` |
| [`test_rds_tool_workbench.py`](../tests/test_rds_tool_workbench.py) | `test_rds_tool_workbench.py:238 self.assertIn('NOT_PROFILE', hints['assurance'])` | `rds_project`, `rds_tool_workbench`, `rds_method_revision` |
| [`test_rds_verify_registration.py`](../tests/test_rds_verify_registration.py) | `test_rds_verify_registration.py:145 self.assertEqual(out["assurance"], "RT_ONLY")` | `rds_verify`, `rds_verify_registry`, `rds_builtin_verifiers` |

### 3.3 账本行身份字段钉死：ident（94 个文件）

`assertEqual(<账本字段>, <字面常量>)`：`kind`/`schema`/`sha256`/`contract_sha256` 之类。改账本行 schema、回执字段命名或哈希口径会命中这一组。

| 测试文件 | 依据（file:line） | 覆盖 scripts 模块 |
| --- | --- | --- |
| [`test_hypergraph.py`](../tests/test_hypergraph.py) | `test_hypergraph.py:203 self.assertEqual([r['kind'] for r in result['record_relations'] if r['to'] == 'opaque-d'], ['ru` | `rds_hypergraph` |
| [`test_import_graph.py`](../tests/test_import_graph.py) | `test_import_graph.py:44 self.assertEqual([e["kind"] for e in report["edges"]],` | `rds_core`, `rds_owned_advisor`, `rds_project` |
| [`test_jump_ai_consumer.py`](../tests/test_jump_ai_consumer.py) | `test_jump_ai_consumer.py:80 self.assertEqual(references[0]['request_sha256'], event['request']['sha256'])` | `rds_advisor_workset`, `rds_autonomy`, `rds_project` |
| [`test_jump_ai_packet.py`](../tests/test_jump_ai_packet.py) | `test_jump_ai_packet.py:75 self.assertEqual(context['sha256'], digest({k: v for k, v in context.items() if k != 'sha256` | `rds_jump`, `rds_structure`, `rds_project` |
| [`test_jump_continuous_boundary.py`](../tests/test_jump_continuous_boundary.py) | `test_jump_continuous_boundary.py:139 self.assertEqual(admission['receipts'], [r['sha256'] for r in store.snapshot()['r` | `rds_project`, `rds_structure`, `rds_autonomy` |
| [`test_jump_control_budget.py`](../tests/test_jump_control_budget.py) | `test_jump_control_budget.py:54 self.assertEqual(events[-2]['owner_claim_sha256'], claim['sha256'])` | `rds_structure`, `rds_autonomy`, `rds_project` |
| [`test_jump_generation.py`](../tests/test_jump_generation.py) | `test_jump_generation.py:53 self.assertEqual([(r['run_id'], r['sha256']) for r in store.snapshot()['receipts']], origin` | `rds_jump`, `rds_structure`, `rds_cli` |
| [`test_jump_interpreter_boundary.py`](../tests/test_jump_interpreter_boundary.py) | `test_jump_interpreter_boundary.py:436 self.assertEqual(jump.strict_json(raw)['kind'], 'refresh')` | `rds_jump`, `rds_project`, `rds_structure` |
| [`test_jump_lineage.py`](../tests/test_jump_lineage.py) | `test_jump_lineage.py:120 self.assertEqual(admission['receipts'], [r['sha256'] for r in store.snapshot()['receipts']])` | `rds_jump`, `rds_project`, `rds_structure` |
| [`test_jump_publication_boundaries.py`](../tests/test_jump_publication_boundaries.py) | `test_jump_publication_boundaries.py:125 self.assertEqual(len([e for e in events if e['kind'] == 'STRUCTURE_PROPOSAL'])` | `rds_jump`, `rds_structure`, `rds_project` |
| [`test_jump_review_boundaries.py`](../tests/test_jump_review_boundaries.py) | `test_jump_review_boundaries.py:296 self.assertEqual(state['receipts'][0]['sha256'], competed[0]['sha256'])` | `rds_jump`, `rds_project`, `rds_structure` |
| [`test_pr301_descendant_binding.py`](../tests/test_pr301_descendant_binding.py) | `test_pr301_descendant_binding.py:103 self.assertEqual(child.recover('r1')['sha256'], receipt['sha256'])` | `rds_workspace`, `rds_project`, `rds_cli` |
| [`test_pr301_linked_state_binding.py`](../tests/test_pr301_linked_state_binding.py) | `test_pr301_linked_state_binding.py:67 self.assertEqual(child.recover('r1')['sha256'], receipt['sha256'])` | `rds_project`, `rds_workspace` |
| [`test_pr301_paid_child_history.py`](../tests/test_pr301_paid_child_history.py) | `test_pr301_paid_child_history.py:68 self.assertEqual(self.allowance['contract_sha256'], self.before_owner['contract_sh` | `rds_workspace`, `rds_project`, `rds_method_revision` |
| [`test_pr301_partial_quick.py`](../tests/test_pr301_partial_quick.py) | `test_pr301_partial_quick.py:101 self.assertEqual(self.child.snapshot()['receipts'][0]['sha256'], receipt['sha256'])` | `rds_workspace`, `rds_project`, `rds_cli` |
| [`test_pr303_affirmed_obstruction.py`](../tests/test_pr303_affirmed_obstruction.py) | `test_pr303_affirmed_obstruction.py:84 self.assertEqual(selection['next_move']['kind'], before['next_move']['kind'])` | `rds_advisor_search`, `rds_owned_advisor`, `rds_project` |
| [`test_pr303_planning_review.py`](../tests/test_pr303_planning_review.py) | `test_pr303_planning_review.py:367 self.assertEqual(report['next_move']['kind'], 'RESOURCE_BLOCKED')` | `rds_advisor_search`, `rds_project`, `rds_quick` |
| [`test_pr303_workset_compaction_bound.py`](../tests/test_pr303_workset_compaction_bound.py) | `test_pr303_workset_compaction_bound.py:79 self.assertEqual(hashlib.sha256(raw).hexdigest(), ref['sha256'])` | `rds_project`, `rds_advisor_workset`, `rds_campaign` |
| [`test_pr311_native_tail_binding.py`](../tests/test_pr311_native_tail_binding.py) | `test_pr311_native_tail_binding.py:95 self.assertEqual(receipt['sha256'], digest({k: v for k, v in receipt.items() if k` | `rds_project`, `rds_math`, `rds_workspace` |
| [`test_rds_advisor_ancestor.py`](../tests/test_rds_advisor_ancestor.py) | `test_rds_advisor_ancestor.py:51 self.assertEqual(current(child)['sha256'], token)` | `rds_tms_store`, `rds_cli`, `rds_project` |
| [`test_rds_advisor_public_context.py`](../tests/test_rds_advisor_public_context.py) | `test_rds_advisor_public_context.py:119 self.assertEqual(current(root)['sha256'], next_token)` | `rds_project`, `rds_tms_store`, `rds_advisor` |
| [`test_rds_advisor_workset.py`](../tests/test_rds_advisor_workset.py) | `test_rds_advisor_workset.py:235 self.assertEqual(digest(rows), section['original']['sha256'])` | `rds_structure`, `rds_project`, `rds_advisor_workset` |
| [`test_rds_autonomy.py`](../tests/test_rds_autonomy.py) | `test_rds_autonomy.py:180 self.assertEqual([r['sha256'] for r in after['receipts']], [r['sha256'] for r in before['rece` | `rds_autonomy`, `rds_project`, `rds_owned_advisor` |
| [`test_rds_campaign_binding.py`](../tests/test_rds_campaign_binding.py) | `test_rds_campaign_binding.py:623 self.assertEqual(helper.native_receipt()['sha256'], receipt['sha256'])` | `rds_campaign`, `rds_project`, `rds_quick` |
| [`test_rds_campaign_cli.py`](../tests/test_rds_campaign_cli.py) | `test_rds_campaign_cli.py:95 self.assertEqual(report['context']['facts']['baseline.score']['kind'], 'UNKNOWN')` | `rds_math`, `rds_tools`, `rds_project` |
| [`test_rds_campaign_native.py`](../tests/test_rds_campaign_native.py) | `test_rds_campaign_native.py:61 self.assertEqual(read_checkpoint(db, 'failed-observation', root=self.root)['sha256'], c` | `rds_project`, `rds_checkpoints`, `rds_campaign` |
| [`test_rds_campaign_record_publication.py`](../tests/test_rds_campaign_record_publication.py) | `test_rds_campaign_record_publication.py:128 self.assertEqual(completed['record']['contract_sha256'], self.original['co` | `rds_math`, `rds_campaign`, `rds_quick` |
| [`test_rds_campaign_tail_settlement.py`](../tests/test_rds_campaign_tail_settlement.py) | `test_rds_campaign_tail_settlement.py:111 self.assertEqual(after['receipts'][0]['sha256'], receipt['sha256'])` | `rds_campaign`, `rds_project`, `rds_cli` |
| [`test_rds_choice_activation.py`](../tests/test_rds_choice_activation.py) | `test_rds_choice_activation.py:87 self.assertEqual(saved['contract_sha256'], self.before['contract_sha256'])` | `rds_project_lifecycle`, `rds_quick`, `rds_tms_store` |
| [`test_rds_cli_project_root_gate.py`](../tests/test_rds_cli_project_root_gate.py) | `test_rds_cli_project_root_gate.py:58 self.assertEqual(report['facts']['steps']['kind'], 'OBSERVED')` | `rds_cli` |
| [`test_rds_code_diff.py`](../tests/test_rds_code_diff.py) | `test_rds_code_diff.py:46 self.assertEqual(diff["schema"], "rds-code-diff-v1")` | `rds_code_diff`, `rds_cpu_jepa`, `rds_predictor_worker` |
| [`test_rds_compass_adversarial.py`](../tests/test_rds_compass_adversarial.py) | `test_rds_compass_adversarial.py:380 self.assertEqual(json.loads(raw)['kind'], 'COMPASS_HANDOFF')` | `rds_compass`, `rds_compass_model`, `rds_compass_declaration` |
| [`test_rds_compass_antiu.py`](../tests/test_rds_compass_antiu.py) | `test_rds_compass_antiu.py:50 self.assertEqual([hole['kind'] for hole in operator['holes']], ['CONST_INT', 'VAR', 'CONS` | `rds_compass_antiu` |
| [`test_rds_compass_hard_followup.py`](../tests/test_rds_compass_hard_followup.py) | `test_rds_compass_hard_followup.py:129 self.assertEqual(seen[disposition['packet_sha256']], 0, disposition)` | `rds_compass_declaration`, `rds_compass_drive`, `rds_compass_model` |
| [`test_rds_compass_model.py`](../tests/test_rds_compass_model.py) | `test_rds_compass_model.py:42 self.assertEqual(a['sha256'], b['sha256'])` | `rds_compass_model`, `rds_compass` |
| [`test_rds_compass_tools.py`](../tests/test_rds_compass_tools.py) | `test_rds_compass_tools.py:90 self.assertEqual(len({t['sha256'] for t in listed}), len(listed))` | `rds_compass`, `rds_compass_rsi`, `rds_compass_store` |
| [`test_rds_continuation.py`](../tests/test_rds_continuation.py) | `test_rds_continuation.py:58 self.assertEqual([r['sha256'] for r in repeated['receipts']], receipts)` | `rds_project`, `rds_autonomy`, `rds_continuation` |
| [`test_rds_costs.py`](../tests/test_rds_costs.py) | `test_rds_costs.py:62 self.assertEqual(identity["code_sha256"], invalid)` | `rds_costs`, `rds_verify_types` |
| [`test_rds_cross_format_reading.py`](../tests/test_rds_cross_format_reading.py) | `test_rds_cross_format_reading.py:73 self.assertEqual(digest(raw), answer["sha256"])` | `rds_verify_types`, `rds_artifacts`, `rds_project` |
| [`test_rds_decision_packet.py`](../tests/test_rds_decision_packet.py) | `test_rds_decision_packet.py:65 self.assertEqual(record["sha256"], hashlib.sha256((self.base / name).read_bytes()).hexd` | `rds_decision_packet`, `rds_research_import` |
| [`test_rds_decision_semantics.py`](../tests/test_rds_decision_semantics.py) | `test_rds_decision_semantics.py:37 self.assertEqual(move['kind'], 'DIAGNOSE_GOAL_GAP')` | `rds_advisor_search`, `rds_quick` |
| [`test_rds_dialogue.py`](../tests/test_rds_dialogue.py) | `test_rds_dialogue.py:124 self.assertEqual(result['instruction']['kind'], kind)` | `rds_project`, `rds_steering`, `rds_tms_store` |
| [`test_rds_execution_semantics.py`](../tests/test_rds_execution_semantics.py) | `test_rds_execution_semantics.py:83 self.assertEqual(result['receipt_sha256'], receipt['sha256'])` | `rds_owned_advisor`, `rds_project`, `rds_steering` |
| [`test_rds_feasibility.py`](../tests/test_rds_feasibility.py) | `test_rds_feasibility.py:399 self.assertEqual(report['feasibility']['repair_request']['kind'], 'IMPROVE_TOOL')` | `rds_feasibility`, `rds_project`, `rds_owned_advisor` |
| [`test_rds_frontier.py`](../tests/test_rds_frontier.py) | `test_rds_frontier.py:68 self.assertEqual(gap["kind"], "MISSING_BRIDGE")` | `rds_frontier` |
| [`test_rds_frontier_formal.py`](../tests/test_rds_frontier_formal.py) | `test_rds_frontier_formal.py:42 self.assertEqual(report["gaps"][0]["kind"], "FORMAL_OBLIGATION")` | `rds_frontier_proposals`, `rds_frontier`, `rds_verify` |
| [`test_rds_global_planning.py`](../tests/test_rds_global_planning.py) | `test_rds_global_planning.py:138 self.assertEqual(next(r for r in self.f.snapshot()['receipts'] if r['run_id'] == 'base` | `rds_project`, `rds_steering`, `rds_tms_store` |
| [`test_rds_host_hook.py`](../tests/test_rds_host_hook.py) | `test_rds_host_hook.py:91 self.assertEqual(guard["schema"], 1)` | `rds_project`, `rds_cli` |
| [`test_rds_hypergraph_archive.py`](../tests/test_rds_hypergraph_archive.py) | `test_rds_hypergraph_archive.py:350 self.assertEqual(selected["readable"]["type"], "cites_evidence_hash")` | `rds_hypergraph_archive`, `rds_hypergraph_view` |
| [`test_rds_outbox.py`](../tests/test_rds_outbox.py) | `test_rds_outbox.py:356 self.assertEqual(set(schema["properties"]["kind"]["enum"]), KINDS)` | `rds_project`, `rds_outbox` |
| [`test_rds_output_recovery.py`](../tests/test_rds_output_recovery.py) | `test_rds_output_recovery.py:174 self.assertEqual(observed['sha256'], receipt['sha256'])` | `rds_project`, `rds_outputs`, `rds_cli` |
| [`test_rds_owned_advisor.py`](../tests/test_rds_owned_advisor.py) | `test_rds_owned_advisor.py:178 self.assertEqual(receipt['sha256'], digest({k: v for k, v in receipt.items() if k != 'sh` | `rds_tms_store`, `rds_project`, `rds_cli` |
| [`test_rds_owned_history.py`](../tests/test_rds_owned_history.py) | `test_rds_owned_history.py:73 self.assertEqual(duplicate['sha256'], saved['sha256'])` | `rds_owned_history`, `rds_checkpoints`, `rds_project` |
| [`test_rds_owned_missing_observation.py`](../tests/test_rds_owned_missing_observation.py) | `test_rds_owned_missing_observation.py:45 self.assertEqual(fact['kind'], 'UNKNOWN')` | — |
| [`test_rds_owned_state.py`](../tests/test_rds_owned_state.py) | `test_rds_owned_state.py:77 self.assertEqual(state['completion_events'], [receipt['sha256']])` | `rds_owned_history`, `rds_owned_advisor` |
| [`test_rds_owned_theory_gate.py`](../tests/test_rds_owned_theory_gate.py) | `test_rds_owned_theory_gate.py:99 self.assertEqual(sum(event['kind'] == 'THEORY_ALLOWANCE' for event in events), 1)` | `rds_advisor`, `rds_project` |
| [`test_rds_owned_tools.py`](../tests/test_rds_owned_tools.py) | `test_rds_owned_tools.py:171 self.assertEqual(checkpoints[1]['decision']['execution']['receipt_sha256'], complete['rece` | `rds_math`, `rds_project`, `rds_method_revision` |
| [`test_rds_plugin_registry.py`](../tests/test_rds_plugin_registry.py) | `test_rds_plugin_registry.py:112 self.assertEqual(set(schema["properties"]["kind"]["enum"]), KINDS)` | `rds_plugin_registry`, `rds_verify_registry` |
| [`test_rds_presentation_assets.py`](../tests/test_rds_presentation_assets.py) | `test_rds_presentation_assets.py:152 self.assertEqual(receipt_inputs[asset]["role"], "data")` | `rds_quick`, `rds_hypergraph_view`, `rds_dashboard` |
| [`test_rds_progressive_routes.py`](../tests/test_rds_progressive_routes.py) | `test_rds_progressive_routes.py:149 self.assertEqual(indexed[relative]["sha256"], development.file_sha(original))` | `build_plugins` |
| [`test_rds_project.py`](../tests/test_rds_project.py) | `test_rds_project.py:174 self.assertEqual(self.store.recover(run_id)['sha256'], receipt['sha256'])` | `rds_project`, `rds_cli`, `rds_quick` |
| [`test_rds_project_assembly.py`](../tests/test_rds_project_assembly.py) | `test_rds_project_assembly.py:152 self.assertEqual(contract['schema'], 1)` | `rds_project_assembly`, `rds_project`, `rds_tms_store` |
| [`test_rds_project_events.py`](../tests/test_rds_project_events.py) | `test_rds_project_events.py:76 self.assertEqual([e["kind"] for e in events], kinds)` | `rds_project` |
| [`test_rds_project_next_shell.py`](../tests/test_rds_project_next_shell.py) | `test_rds_project_next_shell.py:165 self.assertEqual(status['next_move']['receipt_sha256'], before['receipts'][0]['sha2` | `rds_project`, `rds_cli` |
| [`test_rds_project_read_services.py`](../tests/test_rds_project_read_services.py) | `test_rds_project_read_services.py:90 self.assertEqual(autonomy._events(db, (body['kind'],)), [body])` | `rds_project`, `rds_owned_advisor`, `rds_steering` |
| [`test_rds_project_receipt_shape.py`](../tests/test_rds_project_receipt_shape.py) | `test_rds_project_receipt_shape.py:99 self.assertEqual(observed["sha256"], receipt["sha256"])` | `rds_project` |
| [`test_rds_quick.py`](../tests/test_rds_quick.py) | `test_rds_quick.py:266 self.assertEqual(retained['receipt']['sha256'], saved['receipt']['sha256'])` | `rds_project`, `rds_quick`, `rds_workspace` |
| [`test_rds_quick_checkpoint_recovery.py`](../tests/test_rds_quick_checkpoint_recovery.py) | `test_rds_quick_checkpoint_recovery.py:274 self.assertEqual(repeated['receipt']['sha256'], recovered['receipt']['sha256` | `rds_quick`, `rds_project`, `rds_cli` |
| [`test_rds_quick_coverage_admission.py`](../tests/test_rds_quick_coverage_admission.py) | `test_rds_quick_coverage_admission.py:187 self.assertEqual(current_steering(db)['sha256'], changed['received_revision']` | `rds_project`, `rds_quick`, `rds_steering` |
| [`test_rds_reference_services.py`](../tests/test_rds_reference_services.py) | `test_rds_reference_services.py:59 self.assertEqual(hashlib.sha256(raw).hexdigest(), OLD_AST["digest"])` | `rds_reference_values`, `rds_cli`, `rds_state` |
| [`test_rds_reformulation.py`](../tests/test_rds_reformulation.py) | `test_rds_reformulation.py:55 self.assertEqual([g["kind"] for g in report["gaps"]], ["THEORY_REFORMULATION"])` | `rds_frontier`, `rds_frontier_proposals`, `rds_cli` |
| [`test_rds_research_advisor.py`](../tests/test_rds_research_advisor.py) | `test_rds_research_advisor.py:99 self.assertEqual({r["kind"] for r in report["preflight"]}, {"PROJECT", "GOAL", "HYPOTH` | `rds_research_advisor`, `rds_core`, `rds_packet_graph` |
| [`test_rds_result_tools_cli.py`](../tests/test_rds_result_tools_cli.py) | `test_rds_result_tools_cli.py:35 self.assertEqual(len(row['receipt_sha256']), 64)` | — |
| [`test_rds_rsi.py`](../tests/test_rds_rsi.py) | `test_rds_rsi.py:72 self.assertNotEqual(before["sha256"], after["sha256"])` | `rds_rsi`, `rds_meta`, `rds_cli` |
| [`test_rds_search_allocation.py`](../tests/test_rds_search_allocation.py) | `test_rds_search_allocation.py:57 self.assertEqual([s['kind'] for s in initial['slots']], ['explore', 'explore'])` | `rds_structure`, `rds_search_allocation`, `rds_project` |
| [`test_rds_search_neighborhood.py`](../tests/test_rds_search_neighborhood.py) | `test_rds_search_neighborhood.py:133 self.assertEqual([s['kind'] for s in plan['slots']].count('explore'), 4)` | `rds_structure`, `rds_search_allocation`, `rds_project` |
| [`test_rds_self_development.py`](../tests/test_rds_self_development.py) | `test_rds_self_development.py:70 self.assertEqual(entry["sha256"], hashlib.sha256((self.target / entry["path"]).read_by` | — |
| [`test_rds_source_documents.py`](../tests/test_rds_source_documents.py) | `test_rds_source_documents.py:67 self.assertEqual((fact["kind"], fact["value"]), ("OBSERVED", 0.5))` | `rds_artifacts`, `rds_verify_types`, `rds_source_documents` |
| [`test_rds_steering.py`](../tests/test_rds_steering.py) | `test_rds_steering.py:94 self.assertEqual(result['evidence'][0]['receipt_sha256'], before['receipts'][0]['sha256'])` | `rds_project`, `rds_steering`, `rds_autonomy` |
| [`test_rds_structure.py`](../tests/test_rds_structure.py) | `test_rds_structure.py:150 self.assertEqual(after['contract_sha256'], before['contract_sha256'])` | `rds_structure`, `rds_tms_store`, `rds_project` |
| [`test_rds_successor.py`](../tests/test_rds_successor.py) | `test_rds_successor.py:309 self.assertEqual(blocked["kind"], "REPEAT_REJECTED_ROUTE")` | `rds_project`, `rds_project_lifecycle`, `rds_method_revision` |
| [`test_rds_tms_loop.py`](../tests/test_rds_tms_loop.py) | `test_rds_tms_loop.py:49 self.assertEqual(first['record_relations'][0]['kind'], 'run_lifecycle_fact')` | `rds_tms_store`, `rds_project`, `rds_hypergraph` |
| [`test_rds_tms_store.py`](../tests/test_rds_tms_store.py) | `test_rds_tms_store.py:41 self.assertEqual(current(self.root)['sha256'], published)` | `rds_tms_store`, `rds_project` |
| [`test_rds_tool_applicability.py`](../tests/test_rds_tool_applicability.py) | `test_rds_tool_applicability.py:129 self.assertEqual(result['code_sha256'], self.tool['asset']['sha256'])` | `rds_tool_applicability`, `rds_math`, `rds_tools` |
| [`test_rds_tool_calls.py`](../tests/test_rds_tool_calls.py) | `test_rds_tool_calls.py:58 self.assertEqual(found['contract_sha256'], self.store.snapshot()['contract_sha256'])` | `rds_tool_calls`, `rds_owned_advisor`, `rds_cli` |
| [`test_rds_tool_preparation.py`](../tests/test_rds_tool_preparation.py) | `test_rds_tool_preparation.py:118 self.assertEqual(self.native_receipt()['sha256'], receipt['sha256'])` | `rds_project`, `rds_tools`, `rds_tool_compare` |
| [`test_rds_trajectory.py`](../tests/test_rds_trajectory.py) | `test_rds_trajectory.py:463 self.assertEqual({e['kind'] for e in problems}, {'providers', 'tools', 'outcomes', 'receipt` | `rds_verify_types`, `rds_trajectory`, `rds_cli` |
| [`test_rds_transition.py`](../tests/test_rds_transition.py) | `test_rds_transition.py:174 self.assertEqual(prediction["source"]["sha256"], self.before["sha"])` | `rds_transition`, `rds_core` |
| [`test_rds_triple_affirmative.py`](../tests/test_rds_triple_affirmative.py) | `test_rds_triple_affirmative.py:106 self.assertEqual(review['flags'][0]['kind'], 'TRIPLE_AFFIRMATIVE_OPEN')` | `rds_verify_types`, `rds_advisor_search`, `rds_cli` |
| [`test_rds_wheel.py`](../tests/test_rds_wheel.py) | `test_rds_wheel.py:755 self.assertEqual(sha(wheel.path(artifact['path'])), artifact['sha256'])` | `rds_wheel`, `rds_project` |
| [`test_rds_wheel_workspace_admission.py`](../tests/test_rds_wheel_workspace_admission.py) | `test_rds_wheel_workspace_admission.py:96 self.assertEqual(status["contract_sha256"],` | `rds_wheel`, `rds_project`, `rds_workspace` |
| [`test_rds_workspace.py`](../tests/test_rds_workspace.py) | `test_rds_workspace.py:67 self.assertEqual(pointer["contract_sha256"], digest(self.contract))` | `rds_workspace`, `rds_project`, `rds_autonomy` |
| [`test_receipt_body_shape.py`](../tests/test_receipt_body_shape.py) | `test_receipt_body_shape.py:398 self.assertEqual(read_project_receipt(str(fixture.root), receipt['sha256'])['body'], re` | `rds_hypergraph`, `rds_project`, `rds_advisor_search` |
| [`test_structure_control_reservation.py`](../tests/test_structure_control_reservation.py) | `test_structure_control_reservation.py:110 self.assertEqual(self.store.snapshot()['receipts'][0]['sha256'], receipt['sh` | `rds_structure`, `rds_project`, `rds_jump` |

### 3.4 人工复核后归入 contract（3 个文件）

初筛规则没命中，读断言后确认是形状钉死，人工补进来。

| 测试文件 | 依据（file:line） | 覆盖 scripts 模块 |
| --- | --- | --- |
| [`test_rds_feasibility_values.py`](../tests/test_rds_feasibility_values.py) | `test_rds_feasibility_values.py:23 导入面形状钉死：assertEqual(imported - {...}, set(...))` | `rds_feasibility_values`, `rds_feasibility`, `rds_project` |
| [`test_rds_lean_plan_optimizer.py`](../tests/test_rds_lean_plan_optimizer.py) | `test_rds_lean_plan_optimizer.py:43 调度形状钉死：assertEqual([item['name'] for item in ...schedule], [...])` | `rds_verify`, `rds_lean_verify` |
| [`test_rds_plan_optimizer.py`](../tests/test_rds_plan_optimizer.py) | `test_rds_plan_optimizer.py:31 调度形状钉死：assertEqual([item['name'] for item in report['optimization']['schedule']], [...])` | `rds_verify` |

## 4. decision（30 个文件）

覆盖 `decide` / Advisor 选路 / 阈值 / 对比臂 / holdout / 排名取舍的测试。

| 测试文件 | 依据（file:line） | 覆盖 scripts 模块 |
| --- | --- | --- |
| [`test_pr274_final_review_boundaries.py`](../tests/test_pr274_final_review_boundaries.py) | `test_pr274_final_review_boundaries.py:124 self.assertEqual(result[arm]['run_status'], 'SUCCEEDED')` | `rds_project_lifecycle`, `rds_project`, `rds_campaign` |
| [`test_pr274_regular_inputs.py`](../tests/test_pr274_regular_inputs.py) | `test_pr274_regular_inputs.py:47 self.assertEqual(result['decision_requirements']['status'], 'FAIL')` | `rds_hypergraph_view`, `rds_bounded_io`, `rds_blind_runner` |
| [`test_pr303_owned_open_planning.py`](../tests/test_pr303_owned_open_planning.py) | `test_pr303_owned_open_planning.py:81 self.assertEqual(search['selection_review']['goal']['status'], 'TRUE')` | `rds_tms_store`, `rds_owned_advisor`, `rds_project` |
| [`test_pysr_rank.py`](../tests/test_pysr_rank.py) | `test_pysr_rank.py:37 self.assertEqual(result['ranked_total'], 3)` | `rds_compass_model` |
| [`test_pysr_rank_continuous.py`](../tests/test_pysr_rank_continuous.py) | `test_pysr_rank_continuous.py:36 self.assertEqual(result['ranked_total'], 3)` | `rds_compass_model` |
| [`test_rds_admission_values.py`](../tests/test_rds_admission_values.py) | `test_rds_admission_values.py:301 self.assertEqual(updated['decision']['evidence']['status'], 'UNKNOWN')` | `rds_project_values`, `rds_admission_values`, `rds_owned_advisor` |
| [`test_rds_advisor_dependency_reuse.py`](../tests/test_rds_advisor_dependency_reuse.py) | `test_rds_advisor_dependency_reuse.py:62 self.assertEqual(result["selection_review"]["ready_graph_directions"], 3)` | `rds_advisor_search`, `rds_cli` |
| [`test_rds_broker_hook.py`](../tests/test_rds_broker_hook.py) | `test_rds_broker_hook.py:28 self.assertEqual(output['permissionDecision'], 'deny')` | `rds_broker_hook` |
| [`test_rds_campaign_report_boundary.py`](../tests/test_rds_campaign_report_boundary.py) | `test_rds_campaign_report_boundary.py:86 self.assertEqual(store.last_advisor_review['status'], 'COLLECTION_FAILED')` | `rds_campaign`, `rds_quick`, `rds_dialogue` |
| [`test_rds_campaign_settlement.py`](../tests/test_rds_campaign_settlement.py) | `test_rds_campaign_settlement.py:164 with self.assertRaisesRegex(RuntimeError, 'original comparison'):` | `rds_campaign`, `rds_project`, `rds_method_revision` |
| [`test_rds_capability_benchmark.py`](../tests/test_rds_capability_benchmark.py) | `test_rds_capability_benchmark.py:97 self.assertLess(pooled, params["threshold"])` | — |
| [`test_rds_checkpoint_decision.py`](../tests/test_rds_checkpoint_decision.py) | `test_rds_checkpoint_decision.py:109 self.assertEqual(retained["record"]["decision"], decision)` | `rds_checkpoint_decision`, `rds_checkpoints` |
| [`test_rds_checkpoints.py`](../tests/test_rds_checkpoints.py) | `test_rds_checkpoints.py:131 self.assertEqual(self.call("decide", "--run", restored["completed_runs"][0]["run_id"])["as` | `rds_checkpoints`, `rds_cli` |
| [`test_rds_compass_benchmark.py`](../tests/test_rds_compass_benchmark.py) | `test_rds_compass_benchmark.py:26 self.assertEqual((rows[arm]['accepted'], rows[arm]['false_certification']), ('variant` | — |
| [`test_rds_compass_interval.py`](../tests/test_rds_compass_interval.py) | `test_rds_compass_interval.py:109 self.assertIsNone(model.decide(self.predicted, straddling, self.tolerances['y']))` | `rds_compass_model` |
| [`test_rds_compass_routing.py`](../tests/test_rds_compass_routing.py) | `test_rds_compass_routing.py:65 路由/预算决策：assertEqual(result['steps'][0]['action'], 'ESCAPE')` | `rds_compass`, `rds_compass_store` |
| [`test_rds_cpu_jepa.py`](../tests/test_rds_cpu_jepa.py) | `test_rds_cpu_jepa.py:211 self.assertEqual(pred["selection_effect"], "NONE")` | `rds_cpu_jepa`, `rds_predictor_worker`, `rds_predictor_contract` |
| [`test_rds_discrimination.py`](../tests/test_rds_discrimination.py) | `test_rds_discrimination.py:64 self.assertFalse(feedback['discrimination']['measured']['value'])` | `rds_structure`, `rds_discrimination`, `rds_project` |
| [`test_rds_frontier_cli.py`](../tests/test_rds_frontier_cli.py) | `test_rds_frontier_cli.py:99 self.assertIn("explicit direction-search decision", result.stderr)` | `rds_cli` |
| [`test_rds_graph_ranker.py`](../tests/test_rds_graph_ranker.py) | `test_rds_graph_ranker.py:39 self.assertTrue(report['selection_applied'])` | `rds_graph_ranker`, `rds_project`, `rds_owned_advisor` |
| [`test_rds_matrix_verify.py`](../tests/test_rds_matrix_verify.py) | `test_rds_matrix_verify.py:66 self.assertEqual(matrix_verifier.verify(spec([["100"]], threshold="101"))["status"], "PAS` | `rds_matrix_verify` |
| [`test_rds_offline_contracts.py`](../tests/test_rds_offline_contracts.py) | `test_rds_offline_contracts.py:178 self.assertEqual(report["selection_effect"], "NONE")` | `rds_predictor_contract`, `rds_retrieval_contract`, `rds_offline_contract_values` |
| [`test_rds_owned_telemetry.py`](../tests/test_rds_owned_telemetry.py) | `test_rds_owned_telemetry.py:148 with self.assertRaisesRegex(ValueError, 'Owned state changed after Advisor selection')` | `rds_owned_advisor`, `rds_project`, `rds_cli` |
| [`test_rds_predictor_worker.py`](../tests/test_rds_predictor_worker.py) | `test_rds_predictor_worker.py:38 self.assertEqual(report["prediction"]["selection_effect"], "NONE")` | `rds_predictor_worker`, `rds_predictor_contract` |
| [`test_rds_project_next.py`](../tests/test_rds_project_next.py) | `test_rds_project_next.py:47 self.assertIn("record the decision", move["next_move"])` | `rds_project` |
| [`test_rds_research_note.py`](../tests/test_rds_research_note.py) | `test_rds_research_note.py:14 with self.assertRaisesRegex(ValueError, "checkpoint save --decision"):` | `rds_research_note` |
| [`test_rds_research_note_cli.py`](../tests/test_rds_research_note_cli.py) | `test_rds_research_note_cli.py:23 self.assertIn("checkpoint save --decision", result.stderr)` | `rds_cli` |
| [`test_rds_result_diagnostics.py`](../tests/test_rds_result_diagnostics.py) | `test_rds_result_diagnostics.py:46 self.assertIsNone(head['acceptance'][label]['rate'])` | `rds_result_tools`, `rds_tools` |
| [`test_rds_result_methods_cli.py`](../tests/test_rds_result_methods_cli.py) | `test_rds_result_methods_cli.py:72 self.assertEqual(output['cases'][0]['value']['heads']['gate']['acceptance']['harm'][` | — |
| [`test_rds_result_methods_replay.py`](../tests/test_rds_result_methods_replay.py) | `test_rds_result_methods_replay.py:23 self.assertEqual(result['decision_requirements']['status'], 'FAIL')` | — |

## 5. regression（126 个文件）

覆盖具体缺陷修复、边界行为、失败路径、恢复与幂等、篡改检测的测试。数量最多，是「改实现后默认要重跑」的集合。

| 测试文件 | 依据（file:line） | 覆盖 scripts 模块 |
| --- | --- | --- |
| [`test_bash_environment_launch_boundary.py`](../tests/test_bash_environment_launch_boundary.py) | `test_bash_environment_launch_boundary.py:123 self.assertEqual(parent, before)` | `rds_project` |
| [`test_dependency_performance.py`](../tests/test_dependency_performance.py) | `test_dependency_performance.py:49 self.assertIs(sys.modules['rds_hypergraph_blockers'], candidate)` | `rds_hypergraph_blockers` |
| [`test_hypergraph_blockers.py`](../tests/test_hypergraph_blockers.py) | `test_hypergraph_blockers.py:74 self.assertFalse(result['truncated'])` | `rds_hypergraph` |
| [`test_jump_ambient_startup_boundary.py`](../tests/test_jump_ambient_startup_boundary.py) | `test_jump_ambient_startup_boundary.py:43 self.assertEqual(len(f.jump.load_plan(store, before)['stages']), 3)` | `rds_autonomy_worker` |
| [`test_jump_kernel_routes.py`](../tests/test_jump_kernel_routes.py) | `test_jump_kernel_routes.py:57 with self.assertRaisesRegex(ValueError, 'exactly match.*Advisor route'):` | `rds_project`, `rds_jump`, `rds_structure` |
| [`test_jump_python_module_boundary.py`](../tests/test_jump_python_module_boundary.py) | `test_jump_python_module_boundary.py:45 with self.assertRaisesRegex(ValueError, 'Python module execution'):` | — |
| [`test_jump_python_startup_boundary.py`](../tests/test_jump_python_startup_boundary.py) | `test_jump_python_startup_boundary.py:37 with self.assertRaisesRegex(ValueError,refusal):f.jump.load_plan(store,before)` | — |
| [`test_jump_rscript_startup_boundary.py`](../tests/test_jump_rscript_startup_boundary.py) | `test_jump_rscript_startup_boundary.py:47 self.assertEqual(len(jump.load_plan(store, before)['stages']), 3)` | — |
| [`test_jump_ruby_archive_boundary.py`](../tests/test_jump_ruby_archive_boundary.py) | `test_jump_ruby_archive_boundary.py:20 self.assertEqual(len(blocks), 1)` | `rds_hypergraph_archive` |
| [`test_jump_search.py`](../tests/test_jump_search.py) | `test_jump_search.py:28 with self.subTest(ast=ast, env=env), self.assertRaises(ValueError):` | `rds_jump_search` |
| [`test_jump_startup_controls.py`](../tests/test_jump_startup_controls.py) | `test_jump_startup_controls.py:44 with self.subTest(options=options), self.assertRaisesRegex(` | `rds_jump`, `rds_project`, `rds_structure` |
| [`test_lean_updater_windows_fixture.py`](../tests/test_lean_updater_windows_fixture.py) | `test_lean_updater_windows_fixture.py:37 self.assertEqual(result.skipped, [])` | — |
| [`test_newtonbench_comparability.py`](../tests/test_newtonbench_comparability.py) | `test_newtonbench_comparability.py:106 self.assertNotIn('seconds_total', kept)` | — |
| [`test_newtonbench_tolerance.py`](../tests/test_newtonbench_tolerance.py) | `test_newtonbench_tolerance.py:37 容差带缺陷：assertEqual(offenders, [], 'these equations need a relative band')` | `rds_compass_model` |
| [`test_pr274_bounded_readers.py`](../tests/test_pr274_bounded_readers.py) | `test_pr274_bounded_readers.py:45 with self.assertRaisesRegex(ValueError,'shebang'):` | `rds_campaign`, `rds_project`, `rds_dialogue` |
| [`test_pr274_campaign_marker_inputs.py`](../tests/test_pr274_campaign_marker_inputs.py) | `test_pr274_campaign_marker_inputs.py:20 with self.assertRaisesRegex(ValueError, 'regular file'):` | `rds_campaign` |
| [`test_pr274_jump_plan_inputs.py`](../tests/test_pr274_jump_plan_inputs.py) | `test_pr274_jump_plan_inputs.py:28 with self.assertRaisesRegex(ValueError, 'Jump plan must be a regular file'):` | `rds_jump` |
| [`test_pr274_quick_reserved_inputs.py`](../tests/test_pr274_quick_reserved_inputs.py) | `test_pr274_quick_reserved_inputs.py:30 self.assertEqual(after[name],before[name],name)` | `rds_quick`, `rds_project` |
| [`test_pr301_admission_reads.py`](../tests/test_pr301_admission_reads.py) | `test_pr301_admission_reads.py:68 self.assertFalse(reading.done(), 'Admission must wait for pointer and ledger commit')` | `rds_workspace`, `rds_project`, `rds_cli` |
| [`test_pr301_binding_publication.py`](../tests/test_pr301_binding_publication.py) | `test_pr301_binding_publication.py:49 self.assertEqual(workspace.coverage(self.root)['status'], 'BOUND')` | `rds_workspace`, `rds_project` |
| [`test_pr301_campaign_lock_order.py`](../tests/test_pr301_campaign_lock_order.py) | `test_pr301_campaign_lock_order.py:33 锁顺序缺陷：assertFalse/assertTrue(gate_available_to_other_thread())` | `rds_campaign` |
| [`test_pr301_controller_binding.py`](../tests/test_pr301_controller_binding.py) | `test_pr301_controller_binding.py:24 with self.assertRaisesRegex(ValueError, 'active descendant'):` | `rds_workspace`, `rds_cli`, `rds_autonomy` |
| [`test_pr301_pending_revision_binding.py`](../tests/test_pr301_pending_revision_binding.py) | `test_pr301_pending_revision_binding.py:40 with self.assertRaisesRegex(OSError, 'interrupted descendant'):` | `rds_workspace`, `rds_method_revision` |
| [`test_pr301_pending_wheel_binding.py`](../tests/test_pr301_pending_wheel_binding.py) | `test_pr301_pending_wheel_binding.py:31 with self.assertRaisesRegex(ValueError, reason):` | `rds_workspace`, `rds_wheel`, `rds_project` |
| [`test_pr301_pointer_entry_damage.py`](../tests/test_pr301_pointer_entry_damage.py) | `test_pr301_pointer_entry_damage.py:44 with self.assertRaises(ValueError):` | `rds_workspace`, `rds_cli` |
| [`test_pr301_pointer_identity.py`](../tests/test_pr301_pointer_identity.py) | `test_pr301_pointer_identity.py:30 with self.assertRaisesRegex(ValueError, 'identity'):` | `rds_workspace` |
| [`test_pr301_quick_request_damage.py`](../tests/test_pr301_quick_request_damage.py) | `test_pr301_quick_request_damage.py:36 with self.assertRaises(ValueError):` | `rds_workspace`, `rds_cli` |
| [`test_pr301_reference_binding_serialization.py`](../tests/test_pr301_reference_binding_serialization.py) | `test_pr301_reference_binding_serialization.py:47 self.assertEqual(future.result(timeout=5)['status'], 'BOUND')` | `rds_workspace`, `rds_checkpoints`, `rds_project` |
| [`test_pr301_reference_checkpoint_admission.py`](../tests/test_pr301_reference_checkpoint_admission.py) | `test_pr301_reference_checkpoint_admission.py:84 with self.assertRaisesRegex(ValueError, message):` | `rds_workspace`, `rds_checkpoints`, `rds_cli` |
| [`test_pr301_workspace_inventory_limits.py`](../tests/test_pr301_workspace_inventory_limits.py) | `test_pr301_workspace_inventory_limits.py:33 with self.assertRaisesRegex(ValueError, reason):` | `rds_workspace`, `rds_project`, `rds_cli` |
| [`test_pr311_advisor_workspace_admission.py`](../tests/test_pr311_advisor_workspace_admission.py) | `test_pr311_advisor_workspace_admission.py:35 with self.assertRaisesRegex(ValueError, 'identity does not match'):` | `rds_workspace`, `rds_advisor`, `rds_cli` |
| [`test_pr311_meta_workspace_admission.py`](../tests/test_pr311_meta_workspace_admission.py) | `test_pr311_meta_workspace_admission.py:50 self.assertEqual((graph.read_bytes(), self.f.dump(self.child)), before)` | `rds_workspace`, `rds_host_hook`, `rds_rsi` |
| [`test_pr311_pending_quick_allowance.py`](../tests/test_pr311_pending_quick_allowance.py) | `test_pr311_pending_quick_allowance.py:41 with self.assertRaisesRegex(ValueError, 'active descendant'):` | `rds_quick`, `rds_workspace`, `rds_project` |
| [`test_pr311_proof_cache_workspace_admission.py`](../tests/test_pr311_proof_cache_workspace_admission.py) | `test_pr311_proof_cache_workspace_admission.py:39 self.assertEqual(self.f.dump(self.child), before)` | `rds_proof_cache`, `rds_workspace` |
| [`test_pr311_tool_workspace_admission.py`](../tests/test_pr311_tool_workspace_admission.py) | `test_pr311_tool_workspace_admission.py:66 with self.assertRaisesRegex(ValueError, 'identity does not match'):` | `rds_tools`, `rds_workspace`, `rds_project` |
| [`test_pr311_wheel_binding_serialization.py`](../tests/test_pr311_wheel_binding_serialization.py) | `test_pr311_wheel_binding_serialization.py:53 with self.assertRaises(rds_wheel.WheelError):` | `rds_workspace`, `rds_project` |
| [`test_pulse_patch.py`](../tests/test_pulse_patch.py) | `test_pulse_patch.py:38 具体反例回归：assertEqual(counterexamples[0]['id'], 'x20')` | `rds_compass_model` |
| [`test_pysr_adapter.py`](../tests/test_pysr_adapter.py) | `test_pysr_adapter.py:38 解析器行为：assertEqual(parsed_ast['op'], 'add')` | `rds_compass_model` |
| [`test_rds_admission_read.py`](../tests/test_rds_admission_read.py) | `test_rds_admission_read.py:43 with self.assertRaises(FrozenInstanceError):` | `rds_admission_read`, `rds_project` |
| [`test_rds_advancement_cli.py`](../tests/test_rds_advancement_cli.py) | `test_rds_advancement_cli.py:54 self.assertEqual(report["status"], "INVALID")` | `rds_cli` |
| [`test_rds_advisor_concurrency.py`](../tests/test_rds_advisor_concurrency.py) | `test_rds_advisor_concurrency.py:53 self.assertEqual(report["total_knowledge_entries"], 2)` | `rds_advisor` |
| [`test_rds_advisor_review_regressions.py`](../tests/test_rds_advisor_review_regressions.py) | `test_rds_advisor_review_regressions.py:32 self.assertIn('[RDS-REJECT]', result.stderr)` | `rds_owned_advisor`, `rds_quick`, `rds_tms_store` |
| [`test_rds_autonomy_admission.py`](../tests/test_rds_autonomy_admission.py) | `test_rds_autonomy_admission.py:63 self.assertEqual(case.store.snapshot(), before)` | `rds_autonomy_admission`, `rds_autonomy`, `rds_project_values` |
| [`test_rds_autonomy_baseline.py`](../tests/test_rds_autonomy_baseline.py) | `test_rds_autonomy_baseline.py:126 self.assertNotIn(run['run_id'], [r['run_id'] for r in executed])` | `rds_domain_confirmation`, `rds_project`, `rds_cli` |
| [`test_rds_autonomy_read.py`](../tests/test_rds_autonomy_read.py) | `test_rds_autonomy_read.py:40 self.assertEqual(db.total_changes, before)` | `rds_autonomy_read`, `rds_project_values` |
| [`test_rds_blind_runner.py`](../tests/test_rds_blind_runner.py) | `test_rds_blind_runner.py:83 self.assertNotIn('SECRET', json.dumps(projected))` | `rds_blind_runner` |
| [`test_rds_bus_adapter.py`](../tests/test_rds_bus_adapter.py) | `test_rds_bus_adapter.py:47 self.assertEqual(graph.add_edge(bus.EDGE_AND_DEDUCTION, [p0, p1], lemma), 0)` | `rds_bus_adapter` |
| [`test_rds_bus_integration.py`](../tests/test_rds_bus_integration.py) | `test_rds_bus_integration.py:65 self.assertNotIn("proposed-goal", native_result["declared_supported_closure"])` | `rds_hypergraph`, `rds_bus_adapter` |
| [`test_rds_campaign_durability.py`](../tests/test_rds_campaign_durability.py) | `test_rds_campaign_durability.py:29 self.assertEqual(self.fixture.store.snapshot()[key], before[key])` | `rds_campaign` |
| [`test_rds_campaign_exports.py`](../tests/test_rds_campaign_exports.py) | `test_rds_campaign_exports.py:74 with self.assertRaisesRegex(ValueError, 'canonical project ledger'):` | `rds_project`, `rds_campaign`, `rds_tool_application` |
| [`test_rds_campaign_publication.py`](../tests/test_rds_campaign_publication.py) | `test_rds_campaign_publication.py:48 self.assertEqual(self.child.snapshot(), before)` | `rds_campaign`, `rds_tools`, `rds_project` |
| [`test_rds_campaign_read_boundary.py`](../tests/test_rds_campaign_read_boundary.py) | `test_rds_campaign_read_boundary.py:53 self.assertIn('canonical project ledger', self.read(f.root, 'reference')['error'` | `rds_campaign` |
| [`test_rds_campaign_reference_connections.py`](../tests/test_rds_campaign_reference_connections.py) | `test_rds_campaign_reference_connections.py:58 with self.assertRaisesRegex(ValueError, 'transaction'):` | `rds_cli`, `rds_campaign`, `rds_project` |
| [`test_rds_cli_error_tags.py`](../tests/test_rds_cli_error_tags.py) | `test_rds_cli_error_tags.py:48 self.assertIn("[RDS-REJECT] RDS is not initialized", result.stderr)` | `rds_cli` |
| [`test_rds_cli_hints.py`](../tests/test_rds_cli_hints.py) | `test_rds_cli_hints.py:53 self.assertTrue(result.stderr.startswith("[RDS-REJECT] Missing contract fields: budget\n"),` | `rds_cli` |
| [`test_rds_cli_input_errors.py`](../tests/test_rds_cli_input_errors.py) | `test_rds_cli_input_errors.py:51 self.assertEqual(result.stderr, "[RDS-REJECT] " + message + hint)` | `rds_cli` |
| [`test_rds_compass_decompose.py`](../tests/test_rds_compass_decompose.py) | `test_rds_compass_decompose.py:185 self.assertEqual(result['status'], 'SEARCH_TRUNCATED')` | `rds_compass_model`, `rds_compass_decompose` |
| [`test_rds_compass_fits_cache.py`](../tests/test_rds_compass_fits_cache.py) | `test_rds_compass_fits_cache.py:153 self.assertEqual(result['status'], 'NO_RIVAL_WITHIN_BOUND')` | `rds_compass`, `rds_compass_model`, `rds_compass_store` |
| [`test_rds_compass_interval_ledger.py`](../tests/test_rds_compass_interval_ledger.py) | `test_rds_compass_interval_ledger.py:240 with self.assertRaises(model.DomainError):` | `rds_compass_model`, `rds_compass`, `rds_compass_store` |
| [`test_rds_compass_source_seam.py`](../tests/test_rds_compass_source_seam.py) | `test_rds_compass_source_seam.py:48 self.assertNotIn('x + 7', [fit['text'] for fit in result['fits']])` | `rds_compass_model`, `rds_compass`, `rds_compass_declaration` |
| [`test_rds_compass_token_budget.py`](../tests/test_rds_compass_token_budget.py) | `test_rds_compass_token_budget.py:103 with self.assertRaisesRegex(ValueError, 'agent.max_tokens is required'):` | `rds_project`, `rds_compass_declaration`, `rds_compass` |
| [`test_rds_confirmation_admission.py`](../tests/test_rds_confirmation_admission.py) | `test_rds_confirmation_admission.py:75 self.assertEqual(store.snapshot(), before)` | `rds_postcommit_confirmation`, `rds_project_values` |
| [`test_rds_confirmation_privacy.py`](../tests/test_rds_confirmation_privacy.py) | `test_rds_confirmation_privacy.py:40 self.assertNotIn('private_target_canary', json.dumps(result))` | `rds_autonomy`, `rds_project` |
| [`test_rds_confirmation_read.py`](../tests/test_rds_confirmation_read.py) | `test_rds_confirmation_read.py:71 with self.assertRaisesRegex(ValueError, 'escapes'):` | `rds_confirmation_read`, `rds_original_read` |
| [`test_rds_continuous_example.py`](../tests/test_rds_continuous_example.py) | `test_rds_continuous_example.py:23 self.assertNotIn('labels.json', paths)` | `rds_autonomy` |
| [`test_rds_contract_read.py`](../tests/test_rds_contract_read.py) | `test_rds_contract_read.py:113 with self.assertRaisesRegex(ValueError, '^Advisor activation must add exactly one policy` | `rds_project_values`, `rds_contract_read` |
| [`test_rds_control_identity_cli.py`](../tests/test_rds_control_identity_cli.py) | `test_rds_control_identity_cli.py:52 self.assertIn("missing control identity: "+field,answer["reasons"])` | `rds_cli` |
| [`test_rds_core.py`](../tests/test_rds_core.py) | `test_rds_core.py:96 with self.assertRaises(TypeError):` | `rds_core`, `rds_cli`, `rds_quick` |
| [`test_rds_cycle_extraction.py`](../tests/test_rds_cycle_extraction.py) | `test_rds_cycle_extraction.py:123 self.assertFalse(imports & forbidden)` | `rds_hypergraph_view`, `rds_probe`, `rds_scalar_verify` |
| [`test_rds_dashboard.py`](../tests/test_rds_dashboard.py) | `test_rds_dashboard.py:55 self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)` | `rds_dashboard` |
| [`test_rds_declarative_admission.py`](../tests/test_rds_declarative_admission.py) | `test_rds_declarative_admission.py:53 self.assertIn("Formal gate FAIL", error)` | `rds_cli`, `rds_probe` |
| [`test_rds_development_cli.py`](../tests/test_rds_development_cli.py) | `test_rds_development_cli.py:50 self.assertEqual(answer["artifact_import"]["status"], "CONFLICT")` | `rds_cli` |
| [`test_rds_dynamics_probe.py`](../tests/test_rds_dynamics_probe.py) | `test_rds_dynamics_probe.py:37 with self.subTest(spec=str(spec)[:80]), self.assertRaises(ValueError):` | `rds_dynamics_probe` |
| [`test_rds_entrypoint_imports.py`](../tests/test_rds_entrypoint_imports.py) | `test_rds_entrypoint_imports.py:22 self.assertEqual([], [edge for edge in report['upward']` | `rds_adversary`, `rds_reference_values`, `rds_state` |
| [`test_rds_evidence_admission.py`](../tests/test_rds_evidence_admission.py) | `test_rds_evidence_admission.py:101 self.assertEqual(dependency["receipt_blocked_node_ids"], [])` | `rds_advisor_search`, `rds_project` |
| [`test_rds_execute_prevalidation.py`](../tests/test_rds_execute_prevalidation.py) | `test_rds_execute_prevalidation.py:69 self.assertEqual(order[:5], ['advisor', 'maintenance', 'parent-lock', 'guard', 'p` | `rds_project` |
| [`test_rds_explanation_harness.py`](../tests/test_rds_explanation_harness.py) | `test_rds_explanation_harness.py:37 with self.assertRaises(ValueError):` | — |
| [`test_rds_fit_trend.py`](../tests/test_rds_fit_trend.py) | `test_rds_fit_trend.py:85 self.assertEqual((done.returncode, json.loads(done.stderr)['status']), (2, 'INVALID'))` | `rds_compass`, `rds_fit_trend`, `rds_compass_store` |
| [`test_rds_formal_adversarial.py`](../tests/test_rds_formal_adversarial.py) | `test_rds_formal_adversarial.py:36 self.assertNotIn("certificate", answer)` | `rds_verify`, `rds_probe`, `rds_verify_types` |
| [`test_rds_frontier_history.py`](../tests/test_rds_frontier_history.py) | `test_rds_frontier_history.py:31 self.assertFalse(runner.typed_program_matches(tampered, item["input"], targets))` | — |
| [`test_rds_frontier_proposals.py`](../tests/test_rds_frontier_proposals.py) | `test_rds_frontier_proposals.py:48 self.assertNotIn("verified", result["subgraph"]["nodes"][0])` | `rds_frontier_proposals`, `rds_frontier` |
| [`test_rds_graph_io.py`](../tests/test_rds_graph_io.py) | `test_rds_graph_io.py:14 self.assertTrue(graph['edges'])` | `rds_meta` |
| [`test_rds_guard_values.py`](../tests/test_rds_guard_values.py) | `test_rds_guard_values.py:25 with self.subTest(raw=repr(raw)), self.assertRaises(ValueError):` | `rds_guard_values`, `rds_checkpoint_decision` |
| [`test_rds_history_preflight.py`](../tests/test_rds_history_preflight.py) | `test_rds_history_preflight.py:24 self.assertEqual(report["status"], "MISSING")` | `rds_obelisk` |
| [`test_rds_hypergraph_coverage.py`](../tests/test_rds_hypergraph_coverage.py) | `test_rds_hypergraph_coverage.py:35 self.assertEqual(result["node_analysis"]["b"]["minimal_missing_evidence_sets"], [["` | `rds_hypergraph` |
| [`test_rds_hypergraph_verdict.py`](../tests/test_rds_hypergraph_verdict.py) | `test_rds_hypergraph_verdict.py:49 self.assertEqual(result["operator_verdict"]["operator"], "bounded_finite_model")` | `rds_hypergraph`, `rds_operators`, `rds_hypergraph_input` |
| [`test_rds_launch_admission.py`](../tests/test_rds_launch_admission.py) | `test_rds_launch_admission.py:81 self.assertIsNone(owner.poll(), 'owner exited before native admission')` | `rds_owned_advisor`, `rds_cli`, `rds_project` |
| [`test_rds_launch_refusal.py`](../tests/test_rds_launch_refusal.py) | `test_rds_launch_refusal.py:39 self.assertTrue(any('withdrew' in error for error in receipt['errors']))` | `rds_project`, `rds_steering` |
| [`test_rds_lean_catalog.py`](../tests/test_rds_lean_catalog.py) | `test_rds_lean_catalog.py:59 self.assertEqual(result["status"], "PARTIAL")` | `rds_lean_catalog` |
| [`test_rds_lean_research_cli.py`](../tests/test_rds_lean_research_cli.py) | `test_rds_lean_research_cli.py:65 self.assertIn('Duplicate JSON key', run.stderr)` | `rds_lean_obligation`, `rds_lean_research` |
| [`test_rds_maintenance_admission.py`](../tests/test_rds_maintenance_admission.py) | `test_rds_maintenance_admission.py:57 self.assertEqual(store.recover(spec['id']), receipt)` | `rds_maintenance_admission` |
| [`test_rds_method_revision.py`](../tests/test_rds_method_revision.py) | `test_rds_method_revision.py:102 self.assertEqual(live['budget'], before['budget'])` | `rds_method_revision`, `rds_project`, `rds_checkpoints` |
| [`test_rds_model_export.py`](../tests/test_rds_model_export.py) | `test_rds_model_export.py:30 with self.assertRaises(ValueError):` | `rds_model_export` |
| [`test_rds_mutation.py`](../tests/test_rds_mutation.py) | `test_rds_mutation.py:69 with self.assertRaisesRegex(RuntimeError, 'original fixture failure'):` | `rds_mutation` |
| [`test_rds_native_recovery.py`](../tests/test_rds_native_recovery.py) | `test_rds_native_recovery.py:39 self.assertEqual(self.store.recover('r1'), receipt)` | `rds_project`, `rds_project_hooks`, `rds_quick_ledger` |
| [`test_rds_nn_analyze.py`](../tests/test_rds_nn_analyze.py) | `test_rds_nn_analyze.py:40 self.assertEqual(nn.lipschitz_bound(KINK, [['1', '3']])['lipschitz_upper'], '2')  # Slopes 1` | `rds_nn_analyze`, `rds_nn_verify` |
| [`test_rds_packet_graph.py`](../tests/test_rds_packet_graph.py) | `test_rds_packet_graph.py:31 self.assertTrue(all(e["status"] == "PROPOSED" for e in graph["hyperedges"]))` | `rds_packet_graph`, `rds_hypergraph`, `rds_research_import` |
| [`test_rds_path_identity.py`](../tests/test_rds_path_identity.py) | `test_rds_path_identity.py:51 self.assertFalse(identity.same(self.real / 'missing-a', self.real / 'missing-b'))` | `rds_path_identity` |
| [`test_rds_postcommit_confirmation.py`](../tests/test_rds_postcommit_confirmation.py) | `test_rds_postcommit_confirmation.py:124 self.assertEqual(result['confirmation_independence'],'GENERATED_AFTER_WEIGHT_C` | `rds_domain_confirmation`, `rds_project`, `rds_postcommit_confirmation` |
| [`test_rds_primary_action_coverage.py`](../tests/test_rds_primary_action_coverage.py) | `test_rds_primary_action_coverage.py:40 with self.subTest(selected=selected), self.assertRaisesRegex(ValueError, 'Compl` | `rds_advisor_search`, `rds_quick` |
| [`test_rds_project_admission.py`](../tests/test_rds_project_admission.py) | `test_rds_project_admission.py:104 self.assertTrue(parent_ready.wait(3), 'Parent never entered native initialization')` | `rds_project_lifecycle`, `rds_project`, `rds_project_assembly` |
| [`test_rds_project_records.py`](../tests/test_rds_project_records.py) | `test_rds_project_records.py:95 self.assertEqual(outcome(lambda value: records.decode_receipt(value, error_type, digest` | `rds_project`, `rds_project_records` |
| [`test_rds_project_run_integrity.py`](../tests/test_rds_project_run_integrity.py) | `test_rds_project_run_integrity.py:84 self.assertIn("[RDS-REJECT] Project run integrity failure: run 'r1' body", result` | `rds_project` |
| [`test_rds_project_values.py`](../tests/test_rds_project_values.py) | `test_rds_project_values.py:51 with self.assertRaises(ValueError) as original:` | `rds_project_values`, `rds_project` |
| [`test_rds_quick_input_bounds.py`](../tests/test_rds_quick_input_bounds.py) | `test_rds_quick_input_bounds.py:32 with self.assertRaisesRegex(ValueError, 'exceeds 128 files or 64 MiB'):` | `rds_quick` |
| [`test_rds_quick_preparation.py`](../tests/test_rds_quick_preparation.py) | `test_rds_quick_preparation.py:38 self.assertIn(error, result.stderr)` | `rds_project` |
| [`test_rds_rational_voronoi_verify.py`](../tests/test_rds_rational_voronoi_verify.py) | `test_rds_rational_voronoi_verify.py:29 self.assertEqual(result["certificate"]["core_certificate"]["boundary_checks"], ` | `rds_verify`, `rds_rational_voronoi_verify`, `rds_unit_disk_voronoi_core` |
| [`test_rds_release_compatibility.py`](../tests/test_rds_release_compatibility.py) | `test_rds_release_compatibility.py:37 self.assertEqual(path.read_bytes(), before)` | `rds_cli` |
| [`test_rds_research_policy.py`](../tests/test_rds_research_policy.py) | `test_rds_research_policy.py:85 self.assertEqual(rows, before)` | `rds_research_policy` |
| [`test_rds_result_benchmark_preflight.py`](../tests/test_rds_result_benchmark_preflight.py) | `test_rds_result_benchmark_preflight.py:21 self.assertIn('WorkspacePreflightError', proc.stderr)` | — |
| [`test_rds_rsi_confirmation.py`](../tests/test_rds_rsi_confirmation.py) | `test_rds_rsi_confirmation.py:39 self.assertEqual(report["status"], "REJECTED", report)` | `rds_rsi`, `rds_meta`, `rds_cli` |
| [`test_rds_run_records.py`](../tests/test_rds_run_records.py) | `test_rds_run_records.py:202 with self.assertRaisesRegex(ValueError, "Run ID already exists"):` | `rds_project`, `rds_run_records` |
| [`test_rds_scale_verify.py`](../tests/test_rds_scale_verify.py) | `test_rds_scale_verify.py:31 验证器边界：assertEqual(scale.verify(claim)['status'], 'UNKNOWN')` | `rds_scale_verify`, `rds_verify` |
| [`test_rds_session.py`](../tests/test_rds_session.py) | `test_rds_session.py:109 self.assertIn("Missing required session locator", result.stderr)` | `rds_session` |
| [`test_rds_session_isolation.py`](../tests/test_rds_session_isolation.py) | `test_rds_session_isolation.py:58 self.assertEqual(self.snapshot(self.a), before_a)` | `rds_conversation_hook`, `rds_session` |
| [`test_rds_skill_routes.py`](../tests/test_rds_skill_routes.py) | `test_rds_skill_routes.py:34 self.assertIn('campaign-setup.md#new-campaign-after-discovery', new_campaign)` | `rds_cli` |
| [`test_rds_tool_evolution_flow.py`](../tests/test_rds_tool_evolution_flow.py) | `test_rds_tool_evolution_flow.py:170 self.assertIsNone(blocked['selected_run'])` | `rds_project`, `rds_feasibility`, `rds_owned_advisor` |
| [`test_rds_usage.py`](../tests/test_rds_usage.py) | `test_rds_usage.py:60 self.assertEqual(exc.sqlite_errorcode & 255, sqlite3.SQLITE_READONLY)` | `rds_usage`, `rds_cli`, `rds_project` |
| [`test_rds_verify.py`](../tests/test_rds_verify.py) | `test_rds_verify.py:51 self.assertNotIn("certificate", result)` | `rds_verify`, `rds_verify_registry`, `rds_cli` |
| [`test_rds_verify_limits.py`](../tests/test_rds_verify_limits.py) | `test_rds_verify_limits.py:18 with self.assertRaises(ValueError):` | `rds_verify`, `rds_verify_types` |
| [`test_rds_windows_stdio_guard.py`](../tests/test_rds_windows_stdio_guard.py) | `test_rds_windows_stdio_guard.py:30 with self.assertRaisesRegex(ValueError, 'requires all standard streams'):` | `rds_project` |
| [`test_rds_workspace_admission.py`](../tests/test_rds_workspace_admission.py) | `test_rds_workspace_admission.py:67 with self.assertRaisesRegex(ValueError, 'charged allowance'):` | `rds_workspace`, `rds_project`, `rds_cli` |
| [`test_rds_workspace_read.py`](../tests/test_rds_workspace_read.py) | `test_rds_workspace_read.py:40 with self.assertRaises(sqlite3.OperationalError):` | `rds_workspace`, `rds_sqlite`, `rds_cas` |
| [`test_rds_workspace_shell.py`](../tests/test_rds_workspace_shell.py) | `test_rds_workspace_shell.py:49 self.assertEqual(result["status"], "BOUND")` | `rds_workspace`, `rds_cli`, `rds_project` |
| [`test_stage2_hypotheses.py`](../tests/test_stage2_hypotheses.py) | `test_stage2_hypotheses.py:56 self.assertNotIn('RIVALS', self.actions(root))` | `rds_compass`, `rds_compass_store` |
| [`test_update_lean_toolchain.py`](../tests/test_update_lean_toolchain.py) | `test_update_lean_toolchain.py:159 self.assertEqual(self.snapshot(), before)` | `update_lean_toolchain` |

## 6. smoke（2 个文件）

只验证「CLI 能跑 / 模块能导入 / 外部测试驱动能起来」，没有业务字段断言。

| 测试文件 | 依据（file:line） | 覆盖 scripts 模块 |
| --- | --- | --- |
| [`test_rds_omp_extension.py`](../tests/test_rds_omp_extension.py) | `test_rds_omp_extension.py:21 self.assertEqual(result.returncode, 0, result.stdout + result.stderr)` | — |
| [`test_rds_project_hooks.py`](../tests/test_rds_project_hooks.py) | `test_rds_project_hooks.py:29 self.assertEqual(result.returncode, 0, result.stderr)` | `rds_project_hooks` |

## 7. 计数校验

| 类别 | 文件数 |
| --- | ---: |
| contract | 171 |
| decision | 30 |
| regression | 126 |
| smoke | 2 |
| UNKNOWN | 0 |
| **合计** | **329** |

`tests/test_*.py` 实际有 329 个文件，四类相加 = 329，对上。

## 8. 反查表：改 R0/R1 模块至少要跑哪几个 contract 测试

R0/R1 不是自创的：`tools/import_graph.py:11` 的 `GROUPS` 给出仓库自己的责任分层
（同一分层在 `docs/architecture.md:276` 有表）。

| 层 | 责任 | 本表映射 |
| --- | --- | --- |
| L0 | 平台 I/O 原语、跨进程变更锁 | **R0** |
| L1 | 账本读写、准入、结算、回执与证书内核 | **R0** |
| L2 | 派生研究关系、状态投影、诊断数据 | **R1** |
| L3 | Advisor、提案、搜索、预测与研究策略 | **R1** |

「直接 contract 测试数」= 真正调用该模块（别名调用计数 > 0）且主类别为 contract 的文件数。只是 `import` 而没调用的不算。

| # | 模块（层） | 直接 contract 测试数 | 最少必跑集合 | 备注 |
| ---: | --- | ---: | --- | --- |
| 1 | `scripts/rds_cas.py` (L1) | 1 | `tests/test_rds_cas_service.py:50` AST 哈希、`:55` 字节/JSON 身份 | 全仓只有这一个文件直接钉 CAS；`rds_quick` 重导出 `cas_bytes`/`cas_json`，改签名会波及 QUICK 侧 |
| 2 | `scripts/rds_quick_ledger.py` (L1) | 1 | `tests/test_rds_quick_ledger_service.py:74` AST 哈希、`test_rds_quick_checkpoint_recovery.py` | allowance/checkpoint/pending 账本逻辑集中在此（`docs/architecture.md:181`） |
| 3 | `scripts/rds_checkpoints.py` (L1) | 13 | `tests/test_rds_checkpoints.py`、`test_rds_guardrails.py`、`test_rds_owned_history.py`、`test_rds_progress_policy.py` | 回执写入面最宽；18 个直接测试里 13 个是 contract |
| 4 | `scripts/rds_contract_read.py` (L1) | **0** | **无**（只有 1 个 regression：`tests/test_rds_contract_read.py`） | **风险**：ledger/CAS 血缘读取内核没有任何身份钉死测试 |
| 5 | `scripts/rds_sqlite.py` (L0) | **0** | **无**（只有 1 个 regression：`tests/test_rds_workspace_read.py:40`） | **风险**：事务原语本身无 contract 测试，只经上层模块间接覆盖 |
| 6 | `scripts/rds_project.py` (L1) | 81 | `tests/test_rds_project.py`、`test_pr301_linked_state_binding.py`、`test_rds_project_lifecycle.py`（+其余 78 个） | 128 个测试直接调用；改这里基本等于跑全套，别指望挑着跑 |
| 7 | `scripts/rds_verify.py` (L1) | 14 | `tests/test_rds_builtin_verifiers.py`、`test_rds_verify_registration.py`、`test_rds_domain_confirmation.py` | 证书内核装配点，contract 覆盖不同 verifier 路由 |
| 8 | `scripts/rds_checkpoint_decision.py` (L1) | **0** | **无**（2 个直接测试：`test_rds_checkpoint_decision.py` 判 decision，`test_rds_guard_values.py` 判 regression） | **风险**：`decide` 的落点没有字面量身份断言 |
| 9 | `scripts/rds_search_allocation.py` (L3) | 2 | `tests/test_rds_search_allocation.py:57` slots 形状、`test_rds_search_neighborhood.py` | 搜索预算分配的形状钉死，R1 决策资产 |
| 10 | `scripts/rds_advisor_search.py` (L3) | 18 | `tests/test_rds_advisor_search.py:222`、`test_rds_advisor_discrimination.py:71`、`test_rds_advisor_coverage.py:81` | Advisor 选路主入口；contract 多为 `pareto_front`/候选集合形状 |

读法：第 4、5、8 行是「一小改就要命」的位置——不是测试太多，而是**一个 contract 都没有**。

## 9. UNKNOWN

**UNKNOWN = 0 个文件。** 329 个文件全部落进四类之一，没有归类不明的残留。

但初筛有 9 个文件的断言没命中任何判据词（规则收窄后的盲区），是逐条读完断言人工归位的，列在这里备查：

| 文件 | 规则为何漏掉 | 人工归类 | 依据 |
| --- | --- | --- | --- |
| `test_newtonbench_tolerance.py` | 断言是 `assertEqual(offenders, [], 提示语)`，列表为空但没有边界词 | regression | `tests/test_newtonbench_tolerance.py:37` 容差带缺陷：assertEqual(offenders, [], 'these equations need a relative band') |
| `test_pr301_campaign_lock_order.py` | 断言是 `assertFalse/True(gate_available_to_other_thread())`，无判据词 | regression | `tests/test_pr301_campaign_lock_order.py:33` 锁顺序缺陷：assertFalse/assertTrue(gate_available_to_other_thread()) |
| `test_pulse_patch.py` | 断言是反例字段等值，词表里没有对应词 | regression | `tests/test_pulse_patch.py:38` 具体反例回归：assertEqual(counterexamples[0]['id'], 'x20') |
| `test_pysr_adapter.py` | 断言是 AST 解析结果等值，词表里没有对应词 | regression | `tests/test_pysr_adapter.py:38` 解析器行为：assertEqual(parsed_ast['op'], 'add') |
| `test_rds_scale_verify.py` | 断言是 `assertEqual(status, 'UNKNOWN')`，无边界词 | regression | `tests/test_rds_scale_verify.py:31` 验证器边界：assertEqual(scale.verify(claim)['status'], 'UNKNOWN') |
| `test_rds_compass_routing.py` | 断言是 `assertEqual(action, 'ESCAPE')`，决策词表未覆盖 action/trigger | decision | `tests/test_rds_compass_routing.py:65` 路由/预算决策：assertEqual(result['steps'][0]['action'], 'ESCAPE') |
| `test_rds_feasibility_values.py` | 断言是集合差集相等，形状判据要求首参是 set()/sorted() | contract | `tests/test_rds_feasibility_values.py:23` 导入面形状钉死：assertEqual(imported - {...}, set(...)) |
| `test_rds_lean_plan_optimizer.py` | 断言是列表推导式等值，形状判据未覆盖推导式左侧 | contract | `tests/test_rds_lean_plan_optimizer.py:43` 调度形状钉死：assertEqual([item['name'] for item in ...schedule], [...]) |
| `test_rds_plan_optimizer.py` | 断言是列表推导式等值，形状判据未覆盖推导式左侧 | contract | `tests/test_rds_plan_optimizer.py:31` 调度形状钉死：assertEqual([item['name'] for item in report['optimization']['schedule']], [...]) |

范围外的文件（不参与四分类）：

- 5 个 fixture：`tests/compass_fixture.py`、`tests/feasibility_cli_fixture.py`、`tests/startup_recovery_fixture.py`、`tests/tool_evolution_cli_fixture.py`、`tests/usage_cli_fixture.py`。共享 `setUp` 夹具，没有 `test_*` 方法。
- 2 个 Node 测试：`tests/hypergraph_archive_checks.cjs`、`tests/test_omp_status.mjs`。前者由 Python 侧按需调用，后者由 `tests/test_rds_omp_extension.py:21` 用 `node --test` 拉起；Python 侧只断言 `returncode == 0`，所以宿主文件判 smoke。
- 2 个仓外测试：`examples/self-development/test_driver.py`、`native/rds_bus/examples/test_graph_ctypes.py`。它们不在 `tests/`，但正是「336 = 334 + 2」的来源。

## 10. 风险清单（按「改完没人拦你」排序）

1. **`rds_contract_read.py` 零 contract 测试**：`effective_contract` / `contract_history` 是认证链的读侧，改坏了只有 1 个 regression 测试可能察觉。
2. **`rds_cas_read.py` 在 `tests/` 里完全没出现**：它是 `rds_autonomy_admission.py:13`、`rds_autonomy_read.py:9`、`rds_confirmation_read.py:5` 三条 R0 读路径的共同依赖（`blob()`），只经上层间接覆盖。
3. **`rds_advisor_choice.py`（L3 选路验证）零引用**：被 `rds_owned_advisor.py:443,697` 和 `rds_quick.py:20` 使用，没有任何测试点名它；路由合法性完全靠上层测试顺带覆盖。
4. **`rds_checkpoint_decision.py` 没有身份钉死**：它是 `decide` 的落点，2 个直接测试一个判 decision、一个判 regression，没有一条钉死它的输出字段。
5. **`rds_cas.py` 只有 1 个 contract 文件**：CAS 是认证资产最底层，`test_rds_cas_service.py` 钉了 2 个 AST 哈希 + 字节身份，没有第二个文件交叉验证。
6. **`tests/` 里从未出现的模块共 9 个**：`paid_query`、`rds_advisor_choice`、`rds_cas_read`、`rds_compass_dimension`、`rds_compass_report`、`rds_interrupt`、`rds_project_worker`、`rds_rsi_confirmation`、`rds_search_neighborhood`（最后这个由 `test_rds_search_neighborhood.py` 经 `rds_search_allocation` 间接调用，不是死代码；其余待单独确认）。

---

生成方式：`tests/test_*.py` 全量静态扫描（断言行正则 + import 别名校准的调用计数），规则迭代 5 轮 + 9 个文件人工复核。
未修改 `tests/`、`scripts/` 下任何文件。
