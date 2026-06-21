import os
from sqlmodel import create_engine, SQLModel, Session
from src.models import BookMapping, PriceHistory

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DB_DIR, "kindle_monitor.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False
)

def init_db_orm() -> None:
    """データベースのテーブルが存在しない場合に作成する。"""
    os.makedirs(DB_DIR, exist_ok=True)
    SQLModel.metadata.create_all(engine)

def get_session() -> Session:
    """FastAPIの依存関係やスクリプト中で使うセッションを生成して返す。"""
    return Session(engine)
