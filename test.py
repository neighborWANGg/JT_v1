import os
import json
from pathlib import Path

import pandas as pd
import numpy as np
from tqdm import tqdm
from FlagEmbedding import BGEM3FlagModel


# ============================================================
# 1. 路径配置
# ============================================================

ROOT = Path(__file__).resolve().parent
MODEL_PATH = str(ROOT / "model" / "bge-m3")
EXCEL_PATH = str(ROOT / "微信聊天记录测试数据.xlsx")
OUTPUT_PATH = str(ROOT / "embeddings.json")


# ============================================================
# 2. BGE-M3配置
# ============================================================

BATCH_SIZE = 8

MAX_LENGTH = 512

USE_FP16 = True


# ============================================================
# 3. 检查路径
# ============================================================

if not os.path.exists(MODEL_PATH):
    raise FileNotFoundError(
        f"BGE-M3模型不存在：\n{MODEL_PATH}"
    )

if not os.path.exists(EXCEL_PATH):
    raise FileNotFoundError(
        f"Excel不存在：\n{EXCEL_PATH}"
    )


os.makedirs(
    os.path.dirname(OUTPUT_PATH),
    exist_ok=True
)


# ============================================================
# 4. 加载BGE-M3
# ============================================================

print("=" * 60)
print("正在加载 BGE-M3...")
print("=" * 60)

model = BGEM3FlagModel(
    MODEL_PATH,
    use_fp16=USE_FP16
)

print("BGE-M3加载完成")


# ============================================================
# 5. 读取Excel
# ============================================================

print()
print("=" * 60)
print("正在读取Excel...")
print("=" * 60)

df = pd.read_excel(
    EXCEL_PATH,
    sheet_name="聊天记录"
)

print(f"总行数：{len(df)}")

print("字段：")
print(list(df.columns))


# ============================================================
# 6. 检查聊天内容字段
# ============================================================

CONTENT_COLUMN = "聊天内容"

if CONTENT_COLUMN not in df.columns:
    raise ValueError(
        f"Excel中没有找到字段：{CONTENT_COLUMN}\n"
        f"实际字段：{list(df.columns)}"
    )


# ============================================================
# 7. 清洗数据
# ============================================================

df[CONTENT_COLUMN] = (
    df[CONTENT_COLUMN]
    .fillna("")
    .astype(str)
)

# 删除空聊天
df = df[
    df[CONTENT_COLUMN].str.strip() != ""
].copy()

df.reset_index(drop=True, inplace=True)

print(f"有效聊天记录：{len(df)}")


# ============================================================
# 8. 准备文本
# ============================================================

texts = df[CONTENT_COLUMN].tolist()


# ============================================================
# 9. BGE-M3批量生成向量
# ============================================================

print()
print("=" * 60)
print("开始生成向量...")
print("=" * 60)

embeddings = []

for start in tqdm(
    range(0, len(texts), BATCH_SIZE),
    desc="BGE-M3"
):

    batch_texts = texts[
        start:start + BATCH_SIZE
    ]

    output = model.encode(
        batch_texts,
        batch_size=BATCH_SIZE,
        max_length=MAX_LENGTH,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False
    )

    dense_vectors = output["dense_vecs"]

    for vector in dense_vectors:

        vector = np.asarray(
            vector,
            dtype=np.float32
        )

        embeddings.append(
            vector.tolist()
        )


# ============================================================
# 10. 检查向量
# ============================================================

print()
print("=" * 60)
print("向量生成完成")
print("=" * 60)

print(
    "向量数量：",
    len(embeddings)
)

if embeddings:

    print(
        "向量维度：",
        len(embeddings[0])
    )


# ============================================================
# 11. 保存JSON
# ============================================================

print()
print("正在保存JSON...")


records = []

for i, row in df.iterrows():

    record = {
        "message_id": str(i + 1),

        "序号": str(row.get("序号", "")),

        "时间": str(row.get("时间", "")),

        "群ID": str(row.get("群ID", "")),

        "微信号": str(row.get("微信号", "")),

        "聊天内容": str(row.get("聊天内容", "")),

        "IP登录地址": str(
            row.get("IP登录地址", "")
        ),

        "embedding": embeddings[i],

        "model": "BAAI/bge-m3",

        "dimension": len(embeddings[i])
    }

    records.append(record)


with open(
    OUTPUT_PATH,
    "w",
    encoding="utf-8"
) as f:

    json.dump(
        records,
        f,
        ensure_ascii=False,
        indent=2
    )


print()
print("=" * 60)
print("完成！")
print("=" * 60)

print(
    f"JSON文件：\n{OUTPUT_PATH}"
)

print(
    f"记录数量：{len(records)}"
)

if records:

    print(
        f"向量维度：{len(records[0]['embedding'])}"
    )