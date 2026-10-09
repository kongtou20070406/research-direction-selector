# クイックスタート：まず自然な言葉で

Agent には普段の言葉で RDS の利用を頼めます。最初に内部用語を覚えたり、研究の相談だけのために JSON を用意したりする必要はありません。研究の目標、関連ファイル、分析・計画・許可範囲内での実行のどれを望むかを伝えてください。

## 用途に合うプロンプトを選ぶ

### 既存の結果を理解する

> RDS を使って `[ログまたはレポートのパス]` にある結果を調べてください。実際に測定された内容と考えられる説明を分け、主な説明を区別できる最小の次の確認を提案してください。実験の実行やファイル編集はしないでください。

### 二つの実験案を比較する

> RDS を使って `[研究目標]` に対する `[案 A]` と `[案 B]` を比較してください。このプロジェクトで既に定めた指標とリソース上限を使い、結果ごとに何が分かるかを説明して、一つの案を推奨してください。判断が変わる情報が足りなければ私に確認してください。勝手に補ったり、実行を始めたりしないでください。

### 既存プロジェクトを続ける

> RDS を使ってこのプロジェクトを続けてください。まず記録済みの目標、完了済みの実行、現在の証拠、残りの予算を確認してください。分かっていることを要約し、次の一歩を一つ提案してください。プロジェクトを再初期化したり、実行を繰り返したりしないでください。

### 数学的主張を確認する

> RDS を使って次の主張を確認してください：`[主張]`。定義域、仮定、量化子をそのまま保ってください。対応する検査器が確認できることと未解決のことを示し、数値例を証明として扱わないでください。

角括弧内を実際の質問やパスに置き換えてください。プロジェクトに指標、評価方法、リソース上限が定められていない場合は、RDS はその不足を伝え、計画を変え得る情報だけを確認します。これらのプロンプトは分析を頼むものであり、明示していない実験や費用を許可するものではありません。

## 契約 JSON を手書きせずに CLI を試す

Python 3.11 以降が必要です。リポジトリ内から実行し、**新しい空のディレクトリ**を指定してください。準備スクリプトは小さな合成データと、それに対応するコード、評価器、プロトコル、ファイルハッシュを生成します。空でないディレクトリは拒否されます。

```powershell
python -B examples/project-runner/prepare.py --root ./my-project
python -B scripts/rds_cli.py --root ./my-project project init --mode quick --contract ./my-project/contract.json
python -B scripts/rds_cli.py --root ./my-project project status --brief
```

このコマンド列はローカルプロジェクトの準備と確認だけを行い、二つの実験は実行しません。完全な CPU デモを実行する場合は、[project runner の例](../examples/project-runner/README.md)と[リポジトリのクイックスタート手順](../README.ja-JP.md#決定的な実行受け入れカーネル)を参照してください。このデモは合成データを使っており、科学的確認や汎化を示すものではありません。

### 最小コントラクトテンプレート

`prepare.py` を実行できない場合でも、`project init` は次の正確な形式の手書きコントラクトを受け付けます。各 `sha256` はバインドするファイルの実際の 16 進 SHA-256、パスはプロジェクトルートからの相対パス、`min_useful_delta` は厳密な有理数文字列である必要があります。省略可能な項目（`objective_sha256`、`execution_policy`、`stop_policy`、`maintenance_allowance`）は[停止ポリシー](stop-policy.md)と[ネイティブリサーチ](native-research.md)に文書化されています。ハッシュを推測したコントラクトは拒否されます。

```json
{
  "schema": 1,
  "description": "CPU demonstration, not scientific confirmation",
  "bindings": [
    {"role": "code",      "path": "experiment.py", "sha256": "<sha256>"},
    {"role": "config",    "path": "config.json",   "sha256": "<sha256>"},
    {"role": "data",      "path": "data.csv",      "sha256": "<sha256>"},
    {"role": "evaluator", "path": "evaluate.py",   "sha256": "<sha256>"},
    {"role": "protocol",  "path": "protocol.json", "sha256": "<sha256>"}
  ],
  "allowed_commands": [
    ["python", "-B", "experiment.py", "--arm", "control",   "--output", "outputs/control.json"],
    ["python", "-B", "experiment.py", "--arm", "treatment", "--output", "outputs/treatment.json"]
  ],
  "output_roots": ["outputs"],
  "budget": {"wall_seconds": 20},
  "primary_metric": {"name": "mse", "direction": "min", "min_useful_delta": "1/100"}
}
```

## `[RDS-REJECT]` が表示されたら

そのコマンドの前提条件が満たされていないという意味です。それだけでは研究案の良し悪しを示しません。フィールドを推測して再送したり、プロジェクトを再初期化したりしないでください。正確なコマンド、プロジェクトのパス、出力全体を Agent に渡し、次のように頼んでください。

> この RDS の拒否を平易な言葉で説明してください。不足または無効な入力と、最小で安全な修正を示してください。まず現在のプロジェクト状態を確認してください。ファイル編集、再初期化、実験の実行、リソース消費はしないでください。

異なる RDS 台帳を初期化するコマンドがあります。現在の作業と実際のエラーに合うものを使い、推測で `init` と `project init` を置き換えないでください。修正が研究目標、方法、予算、データアクセスを変えたり、実行を開始したりする場合は、その変更を進めるか先に決めてください。

## 必要なときだけ詳しく読む

- [Agent の入口：短いコマンドと出力](agent-entry.md)
- [プロジェクトと開発ループ](development-loop.md)
- [研究ワークフロー](research-workflow.md)
- [形式検証](formal-verification.md)
