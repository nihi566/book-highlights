from typing import Optional
from sqlmodel import Field, SQLModel

class BookMapping(SQLModel, table=True):
    __tablename__ = "book_mappings"

    id: Optional[int] = Field(default=None, primary_key=True)
    # sample_asin は主キーではなくなったが、非NULL値の一意性（旧スキーマでは
    # PRIMARY KEY により保証されていた）は維持する。SQLite の UNIQUE 制約は
    # NULL 同士を別値として扱うため、bookmeter 由来行（sample_asin=None）は
    # 複数存在しても制約に抵触しない。
    sample_asin: Optional[str] = Field(default=None, index=True, unique=True)
    paid_asin: Optional[str] = Field(default=None, index=True)
    title: Optional[str] = Field(default=None)
    created_at: Optional[str] = Field(default=None)
    is_purchased: int = Field(default=0)
    is_wanted: int = Field(default=0)
    source: str = Field(default="kindle_sample")
    from_kindle_sample: bool = Field(default=False)
    from_bookmeter: bool = Field(default=False)


class PriceHistory(SQLModel, table=True):
    __tablename__ = "price_history"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    paid_asin: str = Field(index=True)
    sell_price: Optional[int] = Field(default=None)
    point_value: int = Field(default=0)
    actual_price: Optional[int] = Field(default=None)
    campaign_text: str = Field(default="")
    timestamp: str = Field()
    is_unlimited: int = Field(default=0)


class BookMark(SQLModel, table=True):
    """
    公開ページでブラウザに保存したタグ・★評価・種別を `run.py import-marks` で取り込んだもの。

    ページの状態をここへ集めて「見た」作品と評価を蓄積し、ローカル LLM のおすすめ
    （src/recommender.py）と、次回生成するページの初期状態（report.py）に使う。
    book_mappings から本が消えても読書記録として残せるよう、paid_asin を主キーにした
    別テーブルにしてタイトルも控える（既存テーブルに列を足さないのでマイグレーションは不要。
    create_all / ensure_book_marks_table() が新規作成する）。

    tag: "seen"（見た）/ "wanted" / "unwanted" / "purchased" / ""（なし）
    rating: tag が "seen" のときの★1〜5（未評価は None）
    kind: タイトルからの自動判定（src/book_kind.py）を上書きする "manga" / "book"（上書きなしは None）
    """
    __tablename__ = "book_marks"

    paid_asin: str = Field(primary_key=True)
    title: Optional[str] = Field(default=None)
    tag: str = Field(default="")
    rating: Optional[int] = Field(default=None)
    kind: Optional[str] = Field(default=None)
    updated_at: str = Field()
