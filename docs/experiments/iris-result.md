# iris 对照结果

口径文件 `docs/experiments/iris-criterion.md`。
口径 SHA256：`756ba82bc9bca12b832ebce312853e9231e11840cf5777f26915d929250fd955`。

脚本跑了两次，输出字节相同。结果 SHA256：`e5b34de7003db3ed0e22dad037b15eb4a2c54776cb43dec41306fdb08dfbd2d4`。
数字在 `docs/experiments/iris-result.json`。

## 开发集，22 个样本

- 多数类：7
- 只用花萼两列：16
- 四个特征：22
- 四列比两列多 6 个。冻结规则要求至少多 2 个，所以这一格的判定是 `ALL_FEATURES`。
- 两个模型都超过多数类至少 2 个。

## 测试集，只评一次，23 个样本

- 两列 15，四列 22，方向与开发集一致。
- 测试集没有参与选择。

## 不成立的说法

- 不证明四个特征在别的划分、别的 C 或其他协议下也更好。这里只有一格。
- 不证明科学发现。`scientific_support` 是 `UNKNOWN`。
- 没有调用语言模型。`model_identity` 是 `none`，`token_usage` 是空。不能写更省。
- 22/22 只说明这 22 个开发样本被判对，不说明模型无误。

## 开放任务卡

卡在 `docs/experiments/iris-card.json`。两条试验用同一条命令、同一个结果哈希，指标都是 6。
声明的试验上限是 2，所以卡的下一步是 `STOP`。
复用规则要求换一个记录 id、保持同一条命令路由。相同记录 id 会被账本拒绝，这是身份冲突，不是复用。
