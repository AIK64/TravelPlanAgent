# 06. 运行、测试与新手练习

## 1. 环境准备

项目要求 Python 3.11 或更高版本。

在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

解释：

```text
python -m venv .venv
→ 在项目内创建隔离的 Python 环境

pip install -e .
→ 以 editable 模式安装当前项目

[dev]
→ 同时安装 pytest、httpx、coverage 等开发依赖
```

editable 模式下修改 `src/travel_agent` 后不需要每次重新安装包。

## 2. 运行测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

预期：

```text
6 passed
```

运行覆盖率：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  --cov=travel_agent `
  --cov-report=term-missing
```

当前基线约为 93%。覆盖率不是越高越好，但它可以帮助定位尚未执行过的分支。

当前测试会显示一条来自 FastAPI/Starlette TestClient 的依赖弃用警告。它不影响现有测试结果，后续升级测试客户端时处理。

## 3. 启动 API

```powershell
.\.venv\Scripts\python.exe -m uvicorn travel_agent.app:app --reload
```

命令解释：

```text
travel_agent.app
→ Python 模块

app
→ 模块中的 FastAPI 对象

--reload
→ 开发环境下代码变化后自动重启
```

浏览器访问：

```text
http://127.0.0.1:8000/docs
```

FastAPI 会自动生成 Swagger UI，可以直接在网页里测试接口。

## 4. 调用杭州示例

另开一个 PowerShell：

```powershell
$body = Get-Content .\examples\hangzhou_request.json -Raw

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/plans `
  -ContentType "application/json" `
  -Body $body
```

重点观察返回值：

```text
status
selected_plan.style
selected_plan.days
selected_plan.metrics
selected_plan.validation
candidates
iterations
message
```

## 5. 直接从 Python 调用 Graph

HTTP 只是项目的一层入口。测试和脚本可以直接调用：

```python
import json

from travel_agent.domain.models import PlanningRequest
from travel_agent.graph.workflow import run_planning


with open("examples/hangzhou_request.json", encoding="utf-8") as file:
    request = PlanningRequest.model_validate(json.load(file))

response = run_planning(request, thread_id="learning-example")

print(response.status)
print(response.selected_plan.style if response.selected_plan else None)
```

这有助于理解：FastAPI 不是规划系统本身，它只是调用规划系统的一个适配层。

## 6. 怎样阅读测试

### test_trip_day_count

验证 10 月 2 日到 10 月 4 日是三天，而不是两天。

### test_transport_anchor_requires_timezone

故意传入没有时区的 datetime，确认 Pydantic 会拒绝。

### test_workflow_builds_valid_plan

验证：

- Graph 最终状态是 completed
- 存在 selected_plan
- selected_plan 通过 Validator
- 必去的灵隐寺确实出现

### test_workflow_returns_infeasible_when_required_poi_exceeds_budget

构造不可能同时满足的预算和必去约束，确认 Graph 执行一次 Replan 后返回 infeasible。

### test_health

验证 Web 服务基础可用。

### test_create_plan

从 HTTP JSON 输入一直测试到最终 Graph 输出，是当前最接近端到端的测试。

## 7. 调试建议

遇到错误时按层排查：

```text
HTTP 422
→ 先看 Pydantic 请求校验

status = infeasible
→ 查看每个 candidate.validation.violations

GraphRecursionError
→ 检查条件路由和 Loop 终止条件

POI 为空
→ 检查 destination，目前只支持杭州

测试结果不稳定
→ 检查是否引入随机数、当前时间或真实网络调用
```

## 8. 新手练习 1：修改一个 POI

目标：理解领域模型和 Mock 数据。

步骤：

1. 打开 `planning/mock_data.py`。
2. 添加一个新的杭州 POI。
3. 设置坐标、类别、营业时间和费用。
4. 运行测试。
5. 调用 API，观察候选中是否出现。

学习点：Pydantic Model、Fixture、候选排序。

## 9. 新手练习 2：增加“最大每日活动数”约束

目标：理解输入模型和 Validator。

建议步骤：

1. 在 `MobilityConstraints` 增加字段。
2. 在 Validator 中统计每天活动数。
3. 超出时返回新的 `Violation` 类型。
4. 编写一个先失败、修改代码后通过的测试。

学习点：测试驱动开发、硬约束、Pydantic。

## 10. 新手练习 3：观察 Replan

目标：理解 LangGraph Loop。

步骤：

1. 在 `replan` 节点临时增加日志。
2. 使用低预算或低活动时长上限构造请求。
3. 观察 `iterations` 怎样变化。
4. 比较 r0 和 r1 候选 ID。
5. 恢复或改成正式 logger，避免保留随意 print。

学习点：State 更新、条件边、循环终止。

## 11. 新手练习 4：支持第二个 Mock 城市

目标：理解 Provider 边界。

不要继续在 `get_mock_pois` 中堆积大量 `if`。先思考接口：

```python
class POIProvider(Protocol):
    def search(self, city: str) -> list[POI]: ...
```

然后实现：

```text
HangzhouMockProvider
ChengduMockProvider
```

学习点：依赖倒置、Protocol、Provider 模式。

## 12. 新手练习 5：编写违规测试

为以下场景各写一个测试：

- 活动超出营业时间
- 两个活动时间重叠
- 超过步行上限
- 缺少必去地点
- 其他城市没有 POI

学习点：边界测试、Validator 设计。

## 13. 学习完成检查表

当你能回答下面问题时，说明已经理解 v0.1：

- [ ] 为什么输入使用 Pydantic，而 Graph State 使用 TypedDict？
- [ ] 为什么金额使用 Decimal？
- [ ] FastAPI 在项目中负责什么？
- [ ] Planner 和 Validator 为什么分开？
- [ ] 三种候选方案有什么区别？
- [ ] 最近邻算法为什么不保证最优？
- [ ] 条件边如何形成 Replan Loop？
- [ ] `max_replan_rounds` 和 `recursion_limit` 有什么不同？
- [ ] 为什么无解不是程序异常？
- [ ] InMemorySaver 为什么不能用于生产？
- [ ] v0.1 哪些部分是真实实现，哪些仍是未来设计？

