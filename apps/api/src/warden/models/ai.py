"""AI providers, token/cost usage, agent configuration and agent runs.

AI is optional: nothing else in the product depends on these tables. A
provider's API key lives in the credential vault; only its fingerprint is
ever returned. An agent run keeps its prompts, every tool call and the
model's output as evidence, and is never edited after it finishes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from warden.db import Base, IdMixin, TimestampMixin, UTCDateTime, utcnow


class AiProvider(IdMixin, TimestampMixin, Base):
    __tablename__ = "ai_providers"

    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(24))  # anthropic | openai_compatible | ollama | demo
    base_url: Mapped[str] = mapped_column(String(512))
    credential_id: Mapped[str | None] = mapped_column(ForeignKey("credentials.id"), nullable=True)
    default_model: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # ok | error | unknown
    status_detail: Mapped[str] = mapped_column(String(512), default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    models: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)  # as last listed by the provider
    # Budgets for the current calendar month (UTC); None = no limit.
    monthly_token_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_cost_limit_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Price per million tokens used when the model has no built-in price (local models: 0).
    price_input_per_mtok: Mapped[float] = mapped_column(Float, default=0.0)
    price_output_per_mtok: Mapped[float] = mapped_column(Float, default=0.0)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class AiUsage(IdMixin, Base):
    """One model call: tokens in and out, and what it cost at the price in force."""

    __tablename__ = "ai_usage"

    provider_id: Mapped[str] = mapped_column(ForeignKey("ai_providers.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("ai_agent_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    model: Mapped[str] = mapped_column(String(128))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class AiAgentConfig(Base):
    """Operator settings for one built-in agent. A missing row means the defaults."""

    __tablename__ = "ai_agent_configs"

    key: Mapped[str] = mapped_column(String(48), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # the per-agent kill switch
    autonomy: Mapped[str] = mapped_column(String(16), default="explain")  # off | explain | propose
    provider_id: Mapped[str | None] = mapped_column(ForeignKey("ai_providers.id", ondelete="SET NULL"), nullable=True)
    model: Mapped[str] = mapped_column(String(128), default="")
    max_tokens_per_run: Mapped[int] = mapped_column(Integer, default=60000)
    max_tool_calls: Mapped[int] = mapped_column(Integer, default=12)
    max_turns: Mapped[int] = mapped_column(Integer, default=6)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AiAgentRun(IdMixin, Base):
    __tablename__ = "ai_agent_runs"

    number: Mapped[int] = mapped_column(Integer, unique=True)  # shown as RUN-0001
    agent_key: Mapped[str] = mapped_column(String(48), index=True)
    provider_id: Mapped[str | None] = mapped_column(ForeignKey("ai_providers.id", ondelete="SET NULL"), nullable=True)
    provider_name: Mapped[str] = mapped_column(String(128), default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    autonomy: Mapped[str] = mapped_column(String(16))
    # running | succeeded | failed | budget_exceeded | stopped
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    requested_by: Mapped[str] = mapped_column(String(64))
    subject: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    question: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    # Evidence: every message sent and received, in order, and every tool call with its result.
    transcript: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    output: Mapped[str] = mapped_column(Text, default="")
    proposals: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)  # change requests this run proposed
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
