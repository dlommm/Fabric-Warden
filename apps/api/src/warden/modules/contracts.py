"""Module contract: what every module must declare to the platform."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from warden.jobs import JobContext


class Maturity(StrEnum):
    AVAILABLE = "available"  # implemented, tested, can be enabled
    PREVIEW = "preview"  # implemented but incomplete; enabling shows a warning
    PLANNED = "planned"  # declared for roadmap/dependency purposes only; cannot be enabled


class Category(StrEnum):
    CORE = "core"
    INTEGRATION = "integration"
    NETWORK = "network"
    OBSERVABILITY = "observability"
    SECURITY = "security"
    INFRASTRUCTURE = "infrastructure"
    AUTOMATION = "automation"
    AI = "ai"


class ResourceEstimate(BaseModel):
    memory_mb: int = 0
    cpu_millicores: int = 0
    disk_mb_per_day: int = 0
    extra_containers: list[str] = Field(default_factory=list)


class NavEntry(BaseModel):
    label: str
    href: str
    icon: str = "square"
    section: str = "Network"


class WidgetDecl(BaseModel):
    id: str
    title: str


class JobDecl(BaseModel):
    id: str
    title: str
    default_interval_seconds: int | None = None


class ModuleManifest(BaseModel):
    id: str
    version: str
    name: str
    category: Category
    description: str
    maturity: Maturity
    roadmap_phase: int
    platform_compat: str = ">=0.1,<1.0"
    requires: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)
    controller_capabilities: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    nav: list[NavEntry] = Field(default_factory=list)
    widgets: list[WidgetDecl] = Field(default_factory=list)
    jobs: list[JobDecl] = Field(default_factory=list)
    publishes: list[str] = Field(default_factory=list)
    subscribes: list[str] = Field(default_factory=list)
    external_integrations: list[str] = Field(default_factory=list)
    resources: ResourceEstimate = Field(default_factory=ResourceEstimate)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    data_ownership: list[str] = Field(default_factory=list)
    performs_writes: bool = False
    uninstall_behavior: str = "Disabling stops jobs and write actions; historical data is retained."
    always_enabled: bool = False
    warnings: list[str] = Field(default_factory=list)


JobHandler = Callable[["JobContext"], Awaitable[dict[str, Any]]]
HealthCheck = Callable[[Any], Awaitable[dict[str, Any]]]


@dataclass
class ModuleRuntime:
    """Code-side hooks a bundled module contributes. Planned modules have none."""

    job_handlers: dict[str, JobHandler]
    health_check: HealthCheck | None = None
