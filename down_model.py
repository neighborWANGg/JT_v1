from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT / "model" / "bge-m3"

snapshot_download(
    repo_id="BAAI/bge-m3",
    local_dir=MODEL_PATH,
    local_dir_use_symlinks=False
)

print("模型下载完成：")
print(MODEL_PATH)