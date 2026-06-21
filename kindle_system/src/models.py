from typing import Optional
from sqlmodel import Field, SQLModel

class BookMapping(SQLModel, table=True):
    __tablename__ = "book_mappings"
    
    sample_asin: str = Field(primary_key=True)
    paid_asin: Optional[str] = Field(default=None, index=True)
    title: Optional[str] = Field(default=None)
    created_at: Optional[str] = Field(default=None)
    is_purchased: int = Field(default=0)
    is_wanted: int = Field(default=0)


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
