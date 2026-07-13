from sqlalchemy import create_engine, BigInteger, Integer
from sqlalchemy.orm import declarative_base, sessionmaker
from app.core.config import settings

# Dialect-aware primary-key type. Postgres autoincrements BIGINT identity fine,
# but SQLite (tests / local dev) only autoincrements INTEGER PRIMARY KEY — a
# BIGINT PK stays NULL on insert. This variant renders BIGINT on Postgres and
# INTEGER on SQLite, so autoincrement works on both.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")

# Setup database url configuration
DATABASE_URL = settings.DATABASE_URL

# For SQLite we need to allow access across multiple threads
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    echo=settings.DEBUG
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Dependency to get db session in API endpoints
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
