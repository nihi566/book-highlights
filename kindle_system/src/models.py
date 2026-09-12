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
