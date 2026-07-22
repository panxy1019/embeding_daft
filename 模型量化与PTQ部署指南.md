# 模型量化与 PTQ 部署指南

> 覆盖 FP4 / FP8 / INT8、W8A16 / W8A8，以及 GPTQ / AWQ / SmoothQuant。  
> 面向将已训练 LLM 从 FP16/BF16 部署到更小显存或更高吞吐环境的工程人员。更新：2026-07-22。

## 1. 三个层次，先别混淆

| 层次 | 名词 | 回答的问题 |
| --- | --- | --- |
| 数值格式 | INT8、FP8、FP4 | 一个数以什么编码、多少 bit 保存？ |
| 计算/存储配方 | W8A16、W8A8 | 权重 W 和激活 A 分别用什么精度？ |
| PTQ 算法 | GPTQ、AWQ、SmoothQuant | 不重训时如何选尺度、补偿误差或处理离群值？ |

`W8A16` 不是一种数据类型，也不等于某个算法；它表示“权重 8 bit，激活 16 bit”。GPTQ/AWQ 常见产物是 `W4A16`，SmoothQuant 的典型目标是 INT8 `W8A8`。

## 2. 统一量化数学框架

全篇统一采用 batch 行向量约定。设输入激活、权重和输出分别为

$$
X\in\mathbb{R}^{N\times d},\qquad
W\in\mathbb{R}^{d\times m},\qquad
Y=XW\in\mathbb{R}^{N\times m}.
$$

量化后得到低精度重建值 \(\hat X,\hat W\)，并希望

$$
\hat Y=\hat X\hat W,\qquad
\|\Delta Y\|_F=\|XW-\hat X\hat W\|_F
$$

尽可能小。这是后面 INT8/FP8/FP4、W8A16/W8A8 和三类 PTQ 算法的共同目标。

### 2.1 有限码本视角

量化的本质是用有限码本 \(\mathcal Q\) 逼近连续实数：

$$
\hat x=\underset{q\in\mathcal Q}{\arg\min}\;|x-q|^2.
$$

INT8 经过 scale 后使用近似均匀码本；FP8/FP4 使用由指数和尾数产生的非均匀码本。scale、zero-point、group/block 划分以及 GPTQ/AWQ/SmoothQuant，都是在改变码本位置、选择映射方式或重新分配误差。

### 2.2 均匀仿射量化

给定 bit 数 \(b\)、整数区间 \([q_{\min},q_{\max}]\) 和实数范围 \([\alpha,\beta]\)，定义反量化步长 \(\Delta>0\)：

$$
\Delta=\frac{\beta-\alpha}{q_{\max}-q_{\min}},
\qquad
z=\operatorname{clip}\!\left(
\operatorname{round}\!\left(q_{\min}-\frac{\alpha}{\Delta}\right),
q_{\min},q_{\max}
\right).
$$

量化与反量化为

$$
q=Q(x)=\operatorname{clip}\!\left(
\operatorname{round}\!\left(\frac{x}{\Delta}\right)+z,
q_{\min},q_{\max}
\right),
\qquad
\hat x=D(q)=\Delta(q-z).
$$

有些资料把乘法倍率写成 \(s=1/\Delta\)，即 \(q=\operatorname{round}(sx)\)。本篇固定用 \(\Delta\) 表示“一个整数等级对应的实数步长”，避免 \(s\) 与 \(1/s\) 混淆。

### 2.3 对称量化、误差上界和噪声模型

对称 \(b\)-bit 量化常取 \(Q_{\max}=2^{b-1}-1\)、zero-point 为 0。若

$$
a=\max_i|x_i|,\qquad \Delta=\frac{a}{Q_{\max}},
$$

则

$$
q_i=\operatorname{clip}\!\left(
\operatorname{round}\!\left(\frac{x_i}{\Delta}\right),
-Q_{\max},Q_{\max}
\right),
\qquad
\hat x_i=\Delta q_i.
$$

对称 INT8 通常使用 \([-127,127]\)，放弃 \(-128\) 以保持正负范围对称。若没有 clipping 且采用最近邻舍入，误差 \(\varepsilon_i=\hat x_i-x_i\) 满足

$$
|\varepsilon_i|\leq\frac{\Delta}{2}
=\frac{\max|x|}{2Q_{\max}}.
$$

在高分辨率假设下，可近似令

$$
\varepsilon\sim\mathcal U\!\left(-\frac{\Delta}{2},\frac{\Delta}{2}\right),
\qquad
\mathbb E[\varepsilon]=0,\qquad
\operatorname{Var}(\varepsilon)=\frac{\Delta^2}{12}.
$$

这解释了为什么离群值危险：少量极端值增大 \(a\) 和 \(\Delta\)，使大量普通值的量化噪声一起增大。clipping 则是在“极端值产生截断误差”和“普通值获得更小步长”之间取舍。

### 2.4 非对称量化

当分布明显不关于零对称时，使用

$$
\Delta=\frac{x_{\max}-x_{\min}}{q_{\max}-q_{\min}},
\qquad
z\approx q_{\min}-\frac{x_{\min}}{\Delta}.
$$

非对称量化能更充分利用整数区间，但整数 GEMM 需要处理 zero-point 补偿项，kernel 通常比对称方案复杂。

### 2.5 Per-tensor、Per-channel、Per-group 与 Per-token

| 粒度 | 数学形式 | 精度与工程权衡 |
| --- | --- | --- |
| per-tensor | 整个张量共用 \(\Delta,z\) | metadata 最少，最易受离群值影响。 |
| per-channel | 如 \(\hat W_{:,j}=\Delta_jQ_{:,j}\) | 每个通道适配自身范围，权重量化常用。 |
| per-group | 每 \(g\) 个权重共用 \(\Delta_g,z_g\) | GPTQ/AWQ 常见；group size 128 是常用折中。 |
| per-token | 每个 token 的激活使用 \(\Delta_n\) | 适应 token 间动态范围变化，动态 W8A8/FP8 常用。 |
| per-block | 小块共享 scale，scale 本身也可低精度 | FP4/MXFP4/NVFP4 常见，最依赖硬件和 kernel。 |

粒度越细，局部动态范围越小，量化误差通常越低；代价是更多 scale/zero-point、复杂的 packing 以及更高 kernel 实现成本。[TensorRT-LLM Q/DQ 文档](https://nvidia.github.io/TensorRT-LLM/reference/precision.html)给出了多种粒度的运行时定义。

### 2.6 线性层输出误差的完整分解

定义激活和权重量化误差

$$
E_X=\hat X-X,\qquad E_W=\hat W-W.
$$

于是

$$
\hat Y=(X+E_X)(W+E_W)
=XW+XE_W+E_XW+E_XE_W,
$$

总输出误差为

$$
\Delta Y=\hat Y-Y
=XE_W+E_XW+E_XE_W.
$$

由矩阵范数的次乘性可得

$$
\|\Delta Y\|_F
\leq
\|X\|_2\|E_W\|_F
+\|E_X\|_F\|W\|_2
+\|E_X\|_F\|E_W\|_2.
$$

这个式子给出了全文主线：

- W8A16 中 \(E_X=0\)，只有 \(XE_W\)；
- W8A8 同时包含权重误差、激活误差和二阶交叉项；
- GPTQ 和 AWQ 主要降低 \(XE_W\)；
- SmoothQuant 同时重新平衡 \(E_XW\) 与 \(XE_W\)；
- 误差不仅由 \(\|E_X\|,\|E_W\|\) 决定，还受激活/权重幅值、相关方向和条件数影响。

### 2.7 整数 W8A8 GEMM 的尺度恢复

对称量化下，若激活使用 per-tensor/per-token scale \(\Delta_{X,i}\)，权重第 \(j\) 个输出通道使用 \(\Delta_{W,j}\)，则

$$
\hat Y_{ij}
=\Delta_{X,i}\Delta_{W,j}
\sum_{k=1}^{d}Q^X_{ik}Q^W_{kj}.
$$

整数乘加一般在 INT32 中累加：

$$
A_{ij}=\sum_{k=1}^{d}Q^X_{ik}Q^W_{kj},
\qquad
\hat Y_{ij}=\Delta_{X,i}\Delta_{W,j}A_{ij}.
$$

非对称量化还会出现与 \(z_X,z_W\) 有关的行和、列和补偿项。低精度是否真正加速，取决于目标硬件是否有匹配的低精度 GEMM 与融合反量化 kernel。

## 3. INT8、FP8 与 FP4

### 3.1 三种格式的直观比较

| 格式 | 码本特点 | 主要控制的误差 | 常见定位 | 主要限制 |
| --- | --- | --- | --- | --- |
| INT8 | 经过 scale 后近似均匀 | 绝对误差 | W8A8、W8A16、成熟整数推理 | 对 scale 和 outlier 敏感。 |
| FP8 | 指数型非均匀码本 | 相对误差 | FP8 W8A8 或 weight-only | 格式与硬件支持强相关。 |
| FP4 | 极稀疏浮点码本，通常配 block scale | 相对误差与 block 重构误差 | Blackwell 等新硬件上的极低 bit 推理 | 必须明确 NVFP4/MXFP4，依赖细粒度 scale。 |

### 3.2 浮点格式的统一表达与误差推导

忽略特殊值与非规格化数，一个正规二进制浮点数可写为

$$
x=(-1)^\sigma 2^{e-B}
\left(1+\frac{f}{2^M}\right),
$$

其中 \(\sigma\) 是符号位，\(e\) 是指数编码，\(B\) 是 exponent bias，\(f\) 是尾数编码，\(M\) 是尾数位数。若总位数为 \(b\)，通常有

$$
1+E+M=b,
$$

其中 \(E\) 是指数位数。对满足 \(2^k\leq|x|<2^{k+1}\) 的正规数，该 binade 内相邻数间距约为

$$
\Delta_k=2^{k-M}.
$$

最近邻舍入满足

$$
|x-\hat x|\leq\frac{\Delta_k}{2}
=2^{k-M-1}.
$$

又因为 \(|x|\geq2^k\)，相对误差上界近似为

$$
\frac{|x-\hat x|}{|x|}
\lesssim 2^{-(M+1)}.
$$

因此尾数位决定相对精度，指数位决定动态范围。INT8 在固定 scale 下近似具有固定绝对分辨率，而浮点格式近似具有固定相对分辨率。

### 3.3 FP8：E4M3 与 E5M2

FP8 常见两类格式：

- E4M3：4 位指数、3 位尾数，精度更高、动态范围较小；
- E5M2：5 位指数、2 位尾数，动态范围更大、精度较低。

忽略格式对特殊值的具体编码，正规数的相对舍入误差上界近似为

$$
\text{E4M3}:\quad 2^{-(3+1)}=\frac1{16}=6.25\%,
$$

$$
\text{E5M2}:\quad 2^{-(2+1)}=\frac1{8}=12.5\%.
$$

实际 FP8 推理通常还使用 tensor、row、channel 或 block 外部尺度：

$$
\hat x_i=s_b\,q_i,\qquad q_i\in\mathcal F_{\mathrm{FP8}}.
$$

外部 scale 将张量映射到 FP8 的有效区间；E4M3/E5M2 自身提供的指数范围负责吸收块内不同量级。vLLM 文档列出了 E4M3/E5M2 的字段与硬件要求：[FP8 W8A8](https://docs.vllm.ai/en/stable/features/quantization/llm_compressor/fp8/)。

### 3.4 FP4 与 block scaling

FP4 不是唯一格式。常见值格式可以近似理解为 E2M1：1 位符号、2 位指数、1 位尾数。按上述正规数近似，其相对舍入误差上界为

$$
2^{-(1+1)}=\frac14=25\%.
$$

如此稀疏的码本很难用单个全局 scale 表示大范围张量，因此实际 FP4 通常采用 block scaling。对 block \(b\) 中的元素，

$$
\hat x_i=s_bq_i,\qquad
q_i\in\mathcal F_{\mathrm{FP4}},\quad i\in b.
$$

给定 \(s_b\) 时，最近码本点为

$$
q_i^*=\underset{q\in\mathcal F_{\mathrm{FP4}}}{\arg\min}
\;|x_i-s_bq|^2.
$$

若同时优化 scale 与码本索引，则 block 重构目标为

$$
\left(s_b^*,\{q_i^*\}_{i\in b}\right)
=\underset{s_b,\;q_i\in\mathcal F_{\mathrm{FP4}}}{\arg\min}
\sum_{i\in b}(x_i-s_bq_i)^2.
$$

block 越小，scale 越能适应局部分布，误差通常越低；代价是 scale metadata 增多、packing 更复杂。TensorRT 中 NVFP4 常使用 S1E2M1 值格式和 block scale，激活侧需要动态量化：[TensorRT quantized types](https://docs.nvidia.com/deeplearning/tensorrt/latest/inference-library/quantized-types-schemes.html)。

### 3.5 不能只写“FP4/FP8”

实际格式还会规定 subnormal、NaN/Inf、finite-only、scale 类型与 block 大小。NVFP4 与 MXFP4 不是可任意互换的 checkpoint 格式。部署记录至少要写清：

- 值格式：E4M3、E5M2、NVFP4 或 MXFP4；
- 权重/激活位宽：如 FP8 W8A8、NVFP4 W4A4、MXFP4A16；
- scale 粒度与类型：per-tensor、per-token、per-block，以及 FP16/FP32/E8M0 scale；
- 目标 GPU、runtime、kernel 和 checkpoint packing。

## 4. W8A16 与 W8A8

记号 WmAn 表示权重 \(m\) bit、激活 \(n\) bit；A16 通常是 FP16/BF16，而不是 INT16。还要继续说明数值类型，例如 INT8 W8A8 与 FP8 W8A8 是两种不同配方。

### 4.1 W8A16：只量化权重

W8A16 中激活保持高精度：

$$
\hat X=X,\qquad \hat W=W+E_W.
$$

于是

$$
\hat Y=X\hat W=XW+XE_W,
\qquad
\Delta Y=XE_W.
$$

误差上界为

$$
\|\Delta Y\|_F
\leq\|X\|_2\|E_W\|_F.
$$

即使两个权重通道的 \(\|E_W\|\) 相同，与大激活方向相乘的误差也更重要。GPTQ 使用 \(X^TX\) 的二阶信息，AWQ 使用各输入通道的激活幅值，都是在解决这个问题。

权重从 16 bit 降到 8 bit 时，忽略 metadata 的理论存储比为

$$
\frac{M_{\mathrm{FP16}}}{M_{\mathrm{8bit}}}
=\frac{16}{8}=2.
$$

W8A16 常在 matmul 内融合反量化权重，激活仍以 FP16/BF16 参与计算。它通常能稳定地减少模型权重显存和 decode 阶段的权重带宽，但算力吞吐提升取决于是否存在真正的 weight-only kernel。

### 4.2 W8A8：同时量化权重和激活

W8A8 中

$$
\hat X=X+E_X,\qquad
\hat W=W+E_W,
$$

因此

$$
\Delta Y=XE_W+E_XW+E_XE_W.
$$

相较 W8A16，它多出激活量化误差 \(E_XW\) 与交叉项 \(E_XE_W\)。LLM 激活中的 persistent outlier 会把 per-tensor activation scale 拉大，使 \(E_XW\) 成为主要风险。

对称 INT8 W8A8 的第 \(i,j\) 个输出可以写成

$$
\hat Y_{ij}
=\Delta_{X,i}\Delta_{W,j}
\sum_{k=1}^{d}Q^X_{ik}Q^W_{kj},
$$

整数内积由 INT8 乘法、INT32 累加完成，再乘尺度恢复高精度输出。与 W8A16 相比，W8A8 还减少激活带宽，并能直接利用 INT8/FP8 Tensor Core，因此高 batch 或 prefill 场景的算力收益通常更大，但精度控制也更困难。

### 4.3 两种配方的工程取舍

| 配方 | 主要误差 | 主要收益 | 常见选择 |
| --- | --- | --- | --- |
| W8A16 | \(XE_W\) | 权重显存和带宽 | 质量优先、低 batch decode、旧一些的 GPU。 |
| INT8 W8A8 | \(XE_W+E_XW+E_XE_W\) | 权重/激活带宽与 INT8 GEMM | 常配 SmoothQuant，适合高并发或 prefill。 |
| FP8 W8A8 | 同时量化 W/A，但码本为低精度浮点 | FP8 Tensor Core 与较宽动态范围 | Ada/Hopper/更新硬件，依 runtime 支持。 |
| FP8 W8A16 | 仅 FP8 权重，激活 FP16/BF16 | 权重带宽 | 不具备原生 FP8 W8A8 kernel 时的折中。 |

vLLM 当前 FP8 文档给出的常见 PTQ 配方是静态 per-channel 权重 scale 与动态 per-token 激活 scale；具体硬件支持须查当前版本文档。

## 5. PTQ 的通用部署流程

PTQ（Post-Training Quantization）在模型训练完成后执行，不做或极少做梯度更新；通常用少量校准样本获得统计量，再离线写出量化 checkpoint。它与 QAT（量化感知训练）相对。

```text
FP16/BF16 checkpoint
  -> 选择目标硬件、runtime、格式和配方
  -> 准备覆盖生产分布的校准集
  -> 收集统计量 / 算 scale / 执行 GPTQ、AWQ、SmoothQuant
  -> 写出量化权重、scale、zero-point、quantization config
  -> runtime 加载匹配 kernel
  -> 验收质量、显存、TTFT、prefill/decode 吞吐与稳定性
```

校准集不必等于训练集，却应覆盖生产输入的语言、长度、模板和模态。对 AWQ/SmoothQuant 而言，校准数据决定激活统计；偏离生产分布会直接影响结果。

## 6. GPTQ、AWQ 与 SmoothQuant

### 6.1 GPTQ：二阶信息驱动的权重量化

GPTQ 可理解为 Optimal Brain Surgeon 思想在大模型权重量化上的高效实现。它不独立地 round 每个权重，而是最小化该层在校准输入上的输出重构误差。[GPTQ 原论文](https://arxiv.org/abs/2210.17323)

#### 输出重构目标

对 \(X\in\mathbb R^{N\times d}\)、\(W\in\mathbb R^{d\times m}\)，目标为

$$
\hat W^*
=\underset{\hat W\in\mathcal Q}{\arg\min}
\|XW-X\hat W\|_F^2.
$$

令 \(\Delta W=\hat W-W\)，则

$$
\|X\Delta W\|_F^2
=\operatorname{tr}\!\left(
\Delta W^T X^TX\Delta W
\right).
$$

定义激活 Gram/Hessian 近似

$$
H=X^TX,
$$

便得到

$$
L(\Delta W)
=\operatorname{tr}(\Delta W^TH\Delta W).
$$

所以 GPTQ 最小化的不是普通权重距离 \(\|W-\hat W\|_F^2\)，而是由校准激活协方差加权的输出误差。校准数据经常激发的方向具有更高代价。

#### 单个权重的最优补偿推导

考虑某个输出通道的权重向量 \(w\in\mathbb R^d\)，要把第 \(i\) 个权重量化为 \(q_i\)。令整体修正

$$
\delta=w'-w,\qquad
e_i^T\delta=d_i,\qquad
d_i=q_i-w_i.
$$

在约束下最小化二阶误差：

$$
\min_\delta\frac12\delta^TH\delta
\quad\text{s.t.}\quad
e_i^T\delta=d_i.
$$

拉格朗日函数为

$$
\mathcal J(\delta,\lambda)
=\frac12\delta^TH\delta
+\lambda(e_i^T\delta-d_i).
$$

一阶条件给出

$$
\nabla_\delta\mathcal J
=H\delta+\lambda e_i=0,
\qquad
\delta=-\lambda H^{-1}e_i.
$$

代入约束：

$$
-\lambda e_i^TH^{-1}e_i=d_i,
\qquad
\lambda=-\frac{d_i}{(H^{-1})_{ii}}.
$$

因此最优修正为

$$
\delta^*
=\frac{q_i-w_i}{(H^{-1})_{ii}}H^{-1}e_i,
$$

对任意尚未量化的第 \(j\) 个权重：

$$
w'_j
=w_j+(q_i-w_i)
\frac{(H^{-1})_{ji}}{(H^{-1})_{ii}}.
$$

对应的最小损失增加为

$$
\Delta L_i
=\frac{(q_i-w_i)^2}
{2(H^{-1})_{ii}}.
$$

这揭示了 GPTQ 的核心：舍入误差越大，损失越大；\((H^{-1})_{ii}\) 越小，该位置越敏感；一个权重被固定到码本后，其余权重沿 \(H^{-1}\) 给出的相关方向补偿。

实际 GPTQ 使用分块、Cholesky 分解、group-wise quantization 和 activation order，并加入阻尼

$$
H_\lambda=X^TX+\lambda I
$$

避免 Hessian 奇异或病态。典型产物是 W4A16/W3A16，也可用于 W8A16；速度取决于 runtime 是否支持相同 group size、zero-point 和 packing。

### 6.2 AWQ：由激活识别并保护重要权重

AWQ 不显式使用完整 Hessian，而是以输入通道激活幅度判断对应权重的重要性。[AWQ 原论文](https://arxiv.org/abs/2306.00978)

![AWQ 的激活感知缩放示意图](https://github.com/mit-han-lab/llm-awq/raw/main/figures/example_vis.jpg)

图片来源：[MIT HAN Lab / llm-awq](https://github.com/mit-han-lab/llm-awq)。

#### 为什么仅看权重大小不够

线性层可按输入通道展开：

$$
Y=XW=\sum_{j=1}^{d}X_{:,j}W_{j,:}.
$$

若第 \(j\) 个权重输入通道的量化误差为 \(E_{j,:}\)，其输出误差贡献为

$$
\Delta Y_j=X_{:,j}E_{j,:},
$$

且

$$
\|\Delta Y_j\|_F
\leq
\|X_{:,j}\|_2\|E_{j,:}\|_2.
$$

因此权重本身即使不大，只要对应激活通道很大，量化误差仍会被显著放大。AWQ 常使用

$$
a_j=\frac1N\sum_{n=1}^{N}|X_{n,j}|
\quad\text{或}\quad
a_j=\max_n|X_{n,j}|
$$

作为通道重要性统计。

#### 等价缩放与误差降低

令

$$
S=\operatorname{diag}(s_1,\ldots,s_d),\qquad s_j>0.
$$

有严格恒等式

$$
XW=(XS^{-1})(SW).
$$

AWQ 量化放大后的权重 \(SW\)，推理输出为

$$
\hat Y=XS^{-1}Q(SW).
$$

因此

$$
\Delta Y
=XS^{-1}[Q(SW)-SW].
$$

若第 \(j\) 行缩放权重的量化误差为

$$
\varepsilon_{j,:}
=Q(s_jW_{j,:})-s_jW_{j,:},
$$

恢复到原坐标后的有效权重误差为 \(\varepsilon_{j,:}/s_j\)，对应输出误差为

$$
\Delta Y_j
=X_{:,j}\frac{\varepsilon_{j,:}}{s_j}.
$$

当一个显著元素的放大尚未改变共享 group 的主导范围时，\(\varepsilon_{j,:}\) 的绝对舍入尺度近似不变，增大 \(s_j\) 就能降低显著通道的有效相对误差。但 \(s_j\) 过大可能扩大 group scale、伤害其他权重，因此必须搜索平衡点。

AWQ 的优化目标可写为

$$
S^*
=\underset{S}{\arg\min}
\left\|XW-XS^{-1}Q(SW)\right\|_F^2.
$$

常见快速搜索族为

$$
a_j=\frac1N\sum_n|X_{n,j}|,\qquad
b_j=\max_k|W_{j,k}|,
$$

$$
s_j(\alpha)
=\frac{a_j^\alpha}{b_j^{\,1-\alpha}},
\qquad
\alpha\in[0,1],
$$

再用小网格选取输出 MSE 最小的 \(\alpha\)。实现还常联合搜索 clipping，以避免少数权重异常值拉大 group scale。激活只在离线阶段用于统计与搜索，最终 checkpoint 仍是统一低 bit 的 W4A16/W8A16 weight-only layout。

### 6.3 SmoothQuant：把激活异常值迁移到权重

SmoothQuant 面向 INT8 W8A8。LLM 权重通常容易 INT8 量化，激活却常有少数持续的大通道；per-tensor activation scale 会被这些 outlier 拉大。[SmoothQuant 原论文](https://arxiv.org/abs/2211.10438)

![SmoothQuant 的激活离群值平滑直觉图](https://github.com/mit-han-lab/smoothquant/raw/main/figures/intuition.png)

图片来源：[MIT HAN Lab / SmoothQuant](https://github.com/mit-han-lab/smoothquant)。

#### 等价变换与量化误差

仍令 \(S=\operatorname{diag}(s_1,\ldots,s_d)\)，定义

$$
X'=XS^{-1},\qquad W'=SW.
$$

在量化前严格有

$$
XW=X'W'=(XS^{-1})(SW).
$$

量化后

$$
\hat Y=Q_A(X')Q_W(W').
$$

若 \(E_X=Q_A(X')-X'\)、\(E_W=Q_W(W')-W'\)，则

$$
\hat Y-Y
=X'E_W+E_XW'+E_XE_W.
$$

选择 \(S\) 的目标是让 \(X'\) 更容易量化，同时不让 \(W'\) 变得过难量化。

#### 缩放系数推导

定义第 \(j\) 个激活和权重输入通道的最大幅值：

$$
A_j=\max_n|X_{n,j}|,\qquad
B_j=\max_k|W_{j,k}|.
$$

SmoothQuant 使用

$$
s_j=\frac{A_j^\alpha}{B_j^{\,1-\alpha}},
\qquad 0\leq\alpha\leq1.
$$

变换后的激活范围为

$$
A'_j=\frac{A_j}{s_j}
=A_j^{1-\alpha}B_j^{1-\alpha}
=(A_jB_j)^{1-\alpha},
$$

权重范围为

$$
B'_j=s_jB_j
=A_j^\alpha B_j^\alpha
=(A_jB_j)^\alpha.
$$

因此

$$
A'_jB'_j=A_jB_j.
$$

变换不改变该通道激活与权重幅值的乘积，只是在两侧重新分配动态范围。当 \(\alpha=1/2\) 时，

$$
A'_j=B'_j=\sqrt{A_jB_j},
$$

在对数尺度上达到对称平衡。增大 \(\alpha\) 会更强地压低激活范围，同时让静态权重承担更多范围；权重可在离线阶段使用 per-channel scale 和 clipping 处理。

例如 \(A_j=100,B_j=0.01,\alpha=1/2\)，则

$$
s_j=\frac{\sqrt{100}}{\sqrt{0.01}}=100,
$$

$$
A'_j=\frac{100}{100}=1,\qquad
B'_j=100\times0.01=1.
$$

原来的 \(100\times0.01\) 被等价变成 \(1\times1\)，输出不变但两侧动态范围更平滑。这也是 SmoothQuant 名称的来源。

#### 三种方法的数学定位

| 方法 | 常见配置 | 激活量化 | 核心依据 | 主要解决的问题 |
| --- | --- | --- | --- | --- |
| GPTQ | W4A16、W3A16、W8A16 | 否 | Hessian/Gram 二阶补偿 | 一个权重舍入后，如何最优修正剩余权重。 |
| AWQ | W4A16、W8A16 | 否 | 激活加权的通道敏感性 | 保护高激活通道对应的显著权重。 |
| SmoothQuant | INT8 W8A8 | 是 | 激活-权重等价缩放 | 将动态激活 outlier 迁移到静态权重。 |

## 7. 存储、带宽与计算收益

设模型共有 \(P\) 个参数。忽略 metadata 时：

$$
M_{\mathrm{FP16}}=2P\ \text{bytes},
\qquad
M_{\mathrm{8bit}}=P\ \text{bytes},
\qquad
M_{\mathrm{4bit}}=\frac{P}{2}\ \text{bytes}.
$$

所以相对 FP16 的理论压缩比分别为

$$
\frac{M_{\mathrm{FP16}}}{M_{\mathrm{8bit}}}=2,
\qquad
\frac{M_{\mathrm{FP16}}}{M_{\mathrm{4bit}}}=4.
$$

实际 checkpoint 还包含

$$
M_{\mathrm{total}}
=M_{\mathrm{codes}}
+M_{\mathrm{scale}}
+M_{\mathrm{zero\ point}}
+M_{\mathrm{metadata}}.
$$

若每 \(g\) 个 \(b\)-bit 权重共享一个 \(b_s\)-bit scale，不保存 zero-point，则平均有效位宽为

$$
b_{\mathrm{eff}}=b+\frac{b_s}{g}.
$$

例如 INT4、group size \(g=128\)、每组一个 FP16 scale：

$$
b_{\mathrm{eff}}
=4+\frac{16}{128}
=4.125\ \text{bit/weight},
$$

相对 FP16 的实际权重压缩比约为

$$
\frac{16}{4.125}\approx3.88,
$$

而不是严格 4 倍。若还存 zero-point、双层 scale、padding 或未量化层，压缩比会进一步降低。

显存收益也不等于总显存收益。端到端显存可粗略拆为

$$
M_{\mathrm{GPU}}
=M_{\mathrm{weights}}
+M_{\mathrm{KV\ cache}}
+M_{\mathrm{activations}}
+M_{\mathrm{workspace}}
+M_{\mathrm{runtime}}.
$$

weight-only 量化主要减小第一项；长上下文或高并发时，KV cache 可能成为主导。W8A8/FP8/FP4 是否增加计算吞吐，还取决于低精度 Tensor Core、累加类型、融合反量化、batch size 与算子覆盖率，不能仅由位宽比直接推导。

## 8. 方法选择速查

### 8.1 核心方法横向比较

| 方法/格式 | 常见配置 | 是否量化激活 | 核心数学依据 | 主要解决的问题 |
| --- | --- | --- | --- | --- |
| 普通 INT8 PTQ | W8A8/W8A16 | 视配方而定 | Min-max、MSE clipping、校准 scale | 基础均匀整数化。 |
| GPTQ | W4A16/W3A16/W8A16 | 否 | Hessian 二阶误差补偿 | 权重舍入后最优修正其余权重。 |
| AWQ | W4A16/W8A16 | 否 | 激活加权通道敏感性 | 保护高激活通道对应的显著权重。 |
| SmoothQuant | INT8 W8A8 | 是 | 激活-权重等价缩放 | 将激活 outlier 迁移到静态权重。 |
| FP8 | FP8 W8A8/W8A16 | 视配方而定 | 指数码本与外部 scale | 在动态范围和尾数精度之间取舍。 |
| FP4 | W4A4/FP4A16/混合精度 | 视配方而定 | 极低精度浮点与 block scale | 最大程度减少权重/激活带宽。 |

三种经典算法可压缩成三个目标：

$$
\text{GPTQ:}\qquad
\min_{\hat W\in\mathcal Q}
\|XW-X\hat W\|_F^2,
$$

$$
\text{AWQ:}\qquad
\min_S
\|XW-XS^{-1}Q(SW)\|_F^2,
$$

$$
\text{SmoothQuant:}\qquad
\min_S
\|XW-Q_A(XS^{-1})Q_W(SW)\|_F^2.
$$

### 8.2 按部署目标选择

| 目标 | 优先考虑 | 常见配方 | 原因 |
| --- | --- | --- | --- |
| 单卡装下更大模型、低并发 decode | AWQ 或 GPTQ | W4A16 | 主要压缩权重，激活保留高精度。 |
| 4 bit 下追求离线量化精度基线 | GPTQ | W4A16 / W3A16 | 二阶近似与误差补偿控制逐层输出误差。 |
| 低成本、激活统计驱动的 4 bit 方案 | AWQ | W4A16 | 显著通道缩放保护量化敏感部分。 |
| 高并发、希望 GEMM 真正 INT8 加速 | SmoothQuant | INT8 W8A8 | 处理 activation outlier，使 W 和 A 都可 INT8。 |
| Ada/Hopper 等 FP8 GPU | FP8 PTQ | FP8 W8A8 或 W8A16 | 利用硬件 FP8 kernel。 |
| Blackwell 与新软件栈 | NVFP4/MXFP4 PTQ | FP4 W4A4 或 FP4A16 | 极低 bit 与 block scaling，先确认格式和 kernel。 |

## 9. PTQ 部署建议

### 9.1 从目标 runtime 反推量化产物

PTQ 不是“生成一个低 bit 文件”就结束。量化算法、checkpoint 布局和推理 kernel 必须形成闭环：

$$
\text{FP16/BF16 model}
\xrightarrow[\text{calibration}]{\text{PTQ algorithm}}
\{\text{codes, scales, zero-points, config}\}
\xrightarrow{\text{packing/export}}
\text{runtime kernel}.
$$

常见对应关系如下：

| PTQ 路线 | 常见产物 | 运行时要求 |
| --- | --- | --- |
| GPTQ | INT4/INT3 W4A16/W3A16，group-wise | 支持相同 group size、act-order、zero-point 与 GPTQ packing 的 weight-only kernel。 |
| AWQ | INT4 W4A16，group-wise | 支持 AWQ scale 融合、packing 与 groupwise GEMM。 |
| SmoothQuant | INT8 W8A8 | 支持平滑后的权重、activation scale 策略和 INT8 GEMM。 |
| FP8 PTQ | FP8 W8A8/W8A16 | 支持指定 E4M3/E5M2、per-token/per-channel 或 block scale 的硬件与 kernel。 |
| FP4 PTQ | NVFP4/MXFP4 W4A4 或 W4A16 | 支持指定 FP4 格式、block size、scale 类型与 packing；通常需要更新的 GPU。 |

### 9.2 一条完整的上线流程

1. **建立高精度基线**：固定模型、tokenizer、prompt template、数据版本，记录质量、显存、TTFT、prefill/decode 吞吐。
2. **选择校准集**：覆盖生产语言、长度、领域、指令格式和模态；保留独立验证集，避免只对校准样本变好。
3. **确定量化粒度**：明确 bit、对称/非对称、per-channel/per-group/per-token、group/block size、是否量化 lm_head、embedding 和 KV cache。
4. **执行 PTQ**：收集激活统计或 Hessian，运行 GPTQ/AWQ/SmoothQuant/RTN，必要时搜索 clipping 与 \(\alpha\)。
5. **导出并校验格式**：检查 quantization config、scale/zero-point shape、packing、未量化层和 tokenizer 文件。
6. **在目标 runtime 加载**：不能只在 fake quantization 中验收；必须用真实 kernel 跑真实 checkpoint。
7. **质量回归**：同时测 perplexity、业务任务、长上下文、目标语言、代码/数学、工具调用和安全行为。
8. **性能回归**：按多个 batch 与上下文长度测显存、TTFT、TPOT、tok/s、P50/P95、并发稳定性。
9. **灰度与回退**：保留高精度模型、校准配置、导出命令和版本锁定，出现质量漂移时可定位和回退。

### 9.3 具体工程判断

1. **从硬件和 runtime 反推**。旧 GPU 或质量优先：先评估 W8A16；显存仍不足再上 GPTQ/AWQ W4A16。Ada/Hopper：同时评估 FP8 W8A8 与 SmoothQuant INT8 W8A8。Blackwell：再考虑 NVFP4/MXFP4。
2. **先确认 checkpoint 与 kernel 是同一套协议**。检查 quantization config、group size、对称/非对称、zero-point 与 packing；“都是 4 bit”不代表可以互相加载。
3. **把 runtime 支持矩阵当成约束条件**。TensorRT-LLM 支持 FP8/FP4 以及 GPTQ/AWQ 的多种配方，支持度依模型和 GPU 而变。[TensorRT-LLM Quantization](https://nvidia.github.io/TensorRT-LLM/latest/features/quantization.html)  
   vLLM 支持 GPTQ、AWQ、FP8、INT8 等后端，但不同后端硬件覆盖不同。[vLLM Quantization](https://docs.vllm.ai/en/stable/features/quantization/)
4. **量化后仍须评测**。除 perplexity 外，跑真实业务任务、长上下文、目标语言、工具调用；性能上分开记录 prefill、decode、TTFT、并发 P95。

### 9.4 一个可复用的验收清单

- 质量：业务指标与 FP16/BF16 baseline 的差异是否在可接受范围？
- 性能：各 batch、各上下文长度下的 TTFT、tok/s、P50/P95 是否改善？
- 内存：权重、KV cache、workspace、activation 与 quant metadata 各占多少？
- 兼容：模型格式、GPU 架构、CUDA/runtime、量化 kernel 是否已验证？
- 回退：是否保留 baseline、校准集版本、量化参数和可复现实验命令？

## 10. 一页结论

- INT8/FP8/FP4 是格式；W8A16/W8A8 是权重-激活配方；GPTQ/AWQ/SmoothQuant 是 PTQ 方法。
- 想先省显存且风险较低：从 W8A16 开始；显存仍不够，再用 GPTQ/AWQ 的 W4A16。
- 想让 matmul 获得低精度吞吐：在受支持硬件上评估 INT8/FP8 W8A8；INT8 优先尝试 SmoothQuant。
- 想用 FP4：先说明 NVFP4 还是 MXFP4，并确认 GPU、runtime、kernel 完全匹配。
- 部署效果由模型、校准集、算法、checkpoint 布局、kernel 与真实负载共同决定，任何论文速度数字都不能替代本地压测。

## 11. 参考资料

1. Frantar et al., [GPTQ: Accurate Post-Training Quantization for Generative Pre-trained Transformers](https://arxiv.org/abs/2210.17323), ICLR 2023。
2. Lin et al., [AWQ: Activation-aware Weight Quantization for LLM Compression and Acceleration](https://arxiv.org/abs/2306.00978), MLSys 2024。
3. Xiao et al., [SmoothQuant: Accurate and Efficient Post-Training Quantization for Large Language Models](https://arxiv.org/abs/2211.10438), ICML 2023。
4. NVIDIA, [TensorRT-LLM Numerical Precision](https://nvidia.github.io/TensorRT-LLM/reference/precision.html) 与 [TensorRT-LLM Quantization](https://nvidia.github.io/TensorRT-LLM/latest/features/quantization.html)。
5. vLLM, [Quantization](https://docs.vllm.ai/en/stable/features/quantization/) 与 [FP8 W8A8](https://docs.vllm.ai/en/stable/features/quantization/llm_compressor/fp8/)。
