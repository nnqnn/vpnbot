from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

# Allow direct execution: `python3 scripts/backfill_referral_year_rewards.py`
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.db.models import Payment, PaymentStatus, Referral, ReferralYearReward, SubscriptionCharge, SubscriptionChargeSource, User, UserStatus
from app.db.repositories import ReferralYearRewardRepository, SubscriptionChargeRepository, UserRepository
from app.db.session import build_engine, build_session_maker, init_db
from app.services.user_service import UserService
from app.services.xray_service import XrayService
from app.utils.time import utc_now


@dataclass
class BackfillStats:
    total_referrals: int = 0
    paid_payment_users: int = 0
    existing_subscription_charge_users: int = 0
    eligible_invited_users: int = 0
    subscription_charges_to_create: int = 0
    inviters_with_pending_rewards: int = 0
    total_groups_to_apply: int = 0
    total_days_to_apply: int = 0
    xray_enable_attempts: int = 0
    xray_enable_failures: int = 0


async def _run(apply: bool) -> None:
    settings = get_settings()
    threshold = max(1, int(settings.referral_paid_invites_for_year_reward))
    reward_days = max(1, int(settings.referral_year_reward_days))
    trial_delta = timedelta(days=max(0, int(settings.trial_days)))

    engine = build_engine(settings)
    session_maker = build_session_maker(engine)
    xray_service = XrayService(settings)
    user_service = UserService(settings, xray_service)
    stats = BackfillStats()

    try:
        # Ensure newly added tables exist before querying backfill data.
        await init_db(engine)
        async with session_maker() as session:
            referral_rows = (
                await session.execute(
                    select(
                        Referral.inviter_id,
                        Referral.invited_id,
                        User.created_at,
                        User.expiration_date,
                    ).join(User, User.id == Referral.invited_id)
                )
            ).all()
            stats.total_referrals = len(referral_rows)

            paid_payment_user_ids = set(
                (
                    await session.execute(
                        select(Payment.user_id).where(Payment.status == PaymentStatus.paid)
                    )
                ).scalars()
            )
            stats.paid_payment_users = len(paid_payment_user_ids)

            charged_user_ids = set(
                (await session.execute(select(SubscriptionCharge.user_id))).scalars()
            )
            stats.existing_subscription_charge_users = len(charged_user_ids)

            rewarded_group_rows = (
                await session.execute(
                    select(ReferralYearReward.inviter_id, ReferralYearReward.rewarded_groups)
                )
            ).all()
            rewarded_groups_by_inviter = {int(inviter_id): int(groups) for inviter_id, groups in rewarded_group_rows}

            eligible_invited_ids: set[int] = set()
            invited_by_inviter: dict[int, set[int]] = {}
            for inviter_id, invited_id, created_at, expiration_date in referral_rows:
                if invited_id in invited_by_inviter.get(inviter_id, set()):
                    continue
                invited_by_inviter.setdefault(int(inviter_id), set()).add(int(invited_id))

                has_paid_payment = int(invited_id) in paid_payment_user_ids
                has_extended_term = bool(
                    created_at
                    and expiration_date
                    and expiration_date > (created_at + trial_delta)
                )
                if has_paid_payment and has_extended_term:
                    eligible_invited_ids.add(int(invited_id))

            stats.eligible_invited_users = len(eligible_invited_ids)

            to_create_charge_ids = sorted(eligible_invited_ids - charged_user_ids)
            stats.subscription_charges_to_create = len(to_create_charge_ids)

            effective_paid_ids = set(charged_user_ids)
            effective_paid_ids.update(to_create_charge_ids)

            pending_groups_by_inviter: dict[int, int] = {}
            for inviter_id, invited_ids in invited_by_inviter.items():
                paid_referrals = sum(1 for invited_id in invited_ids if invited_id in effective_paid_ids)
                eligible_groups = paid_referrals // threshold
                rewarded_groups = rewarded_groups_by_inviter.get(inviter_id, 0)
                pending_groups = eligible_groups - rewarded_groups
                if pending_groups > 0:
                    pending_groups_by_inviter[inviter_id] = pending_groups

            stats.inviters_with_pending_rewards = len(pending_groups_by_inviter)
            stats.total_groups_to_apply = sum(pending_groups_by_inviter.values())
            stats.total_days_to_apply = stats.total_groups_to_apply * reward_days

            print("Backfill summary:")
            print(f"  referrals_total: {stats.total_referrals}")
            print(f"  users_with_paid_payment: {stats.paid_payment_users}")
            print(f"  existing_subscription_charge_users: {stats.existing_subscription_charge_users}")
            print(f"  eligible_invited_users: {stats.eligible_invited_users}")
            print(f"  subscription_charges_to_create: {stats.subscription_charges_to_create}")
            print(f"  inviters_with_pending_rewards: {stats.inviters_with_pending_rewards}")
            print(f"  total_groups_to_apply: {stats.total_groups_to_apply}")
            print(f"  total_days_to_apply: {stats.total_days_to_apply}")

            if not apply:
                print("\nDry run complete. Use --apply to persist changes.")
                await session.rollback()
                return

            subscription_repo = SubscriptionChargeRepository(session)
            reward_repo = ReferralYearRewardRepository(session)
            user_repo = UserRepository(session)

            for invited_id in to_create_charge_ids:
                await subscription_repo.create(
                    user_id=invited_id,
                    source=SubscriptionChargeSource.manual,
                )

            for inviter_id, pending_groups in sorted(pending_groups_by_inviter.items()):
                inviter = await user_repo.get_by_id(inviter_id)
                if inviter is None:
                    continue

                total_days = reward_days * pending_groups
                user_service.extend_user_days(inviter, total_days)
                inviter.warning_sent_at = None

                if inviter.status == UserStatus.active and not inviter.device_limit_blocked:
                    stats.xray_enable_attempts += 1
                    try:
                        await xray_service.enable_user(inviter.telegram_id, str(inviter.uuid))
                        inviter.vpn_enabled = True
                    except Exception as exc:  # noqa: BLE001
                        stats.xray_enable_failures += 1
                        print(
                            f"WARN: failed to enable inviter {inviter.telegram_id} in Xray: {exc}"
                        )

                reward_state = await reward_repo.ensure(inviter_id)
                reward_state.rewarded_groups = int(reward_state.rewarded_groups or 0) + pending_groups

                print(
                    f"Applied reward: inviter={inviter.telegram_id} "
                    f"groups=+{pending_groups} days=+{total_days}"
                )

            await session.commit()

            print("\nApplied successfully.")
            print(f"  xray_enable_attempts: {stats.xray_enable_attempts}")
            print(f"  xray_enable_failures: {stats.xray_enable_failures}")
    finally:
        await engine.dispose()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill referral paid-subscription events and year rewards.\n"
            "Criteria for historical paid referral: has at least one PAID payment "
            "and expiration_date exceeds trial window."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist changes. Without this flag script runs in dry-run mode.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(_run(apply=args.apply))
