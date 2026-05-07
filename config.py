import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL", "")
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
FERNET_KEY = os.getenv("FERNET_KEY", "").encode() if os.getenv("FERNET_KEY") else b""
WEBHOOK_PORT = int(os.getenv("PORT", "8080"))
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

MAX_GROUPS = 5
TRIAL_DAYS = 7
SUBSCRIPTION_PRICE = 6900
SUBSCRIPTION_DAYS = 30
