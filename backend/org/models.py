"""The organisation: its business units and the people who sign in.

One organisation per deployment (decided 2026-11-18), but every table carries
`org_id` from the start, so several organisations on one deployment later is a
query change rather than a data migration.

Track H owns this file.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from shared.stages import Role


class Organisation(Base):
    __tablename__ = "organisations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BusinessUnit(Base):
    """Business, Finance, Software, Data Science, AI, ... A BU head sees only theirs."""

    __tablename__ = "business_units"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_business_units_org_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class User(Base):
    """Someone who signs in to the console. The role comes from here, never from the UI."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organisations.id"), index=True)
    #: Stored lowercased, so sign-in is not case-sensitive about it.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16))
    #: Set for BU heads, and only for them. An HR user or an admin with a BU
    #: would be a scoping bug waiting to happen, so `org.auth` refuses it.
    bu_id: Mapped[str | None] = mapped_column(
        ForeignKey("business_units.id"), nullable=True, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(256))
    #: Deactivated users are refused on their next request, not when their
    #: cookie expires: the guard reads this row every time.
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_sign_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def role_enum(self) -> Role:
        return Role(self.role)
