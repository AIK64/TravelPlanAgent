# 05. 约束验证、评分与 Replan

## 1. Planner、Validator 和 Ranker 的区别

这三个概念容易混淆：

```text
Planner
→ 提出一份计划

Validator
→ 判断计划能不能执行

Ranker
→ 在能执行的计划中选择相对更好的一个
```

正确顺序是：

```text
先验证是否合法
再比较哪个更好
```

不能让一个高分计划因为“总体体验好”而绕过预算或营业时间错误。

## 2. Pydantic 校验与业务校验

项目中存在两层校验。

### 2.1 输入结构校验

由 Pydantic 完成，例如：

- 经纬度是否在合法范围
- 预算是否大于零
- 人数是否在 1 到 20 之间
- 日期顺序是否正确
- 时间是否带时区

这一层回答：

> 用户输入的数据结构是否成立？

### 2.2 行程业务校验

由 `validate_candidate` 完成，例如：

- 灵隐寺是否真的被安排
- 活动是否在营业时间内
- 是否超过总预算
- 是否预留返程时间

这一层回答：

> 根据这份输入生成的具体计划是否可执行？

## 3. 当前硬约束逐项说明

### 3.1 empty_plan

如果整个候选没有活动：

```text
valid = false
```

否则空计划可能因为“没有时间冲突、没有超预算”而被错误当成完美计划。

### 3.2 missing_must_visit

系统收集所有已安排活动名称，与 `trip.must_visit` 对比。

必去地点没有出现时生成错误。

Planner 中对必去地点的大幅加分只是提高被选中的概率，Validator 才是最终保证。

### 3.3 time_overlap

同一天的活动先按开始时间排序。如果：

```text
当前活动开始时间 < 上一个活动结束时间
```

则存在重叠。

### 3.4 arrival_buffer

第一天第一个活动不能早于：

```text
到达时间 + 60 分钟
```

这 60 分钟代表下车、取行李和前往住宿区等缓冲。

### 3.5 departure_buffer

最后一天活动不能晚于：

```text
离开时间 - 90 分钟
```

真实项目中，火车和机场应该使用不同缓冲策略；v0.1 统一使用 90 分钟。

### 3.6 outside_opening_hours

每个活动必须满足：

```text
activity.start >= poi.opening
activity.end <= poi.closing
```

当前 Mock POI 每天使用同一个营业时间，还没有处理周一闭馆、节假日和临时关闭。

### 3.7 walking_limit

如果：

```text
day.walking_distance_meters
>
trip.mobility.max_daily_walking_meters
```

计划不合法。

当前步行距离来自路线估算器，不是真实导航结果。

### 3.8 activity_time_limit

将当天所有活动持续时间相加，与每日活动时间上限比较。

当前没有把交通时间计入这个硬约束，但疲劳度计算会考虑活动时间和交通时间。后续可以明确区分：

```text
最大纯活动时间
最大在外总时长
最大连续活动时间
```

### 3.9 budget_exceeded

将所有活动预计费用求和：

```python
actual_cost = sum(item.estimated_cost)
```

如果超过 `total_budget`，候选不合法。

当前费用只包含 POI 活动估算，不包含：

- 住宿
- 城际交通
- 市内交通
- 完整餐饮
- 购物

因此 v0.1 的预算更准确地说是“活动预算”。

## 4. ValidationResult

返回结构：

```python
ValidationResult(
    valid=False,
    violations=[
        Violation(
            type="budget_exceeded",
            severity="error",
            message="预计费用 75 元，超过预算 10 元",
            repair_hint="减少收费活动或提高预算",
        )
    ],
)
```

结构化违规信息比返回一个字符串更有价值，因为未来可以：

- 按违规类型路由到不同修复策略
- 在前端高亮具体日期和 POI
- 统计 Benchmark 中最常见错误
- 生成可靠的用户解释

## 5. 候选指标

### 5.1 preference_match

已安排 POI 中，类别或标签与用户兴趣匹配的比例。

### 5.2 diversity

已安排 POI 的不同类别数量经过归一化后的结果。

### 5.3 data_confidence

已安排 POI 数据置信度的平均值。Mock 数据当前通常是 1.0。

### 5.4 total_travel_minutes

每天所有地点间估算交通时间之和。

### 5.5 walking_distance_meters

各天估算步行距离之和。

### 5.6 estimated_cost

所有已安排活动费用之和。

### 5.7 fatigue_score

单日疲劳度近似为：

```text
(活动时间 + 交通时间)
÷
用户每日活动时间上限
```

结果最大截断为 1.0，再计算多天平均值。

## 6. 综合评分

当前代码大致使用：

```text
+ 0.35 × 兴趣匹配
+ 0.20 × 内容多样性
+ 0.15 × 数据置信度
- 0.10 × 归一化交通时间
- 0.10 × 疲劳度
- 0.10 × 预算使用风险
```

需要注意：

- 这些权重是 v0.1 手工设置的初始值，不是评测得出的最优权重。
- 不同指标必须归一化后才能合理相加。
- `score` 只在通过 Validator 的候选之间比较。
- 后续应通过 Benchmark 和用户反馈调整权重。

## 7. v0.1 的 Replan 策略

当前策略很简单：

```text
第 0 轮
→ relaxed 2、balanced 3、exploration 4 个活动/天

第 1 轮
→ 每种风格活动上限减 1
→ 收费地点增加排序惩罚

第 2 轮
→ 再次降低活动密度
→ 进一步提高低成本地点优先级
```

它适合修复：

- 行程太密
- 每日活动时间过长
- 步行量过高
- 一部分预算超限

它不一定能修复：

- 必去地点本身价格就超过总预算
- 必去地点在用户可用时间内没有营业
- 目的地没有任何 POI 数据
- 约束之间存在根本矛盾

这些情况应该返回 `infeasible`。

## 8. 低预算无解测试

测试将预算改为 10 元，但必去的灵隐寺 Mock 费用是 75 元。

无论系统怎样减少其他活动，都无法同时满足：

```text
灵隐寺必须去
总活动预算 <= 10 元
```

因此正确结果是：

```text
status = infeasible
selected_plan = null
iterations = 1
```

“诚实地返回无解”比删除必去地点或伪造低价更重要。

## 9. 当前 Replan 的限制

v0.1 会重新生成当前轮次的所有候选。它还没有：

- 解析用户具体修改
- 识别 affected_days
- 锁定未受影响日期
- 只重算局部路线矩阵
- 保存修改前后 Diff

因此它是一个基础 Replan Loop，不应在简历上描述为“完整局部重规划”。

## 10. 后续怎样升级为局部重规划

```text
Violation / ChangeEvent
  ↓
Impact Analyzer
  ↓
affected_days、affected_pois
  ↓
冻结 locked_days 和 locked_items
  ↓
只重新生成受影响区域
  ↓
局部 Validator + 全局预算 Validator
  ↓
保存 Plan Diff
```

到那时，Replanner 的输入不再是完整 TripSpec 和所有 POI，而是一个裁剪过的局部上下文。

