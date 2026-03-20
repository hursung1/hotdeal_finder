import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# SQLite DB를 기본으로 사용 (로컬 테스트용)
# 배포 시 환경 변수 DATABASE_URL을 PostgreSQL 접속 정보로 세팅하면 바로 교체됩니다.
SQLALCHEMY_DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./hotdeal.db")

# SQLite는 기본적으로 다른 스레드에서의 접근을 막으므로 check_same_thread=False 옵션이 필요합니다.
connect_args = {"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args=connect_args
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
