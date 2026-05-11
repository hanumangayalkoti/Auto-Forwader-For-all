import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL", "")
FERNET_KEY = os.getenv("FERNET_KEY", "").encode() if os.getenv("FERNET_KEY") else b""
WEBHOOK_PORT = int(os.getenv("PORT", "8080"))
BACKUP_BOT_TOKEN = os.getenv("BACKUP_BOT_TOKEN", "")

# ===== CASHFREE =====
CASHFREE_APP_ID = os.getenv("CASHFREE_APP_ID", "")
CASHFREE_SECRET_KEY = os.getenv("CASHFREE_SECRET_KEY", "")
CASHFREE_ENV = os.getenv("CASHFREE_ENV", "TEST")  # "TEST" ya "PROD"
CASHFREE_WEBHOOK_SECRET = os.getenv("CASHFREE_WEBHOOK_SECRET", "")

MAX_GROUPS = 5
TRIAL_DAYS = 7
SUBSCRIPTION_PRICE = 69.00   # Cashfree mein rupees mein hota hai (Razorpay wala paise mein tha)
SUBSCRIPTION_DAYS = 30
