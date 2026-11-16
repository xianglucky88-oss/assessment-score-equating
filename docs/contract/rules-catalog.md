# 成绩数据契约 · 规则目录

本文档是成绩数据契约的正文。它按**实际数据流**整理规则，供维护者在变更评审时引用。
阅读前请先了解三种标注约定（全维护包通用）：

- **【已验证】**：对当前代码实际运行确认过的行为，附代码位置。代码改动后必须重新验证。
- **【未裁决】**：当前契约（代码 + 文档）无法给出唯一答案的歧义点，需维护者在变更评审中裁决并记录。
- **【建议】**：本维护包提出的管理约定，**不是现状**，未经变更流程采纳前不得当作既成事实引用。

契约版本：**1.0**（本维护包首次固化当前行为；版本约定见 `docs/README.md`，属【建议】）。

---

## 1. 数据流总览

当前流水线只有一条路径，没有分支、没有外部依赖：

```
输入载荷 (JSON)
  └─ parser.load_records   提取 responses 行          src/.../parser.py
  └─ engine.calculate      作废过滤 → 分组 → 计分      src/.../engine.py
  └─ report.build_result   套输出包络                  src/.../report.py
  └─ cli.main              读文件 / 打印 JSON          src/.../cli.py
```

**关于"复核"环节：当前数据流中不存在复核环节。** 流水线没有任何复核状态、
复核标记或复核改分的输入/输出字段；成绩一经 `run()` 输出即为终态。目前若需
改分，唯一途径是修改输入载荷（作答、键、作废表或量尺）后重跑，且输出中不留
任何改分痕迹（见歧义例 case-07）。任何把复核纳入数据流的提议都属于**契约新增**，
必须走变更流程（见 `docs/maintenance/review-checklist.md`），不得在文档中
当作已有能力描述。

## 2. 原始事实与派生结果

### 2.1 原始事实（输入字段，流水线只读不写）

| 字段 | 契约含义 | 缺失/异常时的实际行为【已验证】 |
|---|---|---|
| `responses` | 作答流水，list of dict | 缺失 → 按空表处理，输出空 `scores`（parser.py:2）；非 list → `ValueError`（parser.py:3-4）；其中非 dict 行被**静默丢弃**（parser.py:5，见 case-05a） |
| `responses[].student` | 考生标识，分组键之一 | 缺失 → `KeyError` 崩溃（engine.py:5）；与 `form` 一起要求全载荷内类型一致且可比较，混合类型 → `TypeError`（engine.py:7 排序处） |
| `responses[].form` | 表单标识，分组键之一，量尺查找键 | 同上 |
| `responses[].item` | 题目标识，作废判定与键查找的依据 | 缺失 → `KeyError` 崩溃（engine.py:4，见 case-05b） |
| `responses[].answer` | 考生作答，与 `keys[item]` 做**相等性比较** | 任意 JSON 值均可；`null` 是合法值且会与缺失的键判等（见 case-02） |
| `keys` | 评分键，dict：item → 正确作答 | 缺失 → 按 `{}` 处理（engine.py:2）；某题无键 → 与该题的作答按 `None` 比较（engine.py:5） |
| `void_items` | 作废题清单，管理决定 | 缺失 → 按 `[]` 处理（engine.py:2）；转为 set，重复项无意义 |
| `scale` | 各 form 的量尺乘数 | 缺失 → 按 `{}` 处理；某 form 未声明 → **静默取 10**（engine.py:7，见 case-06） |

注意：`keys`、`void_items`、`scale` 虽属"参考数据/管理决定"，但在契约中与
作答同为原始事实——它们是每次运行的输入快照的一部分，流水线不生成它们。

### 2.2 派生结果（输出字段，全部由输入与代码版本决定）

| 输出字段 | 派生规则【已验证】 | 依赖的原始事实 |
|---|---|---|
| `project` | 常量 `"assessment-score-equating"`（pipeline.py:7） | 无 |
| `status` | 常量 `"ok"`（report.py:5）；**没有错误包络**，任何失败都以异常崩溃形式出现（CLI 退出码非 0，无 JSON 输出） | 无 |
| `scores[]` | 按 `(student, form)` 元组字典序排序（engine.py:7） | 全部作答行 |
| `scores[].student` / `scores[].form` | 分组键回显（原始事实的投影） | `responses[].student/form` |
| `scores[].attempted` | 该 (student, form) 下**非作废作答行的行数**；含重复行、含无键题（engine.py:5） | `responses`、`void_items` |
| `scores[].raw` | 非作废行中 `answer == keys.get(item)` 的计数（engine.py:5） | `responses`、`keys`、`void_items` |
| `scores[].scale` | `round(raw * scale.get(form, 10), 2)`（engine.py:7）：Python 银行家舍入 + 二进制浮点（见 case-04）；JSON number，文本表示随乘数类型变化（`10` vs `10.0`，见 case-10） | `raw`、`scale` |
| `void_items` | 输入 `void_items` 的**排序回显**，含从未出现在作答中的题（engine.py:8，见 case-09） | `void_items` |

### 2.3 派生结果的"存在性"规则

- 一个 `(student, form)` 只有在**至少一行非作废作答**时才出现在 `scores` 中。
  全部作答被作废、或根本没有作答行的考生，**不会**得到 0 分行，而是整条记录
  不存在（见 case-01）。【已验证】
- 输出中不存在"考生清单"概念；`scores` 的并集就是全部可见考生。【已验证】

## 3. 哪些时间点决定历史报告

**当前契约中不存在任何时间戳或版本字段**——输入没有 `effective_date`、
`key_version`，输出没有 `generated_at`、`contract_version`（输出顶层键仅
`project / scores / status / void_items`，见 case-07）。【已验证】

因此，决定一份历史报告内容的因素只有两个：

1. **运行时输入载荷的完整快照**（`responses` + `keys` + `void_items` + `scale`）；
2. **运行时的代码版本**（舍入模式、缺省值、排序等全局语义都在代码里）。

推论（【已验证】，由 case-07 暴露）：用修订后的键重跑同一批作答，会得到不同
成绩，且新旧输出中**没有任何字段**能说明各自用的是哪版键。历史报告的可复现性
完全依赖外部归档。

【建议】管理约定（非现状）：每次用于正式产出的运行，必须把输入载荷快照与代码
版本（git commit）一并归档；否则回滚与审计都无法验证（见兼容矩阵的回滚前置
条件）。是否在输入/输出中增加版本字段，属契约新增，需走变更流程。

## 4. 全局语义（影响所有输出的代码级规则）

以下规则任何一条变化都视为**全局语义变更**（兼容分级 L2，见兼容矩阵）：

| 规则 | 现状【已验证】 | 位置 |
|---|---|---|
| 舍入 | `round(x, 2)`：银行家舍入（half-even）+ 二进制浮点表示误差 | engine.py:7 |
| 缺省量尺 | form 未声明时乘数 = 10 | engine.py:7 |
| 缺省键 | 题无键时与 `None` 比较 | engine.py:5 |
| 排序 | `(student, form)` 字典序；要求类型一致可比较 | engine.py:7 |
| 作废时机 | 作废判定在分组计分**之前**，作废题不计入 `attempted` | engine.py:4-5 |
| 去重 | **不去重**：重复作答行全部计入 | engine.py:5 |
| 未知字段 | 作答行中的未知字段（如 `status`）被**静默忽略** | engine.py:4-5（只读四个键） |
| 包络合并 | `{"project", "status", **data}`：若 `data` 将来出现同名键会**覆盖**包络字段 | report.py:5 |

## 5. 当前测试实际固定了什么

`tests/test_core.py` 只固定了以下事实【已验证】：

- `load_records` 对当前 fixture 返回非空；
- `calculate` 返回非空 dict；
- `run` 输出的 `status == "ok"`、`project == "assessment-score-equating"`；
- 当前 fixture 下 `scores[0].raw == 1`（test_core.py:23）。

除上述外，本目录第 2–4 节描述的行为**均无测试覆盖**；它们是通过探针运行
确认的代码行为，不是测试结论。`fixtures/contract/` 下的快照是文档资产，
当前未接入任何自动化测试或 CI（仓库无 CI 配置），校验需人工运行
`tools/regen_contract_snapshots.py --check`。

## 6. 已识别的契约缺口（陈述事实，不代表改进承诺）

1. 无复核环节（见第 1 节）。
2. 无任何时间戳/版本字段（见第 3 节）。
3. 无缺考/违规表示：缺考只能隐式表达为"无作答行"，结果是该生从输出中消失；
   违规完全无法表达（见 case-08）。
4. 无输入 schema 校验：除"`responses` 必须是 list"外无任何校验，异常行为在
   静默丢弃与硬崩溃之间不一致（见 case-05a/05b）。
5. 无错误包络：失败即崩溃，调用方拿不到结构化错误。

这些缺口的处置方式（补齐或明确拒绝）均需经变更评审裁决，记录于变更记录。
