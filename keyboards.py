"""
All inline keyboards for the main bot.
Callback data prefix reference:
  mm        — main menu
  dm        — delete message
  st        — status menu
  fst       — forwarding status
  sst       — subscription status
  hl        — quick help
  tl        — task list
  ng        — new task
  t:<id>    — task detail
  tst:<id>  — task start
  tsp:<id>  — task stop
  td:<id>   — task delete
  tdc:<id>  — task delete confirm
  tr:<id>   — task rename prompt
  src:<id>  — set source for task
  tgt:<id>  — set target for task
  fi:<id>   — filters menu for task
  ms:<id>   — message settings for task
  as:<id>   — advanced settings for task
  wm:<id>   — watermark settings for task
  lr:<id>   — link replacer menu for task
  sa        — start all confirm screen
  xa        — stop all confirm screen
  sac       — start all confirm yes
  xac       — stop all confirm yes
  scx       — cancel (generic)
  pl        — plans page
  pp:<plan>:<billing> — plan purchase
  ref       — refer & earn
  rws       — withdraw request start
  rst       — refer stats
  sub       — subscribe (alias)
  lg        — logout confirm
  lgc       — logout confirm yes
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def kb_main(plan: str = "free", days_left: int = 0) -> InlineKeyboardMarkup:
    from config import plan_display_name
    plan_label = plan_display_name(plan)
    status_text = f"{plan_label}" + (f" | {days_left}d left" if days_left > 0 else "")

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton(f"📊 {status_text}", callback_data="sst"),
    )
    kb.add(
        InlineKeyboardButton("📋 Manage Tasks", callback_data="tl"),
        InlineKeyboardButton("📡 Status", callback_data="st"),
    )
    kb.add(
        InlineKeyboardButton("▶️ Start All", callback_data="sa"),
        InlineKeyboardButton("⏹ Stop All", callback_data="xa"),
    )
    kb.add(
        InlineKeyboardButton("💳 Plans", callback_data="pl"),
        InlineKeyboardButton("❓ Help", callback_data="hl"),
    )
    kb.add(
        InlineKeyboardButton("👥 Refer & Earn", callback_data="ref"),
    )
    return kb


def kb_main_only() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🏠 Main Menu", callback_data="mm"))
    return kb


def kb_login() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔐 Login Karo", callback_data="do_login"))
    kb.add(InlineKeyboardButton("❓ Help", callback_data="hl"))
    return kb


def kb_status_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📡 Forwarding Status", callback_data="fst"),
        InlineKeyboardButton("💳 Subscription Status", callback_data="sst"),
        InlineKeyboardButton("🏠 Main Menu", callback_data="mm"),
    )
    return kb


def kb_task_list(tasks: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    for t in tasks:
        icon = "🟢" if t["active"] else "🔴"
        kb.add(InlineKeyboardButton(
            f"{icon} {t['name']}",
            callback_data=f"t:{t['id']}"
        ))
    kb.add(InlineKeyboardButton("➕ New Task", callback_data="ng"))
    kb.add(InlineKeyboardButton("🏠 Main Menu", callback_data="mm"))
    return kb


def kb_task(task_id: int, is_active: bool, plan: str) -> InlineKeyboardMarkup:
    from config import has_feature
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📥 Set Source", callback_data=f"src:{task_id}"),
        InlineKeyboardButton("📤 Set Target", callback_data=f"tgt:{task_id}"),
    )
    if is_active:
        kb.add(InlineKeyboardButton("⏹ Stop", callback_data=f"tsp:{task_id}"))
    else:
        kb.add(InlineKeyboardButton("▶️ Start", callback_data=f"tst:{task_id}"))
    kb.add(
        InlineKeyboardButton("✏️ Rename", callback_data=f"tr:{task_id}"),
        InlineKeyboardButton("🗑️ Delete", callback_data=f"td:{task_id}"),
    )
    kb.add(
        InlineKeyboardButton("⚙️ Filters", callback_data=f"fi:{task_id}"),
        InlineKeyboardButton("🛠️ Settings", callback_data=f"ms:{task_id}"),
    )
    kb.add(InlineKeyboardButton("🔙 Tasks", callback_data="tl"))
    kb.add(InlineKeyboardButton("🏠 Main Menu", callback_data="mm"))
    return kb


def kb_task_delete_confirm(task_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Haan Delete", callback_data=f"tdc:{task_id}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"t:{task_id}"),
    )
    return kb


def kb_filters(task_id: int, plan: str) -> InlineKeyboardMarkup:
    from config import has_feature
    kb = InlineKeyboardMarkup(row_width=2)

    # Media filter — Basic+
    if has_feature(plan, "media_filter"):
        kb.add(InlineKeyboardButton("🖼️ Media Filter", callback_data=f"mf:{task_id}"))

    # Pro+ features
    if has_feature(plan, "blacklist"):
        kb.add(
            InlineKeyboardButton("🚫 Blacklist", callback_data=f"bl:{task_id}"),
            InlineKeyboardButton("✅ Whitelist", callback_data=f"wl:{task_id}"),
        )
    if has_feature(plan, "replace_words"):
        kb.add(InlineKeyboardButton("🔄 Word Replace", callback_data=f"wr:{task_id}"))
    if has_feature(plan, "link_replacer"):
        kb.add(InlineKeyboardButton("🔗 Link Replacer", callback_data=f"lr:{task_id}"))

    # Business+
    if has_feature(plan, "regex_filter"):
        kb.add(InlineKeyboardButton("🔤 Regex Filter", callback_data=f"rx:{task_id}"))

    kb.add(InlineKeyboardButton("🔙 Back", callback_data=f"t:{task_id}"))
    return kb


def kb_message_settings(task_id: int, plan: str) -> InlineKeyboardMarkup:
    from config import has_feature
    kb = InlineKeyboardMarkup(row_width=2)
    if has_feature(plan, "header_footer"):
        kb.add(
            InlineKeyboardButton("📝 Header", callback_data=f"hd:{task_id}"),
            InlineKeyboardButton("📝 Footer", callback_data=f"ft:{task_id}"),
        )
        kb.add(
            InlineKeyboardButton("💬 Caption", callback_data=f"cp:{task_id}"),
            InlineKeyboardButton("🔗 Remove Links", callback_data=f"rl:{task_id}"),
        )
    if has_feature(plan, "image_watermark"):
        kb.add(InlineKeyboardButton("🎨 Watermark", callback_data=f"wm:{task_id}"))
    kb.add(InlineKeyboardButton("⚙️ Advanced", callback_data=f"as:{task_id}"))
    kb.add(InlineKeyboardButton("🔙 Back", callback_data=f"t:{task_id}"))
    return kb


def kb_advanced_settings(task_id: int, plan: str, task) -> InlineKeyboardMarkup:
    from config import has_feature
    kb = InlineKeyboardMarkup(row_width=2)
    if has_feature(plan, "delay"):
        kb.add(InlineKeyboardButton("⏱️ Delay", callback_data=f"dl:{task_id}"))
    if has_feature(plan, "skip_duplicates"):
        sd_icon = "✅" if task.skip_duplicates else "❌"
        kb.add(InlineKeyboardButton(f"{sd_icon} Skip Duplicates", callback_data=f"sd:{task_id}"))
    if has_feature(plan, "pinned_only"):
        po_icon = "✅" if task.pinned_only else "❌"
        kb.add(InlineKeyboardButton(f"{po_icon} Pinned Only", callback_data=f"po:{task_id}"))
    if has_feature(plan, "schedule"):
        kb.add(InlineKeyboardButton("📅 Schedule", callback_data=f"sc:{task_id}"))
    kb.add(InlineKeyboardButton("🔙 Back", callback_data=f"ms:{task_id}"))
    return kb


def kb_media_filter(task_id: int, mf: dict) -> InlineKeyboardMarkup:
    defaults = {"images": True, "videos": True, "documents": True,
                "audio": True, "stickers": False, "links": True}
    mf = mf or defaults

    def icon(key):
        return "✅" if mf.get(key, defaults[key]) else "❌"

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton(f"{icon('images')} Images", callback_data=f"mft:images:{task_id}"),
        InlineKeyboardButton(f"{icon('videos')} Videos", callback_data=f"mft:videos:{task_id}"),
    )
    kb.add(
        InlineKeyboardButton(f"{icon('documents')} Docs", callback_data=f"mft:documents:{task_id}"),
        InlineKeyboardButton(f"{icon('audio')} Audio", callback_data=f"mft:audio:{task_id}"),
    )
    kb.add(
        InlineKeyboardButton(f"{icon('stickers')} Stickers", callback_data=f"mft:stickers:{task_id}"),
        InlineKeyboardButton(f"{icon('links')} Links", callback_data=f"mft:links:{task_id}"),
    )
    kb.add(InlineKeyboardButton("✅ Save", callback_data=f"fi:{task_id}"))
    return kb


def kb_plans() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("⭐ Basic — $1/mo | $10/yr", callback_data="pp:basic:choose"),
        InlineKeyboardButton("💎 Pro — $2/mo | $20/yr", callback_data="pp:pro:choose"),
        InlineKeyboardButton("🚀 Business — $5/mo | $45/yr", callback_data="pp:business:choose"),
    )
    kb.add(InlineKeyboardButton("🏠 Main Menu", callback_data="mm"))
    return kb


def kb_plan_billing(plan: str) -> InlineKeyboardMarkup:
    from config import PLAN_INFO
    info = PLAN_INFO[plan]
    monthly = info["monthly_usd"]
    annual = info["annual_usd"]
    monthly_inr = info["monthly_inr"]
    annual_inr = info["annual_inr"]
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton(
            f"📅 Monthly — ${monthly} (₹{monthly_inr})",
            callback_data=f"pp:{plan}:monthly"
        ),
        InlineKeyboardButton(
            f"📆 Annual — ${annual} (₹{annual_inr}) 🎉 2 Months FREE",
            callback_data=f"pp:{plan}:annual"
        ),
    )
    kb.add(InlineKeyboardButton("🔙 Plans", callback_data="pl"))
    return kb


def kb_payment_link(url: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("💳 Pay Now", url=url))
    kb.add(InlineKeyboardButton("🏠 Main Menu", callback_data="mm"))
    return kb


def kb_startall_confirm() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("▶️ Haan, Start Karo", callback_data="sac"),
        InlineKeyboardButton("❌ Cancel", callback_data="scx"),
    )
    return kb


def kb_stopall_confirm() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("⏹ Haan, Band Karo", callback_data="xac"),
        InlineKeyboardButton("❌ Cancel", callback_data="scx"),
    )
    return kb


def kb_logout_confirm() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Haan Logout", callback_data="lgc"),
        InlineKeyboardButton("❌ Nahi", callback_data="mm"),
    )
    return kb


def kb_refer(code: str, balance: float, earned: float, referred: int, bot_username: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("💰 Withdraw", callback_data="rws"),
        InlineKeyboardButton("📊 Stats", callback_data="rst"),
    )
    kb.add(InlineKeyboardButton("🏠 Main Menu", callback_data="mm"))
    return kb


def kb_withdraw_method() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📱 UPI", callback_data="rwm:upi"),
        InlineKeyboardButton("🏦 Bank / NEFT", callback_data="rwm:bank"),
    )
    kb.add(InlineKeyboardButton("❌ Cancel", callback_data="ref"))
    return kb


def kb_withdraw_confirm(amount: float) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton(f"✅ Confirm Withdraw ${amount}", callback_data="rwc"),
        InlineKeyboardButton("❌ Cancel", callback_data="ref"),
    )
    return kb


def kb_admin_withdrawal(wr_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Done (Paid)", callback_data=f"wrd:{wr_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"wrr:{wr_id}"),
    )
    return kb


def kb_confirm_broadcast() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Send", callback_data="bc_confirm"),
        InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel"),
    )
    return kb


def kb_simi_contact_admin() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📩 Admin ko Message Bhejo", callback_data="ca"))
    return kb


def kb_simi_confirm_admin_msg() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Bhejo", callback_data="ca_confirm"),
        InlineKeyboardButton("❌ Cancel", callback_data="ca_cancel"),
    )
    return kb
