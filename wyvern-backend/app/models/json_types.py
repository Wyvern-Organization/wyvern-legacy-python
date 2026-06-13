from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB


# Use JSONB on Postgres while keeping SQLite-backed tests portable.
json_value_type = JSON().with_variant(JSONB, "postgresql")
