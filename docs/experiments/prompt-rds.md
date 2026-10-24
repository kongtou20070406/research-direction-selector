@prompt-shared.md

# 记账方式（RDS 臂）

每次实验必须通过 RDS 记账，不要直接跑：

```bash
python -B "D:/Myfile/work space/RDS/research-direction-selector/scripts/rds_cli.py" \
  --root . exec --name <实验名> --bind code=<脚本> -- python <脚本>
```

- 首次返回 `SUCCEEDED` 并落回执。同一条命令再跑返回 `EXISTING_JOB`，不会重跑。
- 每次实验后往 `ledger.md` 追加一行：`实验名 | dev 平均 PSNR | 保留或丢弃 | 一句话原因`。
- 跑崩或超时也记一行，标 `FAILED`，不要删掉重来。
