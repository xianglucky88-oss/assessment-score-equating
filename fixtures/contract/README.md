# 契约用例与快照

本目录是 `docs/contract/ambiguity-cases.md` 的支撑材料：每个 `case-*.json`
是一个**最小**输入，专门暴露当前契约的一处歧义；`snapshots/` 中是对应用
当前代码实际生成的输出（崩溃用例存为 `.expected.error.txt`）。

## 性质声明

- 这些文件是**文档资产**，不是测试套件：当前没有任何测试或 CI 自动执行它们
  （仓库无 CI 配置）。快照与代码一致这一事实，只在有人运行校验命令时成立。
- 快照由 `tools/regen_contract_snapshots.py` 生成，序列化方式与 CLI 完全一致
  （`json.dumps(..., ensure_ascii=False, indent=2, sort_keys=True)`）。

## 命令

```bash
# 校验快照与当前代码是否一致（不一致时退出码为 1 并列出差异）
python3 tools/regen_contract_snapshots.py --check

# 代码变更后重新生成快照（生成后必须人工逐条核对 diff）
python3 tools/regen_contract_snapshots.py
```

## 用例索引

| 用例 | 暴露的歧义 |
|---|---|
| case-01 | 全部作答被作废的考生从 `scores` 消失，而非 0 分记录 |
| case-02 | 缺键题上 `null` 作答得分、具体作答不得分 |
| case-03 | 重复作答行被重复计入 `attempted`/`raw` |
| case-04 | 银行家舍入 + 浮点误差：1.125→1.12、2.675→2.67、1.005→1.0 |
| case-05a | 非 dict 作答行被静默丢弃 |
| case-05b | 缺 `item` 字段直接 `KeyError` 崩溃 |
| case-06 | 未声明的 form 静默使用缺省量尺 10 |
| case-07a/07b | 键修订前后两份输出均无版本信息，无法自证来源 |
| case-08 | 作答行中的 `status` 字段被静默忽略，缺考标记无效 |
| case-09 | `void_items` 回显含从未出现的题；空 `scores` 仍为 `"ok"` |
| case-10 | 整数 vs 浮点量尺乘数使 `scale` 文本表示不同（`10` vs `10.0`） |

新增用例的规矩：一个用例只暴露一处歧义；输入保持最小；在
`docs/contract/ambiguity-cases.md` 中登记"两种矛盾解读 + 代码实际 + 待裁决问题"。
