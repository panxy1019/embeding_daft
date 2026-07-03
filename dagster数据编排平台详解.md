# Dagster 开源数据编排平台详解

## 目录

1. [Dagster 概述](#1-dagster-概述)
2. [Dagster 核心概念详解](#2-dagster-核心概念详解)
3. [基于 Dagster 的数据湖构建任务架构](#3-基于-dagster-的数据湖构建任务架构)
4. [Daft + Ray Cluster + S3 数据湖 + RUBKITSQL 构建任务详细过程](#4-daft--ray-cluster--s3-数据湖--rubkitsql-构建任务详细过程)

---

## 1. Dagster 概述

### 1.1 什么是 Dagster

Dagster 是一个开源的**数据编排平台**（Data Orchestration Platform），专为数据工程师、数据科学家和分析师设计。它的核心哲学是**Software-Defined Assets（软件定义资产）**，即用代码来定义数据资产是什么、从哪里来、如何计算、多久更新一次。

与传统的以任务为中心的编排工具（如 Airflow）不同，Dagster 采用**以数据资产为中心**的范式，让编排系统围绕数据资产本身来工作，而不是围绕任务 DAG 来工作。

### 1.2 Dagster 的核心价值

- **数据血缘可视化**：自动追踪数据资产之间的依赖关系，形成完整的数据血缘图谱
- **可观测性**：每个资产都有清晰的状态、更新时间和质量检查结果
- **可测试性**：资产和计算逻辑可以独立测试，支持单元测试和集成测试
- **环境隔离**：通过 Resources 和 I/O Managers 实现开发、测试、生产环境的无缝切换
- **声明式自动化**：通过 Automation Condition 声明式地定义资产何时应该被物化

### 1.3 Dagster 架构概览

Dagster 的整体架构包含以下核心组件：

| 组件 | 描述 |
|------|------|
| **Dagster Core** | 运行时引擎，负责执行管道和管理依赖 |
| **Dagster Webserver (Dagit)** | Web 界面，用于管道可视化、监控和调试 |
| **Dagster Daemon** | 后台守护进程，负责调度、传感器、自动物化等 |
| **Storage Layer** | 存储层，处理元数据、日志和运行历史，支持 PostgreSQL、SQLite 等后端 |
| **Code Locations** | 代码位置，定义资产、作业、调度等的 Python 模块 |
| **Execution Environment** | 执行环境，支持本地、云端（AWS/GCP）或容器化（Docker/K8s）执行 |

---

## 2. Dagster 核心概念详解

### 2.1 Software-Defined Assets (软件定义资产)

#### 2.1.1 概念定义

**Asset（资产）** 是 Dagster 中最核心的抽象概念，代表一个**逻辑数据单元**。它可以是：
- 数据库中的一张表
- S3 中的一个文件或数据集
- 机器学习模型
- 数据仓库中的视图
- 任何持久化的数据产物

**Software-Defined Asset (SDA)** 是一个 Dagster 对象，它将一个资产与产生该资产内容的函数和上游资产关联起来。换句话说，资产定义描述了：
- 这个数据资产应该是什么（Asset Key）
- 它依赖哪些上游资产
- 如何计算/物化这个资产

#### 2.1.2 资产的核心属性

- **Asset Key**：资产的唯一标识符，通常是一个字符串或字符串路径（如 `["raw", "users"]`）
- **Upstream Dependencies**：上游依赖，即该资产依赖哪些其他资产
- **Computation Function**：计算函数，定义如何从上游资产生成本资产
- **Description**：资产的描述文档
- **Metadata**：元数据，如列定义、数据格式、存储位置等
- **Partitions**：分区定义（可选）

#### 2.1.3 资产的类型

1. **Regular Assets（普通资产）**：由单个函数计算的资产
2. **Graph-Backed Assets（图支撑资产）**：由多个 Op 组成的计算图生成的资产，适合复杂的多步骤计算
3. **Multi-Assets（多资产）**：一个函数同时生成多个资产
4. **External Assets（外部资产）**：不在 Dagster 中计算，但被 Dagster 追踪和监控的资产
5. **Source Assets（源资产）**：由外部系统产生的原始数据资产，Dagster 只负责监控和消费

#### 2.1.4 资产物化 (Materialization)

**物化**是指执行资产的计算函数，将数据实际写入存储系统的过程。当一个资产被物化时：
- Dagster 会记录物化事件
- 更新资产的状态和最后更新时间
- 触发下游资产的自动化条件评估

### 2.2 Ops（操作）

#### 2.2.1 概念定义

**Op** 是 Dagster 中的**核心计算单元**。每个 Op 执行一个相对简单的任务，例如：
- 从其他数据集派生出新数据集
- 执行数据库查询
- 在远程集群中启动 Spark 作业
- 查询 API 并将结果存储到数据仓库
- 发送邮件或 Slack 通知

#### 2.2.2 Op 与 Asset 的关系

- 资产定义的计算核心是一个 Op
- 多个 Op 可以组合成一个 Graph（图）
- Graph 可以用来支撑 Graph-Backed Asset
- Op 也可以独立于资产存在，用于传统的任务编排

#### 2.2.3 Op 的核心特性

- **输入/输出**：Op 可以接收输入并产生输出
- **Context**：Op 可以访问执行上下文，获取配置、资源、日志记录器等
- **重试机制**：支持配置重试策略
- **Hook**：支持在 Op 执行前后触发钩子函数
- **事件系统**：Op 可以发出各种事件（如预期结果、元数据等）

### 2.3 Graphs（图）

#### 2.3.1 概念定义

**Graph** 是由多个 Op 组成的**有向无环图（DAG）**，用于完成复杂的计算任务。Graph 定义了 Op 之间的依赖关系和数据流动方式。

#### 2.3.2 Graph 的用途

- **组合复杂逻辑**：将多个简单的 Op 组合成复杂的计算流程
- **复用计算逻辑**：Graph 可以在多个地方复用
- **支撑资产**：Graph 可以作为 Graph-Backed Asset 的计算后端
- **组成作业**：Graph 可以编译成 Job 进行执行

#### 2.3.3 Graph-Backed Assets

当生成一个资产涉及多个离散的计算步骤时，可以使用 Graph-Backed Asset：
- 每个计算步骤是一个独立的 Op
- 这些 Op 组装成一个 Op Graph
- 允许在 Op 边界重新执行运行
- 不需要将每个中间值都链接到持久存储中的资产

### 2.4 Jobs（作业）

#### 2.4.1 概念定义

**Job** 是 Dagster 中的**可执行单元**。Job 可以从 Graph 编译而来，也可以直接从资产选择生成。

#### 2.4.2 Job 的类型

1. **Asset Jobs（资产作业）**：选择一组资产进行物化的作业
2. **Op Jobs（操作作业）**：基于 Op Graph 的传统作业

#### 2.4.3 Job 的核心特性

- **可执行**：Job 是可以被触发执行的实体
- **配置化**：Job 可以接受运行时配置
- **资源绑定**：Job 可以绑定特定的资源配置
- **执行器**：Job 可以配置不同的执行器（多进程、Ray 等）

### 2.5 Resources（资源）

#### 2.5.1 概念定义

**Resource** 是 Dagster 中用于**管理外部系统连接和依赖**的抽象。Resource 封装了与外部系统交互的逻辑，例如：
- 数据库连接
- S3 客户端
- API 客户端
- 计算集群连接
- 密钥管理

#### 2.5.2 Resource 的核心价值

- **环境隔离**：不同环境（开发/测试/生产）可以使用不同的 Resource 实现
- **可测试性**：测试时可以使用 Mock Resource
- **配置管理**：Resource 可以接受配置参数
- **代码复用**：Resource 可以在多个资产和作业之间共享

#### 2.5.3 常见的 Resource 类型

- **IO Manager**：特殊的 Resource，负责数据的读写
- **S3 Resource**：S3 存储访问
- **Database Resource**：数据库连接
- **Ray Resource**：Ray 集群连接
- **Slack Resource**：Slack 通知

### 2.6 I/O Managers（输入输出管理器）

#### 2.6.1 概念定义

**I/O Manager** 是一种特殊的 Resource，负责**存储 Op/Asset 的输出，并将它们作为输入加载到下游 Op/Asset 中**。

I/O Manager 让数据处理代码与数据读写代码分离，减少重复代码，并使更改数据存储位置变得更加容易。

#### 2.6.2 I/O Manager 的工作原理

1. 当一个 Op/Asset 执行完成后，它的输出被传递给 I/O Manager
2. I/O Manager 负责将输出持久化到存储系统（文件系统、S3、数据库等）
3. 当下游 Op/Asset 需要这个输入时，I/O Manager 负责从存储系统中加载数据

#### 2.6.3 内置 I/O Manager

| I/O Manager | 描述 |
|-------------|------|
| `FilesystemIOManager` | 默认 I/O Manager，将输出存储为本地文件系统上的 pickle 文件 |
| `InMemoryIOManager` | 将输出存储在内存中，主要用于单元测试 |
| `S3PickleIOManager` | 将输出存储为 S3 上的 pickle 文件 |
| `UPathIOManager` | 基于 universal-pathlib 的通用文件系统 I/O Manager，支持本地和远程文件系统 |

#### 2.6.4 自定义 I/O Manager

可以通过继承 `ConfigurableIOManager` 或 `UPathIOManager` 来自定义 I/O Manager，支持特定的数据格式（如 Parquet、Delta Lake、数据库表等）。

### 2.7 Partitions（分区）

#### 2.7.1 概念定义

**Partition** 是将一个资产**分割成多个独立可计算的部分**的机制。每个分区可以独立地被物化和追踪。

#### 2.7.2 分区的类型

1. **Time Window Partitions（时间窗口分区）**：按时间范围分区，如每天、每小时、每月
2. **Static Partitions（静态分区）**：预定义的固定分区集合
3. **Dynamic Partitions（动态分区）**：可以在运行时添加和删除的分区
4. **Multi-Dimensional Partitions（多维分区）**：多个维度的组合分区

#### 2.7.3 分区的应用场景

- **增量处理**：只处理新增的时间窗口数据
- **并行计算**：多个分区可以并行计算
- **数据保留**：按分区管理数据生命周期
- **回填（Backfill）**：按分区重新处理历史数据

#### 2.7.4 分区与资产存储

- 如果资产存储在文件系统或对象存储中，每个分区通常对应一个文件或对象
- 如果资产存储在数据库中，每个分区通常对应表中特定范围内的值

### 2.8 Schedules（调度）

#### 2.8.1 概念定义

**Schedule** 是 Dagster 中**基于时间触发作业执行**的机制。它支持传统的基于时间的自动化，例如指定作业在每周一早上 9 点运行。

#### 2.8.2 Schedule 的核心特性

- **Cron 表达式**：支持标准的 cron 表达式定义调度时间
- **时区支持**：可以指定调度的时区
- **分区调度**：可以从分区化的作业自动创建调度
- **标签**：可以为调度触发的运行添加标签
- **默认状态**：可以配置调度的默认启用/停用状态

#### 2.8.3 分区调度

对于时间窗口分区的资产，可以使用 `build_schedule_from_partitioned_job` 自动创建调度，每个调度周期对应一个分区。

### 2.9 Sensors（传感器）

#### 2.9.1 概念定义

**Sensor** 是 Dagster 中**基于事件触发作业执行**的机制。传感器持续轮询外部系统或 Dagster 内部状态，当特定条件满足时触发作业运行。

#### 2.9.2 常见的传感器类型

1. **Asset Sensors（资产传感器）**：监控资产物化事件，当下游资产更新时触发
2. **Multi-Asset Sensors（多资产传感器）**：监控多个资产的状态
3. **S3 Sensors（S3 传感器）**：监控 S3 桶中的新文件
4. **Run Status Sensors（运行状态传感器）**：监控作业运行的状态变化
5. **自定义传感器**：可以编写自定义的传感器逻辑

#### 2.9.3 传感器的工作原理

- 传感器由 Dagster Daemon 定期评估
- 每次评估时，传感器检查条件是否满足
- 如果条件满足，传感器发出 RunRequest（运行请求）
- RunRequest 被提交到运行队列等待执行

### 2.10 Declarative Automation（声明式自动化）

#### 2.10.1 概念定义

**Declarative Automation** 是 Dagster 的一个高级自动化框架，允许你**声明式地定义资产应该在什么条件下被物化**，而不是命令式地编写触发逻辑。

#### 2.10.2 Automation Condition（自动化条件）

Automation Condition 是附加到资产定义上的条件对象，用于指定物化逻辑。常见的条件包括：

- `AutomationCondition.eager()`：当上游资产更新时立即物化
- `AutomationCondition.on_missing()`：当资产缺失时物化
- `AutomationCondition.on_cron(...)`：按 cron 调度物化
- `AutomationCondition.any_deps_match(...)`：当任何依赖满足条件时物化
- `AutomationCondition.all_deps_match(...)`：当所有依赖满足条件时物化

#### 2.10.3 声明式自动化的优势

- **可读性**：在资产定义旁边直接看到自动化规则
- **可组合性**：条件可以组合和嵌套，表达复杂逻辑
- **可观测性**：在 Dagster UI 中可以直接看到条件评估结果
- **自动分区处理**：自动处理分区的 fan-in/fan-out

#### 2.10.4 与传感器的关系

声明式自动化在底层是通过 `AutomationConditionSensorDefinition` 传感器实现的，但提供了更高级、更声明式的接口。

### 2.11 Asset Checks（资产检查）

#### 2.11.1 概念定义

**Asset Check** 是对资产数据质量的**验证规则**。每个资产检查验证资产的某个方面，例如：
- 行数是否在预期范围内
- 列是否包含空值
- 数值是否在合理范围内
- 主键是否唯一

#### 2.11.2 资产检查的定义方式

1. **`@asset_check` 装饰器**：为单个资产定义检查
2. **`@multi_asset_check` 装饰器**：一次定义多个资产检查
3. **内联检查**：在资产物化函数内部执行检查

#### 2.11.3 资产检查的执行

- 资产检查可以与资产物化一起执行
- 也可以独立于物化单独执行
- 检查结果会显示在 Dagster UI 中
- 可以配置检查失败时的行为（如阻止下游执行、发送告警等）

### 2.12 Runs（运行）

#### 2.12.1 概念定义

**Run** 是 Dagster 中**一次作业执行的实例**。每次触发作业执行都会创建一个新的 Run。

#### 2.12.2 Run 的核心属性

- **Run ID**：运行的唯一标识符
- **Status**：运行状态（QUEUED、STARTED、SUCCESS、FAILURE 等）
- **Start Time / End Time**：开始和结束时间
- **Tags**：标签，用于分类和筛选
- **Logs**：运行日志
- **Events**：运行过程中产生的事件（物化事件、检查事件等）

#### 2.12.3 Run 的生命周期

1. **QUEUED**：运行被提交到队列，等待启动
2. **STARTED**：运行开始执行
3. **IN_PROGRESS**：运行正在执行中
4. **SUCCESS / FAILURE**：运行成功或失败完成
5. **CANCELED**：运行被取消

### 2.13 Code Locations（代码位置）

#### 2.13.1 概念定义

**Code Location** 是 Dagster 中**可加载和访问的 Dagster 定义的集合**。一个代码位置包含：
- 对包含 `Definitions` 实例的 Python 模块的引用
- 能够成功加载该模块的 Python 环境

#### 2.13.2 Definitions 对象

`Definitions` 对象是 Dagster 代码的入口点，它聚合了所有的定义：
- Assets（资产）
- Jobs（作业）
- Schedules（调度）
- Sensors（传感器）
- Resources（资源）
- Asset Checks（资产检查）
- Executors（执行器）

#### 2.13.3 代码位置的优势

- **隔离性**：每个代码位置在独立的进程中加载，互不影响
- **多环境支持**：可以有多个代码位置，对应不同的环境或项目
- **热重载**：更新代码后可以重新加载代码位置，无需重启服务
- **独立部署**：不同团队的代码可以独立部署和管理

#### 2.13.4 Workspace 文件

`workspace.yaml` 文件告诉 Dagster 在哪里找到代码以及如何加载它。每个条目都是一个代码位置。

```yaml
load_from:
  - python_file: my_pipeline.py
  - python_module: my_package.definitions
```

### 2.14 Executors（执行器）

#### 2.14.1 概念定义

**Executor** 负责**管理作业中各个步骤的执行方式**。Dagster 支持多种执行器，适用于不同的场景。

#### 2.14.2 常见的执行器类型

1. **In-Process Executor**：在单个进程中串行执行所有步骤，适合调试和测试
2. **Multiprocess Executor**：使用多个进程并行执行步骤，是默认的执行器
3. **Ray Executor**：将步骤提交到 Ray 集群执行，支持分布式计算
4. **Dask Executor**：使用 Dask 分布式执行
5. **Celery Executor**：使用 Celery 队列执行

#### 2.14.3 执行器的配置

执行器可以在 Definitions 中配置，也可以在作业级别覆盖配置。

### 2.15 Run Launchers（运行启动器）

#### 2.15.1 概念定义

**Run Launcher** 负责**启动新的 Run Worker 来执行作业运行**。不同的 Run Launcher 决定了运行在什么环境中执行。

#### 2.15.2 常见的 Run Launcher

1. **DefaultRunLauncher**：在当前进程中启动运行，适合开发环境
2. **DockerRunLauncher**：为每个运行启动一个 Docker 容器
3. **K8sRunLauncher**：在 Kubernetes 集群中启动 Pod 执行运行
4. **RayRunLauncher**：将运行作为 Ray 作业提交到 Ray 集群
5. **SlurmRunLauncher**：将运行提交到 SLURM 集群

### 2.16 Run Coordinator（运行协调器）

#### 2.16.1 概念定义

**Run Coordinator** 是 Dagster Webserver 在启动运行时调用的类，负责**管理运行的提交和排队策略**。

#### 2.16.2 常见的 Run Coordinator

1. **DefaultRunCoordinator**：直接启动运行，不排队
2. **QueuedRunCoordinator**：将运行放入队列，由守护进程按顺序启动
3. **Custom Run Coordinator**：可以自定义排队和优先级逻辑

### 2.17 Backfills（回填）

#### 2.17.1 概念定义

**Backfill** 是指**对分区化资产的历史分区进行批量物化**的操作。

#### 2.17.2 回填的应用场景

- 新添加一个资产，需要计算所有历史分区
- 修复了计算逻辑中的 bug，需要重新计算受影响的分区
- 添加了新的列，需要重新计算所有分区

#### 2.17.3 回填的特性

- 可以选择特定的分区范围进行回填
- 支持并行执行多个分区
- 可以监控回填的进度
- 支持取消和重试

### 2.18 Auto-Materialize（自动物化）

#### 2.18.1 概念定义

**Auto-Materialize** 是 Dagster 的一项功能，允许资产**根据特定条件自动被物化**，无需手动触发或配置调度/传感器。

#### 2.18.2 自动物化的触发条件

- 上游资产更新
- 资产缺失
- 按时间调度
- 自定义条件

#### 2.18.3 自动物化策略

- **Eager**：上游更新后立即物化
- **Lazy**：按需物化
- **Scheduled**：按调度物化

---

## 3. 基于 Dagster 的数据湖构建任务架构

### 3.1 架构概述

在本方案中，Dagster 作为**数据架构中央面板**，统一编排和管理整个数据湖构建流程。整个架构包含以下核心组件：

| 组件 | 角色 | Dagster 中的对应概念 |
|------|------|---------------------|
| **Dagster** | 数据编排与中央控制面板 | 编排引擎 + UI |
| **Daft** | 分布式数据处理引擎 | 计算层（Op/Asset 的计算逻辑） |
| **Ray Cluster** | 分布式计算资源池 | 执行环境（Ray Executor/Run Launcher） |
| **S3 数据湖** | 数据存储层 | I/O Manager + 资产物化目标 |
| **RUBKITSQL** | SQL 查询引擎 | 消费层 + 资产查询接口 |

### 3.2 Dagster 作为中央面板的核心职责

1. **资产目录管理**：统一管理数据湖中所有数据资产的元数据和血缘
2. **任务编排调度**：协调数据处理任务的执行顺序和依赖关系
3. **资源统一管理**：统一管理 Ray 集群、S3、SQL 引擎等外部资源
4. **数据质量监控**：通过 Asset Checks 监控各层数据的质量
5. **运行状态可观测**：提供统一的 UI 查看所有任务的运行状态和日志
6. **分区与回填管理**：管理数据湖的分区策略和历史数据回填

### 3.3 数据湖分层架构（以 Dagster 资产视角）

从 Dagster 资产的视角，数据湖分为以下几层，每一层都是一组相关的资产：

```
┌─────────────────────────────────────────────────────────┐
│                    RUBKITSQL 消费层                     │
│  (数据集市资产 / 报表资产 / 分析视图资产)                 │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│                    Gold 层 (黄金层)                      │
│  (应用级数据资产 / 宽表资产 / 指标资产)                   │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│                    Silver 层 (白银层)                    │
│  (清洗标准化资产 / 关联整合资产 / 维度建模资产)            │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│                    Bronze 层 (青铜层)                    │
│  (原始数据资产 / 落地资产 / 源系统镜像)                   │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│                    Source 层 (源数据层)                  │
│  (外部数据源 / Source Assets / 外部资产)                 │
└─────────────────────────────────────────────────────────┘
```

---

## 4. Daft + Ray Cluster + S3 数据湖 + RUBKITSQL 构建任务详细过程

### 4.1 整体数据流（以 Dagster 资产视角）

从 Dagster 的视角来看，整个数据湖构建过程是一系列**资产的物化过程**，每个资产都有明确的上游依赖和计算逻辑。

```
Source Assets (外部数据源)
       │
       ▼
┌─────────────────────────────────────────────────────────┐
│  Bronze Layer Assets (原始落地资产)                      │
│  • raw_users / raw_orders / raw_products               │
│  • 由 Daft 从源系统读取，写入 S3 (Parquet 格式)          │
│  • 按日期分区                                           │
└───────────────────────────┬─────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│  Silver Layer Assets (清洗整合资产)                      │
│  • cleaned_users / cleaned_orders / cleaned_products    │
│  • 由 Daft 进行数据清洗、标准化、去重                    │
│  • 写入 S3 (Delta Lake 或 Parquet 格式)                 │
└───────────────────────────┬─────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│  Gold Layer Assets (应用数据资产)                        │
│  • user_order_summary / product_sales_mart             │
│  • 由 Daft 进行聚合、关联、宽表构建                      │
│  • 写入 S3，供 RUBKITSQL 查询                           │
└───────────────────────────┬─────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│  RUBKITSQL Consumption Assets (消费层资产)               │
│  • 视图 / 报表 / 数据集市                                │
│  • 通过 RUBKITSQL 在 Gold 层数据上构建                   │
└─────────────────────────────────────────────────────────┘
```

### 4.2 Dagster 资源配置

在开始构建之前，首先需要在 Dagster 的 `Definitions` 中配置所有必要的资源。

#### 4.2.1 Ray Cluster Resource

Ray 集群作为分布式计算资源，通过 `dagster-ray` 集成：

- **Ray Resource**：管理与 Ray 集群的连接
- **Ray Executor**：将 Dagster 的 Op/Asset 步骤提交到 Ray 集群执行
- **Ray Run Launcher**（可选）：将整个 Dagster Run 作为 Ray 作业提交

#### 4.2.2 S3 I/O Manager

S3 作为数据湖的存储层，需要自定义 I/O Manager：

- **S3 Parquet I/O Manager**：读写 S3 上的 Parquet 文件
- **S3 Delta Lake I/O Manager**：读写 S3 上的 Delta Lake 表
- 每个 I/O Manager 配置对应的 S3 桶、前缀、访问凭证等

#### 4.2.3 Daft Resource

Daft 作为数据处理引擎，封装为 Dagster Resource：

- 管理 Daft 的 Ray Runner 配置
- 统一管理 Daft 的执行参数（并行度、内存配置等）
- 确保 Daft 与 Ray 集群的正确连接

#### 4.2.4 RUBKITSQL Resource

RUBKITSQL 作为查询引擎，封装为 Dagster Resource：

- 管理 RUBKITSQL 的连接配置
- 支持执行 SQL 语句创建视图、表
- 支持查询数据质量验证

### 4.3 Bronze 层：原始数据落地

#### 4.3.1 资产定义

Bronze 层的资产是**原始数据落地资产**，对应源系统数据在 S3 中的原始镜像。

**资产示例：**
- `raw_users`：用户表原始数据
- `raw_orders`：订单表原始数据
- `raw_products`：产品表原始数据

#### 4.3.2 资产特性

- **分区策略**：按日期分区（`DailyPartitionsDefinition`），每天一个分区
- **存储格式**：Parquet 格式，按日期分区目录存储在 S3 中
- **数据血缘**：上游依赖为 Source Asset（外部数据源）
- **物化方式**：全量或增量落地，取决于源系统特性

#### 4.3.3 物化过程（Dagster 视角）

当 `raw_users` 资产被物化时：

1. **Dagster 触发资产物化**
   - 可以通过调度（每天凌晨触发）
   - 可以通过传感器（源系统有新数据时触发）
   - 可以通过声明式自动化（`AutomationCondition.on_cron(...)`）

2. **Dagster 分配计算资源**
   - 通过 Ray Executor 将物化任务提交到 Ray Cluster
   - 为该任务分配必要的 CPU/内存资源

3. **Daft 执行数据抽取**
   - Daft 使用 Ray Runner 连接到 Ray Cluster
   - Daft 从源系统（数据库/API/消息队列）读取数据
   - Daft 将数据转换为分布式 DataFrame

4. **数据写入 S3**
   - Dagster 的 S3 Parquet I/O Manager 接管输出
   - I/O Manager 将 Daft DataFrame 写入 S3 的对应分区路径
   - 写入路径：`s3://data-lake/bronze/users/date=YYYY-MM-DD/`

5. **Dagster 记录物化事件**
   - 记录物化时间、数据行数、文件大小等元数据
   - 更新资产状态为"已物化"
   - 触发下游资产的自动化条件评估

#### 4.3.4 资产检查（Asset Checks）

对 Bronze 层资产配置以下检查：
- **行数检查**：确保数据行数在合理范围内（不为 0，不超过历史均值的 200%）
- **列存在性检查**：确保所有预期列都存在
- **文件完整性检查**：确保 Parquet 文件可正常读取

### 4.4 Silver 层：数据清洗与整合

#### 4.4.1 资产定义

Silver 层的资产是**清洗和标准化后的资产**，经过数据质量处理后的数据。

**资产示例：**
- `cleaned_users`：清洗后的用户数据
- `cleaned_orders`：清洗后的订单数据
- `cleaned_products`：清洗后的产品数据
- `user_order_joined`：用户与订单关联后的宽表

#### 4.4.2 资产特性

- **分区策略**：与 Bronze 层对应，按日期分区
- **存储格式**：Parquet 或 Delta Lake 格式，支持 ACID
- **数据血缘**：上游依赖 Bronze 层的对应资产
- **物化方式**：增量处理，每天处理新增分区

#### 4.4.3 物化过程（Dagster 视角）

当 `cleaned_users` 资产被物化时：

1. **Dagster 检测上游更新**
   - 声明式自动化条件 `AutomationCondition.eager()` 检测到 `raw_users` 有新分区物化
   - 触发 `cleaned_users` 对应分区的物化

2. **加载上游数据**
   - S3 Parquet I/O Manager 从 S3 加载 `raw_users` 的对应分区数据
   - 数据以 Daft DataFrame 的形式传递给计算函数

3. **Daft 执行数据清洗**
   - 在 Ray Cluster 上分布式执行清洗逻辑
   - 清洗操作包括：去重、空值处理、类型转换、标准化命名、数据过滤
   - Daft 的惰性执行优化器自动优化执行计划

4. **写入 Silver 层 S3**
   - I/O Manager 将清洗后的 Daft DataFrame 写入 S3 的 Silver 层路径
   - 写入路径：`s3://data-lake/silver/cleaned_users/date=YYYY-MM-DD/`
   - 如果使用 Delta Lake，则更新 Delta 表的对应分区

5. **Dagster 记录物化与质量检查**
   - 记录物化事件和元数据
   - 执行资产检查（空值率、重复率、数据分布等）
   - 更新资产血缘图谱

#### 4.4.4 关联资产的物化

对于 `user_order_joined` 这样的关联资产：

- 上游依赖：`cleaned_users` 和 `cleaned_orders`
- 当两个上游资产的对应分区都物化完成后，触发关联资产的物化
- Dagster 的声明式自动化自动处理分区的 fan-in（扇入）逻辑
- Daft 在 Ray 上执行分布式 Join 操作

### 4.5 Gold 层：应用数据构建

#### 4.5.1 资产定义

Gold 层的资产是**面向应用的、业务级的数据资产**，直接供分析和报表使用。

**资产示例：**
- `user_order_summary`：用户订单汇总宽表
- `product_sales_mart`：产品销售数据集市
- `daily_kpi_metrics`：每日 KPI 指标表

#### 4.5.2 资产特性

- **分区策略**：按日期分区，部分指标资产可能按小时分区
- **存储格式**：Parquet 或 Delta Lake，优化查询性能
- **数据血缘**：上游依赖 Silver 层的多个资产
- **物化方式**：增量聚合，支持全量重算

#### 4.5.3 物化过程（Dagster 视角）

当 `daily_kpi_metrics` 资产被物化时：

1. **Dagster 调度或触发物化**
   - 可以通过调度（每天凌晨 2 点，确保 Silver 层数据就绪）
   - 也可以通过声明式自动化，当所有上游依赖都更新后自动触发

2. **多上游数据加载**
   - I/O Manager 加载多个上游 Silver 资产的对应分区
   - 数据以 Daft DataFrame 的形式准备好

3. **Daft 执行复杂聚合**
   - 在 Ray Cluster 上分布式执行复杂的聚合、窗口计算、指标计算
   - 利用 Daft 的向量化执行和查询优化器提升性能
   - 支持复杂的业务逻辑：同比、环比、累计值、分位数等

4. **写入 Gold 层 S3**
   - I/O Manager 将结果写入 S3 的 Gold 层路径
   - 写入路径：`s3://data-lake/gold/daily_kpi_metrics/date=YYYY-MM-DD/`
   - 数据按查询模式优化排序和存储格式

5. **注册到 RUBKITSQL**
   - 物化完成后，通过 RUBKITSQL Resource 将新分区注册到 SQL 引擎
   - 更新表的元数据，使新数据可被 SQL 查询

#### 4.5.4 资产检查（Gold 层）

Gold 层作为直接面向业务的层，配置更严格的质量检查：
- **指标合理性检查**：确保指标值在历史合理范围内
- **完整性检查**：确保所有维度组合都有数据
- **一致性检查**：与历史数据的趋势一致性校验
- **业务规则检查**：特定业务逻辑的验证

### 4.6 RUBKITSQL 消费层

#### 4.6.1 资产定义

RUBKITSQL 层的资产是**基于 Gold 层数据构建的 SQL 视图、报表和数据集市**。

**资产示例：**
- `view_user_retention`：用户留存分析视图
- `view_sales_dashboard`：销售仪表盘视图
- `mart_finance_report`：财务报表数据集市

#### 4.6.2 资产特性

- **资产类型**：这些是 Dagster 中的"外部资产"或"视图资产"
- **存储位置**：逻辑视图，存储在 RUBKITSQL 的元数据中
- **数据血缘**：上游依赖 Gold 层的物理资产
- **物化方式**：视图刷新或表重建

#### 4.6.3 视图刷新过程（Dagster 视角）

当 `view_sales_dashboard` 资产需要更新时：

1. **Dagster 检测上游 Gold 层更新**
   - 当 `product_sales_mart` 等上游资产物化完成后
   - 触发视图资产的"物化"（实际是刷新视图）

2. **执行 SQL 刷新**
   - 通过 RUBKITSQL Resource 连接到 SQL 引擎
   - 执行 `REFRESH MATERIALIZED VIEW` 或 `CREATE OR REPLACE VIEW` 语句
   - 或者重新生成物理表（如果是物化视图）

3. **Dagster 记录事件**
   - 记录视图刷新事件
   - 更新资产的最后更新时间
   - 记录视图的元数据（列数、行数等）

#### 4.6.4 数据验证

- 通过 RUBKITSQL 执行查询，验证视图数据的正确性
- 与 Gold 层数据进行一致性校验
- 记录数据质量检查结果

### 4.7 分区与回填管理

#### 4.7.1 分区策略

整个数据湖采用**日期分区策略**，各层保持一致的分区粒度：

- **Bronze 层**：按天分区，对应源系统的每日数据
- **Silver 层**：按天分区，与 Bronze 层一一对应
- **Gold 层**：按天分区，部分指标按小时分区
- **消费层**：按天分区或全量视图

#### 4.7.2 Dagster 中的分区管理

- 使用 `DailyPartitionsDefinition` 定义日期分区
- 每个资产的分区独立追踪状态
- Dagster UI 中可以查看每个分区的物化状态、质量检查结果
- 支持按分区进行回溯和重算

#### 4.7.3 回填（Backfill）流程

当需要重新计算历史数据时：

1. **在 Dagster UI 中发起回填请求**
   - 选择要回填的资产
   - 选择回填的分区范围
   - 配置并行度和优先级

2. **Dagster Run Coordinator 管理回填队列**
   - 将回填任务加入运行队列
   - 控制并发数，避免冲击 Ray 集群

3. **分区并行执行**
   - 多个分区的物化任务并行提交到 Ray Cluster
   - Ray 调度器在集群中分配资源
   - Daft 分布式执行每个分区的计算

4. **回填进度监控**
   - Dagster UI 实时显示回填进度
   - 可以查看每个分区的执行状态
   - 支持暂停、取消、重试

### 4.8 自动化与调度

#### 4.8.1 声明式自动化配置

各层资产配置不同的自动化策略：

**Bronze 层资产：**
```python
@asset(
    automation_condition=AutomationCondition.on_cron("0 1 * * *"),
    partitions_def=DailyPartitionsDefinition()
)
def raw_users():
    ...
```
- 每天凌晨 1 点自动物化前一天的分区

**Silver 层资产：**
```python
@asset(
    automation_condition=AutomationCondition.eager(),
    partitions_def=DailyPartitionsDefinition()
)
def cleaned_users(raw_users):
    ...
```
- 当上游 `raw_users` 有新分区物化后，立即自动物化

**Gold 层资产：**
```python
@asset(
    automation_condition=AutomationCondition.all_deps_match(
        AutomationCondition.eager()
    ),
    partitions_def=DailyPartitionsDefinition()
)
def daily_kpi_metrics(cleaned_users, cleaned_orders, cleaned_products):
    ...
```
- 当所有上游依赖的对应分区都物化后，自动触发物化

**消费层视图：**
```python
@asset(
    automation_condition=AutomationCondition.eager()
)
def view_sales_dashboard(product_sales_mart):
    ...
```
- 当上游 Gold 层资产更新后，自动刷新视图

#### 4.8.2 传感器补充

除了声明式自动化，还可以配置传感器处理特殊场景：

- **S3 传感器**：监控源数据 S3 桶，有新文件到达时触发 Bronze 层物化
- **运行状态传感器**：关键作业失败时发送告警通知
- **自定义传感器**：监控业务系统的特定事件

### 4.9 运行时执行流程

一次完整的数据湖构建运行（Run）在 Dagster 中的执行流程：

```
1. 触发源
   ├─ 调度触发 (Schedule)
   ├─ 传感器触发 (Sensor)
   ├─ 声明式自动化触发 (Automation Condition)
   └─ 手动触发 (UI/API)
         │
         ▼
2. Run Coordinator
   └─ 将 Run 放入队列，管理优先级和并发
         │
         ▼
3. Run Launcher (Ray Run Launcher)
   └─ 提交 Ray Job 到 Ray Cluster 启动 Run Worker
         │
         ▼
4. Run Worker 启动
   ├─ 加载代码位置 (Code Location)
   ├─ 初始化资源 (Resources)
   └─ 准备执行环境
         │
         ▼
5. Executor (Ray Executor)
   └─ 将资产物化步骤分发为 Ray 任务
         │
         ▼
6. Ray Cluster 执行
   ├─ Ray 调度器分配 Worker
   ├─ Daft 使用 Ray Runner 执行计算
   └─ 分布式处理数据
         │
         ▼
7. I/O Manager
   ├─ 从 S3 读取上游数据
   └─ 将结果写入 S3 对应层
         │
         ▼
8. 资产检查 (Asset Checks)
   └─ 验证数据质量，记录检查结果
         │
         ▼
9. 事件记录
   ├─ 物化事件 (Materialization Event)
   ├─ 质量检查事件 (Asset Check Event)
   └─ 日志和元数据
         │
         ▼
10. 下游触发
    └─ 触发声明式自动化条件评估，级联更新下游资产
```

### 4.10 可观测性与监控

Dagster 作为中央面板，提供统一的可观测性：

#### 4.10.1 资产目录视图
- 所有数据湖资产的统一目录
- 按层（Bronze/Silver/Gold/消费）组织
- 每个资产显示：状态、最后更新时间、分区状态、质量检查结果

#### 4.10.2 数据血缘视图
- 可视化展示资产之间的依赖关系
- 可以追溯数据从源系统到消费层的完整链路
- 支持影响分析：修改一个资产会影响哪些下游

#### 4.10.3 运行监控
- 所有运行的历史记录和状态
- 运行时长、成功率等统计指标
- 失败运行的日志和错误详情

#### 4.10.4 数据质量监控
- 所有资产检查的结果汇总
- 质量趋势和异常告警
- 按层、按资产维度的质量报表

### 4.11 环境管理

通过 Dagster 的 Resources 和 I/O Manager 机制，实现多环境隔离：

#### 4.11.1 开发环境
- **Ray**：本地 Ray 实例或小型开发集群
- **S3**：开发专用 S3 桶或本地 MinIO
- **RUBKITSQL**：开发实例
- **数据**：采样数据或测试数据

#### 4.11.2 测试环境
- **Ray**：测试集群
- **S3**：测试 S3 桶
- **RUBKITSQL**：测试实例
- **数据**：完整的测试数据集

#### 4.11.3 生产环境
- **Ray**：生产级 Ray 集群，自动扩缩容
- **S3**：生产数据湖桶，版本化管理
- **RUBKITSQL**：生产集群，高可用部署
- **数据**：全量生产数据

**Dagster 的优势**：资产定义代码完全相同，只需要切换 Resource 配置即可在不同环境间迁移。

---

## 总结

Dagster 作为数据架构的中央面板，为 Daft + Ray Cluster + S3 数据湖 + RUBKITSQL 的技术栈提供了：

1. **统一的资产视图**：所有数据资产统一管理，清晰的血缘关系
2. **声明式的编排**：通过 Software-Defined Assets 和 Automation Condition 实现自动化
3. **分布式计算集成**：通过 Ray 集成支持大规模分布式数据处理
4. **灵活的存储抽象**：通过 I/O Manager 统一管理 S3 数据湖的读写
5. **完整的可观测性**：从任务运行到数据质量的全面监控
6. **环境级的隔离**：开发、测试、生产环境的无缝切换

这种架构使得数据湖的构建和维护变得更加可控、可观测、可扩展，Dagster 真正成为了整个数据架构的"中央控制面板"。
