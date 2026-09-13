import os
import shutil
import stat
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(__file__).parent / '.data' / 'test.db'}")
os.environ.setdefault("DATA_DIR", str(Path(__file__).parent / ".data"))
os.environ.setdefault("EMBEDDING_BACKEND", "hash")
os.environ.setdefault("LLM_ENABLED", "0")

from app.db import Base, engine
from app.config import settings


def remove_readonly(function, path, _error):
    os.chmod(path, stat.S_IWRITE)
    function(path)


@pytest.fixture(autouse=True)
def clean_database():
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.drop_all(engine)
    yield
    Base.metadata.drop_all(engine)
    engine.dispose()
    if settings.data_dir.exists():
        shutil.rmtree(settings.data_dir, onexc=remove_readonly)
