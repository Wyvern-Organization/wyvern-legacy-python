import os


os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-with-at-least-32-chars")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test-db.local/wyvern")
