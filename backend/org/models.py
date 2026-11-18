"""The organisation: its business units and the people who sign in.

One organisation per deployment (decided 2026-11-18), but every table carries
`org_id` from the start, so several organisations on one deployment later is a
query change rather than a data migration.

Track H owns this file.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from shared.stages import Role

#: Retention defaults, in days after a candidate's process ended (shortlisted,
#: rejected or withdrew). DPDP Act 2023: keep personal data only as long as the
#: purpose needs it. The org admin can change them; nothing is deleted until a
#: person runs the deletion from the privacy screen.
DEFAULT_RETENTION_DAYS = {"cv": 180, "recording": 30, "transcript": 365}


class Organisation(Base):
    __tablename__ = "organisations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    #: CVs, the screening built from them, and the interview plan.
    retention_cv_days: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_RETENTION_DAYS["cv"], server_default=str(DEFAULT_RETENTION_DAYS["cv"])
    )
    #: Recordings of the HR and technical rounds.
    retention_recording_days: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_RETENTION_DAYS["recording"],
        server_default=str(DEFAULT_RETENTION_DAYS["recording"]),
    )
    #: Transcripts, HR notes, evaluations and reports.
    retention_transcript_days: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_RETENTION_DAYS["transcript"],
        server_default=str(DEFAULT_RETENTION_DAYS["transcript"]),
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


class DeletionRequest(Base):
    """A candidate asked for their personal data to be erased (DPDP Act 2023).

    Logged when it arrives, then completed (the data is erased) or refused with
    a written reason, such as a legal hold. The decisions made about the
    candidate are kept either way: who decided what, and when, is the record
    the organisation must be able to account for.
    """

    __tablename__ = "deletion_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        ForeignKey("organisations.id", name="fk_deletion_requests_org"), index=True
    )
    candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("candidates.id", name="fk_deletion_requests_candidate", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    #: As the request named them; kept so the request stays readable after erasure.
    candidate_name: Mapped[str] = mapped_column(String(256))
    #: How the request arrived and how to reply, in the admin's words.
    contact_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    received_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", name="fk_deletion_requests_received_by"), nullable=True
    )
    #: open, completed or refused.
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open", index=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", name="fk_deletion_requests_resolved_by"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
