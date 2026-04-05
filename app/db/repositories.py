from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Payment, PaymentStatus, Referral, User, UserPolicy, UserStatus


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        query: Select[tuple[User]] = select(User).where(User.telegram_id == telegram_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_referral_code(self, referral_code: str) -> User | None:
        query: Select[tuple[User]] = select(User).where(User.referral_code == referral_code)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> User | None:
        query: Select[tuple[User]] = select(User).where(User.id == user_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create(self, **kwargs) -> User:
        user = User(**kwargs)
        self.session.add(user)
        await self.session.flush()
        return user

    async def list_users(self, limit: int = 50, offset: int = 0) -> list[User]:
        query: Select[tuple[User]] = (
            select(User)
            .order_by(User.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_all_users(self) -> list[User]:
        query: Select[tuple[User]] = select(User).order_by(User.id.asc())
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def users_for_auto_renew(self, now: datetime) -> list[User]:
        query: Select[tuple[User]] = select(User).where(
            User.status == UserStatus.active,
            User.expiration_date.is_not(None),
            User.expiration_date <= now,
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def users_for_warning(self, now: datetime, warning_window_hours: int = 24) -> list[User]:
        window_end = now + timedelta(hours=warning_window_hours)
        query: Select[tuple[User]] = select(User).where(
            User.status == UserStatus.active,
            User.expiration_date.is_not(None),
            User.expiration_date > now,
            User.expiration_date <= window_end,
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count_referrals(self, inviter_id: int) -> int:
        result = await self.session.execute(
            select(func.count(Referral.id)).where(Referral.inviter_id == inviter_id)
        )
        return int(result.scalar_one() or 0)


class PaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_pending(self, user_id: int, amount: Decimal, provider_label: str) -> Payment:
        payment = Payment(user_id=user_id, amount=amount, provider_label=provider_label, status=PaymentStatus.pending)
        self.session.add(payment)
        await self.session.flush()
        return payment

    async def get_by_id(self, payment_id: int) -> Payment | None:
        query: Select[tuple[Payment]] = select(Payment).where(Payment.id == payment_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_id_for_user(self, payment_id: int, user_id: int) -> Payment | None:
        query: Select[tuple[Payment]] = select(Payment).where(
            Payment.id == payment_id,
            Payment.user_id == user_id,
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def pending(self, limit: int = 200) -> list[Payment]:
        query: Select[tuple[Payment]] = (
            select(Payment)
            .where(Payment.status == PaymentStatus.pending)
            .order_by(Payment.created_at.asc())
            .limit(limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def user_payments(self, user_id: int, limit: int = 10) -> list[Payment]:
        query: Select[tuple[Payment]] = (
            select(Payment)
            .where(Payment.user_id == user_id)
            .order_by(Payment.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def mark_paid(self, payment: Payment, operation_id: str, paid_at: datetime | None = None) -> None:
        payment.status = PaymentStatus.paid
        payment.external_operation_id = operation_id
        payment.paid_at = paid_at or datetime.now(timezone.utc)

    async def mark_cancelled(self, payment: Payment) -> None:
        payment.status = PaymentStatus.cancelled


class ReferralRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def exists_for_invited(self, invited_id: int) -> bool:
        query: Select[tuple[Referral]] = select(Referral).where(Referral.invited_id == invited_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none() is not None

    async def create(self, inviter_id: int, invited_id: int, bonus_applied: bool = True) -> Referral:
        referral = Referral(inviter_id=inviter_id, invited_id=invited_id, bonus_applied=bonus_applied)
        self.session.add(referral)
        await self.session.flush()
        return referral


class UserPolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_user_id(self, user_id: int) -> UserPolicy | None:
        query: Select[tuple[UserPolicy]] = select(UserPolicy).where(UserPolicy.user_id == user_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def is_terms_accepted(self, user_id: int) -> bool:
        policy = await self.get_by_user_id(user_id)
        return bool(policy and policy.accepted_terms)

    async def ensure(self, user_id: int) -> UserPolicy:
        policy = await self.get_by_user_id(user_id)
        if policy is not None:
            return policy
        policy = UserPolicy(user_id=user_id, accepted_terms=False)
        self.session.add(policy)
        await self.session.flush()
        return policy

    async def accept_terms(self, user_id: int) -> UserPolicy:
        policy = await self.ensure(user_id)
        policy.accepted_terms = True
        policy.accepted_at = datetime.now(timezone.utc)
        await self.session.flush()
        return policy
