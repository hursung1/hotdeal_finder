import uuid
import datetime
from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from database import Base

def generate_uuid():
    return uuid.uuid4().hex

class Keyword(Base):
    __tablename__ = "keywords"

    id = Column(String, primary_key=True, default=generate_uuid, index=True)
    name = Column(String, nullable=False, index=True)
    
    # 콤마로 구분된 문자열 (예: "에어팟프로,에팟프")
    aliases = Column(String, nullable=True) 
    exclude_words = Column(String, nullable=True)
    target_price = Column(Integer, nullable=True)
    
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    is_active = Column(Boolean, default=True)
    
    # 쿼리 성능 최적화를 위한 최저가 캐싱 필드
    current_lowest_price = Column(Integer, nullable=True)
    lowest_price_url = Column(String, nullable=True)

    # 1:N 관계 설정 (수집 이력 접근용)
    deal_histories = relationship("DealHistory", back_populates="keyword", cascade="all, delete-orphan")


class DealHistory(Base):
    __tablename__ = "deal_histories"

    id = Column(String, primary_key=True, default=generate_uuid, index=True)
    keyword_id = Column(String, ForeignKey("keywords.id"), nullable=False, index=True)
    
    platform = Column(String, nullable=False) # 게시글 출처 (예: 루리웹, 펨코)
    title = Column(String, nullable=False)
    url = Column(String, nullable=False, unique=True, index=True)
    extracted_price = Column(Integer, nullable=False)
    
    is_alert_sent = Column(Boolean, default=False)
    collected_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    # N:1 관계 설정
    keyword = relationship("Keyword", back_populates="deal_histories")


class HotdealPriceRecord(Base):
    __tablename__ = "hotdeal_price_records"

    id = Column(String, primary_key=True, default=generate_uuid, index=True)
    platform = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    url = Column(String, nullable=False, unique=True, index=True)
    listed_price = Column(Integer, nullable=False, index=True)
    posted_at = Column(DateTime, nullable=False, index=True)
    crawled_page = Column(Integer, nullable=True)
    collected_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), nullable=False)
