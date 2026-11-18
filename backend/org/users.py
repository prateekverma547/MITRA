"""Creating organisations, business units and users, and checking sign-ins.

Used by the seed script, `scripts/create_user.py`, the sign-in route and, in
phase 4, user management. The rules about who may have a BU live here so that
no caller can create a user the guards would scope wrongly.
"""

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from org.auth import check_credentials_timing_safe, hash_password
from org.models import BusinessUnit, Organisation, User
from shared.stages import Role

#: Long enough that a guessed password is unlikely, short enough to type.
MIN_PASSWORD_LENGTH = 10

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class UserError(ValueError):
    """A user that would break the access rules. The message says which rule."""


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


async def get_or_create_org(session: AsyncSession, name: str) -> Organisation:
    org = (await session.execute(select(Organisation).where(Organisation.name == name))).scalar_one_or_none()
    if org is None:
        org = Organisation(id=new_id("org"), name=name)
        session.add(org)
        await session.flush()
    return org


async def get_or_create_bu(session: AsyncSession, org: Organisation, name: str) -> BusinessUnit:
    bu = (
        await session.execute(
            select(BusinessUnit).where(BusinessUnit.org_id == org.id, BusinessUnit.name == name)
        )
    ).scalar_one_or_none()
    if bu is None:
        bu = BusinessUnit(id=new_id("bu"), org_id=org.id, name=name)
        session.add(bu)
        await session.flush()
    return bu


async def create_user(
    session: AsyncSession,
    *,
    org_id: str,
    email: str,
    name: str,
    role: Role | str,
    password: str,
    bu_id: str | None = None,
) -> User:
    role = Role(role)
    email = normalise_email(email)
    if not _EMAIL.match(email):
        raise UserError(f"{email!r} is not an email address.")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise UserError(f"Passwords need at least {MIN_PASSWORD_LENGTH} characters.")
    if role is Role.BU_HEAD and not bu_id:
        raise UserError("A BU head needs a business unit; without one they would see nothing.")
    if role is not Role.BU_HEAD and bu_id:
        raise UserError("Only BU heads belong to a business unit. HR and admins see every BU.")
    if bu_id:
        bu = await session.get(BusinessUnit, bu_id)
        if bu is None or bu.org_id != org_id:
            raise UserError("That business unit is not in this organisation.")
    taken = await session.scalar(select(func.count()).select_from(User).where(User.email == email))
    if taken:
        raise UserError(f"{email} already has an account.")

    user = User(
        id=new_id("user"),
        org_id=org_id,
        email=email,
        name=name.strip() or email,
        role=role.value,
        bu_id=bu_id,
        password_hash=hash_password(password),
        active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def authenticate(session: AsyncSession, email: str, password: str) -> User | None:
    """The active user these credentials belong to, or None. Never says which part was wrong."""
    user = (
        await session.execute(select(User).where(User.email == normalise_email(email)))
    ).scalar_one_or_none()
    if not check_credentials_timing_safe(password or "", user):
        return None
    if not user.active:
        return None
    user.last_sign_in_at = datetime.now(UTC)
    return user
