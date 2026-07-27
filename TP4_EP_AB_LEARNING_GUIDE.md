# Qwen3.6-35B-A3B W8A8: 四卡 TP=4 + EP 学习与 A/B 优化手册

## 1. 目的和边界

本手册用于在 K12 集群上学习一个 **单实例** Qwen 推理服务的完整基础设施链路：Kubernetes 设备调度、Ascend 卡可见性、vLLM-Ascend 的 TP/EP 并行、OpenAI 兼容 HTTP 服务，以及逐项 A/B 推理优化。

本轮目标是：

```text
模型:       /home/admin/models/Qwen3.6-35B-A3B-w8a8
实例数:     1 个 vLLM server
物理 NPU:   Phy-ID 8, 9, 10, 11
并行策略:   data parallel = 1, tensor parallel = 4, expert parallel = enabled
端口:       8000
范围:       单服务学习与基准测试；暂不接入 Ray、Dagster 或 Stage 2
```

不要将登录密码写入 YAML、脚本、Git 或本手册。所有 Kubernetes 控制命令都在 `server-00` 执行，A3 只用于观察本机 NPU、宿主模型文件和容器运行情况。

## 2. 当前已核实的集群状态

核实时间：2026-07-27（UTC）。

```text
server-00: control-plane，K3s v1.34.6+k3s1
a3-server-00: arm64 worker，openEuler 24.03，NPU driver/CANN 工具报告 npu-smi 26.0.rc1
K12 正在运行: mineru-dagster、raycluster-k12-smoke-head
Qwen Deployment: qwen36-35b-a3b-worker-14-15 = 0/0
Qwen Deployment: qwen36-35b-a3b-worker-8npu = 0/0
A3 NPU: Phy-ID 0-15 当前无 NPU 进程
```

当前被分配的八张卡按已有 8-NPU 清单是 `Phy-ID 8-15`。四卡学习组取其中连续的 `8-11`，保留 `12-15` 供以后部署第二个 TP=4 副本或作对照实验。容器中的逻辑设备编号由 Ascend 设备插件决定，**不能假定仍是物理编号 8-11**。

## 3. 先理解组件和并行模型

```text
Client / benchmark
        |
        v
Kubernetes Service :8000
        |
        v
一个 Pod
  └─ 一个 vLLM-Ascend server
       ├─ rank 0..3
       ├─ TP=4：一个模型层的张量计算分到四张卡
       ├─ EP=on：MoE 专家按 rank 分布，token 路由到对应专家
       ├─ DP=1：没有第二个模型副本
       └─ HCCL：四个 rank 之间完成 all-reduce / all-to-all 等通信
```

### 3.1 Tensor Parallelism（TP）

`tensor_parallel_size=4` 表示一个请求由四卡协同完成。模型权重和 KV cache 按 TP 规则分片，单请求延迟通常低于 TP=1，代价是 HCCL 通信和四卡必须同时可用。TP 值必须等于同一个 vLLM 实例可见的设备数。

### 3.2 Expert Parallelism（EP）

A3B 是 MoE（Mixture of Experts）模型。每个 token 会被 router 送往少量激活专家。`--enable-expert-parallel` 让专家在 TP ranks 间分布；它不能替代 TP，而是与 TP=4 同时存在。EP 的收益取决于 token 路由均衡、batch 大小和 all-to-all 通信。

### 3.3 Data Parallelism（DP）

`data_parallel_size=1` 是本阶段的关键：只有一个完整服务实例。以后若使用 8 张卡的吞吐扩展，优先比较：

```text
方案 A: 一个 TP=8 实例
方案 B: 两个独立 TP=4 实例（DP=2 的业务效果）
```

对多数在线请求场景，方案 B 往往更易隔离故障、尾延迟更稳，也更适合 Ray 根据队列分流；但应以实际压测数据决定。

### 3.4 vLLM 调度器的三个关键量

```text
running:   已被 vLLM 调度、正在 prefill 或 decode 的序列数
waiting:   进入服务但尚未被调度的序列数
max-num-seqs: 同一轮可同时容纳的序列上限
max-num-batched-tokens: 单次调度批允许的 token 预算
```

`waiting` 长期大于零且 AICore 高，通常说明实例接近饱和；`waiting=0` 且 AICore 低，通常是客户端供给、CPU、网络或请求长度分布不足，不应盲目加大 vLLM 批参数。

## 4. 上线前只读检查

### 4.1 在 server-00 检查控制面

```bash
ssh admin@110.120.0.3
sudo -i

k3s kubectl -n k12 get pods -o wide
k3s kubectl -n k12 get deploy,svc
k3s kubectl get node a3-server-00 -o wide
k3s kubectl describe node a3-server-00 | sed -n '/Allocatable:/,/System Info:/p'
```

检查是否存在占用 `Ascend910-8` 至 `Ascend910-11` 的 Pod。不要只看 `resources.requests`：本集群通过 `huawei.com/Ascend910` 注解选择物理卡，必须同时检查 Pod annotation：

```bash
k3s kubectl -n k12 get pod -o json | \
  jq -r '.items[] | [.metadata.name, (.metadata.annotations["huawei.com/Ascend910"] // "-")] | @tsv'
```

### 4.2 在 A3 检查物理设备和模型

```bash
ssh admin@110.123.0.3
npu-smi info
ls -ld /home/admin/models/Qwen3.6-35B-A3B-w8a8
sed -n '1,80p' /home/admin/models/Qwen3.6-35B-A3B-w8a8/config.json
```

当前权重目录的配置架构是 `Qwen3_5MoeForConditionalGeneration`，是多模态 MoE 模型。部署命令中的模型名必须与实际 API 的 `--served-model-name` 保持一致；本工程现有约定为 `qwen3.6-35b-a3b`。

## 5. 学习版资源设计

学习阶段不要复用历史 2 卡或 8 卡 Deployment 名称，避免 Service selector 和 ConfigMap 相互影响。建议使用独立资源：

```text
Deployment: qwen36-35b-a3b-tp4-learning
Service:    qwen36-35b-a3b-tp4-learning
ConfigMap:  qwen36-35b-a3b-tp4-learning-launcher
Label:      app.kubernetes.io/name=qwen36-35b-a3b-tp4-learning
```

建议起步资源：

```yaml
requests:
  cpu: "64"
  memory: 256Gi
  huawei.com/Ascend910: "4"
limits:
  cpu: "64"
  memory: 256Gi
  huawei.com/Ascend910: "4"
```

这里的 CPU 和内存是学习阶段的上限，不代表模型一定会使用满。CPU 主要用于 tokenizer、HTTP、请求调度、HCCL 辅助线程和多模态图像预处理；NPU HBM 是模型权重与 KV cache 的硬约束。

## 6. 最小可用 YAML 的关键片段

完整 YAML 应从工程现有 `k8s/qwen36-35b-a3b-8npu-deployment.yaml` 复制后收敛为一个服务，至少满足以下内容。

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: qwen36-35b-a3b-tp4-learning
  namespace: k12
  annotations:
    huawei.com/Ascend910: Ascend910-8,Ascend910-9,Ascend910-10,Ascend910-11
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app.kubernetes.io/name: qwen36-35b-a3b-tp4-learning
  template:
    metadata:
      annotations:
        huawei.com/Ascend910: Ascend910-8,Ascend910-9,Ascend910-10,Ascend910-11
      labels:
        app.kubernetes.io/name: qwen36-35b-a3b-tp4-learning
    spec:
      nodeSelector:
        kubernetes.io/arch: arm64
        kubernetes.io/hostname: a3-server-00
        node.kubernetes.io/npu.chip.name: Ascend910
      containers:
      - name: vllm-ascend
        image: 110.120.0.3:8889/mineru/vllm-ascend-worker:v0.21.0rc1-a3-20260713-s3
        imagePullPolicy: Never
        resources:
          requests:
            cpu: "64"
            memory: 256Gi
            huawei.com/Ascend910: "4"
          limits:
            cpu: "64"
            memory: 256Gi
            huawei.com/Ascend910: "4"
        securityContext:
          privileged: true
```

需要保留的挂载：模型权重 `/home/admin/models/Qwen3.6-35B-A3B-w8a8`、Ascend driver、DCMI、`npu-smi` 和足够大的 `/dev/shm`。学习版第一阶段不要加 `ray-bridge` sidecar；它会把 Ray 的资源注册和 HTTP 调用也混入故障面，降低 A/B 结论的可解释性。

## 7. vLLM 启动脚本：B0 基线

先在容器中查看本镜像真实支持的参数；不同 vLLM-Ascend 发布版的参数可能有差异：

```bash
vllm serve --help | less
```

基线命令如下。`bfloat16`、`quantization ascend`、TP=4 和 EP 是本实验固定项。

```bash
export PYTORCH_NPU_ALLOC_CONF=expandable_segments:True
export HCCL_OP_EXPANSION_MODE=AIV
export HCCL_BUFFSIZE=1024
export OMP_NUM_THREADS=1
export TASK_QUEUE_ENABLE=1

python - <<'PY'
import torch
import torch_npu
count = torch.npu.device_count()
assert count == 4, f"expected 4 visible devices, got {count}"
print("logical visible NPUs:", count)
PY

npu-smi info
ls -l /dev/davinci*

exec vllm serve /models/Qwen3.6-35B-A3B-w8a8 \
  --host 0.0.0.0 \
  --port 8000 \
  --served-model-name qwen3.6-35b-a3b \
  --data-parallel-size 1 \
  --tensor-parallel-size 4 \
  --enable-expert-parallel \
  --quantization ascend \
  --dtype bfloat16 \
  --max-model-len 8192 \
  --max-num-seqs 16 \
  --max-num-batched-tokens 4096 \
  --gpu-memory-utilization 0.85 \
  --trust-remote-code \
  --no-enable-prefix-caching
```

不要设置 `ASCEND_VISIBLE_DEVICES=8,9,10,11` 作为 vLLM rank 的依据。该环境变量是否需要由本集群 Ascend 设备插件的容器映射决定；先从容器内的 `torch.npu.device_count()`、`npu-smi info` 和 `/dev/davinci*` 得出事实，再把每个逻辑设备对应到宿主 `Phy-ID`。

## 8. 部署、映射验证和 API 冒烟

在 server-00：

```bash
cd /home/admin/Desktop/sql/qwen36_35b_a3b
sudo -i

k3s kubectl apply -f k8s/qwen36-35b-a3b-tp4-learning.yaml
k3s kubectl -n k12 rollout status deploy/qwen36-35b-a3b-tp4-learning --timeout=1800s
k3s kubectl -n k12 get pod -l app.kubernetes.io/name=qwen36-35b-a3b-tp4-learning -o wide
k3s kubectl -n k12 logs deploy/qwen36-35b-a3b-tp4-learning -c vllm-ascend -f
```

确认容器设备映射：

```bash
k3s kubectl -n k12 exec deploy/qwen36-35b-a3b-tp4-learning -c vllm-ascend -- \
  bash -lc 'echo "ASCEND_VISIBLE_DEVICES=${ASCEND_VISIBLE_DEVICES:-unset}"; npu-smi info; ls -l /dev/davinci*; python -c "import torch,torch_npu; print(torch.npu.device_count())"'
```

在 A3 宿主确认物理卡上出现 vLLM 进程，且仅使用 `Phy-ID 8-11`：

```bash
npu-smi info
```

建立本地 API 通道：

```bash
k3s kubectl -n k12 port-forward svc/qwen36-35b-a3b-tp4-learning 8000:8000
```

另开一个终端测试：

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/v1/models | jq .

curl -fsS http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "qwen3.6-35b-a3b",
    "messages": [{"role":"user","content":"用两句话说明张量并行和专家并行的区别。"}],
    "temperature": 0,
    "max_tokens": 128,
    "chat_template_kwargs": {"enable_thinking": false}
  }' | jq .
```

## 9. A/B 实验方法

### 9.1 固定测试条件

每个实验只改一个变量。其余保持一致：模型权重、镜像、TP=4、EP=on、输入数据集、随机种子、请求并发、最大输出长度、预热时间和采样时间。

建议压测负载：

```text
40% 短请求：输入约 300 tokens，输出 128 tokens
40% 中请求：输入约 2,000 tokens，输出 512 tokens
20% 长请求：输入约 8,000 tokens，输出 1,024 tokens
预热：2 分钟
正式采样：10 分钟
```

每轮记录：成功率、请求/秒、输入 tokens/秒、输出 tokens/秒、TTFT P50/P95、ITL P50/P95、端到端 P50/P95、running/waiting、每卡 AICore、每卡 HBM、Pod CPU/RSS 与错误数。

### 9.2 推荐实验序列

| ID | 唯一变化 | 对照 | 实验 | 目的 |
|---|---|---:|---:|---|
| B0 | 基线 | - | `seqs=16`，`tokens=4096` | 找到正确且稳定的起点 |
| B1 | 异步调度 | 默认 | `--async-scheduling` | 减少调度空洞，观察 P95 与健康性 |
| B2 | 序列容量 | 16 | 32 | 增加同时活跃序列数 |
| B3 | 序列容量 | 32 | 48 | 仅在 B2 无队列/无 HBM 风险时继续 |
| B4 | token 预算 | 4096 | 8192 | 提升 prefill 合批能力 |
| B5 | Prefix cache | off | on | 只对共享长系统提示词有效 |
| B6 | 编译策略 | 默认 | `FULL_DECODE_ONLY` | 单独比较 decode 吞吐与稳定性 |
| B7 | 上下文长度 | 8192 | 16384 | 测 HBM 与长文本尾延迟 |

`--async-scheduling`、编译配置等参数必须先由该镜像的 `vllm serve --help` 或启动日志确认可用。若某轮参数不支持，记录为“不支持”，回到前一稳定版本，不要混入其他改动。

### 9.3 结果判断

```text
waiting > 0、AICore 高、P95 上升：实例接近饱和；不要再加 max-num-seqs。
waiting > 0、AICore 低、CPU 高：CPU/tokenizer/客户端/网络可能是瓶颈。
waiting = 0、AICore 低：压测供给不足，应增加客户端并发或改进请求流水。
HBM 持续逼近上限：降低 max-model-len、max-num-seqs 或 memory utilization。
吞吐提高但错误、重试或 P95 显著恶化：不应作为默认配置。
```

## 10. 监控命令

### 10.1 A3 物理 NPU

```bash
watch -n 1 npu-smi info
```

重点看 `AICore(%)`、`HBM-Usage(MB)` 与 process table。TP=4 正常时，物理 8-11 都应该有同一个 vLLM 实例的 rank 进程；四张卡 HBM 不一定完全相同，但长期只有一张卡活动通常意味着绑定或 HCCL 问题。

### 10.2 Pod 和 vLLM 指标

```bash
# server-00
k3s kubectl -n k12 top pod -l app.kubernetes.io/name=qwen36-35b-a3b-tp4-learning
k3s kubectl -n k12 describe pod -l app.kubernetes.io/name=qwen36-35b-a3b-tp4-learning

# 在 Pod 内。/metrics 是否暴露取决于当前 vLLM 版本。
k3s kubectl -n k12 exec deploy/qwen36-35b-a3b-tp4-learning -c vllm-ascend -- \
  curl -fsS http://127.0.0.1:8000/metrics
```

需要长期记录时，采样重定向到单独的实验目录，文件名带实验 ID，例如 `reports/tp4/b2-seqs32-npu-smi.log`。不要覆盖上一轮结果。

## 11. 停止与清理

学习结束时只删除本手册定义的独立学习资源：

```bash
# server-00；先确认名称精确匹配。
k3s kubectl -n k12 get deploy,svc | grep qwen36-35b-a3b-tp4-learning
k3s kubectl -n k12 delete -f k8s/qwen36-35b-a3b-tp4-learning.yaml
```

不要删除历史 `qwen36-35b-a3b-worker-14-15`、`qwen36-35b-a3b-worker-8npu`、Ray Head、Dagster 或 MinIO 资源；它们可能是后续生产/回退路径的一部分。

## 12. 下一阶段

只有在 B0 至 B6 中选定稳定配置后，才进入第二阶段：给此 Pod 增加 Ray bridge，将服务注册为一个 API 资源，Ray Worker 只通过 HTTP 调用它。Dagster 最后负责提交与记录实验参数。这样能分别定位：

```text
模型 / HCCL / vLLM 造成的性能问题
Ray 排队、HTTP 并发造成的问题
Dagster 编排造成的问题
```

建议的验收标准：所有 TP ranks 正常加载，物理 8-11 绑定正确，API 冒烟成功，30 分钟持续混合负载无重启、无 HBM OOM、无健康检查失败，并有一份可复现实验结果表。
