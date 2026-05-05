# Dota 2 历史相遇玩家标记工具 技术方案

## 1. 文档信息
- 文档名称：Dota 2 历史相遇玩家标记工具 技术方案
- 文档版本：v0.1
- 文档日期：2026-04-29
- 对应 PRD：[prd-local-background-mvp.md](/Users/jiangxinyuan/vibe_code/dota2_bounty/docs/prd-local-background-mvp.md)
- 当前阶段：Phase 0 / MVP 技术设计

## 2. 目标与原则

### 2.1 目标
本方案用于指导 MVP 阶段的本地后台进程实现，覆盖以下内容：
- 本地进程总体架构
- 数据采集链路拆分
- provider 抽象设计
- 本地存储与状态模型
- 事件流与状态机
- 日志、失败恢复与测试支撑
- 开发里程碑与任务拆解

### 2.2 技术原则
- 先验证数据可得性，再投入完整实现
- 优先本地能力，不依赖重平台
- 优先稳定可恢复，而不是极致实时
- 采集、解析、存储、命中解耦
- 所有外部依赖统一封装为 provider
- 全链路幂等，防止重复写入和状态污染
- 默认本地存储，不上云

## 3. 技术范围

### 3.1 本期范围
- Windows 本地常驻小进程
- Dota 2 运行状态探测
- 对局上下文识别
- roster 采集与标准化
- 历史命中计算
- 赛后结果补全
- SQLite 本地存储
- 结构化日志与状态输出

### 3.2 不在本期范围
- 图形化前端
- 托盘交互
- 游戏内悬浮提醒
- 云同步
- 远程 API 服务
- 多用户共享数据库

## 4. 推荐技术栈

### 4.1 推荐栈
- 语言：TypeScript
- 运行时：Node.js LTS
- 本地数据库：SQLite
- ORM / Query Builder：`better-sqlite3` 或 `drizzle + sqlite`
- 日志：`pino`
- 配置：`zod + dotenv + JSON config`
- 任务调度：Node 原生 timer + 内部 job scheduler

### 4.2 为什么先用 Node.js
- 开发效率高，适合快速验证 POC
- 便于后续平滑接入桌面层
- 文件监听、进程探测、HTTP 轮询、SQLite 接入都较成熟
- MVP 阶段 CPU 密集计算少，运行时性能足够

### 4.3 后续可替换方向
若 POC 成功、并对资源占用更敏感，可在后续阶段评估：
- Rust 核心后台服务
- Tauri 前端壳
- Node 原型迁移为 Rust 守护进程

## 5. 总体架构

### 5.1 架构概览
系统采用`单机单进程 + 模块化`架构。

主要模块：
- `process-supervisor`
- `config-manager`
- `game-state-watcher`
- `roster-provider`
- `roster-collector`
- `history-matcher`
- `match-result-provider`
- `match-resolver`
- `storage`
- `event-bus`
- `state-store`
- `logger`

### 5.2 数据流概览
1. 进程启动并加载配置
2. 轮询 Dota 2 运行状态
3. 检测到进入比赛上下文
4. 调用 roster provider 获取当前局玩家信息
5. 标准化 roster 并触发历史命中
6. 输出 `historical_player_hit` 事件
7. 比赛结束后调用赛果 provider 补全比赛信息
8. 更新 `matches / players / encounters`
9. 输出 `match_result_resolved` 与 `encounter_persisted` 事件

## 6. 模块设计

## 6.1 `process-supervisor`
职责：
- 单实例控制
- 进程生命周期管理
- 模块装配
- 异常恢复与优雅退出

输入：
- 配置
- 启动参数

输出：
- 进程状态
- 初始化成功/失败事件

关键要求：
- 防止重复启动多个后台实例
- 捕获未处理异常并落日志
- 支持 `SIGINT` / `SIGTERM` 优雅退出

## 6.2 `config-manager`
职责：
- 加载本地配置文件
- 合并默认配置
- 校验配置合法性

建议配置项：
- 轮询间隔
- 数据目录
- 日志级别
- provider 开关
- 补全重试次数
- 调试模式

## 6.3 `game-state-watcher`
职责：
- 判断 Dota 2 是否运行
- 判断是否进入“比赛上下文”
- 维护高层状态机

建议状态：
- `idle`
- `game_process_detected`
- `match_context_pending`
- `roster_collecting`
- `match_in_progress`
- `result_resolving`
- `cooldown`
- `error`

检测方式建议：
- 本地进程探测
- 必要文件或状态输出存在性检查
- provider 心跳判断

## 6.4 `roster-provider`
职责：
- 提供“当前局玩家列表”能力

统一接口：
```ts
interface RosterProvider {
  name: string;
  isAvailable(): Promise<boolean>;
  getRoster(ctx: MatchContext): Promise<RosterSnapshot | null>;
}
```

返回结构建议：
```ts
type RosterSnapshot = {
  source: string;
  collectedAt: string;
  localPlayerId?: string;
  tempMatchKey?: string;
  players: Array<{
    steamId?: string;
    accountId?: string;
    name?: string;
    team?: "radiant" | "dire" | "unknown";
    heroId?: number | null;
    heroName?: string | null;
    isLocalPlayer?: boolean;
  }>;
  completeness: "partial" | "completed";
  rawRef?: string;
};
```

设计要求：
- provider 不关心历史命中逻辑
- provider 只负责采集和原始结构转换
- 允许返回 `partial`

## 6.5 `roster-collector`
职责：
- 调用一个或多个 roster provider
- 做去重、字段归一、数据质量评分
- 生成统一的 `NormalizedRoster`

关键逻辑：
- 统一玩家 ID
- 判断 roster 是否足够可用
- 处理多 provider 冲突

归一规则建议：
- `steam_id` 优先级高于昵称
- 英雄字段允许缺失
- 无法判断队伍时标记 `unknown`

## 6.6 `history-matcher`
职责：
- 根据 `steam_id / account_id` 查询历史记录
- 生成当前局命中结果

输入：
- `NormalizedRoster`

输出：
- `HistoricalHit[]`

示例结构：
```ts
type HistoricalHit = {
  playerId: string;
  latestName: string | null;
  encounterCount: number;
  lastEncounterAt: string | null;
  lastHeroId: number | null;
  lastMyHeroId: number | null;
  lastResult: "win" | "lose" | "unknown";
  lastSameTeam: boolean | null;
  tag: string | null;
  note: string | null;
};
```

## 6.7 `match-result-provider`
职责：
- 提供赛后完整比赛结果补全能力

统一接口：
```ts
interface MatchResultProvider {
  name: string;
  isAvailable(): Promise<boolean>;
  resolveResult(ctx: MatchResolveContext): Promise<ResolvedMatchResult | null>;
}
```

返回结构建议：
```ts
type ResolvedMatchResult = {
  source: string;
  matchId: string;
  localPlayerId: string;
  startedAt?: string | null;
  endedAt?: string | null;
  result: "win" | "lose" | "unknown";
  players: Array<{
    playerId: string;
    name?: string | null;
    heroId?: number | null;
    team?: "radiant" | "dire" | "unknown";
    isLocalPlayer?: boolean;
  }>;
  completeness: "partial" | "completed";
  rawRef?: string;
};
```

## 6.8 `match-resolver`
职责：
- 在赛后触发结果补全
- 合并 roster 阶段和 result 阶段的数据
- 更新 encounter 状态

关键逻辑：
- 把 `tempMatchKey` 映射到真实 `match_id`
- 补齐玩家英雄、敌友关系和输赢
- 重试 pending encounter

## 6.9 `storage`
职责：
- 统一管理 SQLite 访问
- 提供 repository 层
- 负责事务和幂等约束

建议 repository：
- `playersRepository`
- `matchesRepository`
- `encountersRepository`
- `eventsRepository`
- `jobsRepository`

## 6.10 `event-bus`
职责：
- 在进程内部传递事件
- 统一事件结构

建议事件：
- `APP_STARTED`
- `GAME_STATE_CHANGED`
- `ROSTER_COLLECTED`
- `HISTORICAL_PLAYER_HIT`
- `MATCH_RESULT_RESOLVED`
- `ENCOUNTER_PERSISTED`
- `JOB_RETRY_SCHEDULED`
- `PROVIDER_FAILED`

## 6.11 `state-store`
职责：
- 保存当前进程瞬时状态
- 保存当前局上下文
- 进程重启后恢复未完成任务

建议状态：
- 当前状态机状态
- 当前局临时 key
- 上次成功采集时间
- 待重试 job 列表引用

## 6.12 `logger`
职责：
- 输出结构化日志
- 区分模块来源
- 支持调试与生产两种粒度

日志字段建议：
- `timestamp`
- `level`
- `module`
- `event`
- `matchId`
- `playerId`
- `state`
- `provider`
- `resultCode`
- `message`

## 7. 数据源策略

## 7.1 核心策略
MVP 不把任何单一数据源写死在系统内部，而是通过 provider 抽象隔离。

分两类 provider：
- `RosterProvider`
- `MatchResultProvider`

## 7.2 选择原则
- 合规
- 可持续
- 可本地运行
- 可被替换
- 能输出最小必要字段

## 7.3 降级策略
若实时 roster 不可完整获取：
- 先建立 `pending encounter`
- 延后到赛后补全
- 最小可接受目标是“能完成跨局命中”

若赛果补全失败：
- encounter 保留为 `partial`
- 进入重试队列
- 超出阈值后标记 `failed`

## 8. 状态机设计

### 8.1 进程级状态机
```text
idle
  -> game_process_detected
  -> match_context_pending
  -> roster_collecting
  -> match_in_progress
  -> result_resolving
  -> cooldown
  -> idle
```

### 8.2 对局级状态机
```text
detected
  -> roster_partial
  -> roster_completed
  -> result_pending
  -> result_partial
  -> result_completed
  -> archived
```

### 8.3 状态转换要求
- 所有状态转换必须带时间戳
- 状态变化必须写事件日志
- 同一对局允许重复进入 `result_pending`，用于重试

## 9. 数据模型与 Schema 建议

## 9.1 `players`
用途：
- 玩家主档

字段建议：
- `steam_id TEXT PRIMARY KEY`
- `latest_name TEXT`
- `tag TEXT`
- `note TEXT`
- `first_seen_at TEXT NOT NULL`
- `last_seen_at TEXT NOT NULL`
- `encounter_count INTEGER NOT NULL DEFAULT 0`
- `updated_at TEXT NOT NULL`

## 9.2 `matches`
用途：
- 一局比赛的聚合记录

字段建议：
- `match_id TEXT PRIMARY KEY`
- `my_steam_id TEXT NOT NULL`
- `temp_match_key TEXT`
- `started_at TEXT`
- `ended_at TEXT`
- `result TEXT NOT NULL`
- `data_status TEXT NOT NULL`
- `raw_source_ref TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

## 9.3 `encounters`
用途：
- 某局比赛中某个玩家与本用户的相遇记录

字段建议：
- `id TEXT PRIMARY KEY`
- `match_id TEXT`
- `temp_match_key TEXT`
- `player_steam_id TEXT NOT NULL`
- `player_name TEXT`
- `player_hero_id INTEGER`
- `my_hero_id INTEGER`
- `same_team INTEGER`
- `result TEXT NOT NULL`
- `played_at TEXT`
- `data_status TEXT NOT NULL`
- `source TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

唯一约束建议：
- `UNIQUE(match_id, player_steam_id)`
- 在 `match_id` 未知阶段，使用 `temp_match_key + player_steam_id` 做幂等

## 9.4 `events`
用途：
- 记录关键系统事件

字段建议：
- `id TEXT PRIMARY KEY`
- `event_type TEXT NOT NULL`
- `payload_json TEXT NOT NULL`
- `created_at TEXT NOT NULL`

## 9.5 `jobs`
用途：
- 重试和补全任务

字段建议：
- `id TEXT PRIMARY KEY`
- `job_type TEXT NOT NULL`
- `payload_json TEXT NOT NULL`
- `status TEXT NOT NULL`
- `attempt_count INTEGER NOT NULL DEFAULT 0`
- `next_run_at TEXT`
- `last_error TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

## 10. Repository 设计

### 10.1 玩家仓储
建议方法：
- `upsertPlayer()`
- `getPlayerById()`
- `touchPlayerEncounter()`
- `updatePlayerTagAndNote()`

### 10.2 对局仓储
建议方法：
- `createOrUpdateMatch()`
- `linkTempMatchKey()`
- `getPendingMatches()`

### 10.3 相遇仓储
建议方法：
- `upsertEncounter()`
- `findRecentEncounterByPlayerId()`
- `listHistoricalHitsByRosterIds()`
- `listPendingEncounters()`

### 10.4 事件仓储
建议方法：
- `appendEvent()`
- `listRecentEvents()`

### 10.5 任务仓储
建议方法：
- `scheduleJob()`
- `claimRunnableJobs()`
- `markJobSuccess()`
- `markJobFailed()`

## 11. Provider 合并策略

### 11.1 单 provider 优先
POC 阶段先实现单一 provider，尽快验证可行性。

### 11.2 多 provider 合并
如果引入多个 provider，合并规则建议：
- 稳定 ID 优先
- 赛后结果优先于实时阶段
- 新鲜数据优先于旧数据
- `completed` 优先于 `partial`

### 11.3 质量评分
对每次 provider 返回结果计算 `qualityScore`：
- 有稳定 ID：+50
- 有完整 10 人 roster：+20
- 有队伍归属：+10
- 有英雄：+10
- 有 match_id：+10

低于阈值的数据只记录，不触发强命中逻辑。

## 12. 幂等与一致性策略

### 12.1 幂等目标
- 同一局重复采集不产生重复 encounter
- 同一赛果重复补全不污染历史数据
- 进程重启后重复跑任务不造成多写

### 12.2 实现方式
- 基于唯一键约束
- upsert 优先于 insert
- job 执行状态持久化
- 事件允许重复，但业务记录不允许重复

## 13. 错误处理与恢复

### 13.1 错误类型
- provider 不可用
- 数据字段不完整
- 数据库写入失败
- 文件权限问题
- 进程状态异常

### 13.2 恢复策略
- provider 失败：记录日志并退避重试
- DB 失败：进入本地重试任务
- 部分数据：以 `partial` 状态保存
- 关键字段缺失：保留原始快照，等待后续补全
- 进程异常退出：下次启动扫描 pending job 恢复

### 13.3 退避建议
- 第 1 次失败：30 秒
- 第 2 次失败：2 分钟
- 第 3 次失败：10 分钟
- 达上限后标记 `failed`

## 14. 可观测性设计

### 14.1 运行可见性
MVP 没有前端，因此必须通过以下方式验证：
- 日志文件
- SQLite 事件表
- 本地状态文件 `runtime-status.json`

### 14.2 状态文件建议
建议输出：
- 当前进程状态
- 当前 provider 状态
- 当前局临时 key
- 最近一次命中玩家摘要
- 最近一次错误摘要

### 14.3 调试命令建议
后续 CLI 可预留：
- `status`
- `tail-events`
- `list-pending-jobs`
- `replay-match <matchId>`

## 15. 安全与合规边界
- 不做注入
- 不做内存扫描
- 不做规避平台限制的抓取
- 不依赖高封禁风险方案
- 只接入公开、合法、可替换的数据源
- 用户数据默认仅本地存储

## 16. 性能目标

### 16.1 MVP 目标
- 空闲时 CPU 占用接近 0
- 空闲时内存保持轻量
- 磁盘写入以事件和状态变化为主，不做高频刷盘

### 16.2 设计手段
- 轮询间隔可配置
- 只有状态变化或关键事件才写库
- 原始快照不做大对象无限累计
- 日志按天切分与归档

## 17. 目录结构建议

```text
src/
  app/
    bootstrap.ts
    supervisor.ts
  config/
    index.ts
    schema.ts
  domain/
    models/
    events/
    services/
  modules/
    game-state/
    roster/
    history/
    match-result/
    jobs/
  providers/
    roster/
      provider-a.ts
    match-result/
      provider-a.ts
  storage/
    db.ts
    migrations/
    repositories/
  infra/
    logger/
    runtime-status/
    process-lock/
  scripts/
  tests/
```

## 18. 开发里程碑

## 18.1 Milestone 0：数据采集 POC
目标：
- 确认是否能稳定获得当前局 roster
- 确认是否能拿到赛后结果和英雄信息
- 确认是否能关联本地玩家 ID

交付物：
- provider 原型
- 原始样本记录
- 可行性结论

通过标准：
- 至少能在多局样本中稳定拿到玩家稳定 ID
- 至少能在赛后补全输赢和英雄

## 18.2 Milestone 1：基础进程骨架
目标：
- 完成 supervisor、config、logger、db 初始化
- 完成状态机基础框架

交付物：
- 可启动的后台进程
- SQLite schema 初始化
- 基础日志输出

## 18.3 Milestone 2：roster 采集闭环
目标：
- 采集并标准化当前局 roster
- 写入 players 和 pending encounters
- 触发历史命中

交付物：
- `roster provider`
- `history matcher`
- 首版命中事件输出

## 18.4 Milestone 3：赛后补全闭环
目标：
- 补全 match_id、英雄、敌友关系、输赢
- 完成 encounter 最终落库

交付物：
- `match result provider`
- `match resolver`
- job 重试机制

## 18.5 Milestone 4：可验证 MVP
目标：
- 输出可供人工验证的完整数据链路
- 提供调试状态文件
- 补全失败恢复逻辑

交付物：
- 可读日志
- 事件表
- 状态输出

## 19. 开发任务拆解

### 19.1 Phase 0 任务
- 建立项目骨架
- 搭建日志与配置模块
- 实现单实例锁
- 预留 provider 接口
- 开始数据采样脚本

### 19.2 数据层任务
- 建表 migration
- repository 实现
- upsert 策略实现
- job 表与任务调度

### 19.3 业务层任务
- 进程状态机
- roster 采集管线
- 历史命中服务
- 赛果补全服务
- 相遇记录持久化服务

### 19.4 运维与调试任务
- 状态文件输出
- 调试日志增强
- 数据修复脚本
- 本地清理脚本

## 20. 测试建议

### 20.1 单元测试优先模块
- roster 归一逻辑
- provider 合并逻辑
- 历史命中逻辑
- 幂等 upsert 逻辑
- 状态机切换逻辑

### 20.2 集成测试优先链路
- 一局完整采集到落库
- 再次相遇命中
- 赛果补全失败重试
- 进程重启后恢复 pending job

## 21. 当前最关键的工程结论
这套产品能否成立，取决于下面 4 个问题是否同时成立：
- 能否稳定拿到本局玩家稳定 ID
- 能否在足够早的阶段拿到 roster
- 能否在赛后拿到英雄、敌友关系和输赢
- 能否把实时阶段与赛后阶段的数据稳定归并

如果这 4 个问题里有任意 2 个无法成立，就不建议继续推进 UI 或更大规模开发。

## 22. 下一步建议
- 立即进入 `Milestone 0`
- 不先做前端
- 不先做托盘
- 先产出 provider POC 设计
- 用真实样本跑 5 到 10 局数据验证链路
