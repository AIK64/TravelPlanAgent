# v1.1 → v1.2 最终开发入口

本目录冻结本项目从 v1.0.0 演进到最终 v1.2.0 的实现范围。开发采用一次连续交付，但保留两个内部 Gate：

~~~text
v1.0.0 baseline
  → v1.1 Gate
      Preference Memory
      Context Projection
      Cross-session Personalization
      Specialist Subagent Experiment
      Memory / Context / Subagent Ablation
  → v1.2 Gate
      Application Service
      Travel MCP Server
      Real Provider Failover
      Full Agent Frontend
      PostgreSQL / Redis / Worker / Observability
  → v1.2.0 final release
~~~

核心设计决策：

- 一个 Orchestrator 持有全局 State、预算、Checkpoint 和终止权。
- Planner、Critic、Replanner 使用进程内 Specialist Subagent 隔离上下文。
- Specialist 只通过强类型 Handoff 返回结果，不直接写 Plan 或 Memory。
- single_graph 始终保留为 Baseline；Subagent 只有通过消融门禁才成为默认。
- API、MCP、Worker 共用同一 Application Service。
- 地图采用 AMap 主、Baidu 备；天气采用 AMap 主、QWeather 备。
- 最终形态是可生产部署的模块化单体，不建设分布式自主多 Agent。

开发前先阅读：

1. [统一设计报告](design.md)：完整架构、Graph、State、Memory、MCP、Provider、前端、生产化和实施顺序。
2. [需求追踪矩阵](requirements-traceability.md)：历史需求到代码、测试和门禁的逐项映射。
3. [v1.0 设计](../v1.0/design.md)：本轮不得削弱的 Run、Budget、Trace 和评测基线。
4. [长期路线](../roadmap-to-v1.2.md)：版本边界和共同守则。

实现规则：

- 每个 PR 引用需求追踪矩阵中的 Requirement ID。
- Phase 必须按依赖顺序推进，不跳过 Identity、Repository 和 Context 基础直接开发前端或 MCP。
- 每个 Phase 先写 Contract/Trajectory Test，再实现功能。
- v1.1 Gate 未通过前，不进入 MCP、完整前端和生产发布收口。
- 任何真实质量声明都必须附带 Provider、模型、数据集版本、日期和证据等级。
