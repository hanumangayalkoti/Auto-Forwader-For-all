import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SIMI_BOT_TOKEN = os.getenv("SIMI_BOT_TOKEN", "")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL", "")
FERNET_KEY = os.getenv("FERNET_KEY", "").encode() if os.getenv("FERNET_KEY") else b""
WEBHOOK_PORT = int(os.getenv("PORT", "8080"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

MAIN_BOT_USERNAME = "Sandesh_forward_bot"
SIMI_BOT_USERNAME = "Sandesh_Forwader_Help_bot"

# ===== RAZORPAY =====
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

# ===== CURRENCY =====
USD_TO_INR = 90  # Fixed conversion: $1 = ₹90

# ===== PLANS =====
# plan_key → display info
PLAN_INFO = {
    "free": {
        "name": "🆓 Free",
        "monthly_usd": 0,
        "annual_usd": 0,
        "monthly_inr": 0,
        "annual_inr": 0,
        "days_monthly": 0,    # 0 = permanent
        "days_annual": 0,
    },
    "basic": {
        "name": "⭐ Basic",
        "monthly_usd": 1,
        "annual_usd": 10,
        "monthly_inr": 1 * USD_TO_INR,      # ₹90
        "annual_inr": 10 * USD_TO_INR,      # ₹900
        "days_monthly": 30,
        "days_annual": 365,
    },
    "pro": {
        "name": "💎 Pro",
        "monthly_usd": 2,
        "annual_usd": 20,
        "monthly_inr": 2 * USD_TO_INR,      # ₹180
        "annual_inr": 20 * USD_TO_INR,      # ₹1800
        "days_monthly": 30,
        "days_annual": 365,
    },
    "business": {
        "name": "🚀 Business",
        "monthly_usd": 5,
        "annual_usd": 45,
        "monthly_inr": 5 * USD_TO_INR,      # ₹450
        "annual_inr": 45 * USD_TO_INR,      # ₹4050
        "days_monthly": 30,
        "days_annual": 365,
    },
}

# ===== PLAN LIMITS =====
PLAN_LIMITS = {
    "free": {
        "max_tasks": 1,
        "max_sources": 1,
        "max_targets": 1,
        "msgs_per_day": 60,
        "features": {
            "remove_forward_tag": True,
            "header_footer": False,
            "delay": False,
            "media_filter": False,
            "skip_duplicates": False,
            "schedule": False,
            "whitelist": False,
            "blacklist": False,
            "replace_words": False,
            "link_replacer": False,
            "bulk_forward": False,
            "regex_filter": False,
            "multi_path_routing": False,
            "image_watermark": False,
            "pinned_only": False,
        },
    },
    "basic": {
        "max_tasks": 3,
        "max_sources": 3,
        "max_targets": 3,
        "msgs_per_day": -1,   # unlimited
        "features": {
            "remove_forward_tag": True,
            "header_footer": True,
            "delay": True,
            "media_filter": True,
            "skip_duplicates": True,
            "schedule": False,
            "whitelist": False,
            "blacklist": False,
            "replace_words": False,
            "link_replacer": False,
            "bulk_forward": False,
            "regex_filter": False,
            "multi_path_routing": False,
            "image_watermark": False,
            "pinned_only": True,
        },
    },
    "pro": {
        "max_tasks": 5,
        "max_sources": 8,
        "max_targets": 8,
        "msgs_per_day": -1,
        "features": {
            "remove_forward_tag": True,
            "header_footer": True,
            "delay": True,
            "media_filter": True,
            "skip_duplicates": True,
            "schedule": True,
            "whitelist": True,
            "blacklist": True,
            "replace_words": True,
            "link_replacer": True,
            "bulk_forward": True,
            "regex_filter": False,
            "multi_path_routing": False,
            "image_watermark": False,
            "pinned_only": True,
        },
    },
    "business": {
        "max_tasks": 10,
        "max_sources": 15,
        "max_targets": 15,
        "msgs_per_day": -1,
        "features": {
            "remove_forward_tag": True,
            "header_footer": True,
            "delay": True,
            "media_filter": True,
            "skip_duplicates": True,
            "schedule": True,
            "whitelist": True,
            "blacklist": True,
            "replace_words": True,
            "link_replacer": True,
            "bulk_forward": True,
            "regex_filter": True,
            "multi_path_routing": True,
            "image_watermark": True,
            "pinned_only": True,
        },
    },
}


def get_plan_limits(plan: str) -> dict:
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])


def has_feature(plan: str, feature: str) -> bool:
    return get_plan_limits(plan)["features"].get(feature, False)


def plan_display_name(plan: str) -> str:
    return PLAN_INFO.get(plan, PLAN_INFO["free"])["name"]


# ===== AFFILIATE =====
AFFILIATE_COMMISSION_PERCENT = 80        # 80% of first payment
AFFILIATE_MIN_WITHDRAW_USD = 2           # $2 minimum
AFFILIATE_MIN_WITHDRAW_INR = AFFILIATE_MIN_WITHDRAW_USD * USD_TO_INR  # ₹180

# ===== MISC =====
FORWARD_DELAY = 0.4
FAIL_NOTIFY_THRESHOLD = 3
DIALOG_LIMIT = 100
DISPLAY_LIMIT = 20       # Max channels shown in list
IST_OFFSET_HOURS = 5.5   # For IST midnight calculations
