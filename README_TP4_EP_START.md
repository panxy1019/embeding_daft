# Qwen TP=4 EP 学习实例：启动与验证

## 目标

启动一个独立的 Qwen3.6-35B-A3B W8A8 vLLM-Ascend 服务：一个 Pod、一个服务、四张物理卡 `8-11`、`TP=4`、`EP=on`、`DP=1`。本实例不加入 Ray，也不会改动 Dagster、Ray Head、MinerU 或已有的已缩容 Qwen Deployment。

YAML 文件：`qwen36-35b-a3b-tp4-learning.yaml`。

## 启动

在 `server-00`：

```bash
cd /home/admin/testpanxy/infralearning
sudo -i

# 启动前确认没有业务占用学习组物理卡。
k3s kubectl -n k12 get pod -o json | \
  jq -r '.items[] | [.metadata.name, (.metadata.annotations["huawei.com/Ascend910"] // "-")] | @tsv'

# 创建 ConfigMap、Deployment、Service。
k3s kubectl apply -f qwen36-35b-a3b-tp4-learning.yaml
k3s kubectl -n k12 rollout status deploy/qwen36-35b-a3b-tp4-learning --timeout=1800s
```

模型首次载入很慢；启动探针允许最长 60 分钟。跟踪日志：

```bash
k3s kubectl -n k12 logs deploy/qwen36-35b-a3b-tp4-learning -c vllm-ascend -f
```

## 设备绑定验证

在 `server-00`，容器中必须看到四个逻辑设备：

```bash
k3s kubectl -n k12 exec deploy/qwen36-35b-a3b-tp4-learning -c vllm-ascend -- \
  bash -lc 'echo "ASCEND_VISIBLE_DEVICES=${ASCEND_VISIBLE_DEVICES:-unset}"; npu-smi info; ls -l /dev/davinci*; python -c "import torch,torch_npu; print(torch.npu.device_count())"'
```

在 A3 宿主确认 vLLM 只占用物理 `Phy-ID 8-11`：

```bash
ssh admin@110.123.0.3
npu-smi info
```

容器内逻辑编号可以是 `0-3`，这没有问题。Kubernetes annotation 决定物理卡，vLLM 的 `TP rank 0-3` 使用容器逻辑设备。

## API 验证

在 `server-00` 开启端口转发：

```bash
k3s kubectl -n k12 port-forward svc/qwen36-35b-a3b-tp4-learning 8000:8000
```

另开终端：

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/v1/models | jq .

curl -fsS http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "qwen3.6-35b-a3b",
    "messages": [{"role":"user","content":"解释 TP=4 和 EP 的作用。"}],
    "temperature": 0,
    "max_tokens": 128,
    "chat_template_kwargs": {"enable_thinking": false}
  }' | jq .
```

## B0 基线参数

```text
TP=4
EP=on
DP=1
max_model_len=8192
max_num_seqs=16
max_num_batched_tokens=4096
gpu_memory_utilization=0.85
prefix cache=off
async scheduling=off/default
```

在基线验证稳定前，不要同时开启 `--async-scheduling`、改变 max-num-seqs 或修改 compilation config。后续每个 A/B 轮次只改一个参数，并保存 AICore、HBM、vLLM running/waiting、TTFT/P95、输出 tokens/s。

## 停止

仅删除本学习实例：

```bash
cd /home/admin/testpanxy/infralearning
sudo -i
k3s kubectl -n k12 delete -f qwen36-35b-a3b-tp4-learning.yaml
```

不要删除 `qwen36-35b-a3b-worker-14-15`、`qwen36-35b-a3b-worker-8npu`、Ray Head、Dagster 或 MinIO 资源。
