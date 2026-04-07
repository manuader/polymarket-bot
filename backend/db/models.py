from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Interval,
    String,
    Text,
    CheckConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


def utcnow():
    return datetime.now(timezone.utc)


# ── Markets ──────────────────────────────────────────────────────────

class Market(Base):
    __tablename__ = "markets"

    condition_id = Column(String, primary_key=True)
    question = Column(Text, nullable=False)
    description = Column(Text)
    category = Column(String)
    end_date = Column(DateTime(timezone=True))
    slug = Column(String)
    tags = Column(ARRAY(String))
    volume = Column(Float, default=0)
    liquidity = Column(Float, default=0)
    active = Column(Boolean, default=True)
    neg_risk = Column(Boolean, default=False)
    clob_token_ids = Column(ARRAY(String))  # YES and NO token IDs
    outcome_prices = Column(ARRAY(Float))   # current prices [yes_price, no_price]
    image = Column(String)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    trades = relationship("Trade", back_populates="market")
    signals = relationship("Signal", back_populates="market")
    volume_snapshots = relationship("MarketVolumeSnapshot", back_populates="market")
    paper_trades = relationship("PaperTrade", back_populates="market")


# ── Trades ───────────────────────────────────────────────────────────

class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(String, ForeignKey("markets.condition_id"), nullable=False)
    token_id = Column(String, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    price = Column(Float, nullable=False)
    size = Column(Float, nullable=False)
    side = Column(String, nullable=False)       # BUY or SELL
    outcome = Column(String, nullable=False)     # YES or NO
    maker_address = Column(String)
    taker_address = Column(String)
    usd_value = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    market = relationship("Market", back_populates="trades")

    __table_args__ = (
        Index("idx_trades_market_time", "market_id", "timestamp"),
        Index("idx_trades_taker", "taker_address"),
        Index("idx_trades_usd", "usd_value"),
        Index("idx_trades_timestamp", "timestamp"),
    )


# ── Wallets ──────────────────────────────────────────────────────────

class Wallet(Base):
    __tablename__ = "wallets"

    address = Column(String, primary_key=True)
    first_seen = Column(DateTime(timezone=True))
    total_trades = Column(Integer, default=0)
    total_volume = Column(Float, default=0)
    markets_traded = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    win_rate = Column(Float)
    avg_trade_size = Column(Float)
    is_flagged_hashdive = Column(Boolean, default=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ── Market Volume Snapshots ──────────────────────────────────────────

class MarketVolumeSnapshot(Base):
    __tablename__ = "market_volume_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(String, ForeignKey("markets.condition_id"), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    volume_1h = Column(Float)
    volume_4h = Column(Float)
    volume_24h = Column(Float)
    trade_count_1h = Column(Integer)
    avg_trade_size_1h = Column(Float)
    price_change_1h = Column(Float)

    market = relationship("Market", back_populates="volume_snapshots")

    __table_args__ = (
        Index("idx_vol_snap_market_time", "market_id", "timestamp"),
    )


# ── Signals ──────────────────────────────────────────────────────────

class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(String, ForeignKey("markets.condition_id"), nullable=False)
    detected_at = Column(DateTime(timezone=True), default=utcnow)
    signal_type = Column(String, nullable=False)
    score = Column(Integer, CheckConstraint("score BETWEEN 1 AND 10"))
    direction = Column(String)                   # YES or NO
    confidence = Column(Float)                   # 0.0 - 1.0
    analysis = Column(Text)
    trigger_wallets = Column(ARRAY(String))
    trigger_trade_ids = Column(ARRAY(Integer))
    total_suspicious_volume = Column(Float)
    market_price_at_detection = Column(Float)
    time_to_resolution = Column(Interval)
    web_context = Column(Text)
    key_findings = Column(ARRAY(String))
    recommendation = Column(String)              # STRONG_BUY, BUY, HOLD, SKIP
    status = Column(String, default="active")    # active, expired, resolved_win, resolved_loss

    market = relationship("Market", back_populates="signals")
    paper_trades = relationship("PaperTrade", back_populates="signal")


# ── Paper Trades ─────────────────────────────────────────────────────

class PaperTrade(Base):
    __tablename__ = "paper_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(Integer, ForeignKey("signals.id"))
    opened_at = Column(DateTime(timezone=True), default=utcnow)
    closed_at = Column(DateTime(timezone=True))
    market_id = Column(String, ForeignKey("markets.condition_id"), nullable=False)
    direction = Column(String, nullable=False)    # YES or NO
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float)
    size = Column(Float, nullable=False)          # number of shares
    usd_invested = Column(Float, nullable=False)
    usd_returned = Column(Float)
    pnl = Column(Float)
    pnl_pct = Column(Float)
    exit_reason = Column(String)                  # resolution, stop_loss, take_profit, trailing_stop, near_resolution, time_decay
    status = Column(String, default="open")       # open, closed
    score_at_entry = Column(Integer)
    confidence_at_entry = Column(Float)
    category = Column(String)
    # Trailing stop tracking
    highest_price_since_entry = Column(Float)
    trailing_stop_price = Column(Float)

    signal = relationship("Signal", back_populates="paper_trades")
    market = relationship("Market", back_populates="paper_trades")


# ── Portfolio ────────────────────────────────────────────────────────

class Portfolio(Base):
    __tablename__ = "portfolio"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), default=utcnow)
    balance = Column(Float, nullable=False)       # available USDC
    invested = Column(Float, nullable=False)      # USDC in open positions
    total_value = Column(Float, nullable=False)   # balance + current positions value
    total_pnl = Column(Float)
    total_trades = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    losing_trades = Column(Integer, default=0)


# ── Score Bracket Stats (for Bayesian Kelly calibration) ─────────────

class ScoreBracketStats(Base):
    __tablename__ = "score_bracket_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bracket = Column(String, nullable=False, unique=True)  # "5-6", "7", "8-10"
    total_trades = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    win_rate = Column(Float)
    avg_pnl_win = Column(Float)
    avg_pnl_loss = Column(Float)
    profit_factor = Column(Float)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ── AI Cache ─────────────────────────────────────────────────────────

class AICache(Base):
    __tablename__ = "ai_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(String, ForeignKey("markets.condition_id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    response_json = Column(Text, nullable=False)

    __table_args__ = (
        Index("idx_ai_cache_market", "market_id", "expires_at"),
    )


# ── Circuit Breaker State ────────────────────────────────────────────

class CircuitBreakerState(Base):
    __tablename__ = "circuit_breaker_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(DateTime(timezone=True), nullable=False, unique=True)
    starting_value = Column(Float, nullable=False)
    current_value = Column(Float, nullable=False)
    daily_pnl_pct = Column(Float, nullable=False)
    is_tripped = Column(Boolean, default=False)
    tripped_at = Column(DateTime(timezone=True))
