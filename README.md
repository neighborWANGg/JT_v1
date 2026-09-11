# JT_V1

用本地 **BGE-M3** 给微信聊天记录生成向量。

## 不要提交到 Git 的内容

- `venv/`：虚拟环境
- `model/`：BGE-M3 权重（约 4GB+），每人本机下载
- `embeddings.json`：跑出来的结果

## 团队怎么跑

1. 克隆仓库，进入项目目录。
2. 用 Python 3.10 建环境并安装依赖：

```bash
conda create -n py10 python=3.10 -y
conda activate py10
pip install -r requirements.txt
```

3. **安装 GPU 版 PyTorch**（Windows + NVIDIA）。不要装 `torch==...+cpu`。例如 CUDA 12.6：

```bash
pip uninstall -y torch torchvision
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
```

装完后检查：

```bash
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

应看到类似 `2.x.x+cu126 True`。

4. 下载模型到 `model/bge-m3`：

```bash
python down_model.py
```

5. 把聊天 Excel 放在项目根目录（默认文件名：`微信聊天记录测试数据.xlsx`，工作表 `聊天记录`，需有列 `聊天内容`），然后：

```bash
python test.py
```

向量会写到 `embeddings.json`。
