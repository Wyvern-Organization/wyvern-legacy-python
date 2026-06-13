import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-with-at-least-32-chars")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test-db.local/wyvern")
