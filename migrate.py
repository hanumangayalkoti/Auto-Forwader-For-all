"""
DB Migration Script — purane schema ko naye schema mein convert karta hai.
Railway pe pehli baar run karo: python migrate.py
Ya main.py mein startup pe auto-run hoga.
"""
import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


MIGRATIONS = [
    # ── USERS table — naye columns add karo ──
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name VARCHAR(128) DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS plan VARCHAR(16) DEFAULT 'free'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_end TIMESTAMP",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN DEFAULT FALSE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by BIGINT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS join_date TIMESTAMP DEFAULT NOW()",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()",

    # ── TASKS table — rename from forwarding_groups if exists ──
    # (New table 'tasks' will be created by init_db, so just handle old data)

    # ── Backfill plan from old subscription fields if they exist ──
    # (Safe no-op if columns don't exist — handled by IF NOT EXISTS above)

    # ── Drop old columns that conflict with new schema ──
    # Note: old bots had 'plan' as 'paid'/'free' text — standardize
    "UPDATE users SET plan = 'free' WHERE plan NOT IN ('free', 'basic', 'pro', 'business')",
    "UPDATE users SET plan = 'free' WHERE plan IS NULL",
    "UPDATE users SET is_banned = FALSE WHERE is_banned IS NULL",
    "UPDATE users SET full_name = '' WHERE full_name IS NULL",
]


async def run_migrations():
    engine = create_async_engine(DATABASE_URL, echo=False)
    logger.info("Migration shuru ho rahi hai...")

    async with engine.begin() as conn:
        # Check if users table exists
        result = await conn.execute(text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'users')"
        ))
        users_exists = result.scalar()

        if not users_exists:
            logger.info("Users table nahi hai — init_db create karega, migration skip.")
            await engine.dispose()
            return

        for sql in MIGRATIONS:
            try:
                await conn.execute(text(sql))
                logger.info(f"✅ {sql[:60]}...")
            except Exception as e:
                # Most errors are harmless (column already exists, etc.)
                logger.warning(f"⚠️ Skip: {sql[:60]}... → {e}")

    await engine.dispose()
    logger.info("✅ Migration complete!")


if __name__ == "__main__":
    asyncio.run(run_migrations())
