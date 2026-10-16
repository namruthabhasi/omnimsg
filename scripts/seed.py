"""
Database Seed Script:
Creates initial admin and agent users, default system settings, sample FAQ knowledge base,
and realistic multi-channel conversations with mixed statuses and messages.

Idempotent: Safe to run multiple times without duplicating or corrupting data.

Usage:
    python scripts/seed.py
"""

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.core import database
from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.models.conversation import Conversation
from app.models.knowledge_base import KnowledgeBase
from app.models.message import Message
from app.models.system_settings import SystemSettings
from app.models.user import User

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "AdminPass123!")
AGENT_EMAIL = os.getenv("AGENT_EMAIL", "agent@example.com")
AGENT_PASSWORD = os.getenv("AGENT_PASSWORD", "AgentPass123!")


async def seed() -> None:
    print("🌱 Seeding database...")
    async with database.async_session_factory() as session:
        # 1. Seed Users (Admin & Agent)
        res_admin = await session.execute(select(User).where(User.email == ADMIN_EMAIL))
        admin = res_admin.scalar_one_or_none()
        if not admin:
            admin = User(
                email=ADMIN_EMAIL,
                hashed_password=hash_password(ADMIN_PASSWORD),
                full_name="System Administrator",
                role="admin",
                is_active=True,
            )
            session.add(admin)
            print(f"  ✓ Created admin user: {ADMIN_EMAIL}")
        else:
            print(f"  · Admin user already exists: {ADMIN_EMAIL}")

        res_agent = await session.execute(select(User).where(User.email == AGENT_EMAIL))
        agent = res_agent.scalar_one_or_none()
        if not agent:
            agent = User(
                email=AGENT_EMAIL,
                hashed_password=hash_password(AGENT_PASSWORD),
                full_name="Sarah Support",
                role="agent",
                is_active=True,
            )
            session.add(agent)
            print(f"  ✓ Created agent user: {AGENT_EMAIL}")
        else:
            print(f"  · Agent user already exists: {AGENT_EMAIL}")

        if settings.DEMO_MODE:
            demo_email = "demo@example.com"
            res_demo = await session.execute(
                select(User).where(User.email == demo_email)
            )
            demo_user = res_demo.scalar_one_or_none()
            if not demo_user:
                demo_user = User(
                    email=demo_email,
                    hashed_password=hash_password(settings.DEMO_PASSWORD),
                    full_name="Demo Agent",
                    role="agent",
                    is_active=True,
                )
                session.add(demo_user)
                print(f"  ✓ Created demo user: {demo_email}")
            else:
                if not verify_password(
                    settings.DEMO_PASSWORD, demo_user.hashed_password
                ):
                    demo_user.hashed_password = hash_password(settings.DEMO_PASSWORD)
                demo_user.role = "agent"
                demo_user.is_active = True
                print(f"  · Demo user already exists: {demo_email}")

        # 2. Seed System Settings
        res_sys = await session.execute(select(SystemSettings).limit(1))
        sys_settings = res_sys.scalar_one_or_none()
        if not sys_settings:
            sys_settings = SystemSettings(
                global_ai_enabled=True,
                system_prompt="You are a professional, helpful customer service AI representative for OmniMessage.",
                fallback_message="I'm transferring you to a human support agent who can assist you shortly.",
            )
            session.add(sys_settings)
            print("  ✓ Created default SystemSettings")
        else:
            print("  · SystemSettings already exist")

        # 3. Seed Knowledge Base FAQs
        sample_faqs = [
            (
                "What is your return policy?",
                "We accept returns within 30 days of purchase for a full refund in original packaging.",
                "Returns",
            ),
            (
                "Do you ship internationally?",
                "Yes, we ship to over 50 countries worldwide with standard delivery in 7-14 business days.",
                "Shipping",
            ),
            (
                "What are your customer support hours?",
                "Our AI responds 24/7. Human support agents are available Mon-Fri 9AM-6PM EST.",
                "Support",
            ),
            (
                "How do I track my order status?",
                "You can track your order using the tracking number sent to your email upon fulfillment.",
                "Orders",
            ),
            (
                "What payment methods do you accept?",
                "We accept Visa, MasterCard, American Express, PayPal, and Apple Pay.",
                "Billing",
            ),
        ]

        for q, a, cat in sample_faqs:
            q_res = await session.execute(select(KnowledgeBase).where(KnowledgeBase.question == q))
            if not q_res.scalar_one_or_none():
                session.add(KnowledgeBase(question=q, answer=a, category=cat, is_active=True))
                print(f"  ✓ Added KB FAQ: '{q}'")

        # 4. Seed Multi-Channel Conversations
        sample_threads = [
            {
                "channel": "whatsapp",
                "user_id": "15550101",
                "name": "Carlos Gomez",
                "status": "open",
                "ai_enabled": True,
                "days_ago": 2,
                "messages": [
                    ("inbound", "customer", "Hi! What is your return policy?"),
                    ("outbound", "ai", "We accept returns within 30 days of purchase for a full refund in original packaging."),
                    ("inbound", "customer", "Great, thanks!"),
                ],
                "unread": 0,
            },
            {
                "channel": "telegram",
                "user_id": "987111",
                "name": "Elena Rostova (@elena_r)",
                "status": "needs_human",
                "ai_enabled": False,
                "days_ago": 1,
                "messages": [
                    ("inbound", "customer", "My order arrived damaged, I need to talk to a manager immediately."),
                    ("outbound", "ai", "I'm transferring you to a human support agent who can assist you shortly."),
                ],
                "unread": 1,
            },
            {
                "channel": "instagram",
                "user_id": "ig_882910",
                "name": "Jordan Lee",
                "status": "closed",
                "ai_enabled": True,
                "days_ago": 5,
                "messages": [
                    ("inbound", "customer", "Do you have any discount codes?"),
                    ("outbound", "ai", "Use code WELCOME10 for 10% off your first order!"),
                    ("inbound", "customer", "Worked perfectly, thank you!"),
                    ("outbound", "human", "You're very welcome, enjoy your order!"),
                ],
                "unread": 0,
            },
            {
                "channel": "messenger",
                "user_id": "fb_445566",
                "name": "Liam Murphy",
                "status": "open",
                "ai_enabled": True,
                "days_ago": 0,
                "messages": [
                    ("inbound", "customer", "Do you ship to Ireland?"),
                    ("outbound", "ai", "Yes, we ship to Ireland! Standard delivery takes 7-14 business days."),
                ],
                "unread": 0,
            },
        ]

        now = datetime.now(timezone.utc)
        for t in sample_threads:
            stmt = select(Conversation).where(
                Conversation.channel == t["channel"],
                Conversation.external_user_id == t["user_id"],
            )
            res = await session.execute(stmt)
            conv = res.scalar_one_or_none()
            if not conv:
                thread_created = now - timedelta(days=t["days_ago"], hours=2)
                conv = Conversation(
                    channel=t["channel"],
                    external_user_id=t["user_id"],
                    customer_name=t["name"],
                    status=t["status"],
                    ai_enabled=t["ai_enabled"],
                    unread_count=t["unread"],
                    created_at=thread_created,
                    updated_at=now,
                )
                session.add(conv)
                await session.flush()

                # Add messages with sequential timestamps
                for idx, (direction, sender_type, text) in enumerate(t["messages"]):
                    msg_time = thread_created + timedelta(minutes=idx * 2)
                    msg = Message(
                        conversation_id=conv.id,
                        channel=t["channel"],
                        external_message_id=f"seed-{conv.channel}-{conv.external_user_id}-{idx}",
                        direction=direction,
                        sender_type=sender_type,
                        text=text,
                        timestamp=msg_time,
                        created_at=msg_time,
                    )
                    session.add(msg)
                    conv.last_message_at = msg_time

                print(f"  ✓ Seeded {t['channel']} conversation with {t['name']}")
            else:
                print(f"  · Conversation for {t['channel']}:{t['user_id']} already exists")

        await session.commit()
    print("✅ Seed completed successfully!")


if __name__ == "__main__":
    asyncio.run(seed())
