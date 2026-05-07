# DealsKoti Forward Bot

Multi-user Telegram forwarding bot — bina "Forwarded" tag ke messages forward karo.

## Features
- 7 din free trial, uske baad ₹69/month
- Razorpay payment (UPI, Card, Net Banking)
- Private & restricted channels support
- Max 5 forwarding groups per user
- Data kabhi nahi jata (PostgreSQL — restart-proof)
- Admin panel with broadcast, ban, revenue tracking

---

## Setup — Step by Step

### Step 1: Razorpay Account
1. [razorpay.com](https://razorpay.com) pe signup karo
2. Dashboard → Settings → API Keys → Generate Key
3. `Key ID` aur `Key Secret` copy karo
4. Test Mode mein pehle test karo

### Step 2: Fernet Key Generate Karo
Python mein ek baar run karo:
```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```
Ye key `.env` mein `FERNET_KEY` mein daalo.

### Step 3: .env File Banao
```bash
cp .env.example .env
```
Sari values fill karo (BOT_TOKEN, API_ID, API_HASH, OWNER_ID, etc.)

### Step 4: Local Test (Optional)
```bash
pip install -r requirements.txt
python main.py
```

---

## GitHub pe Upload Kaise Kare

1. **GitHub account banao** — github.com pe signup
2. **New Repository banao**:
   - github.com/new pe jao
   - Repository name: `dealskoti-bot`
   - Private rakho (important!)
   - "Create Repository" dabao

3. **Code upload karo** (terminal/command prompt mein):
```bash
cd dealskoti-bot
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/TERA_USERNAME/dealskoti-bot.git
git push -u origin main
```

> ⚠️ `.env` file GitHub pe KABHI upload mat karna — `.gitignore` mein already add hai.

---

## Railway pe Deploy Kaise Kare

### Step 1: Railway Account
- [railway.app](https://railway.app) pe jao
- "Login with GitHub" se login karo

### Step 2: New Project
- "New Project" → "Deploy from GitHub repo"
- Apna `dealskoti-bot` repository select karo

### Step 3: PostgreSQL Database Add Karo
- Project mein "New" → "Database" → "Add PostgreSQL"
- Railway automatically `DATABASE_URL` set kar dega

### Step 4: Environment Variables Set Karo
- Project settings → "Variables" tab mein jao
- Ye sab add karo:

| Variable | Value |
|----------|-------|
| `BOT_TOKEN` | Tera bot token |
| `API_ID` | Telegram API ID |
| `API_HASH` | Telegram API Hash |
| `OWNER_ID` | Tera Telegram User ID |
| `DATABASE_URL` | PostgreSQL → `${{Postgres.DATABASE_URL}}` (Railway auto-fill karta hai) |
| `RAZORPAY_KEY_ID` | rzp_test_... ya rzp_live_... |
| `RAZORPAY_KEY_SECRET` | Razorpay secret key |
| `RAZORPAY_WEBHOOK_SECRET` | Razorpay webhook secret |
| `FERNET_KEY` | Generated Fernet key |

### Step 5: Razorpay Webhook Set Karo
1. Razorpay Dashboard → Settings → Webhooks → Add New
2. URL: `https://YOUR-APP.railway.app/webhook`
3. Events: `payment.captured` aur `payment_link.paid` select karo
4. Secret: jo bhi `RAZORPAY_WEBHOOK_SECRET` mein dala hai

### Step 6: Deploy!
- Railway automatically deploy kar dega
- Logs mein "Bot polling shuru ho raha hai" dikhega

---

## Admin Commands (Sirf Owner ke liye)

| Command | Kaam |
|---------|------|
| `/admin` | Dashboard — users, revenue |
| `/users` | All users list with status |
| `/broadcast` | Saare users ko message bhejo |
| `/give <id> <days>` | Kisi ko free days do |
| `/ban <id>` | User ban karo |
| `/unban <id>` | Unban karo |
| `/check <id>` | User ki full details |
| `/revenue` | Revenue report |
| `/expiring` | Agle 5 din mein expire wale users |

## User Commands

| Command | Kaam |
|---------|------|
| `/start` | Main menu |
| `/login` | Telegram account se login |
| `/logout` | Logout |
| `/groups` | Groups manage karo |
| `/status` | Forwarding status |
| `/startall` | Saare groups start |
| `/stopall` | Saare groups band |
| `/subscribe` | ₹69/month subscription |
| `/help` | Full help guide |

---

## Environment Variables — Full List

```
BOT_TOKEN           — BotFather se milta hai
API_ID              — my.telegram.org se
API_HASH            — my.telegram.org se
OWNER_ID            — Apna Telegram user ID (userinfobot se pata karo)
DATABASE_URL        — PostgreSQL connection string
RAZORPAY_KEY_ID     — Razorpay API key ID
RAZORPAY_KEY_SECRET — Razorpay API secret
RAZORPAY_WEBHOOK_SECRET — Razorpay webhook secret
FERNET_KEY          — Session encryption key
PORT                — Railway auto set karta hai (default 8080)
```

---

## Apna Telegram User ID Kaise Pata Kare
Telegram pe [@userinfobot](https://t.me/userinfobot) ko `/start` bhejo — ye tera ID batayega.

## API ID aur API Hash Kaise Lein
1. [my.telegram.org](https://my.telegram.org) pe jao
2. Login karo phone number se
3. "API Development Tools" → "Create new application"
4. `App api_id` aur `App api_hash` copy karo
