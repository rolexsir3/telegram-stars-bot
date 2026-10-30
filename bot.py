#!/usr/bin/env python3
"""Sell private-channel access for Telegram Stars (XTR)."""
from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update,
)
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationBuilder, CallbackQueryHandler, ChatMemberHandler, CommandHandler,
    ContextTypes, MessageHandler, PreCheckoutQueryHandler, filters,
)

# ---------------------------------------------------------------- config
TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = int(os.environ["CHANNEL_ID"])
ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
DB_PATH = os.getenv("DB_PATH", "access.db")
SUPPORT = os.getenv("SUPPORT_CONTACT", "@your_username")
INVITE_TTL_MIN = int(os.getenv("INVITE_TTL_MIN", "30"))
CHANNEL_NAME = os.getenv("CHANNEL_NAME", "the private channel")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("stars-bot")


@dataclass(frozen=True)
class Plan:
    id: str
    title: str
    stars: int
    days: int | None          # None = lifetime
    description: str
    recurring: bool = False   # True -> native Stars subscription (30 days only)


PLANS: dict[str, Plan] = {
    "life": Plan("life", "Lifetime Access", 1000, None,
                 "Permanent access to " + CHANNEL_NAME + ". One time payment, Lifetime access."),
    # To auto-renew instead of expiring, use:
    # "sub": Plan("sub", "Monthly subscription", 150, 30, "Renews every 30 days.",
    #             recurring=True),
}

# ---------------------------------------------------------------- storage
DB = sqlite3.connect(DB_PATH, check_same_thread=False)
DB.row_factory = sqlite3.Row
DB.executescript("""
CREATE TABLE IF NOT EXISTS access (
    user_id    INTEGER PRIMARY KEY,
    expires_at INTEGER,              -- NULL = lifetime
    reminded   INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    charge_id  TEXT PRIMARY KEY,     -- telegram_payment_charge_id (idempotency)
    user_id    INTEGER NOT NULL,
    plan_id    TEXT NOT NULL,
    stars      INTEGER NOT NULL,
    recurring  INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    refunded   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS invites (
    link       TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exp ON access(expires_at);
""")
DB.commit()


def q(sql: str, args=()) -> list[sqlite3.Row]:
    return DB.execute(sql, args).fetchall()


def q1(sql: str, args=()):
    return DB.execute(sql, args).fetchone()


def x(sql: str, args=()) -> int:
    cur = DB.execute(sql, args)
    DB.commit()
    return cur.rowcount


def now() -> int:
    return int(time.time())


def fmt_exp(ts: int | None) -> str:
    if ts is None:
        return "lifetime (never expires)"
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d %b %Y, %H:%M UTC")


# ---------------------------------------------------------------- helpers
def menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Get Lifetime Access — ⭐ 1000", callback_data="buy:life")],
        [InlineKeyboardButton("📋 My access", callback_data="status")],
    ])


def is_admin(uid: int) -> bool:
    return uid in ADMINS


async def kick(bot, user_id: int) -> None:
    """Remove from channel but allow rejoining later."""
    try:
        await bot.ban_chat_member(CHANNEL_ID, user_id)
        await bot.unban_chat_member(CHANNEL_ID, user_id, only_if_banned=True)
    except TelegramError as e:
        log.warning("kick %s failed: %s", user_id, e)


async def in_channel(bot, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(CHANNEL_ID, user_id)
        return m.status in (
            ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER, ChatMemberStatus.RESTRICTED,
        )
    except TelegramError:
        return False


# ---------------------------------------------------------------- commands
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_url = "https://pixhost.top/uploads/C6QeWZGFPSMO1FFqh6Ygqs1ikSm0R6qRLAV4l5b5.jpg"  # <-- Change this URL
    
    await update.effective_message.reply_photo(
        photo=photo_url,
        caption=(
            f"👋 <b>Access to {CHANNEL_NAME}</b>\n\n"
            "Pick a plan and pay with <b>Telegram Stars</b> — no cards, no crypto, "
            "paid right inside Telegram. Your invite link arrives instantly.\n\n"
            f"Questions or payment issues: {SUPPORT}"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=menu_kb(),
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    row = q1("SELECT expires_at FROM access WHERE user_id=?", (uid,))
    if not row:
        text = "🔒 You don't have active access."
    else:
        text = f"✅ <b>Access active</b>\nExpires: {fmt_exp(row['expires_at'])}"
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=menu_kb()
    )


async def cmd_paysupport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telegram requires a reachable support contact for paid goods."""
    await update.effective_message.reply_text(
        f"💬 Payment support: {SUPPORT}\n"
        "Send your Telegram ID and the payment receipt (Settings → Stars → "
        "Transactions) and we'll sort it out. Refunds available on request."
    )


# ---------------------------------------------------------------- buying
async def on_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id

    if query.data == "status":
        await query.answer()
        row = q1("SELECT expires_at FROM access WHERE user_id=?", (uid,))
        await query.message.reply_text(
            f"✅ Access until {fmt_exp(row['expires_at'])}" if row
            else "🔒 No active access.",
            reply_markup=menu_kb(),
        )
        return

    plan = PLANS.get(query.data.split(":", 1)[1])
    if not plan:
        await query.answer("Unknown plan.", show_alert=True)
        return
    await query.answer()

    payload = f"{plan.id}:{uid}:{secrets.token_hex(4)}"  # <=128 bytes
    kwargs = dict(
        chat_id=uid,
        title=f"{plan.title} — {CHANNEL_NAME}",
        description=plan.description,
        payload=payload,
        currency="XTR",              # Telegram Stars
        provider_token="",           # MUST be empty for Stars
        prices=[LabeledPrice(plan.title, plan.stars)],  # exactly ONE price
    )
    if plan.recurring:
        kwargs["subscription_period"] = 2592000  # 30 days, only allowed value

    try:
        await context.bot.send_invoice(**kwargs)
    except TypeError:
        log.error("subscription_period unsupported — upgrade python-telegram-bot")
        await query.message.reply_text("This plan is temporarily unavailable.")
    except TelegramError as e:
        log.error("send_invoice failed: %s", e)
        await query.message.reply_text("⚠️ Couldn't create the invoice. Try again.")


async def on_pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Must answer within 10 seconds. Re-validate everything server-side."""
    pcq = update.pre_checkout_query
    try:
        plan_id, uid, _nonce = pcq.invoice_payload.split(":")
        plan = PLANS[plan_id]
        if int(uid) != pcq.from_user.id:
            raise ValueError("payer mismatch")
        if pcq.currency != "XTR" or pcq.total_amount != plan.stars:
            raise ValueError("amount mismatch")
    except Exception as e:
        log.warning("pre_checkout rejected: %s", e)
        await pcq.answer(ok=False, error_message="This payment link is invalid. Please start over with /start.")
        return
    await pcq.answer(ok=True)


async def on_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    sp = msg.successful_payment
    uid = update.effective_user.id
    plan_id, payload_uid, _ = sp.invoice_payload.split(":")
    plan = PLANS.get(plan_id)
    if not plan or int(payload_uid) != uid:
        log.error("bad payload after payment: %s", sp.invoice_payload)
        await msg.reply_text(f"⚠️ Payment received but couldn't be matched. Contact {SUPPORT}.")
        return

    # idempotency: Telegram may retry the update
    first = x(
        "INSERT OR IGNORE INTO payments "
        "(charge_id,user_id,plan_id,stars,recurring,created_at) VALUES (?,?,?,?,?,?)",
        (sp.telegram_payment_charge_id, uid, plan.id, sp.total_amount,
         int(bool(getattr(sp, "is_recurring", False))), now()),
    )
    if not first:
        log.info("duplicate payment update %s ignored", sp.telegram_payment_charge_id)
        return

    expires_at = grant(uid, plan, sp)
    log.info("PAID uid=%s plan=%s stars=%s exp=%s", uid, plan.id, sp.total_amount, expires_at)

    if await in_channel(context.bot, uid):
        await msg.reply_text(
            f"🎉 <b>Payment received — access extended!</b>\n"
            f"Expires: {fmt_exp(expires_at)}\n\nYou're already in the channel.",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        link = await context.bot.create_chat_invite_link(
            CHANNEL_ID,
            name=f"u{uid}",
            member_limit=1,                                   # single use
            expire_date=datetime.now(timezone.utc) + timedelta(minutes=INVITE_TTL_MIN),
        )
    except TelegramError as e:
        log.error("invite creation failed: %s", e)
        await msg.reply_text(
            f"✅ Payment received, but I couldn't create your invite link. "
            f"Contact {SUPPORT} — you won't lose your access."
        )
        return

    x("INSERT OR REPLACE INTO invites (link,user_id,created_at) VALUES (?,?,?)",
      (link.invite_link, uid, now()))

    await msg.reply_text(
        f"🎉 <b>Payment received!</b>\n\n"
        f"Expires: <b>{fmt_exp(expires_at)}</b>\n\n"
        f"👉 {link.invite_link}\n\n"
        f"⚠️ This link works <b>once</b>, only for your account, "
        f"and expires in {INVITE_TTL_MIN} minutes. Don't share it.",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


def grant(uid: int, plan: Plan, sp) -> int | None:
    """Extend access; renewals stack on top of remaining time."""
    row = q1("SELECT expires_at FROM access WHERE user_id=?", (uid,))
    current = row["expires_at"] if row else -1  # -1 = no row

    if current is None:                     # already lifetime
        new_exp = None
    elif plan.days is None:
        new_exp = None
    else:
        base = max(now(), current) if current not in (-1, None) else now()
        new_exp = base + plan.days * 86400

    # Native subscription: trust Telegram's own expiration date
    sub_exp = getattr(sp, "subscription_expiration_date", None)
    if sub_exp is not None:
        new_exp = int(sub_exp.timestamp())

    x("INSERT INTO access (user_id,expires_at,reminded,updated_at) VALUES (?,?,0,?) "
      "ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at, "
      "reminded=0, updated_at=excluded.updated_at",
      (uid, new_exp, now()))
    return new_exp


# -------------------------------------------------- anti-sharing on join
async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cm = update.chat_member
    if not cm or cm.chat.id != CHANNEL_ID:
        return
    joined = cm.new_chat_member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED)
    was_out = cm.old_chat_member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED)
    if not (joined and was_out):
        return

    link = getattr(cm.invite_link, "invite_link", None)
    if not link:
        return
    row = q1("SELECT user_id FROM invites WHERE link=?", (link,))
    if not row:
        return  # link made manually by the owner — allow

    buyer, joiner = row["user_id"], cm.new_chat_member.user.id
    x("DELETE FROM invites WHERE link=?", (link,))
    if buyer != joiner:
        log.warning("link shared: buyer=%s joiner=%s — removing", buyer, joiner)
        await kick(context.bot, joiner)
        try:
            await context.bot.send_message(
                buyer, "⚠️ Your invite link was used by someone else and was revoked. "
                       f"Contact {SUPPORT} if this wasn't you."
            )
        except TelegramError:
            pass


# ---------------------------------------------------------------- expiry job
async def sweep(context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot
    t = now()

    # 1) expire
    for r in q("SELECT user_id FROM access WHERE expires_at IS NOT NULL AND expires_at<=?", (t,)):
        uid = r["user_id"]
        x("DELETE FROM access WHERE user_id=?", (uid,))
        await kick(bot, uid)
        try:
            await bot.send_message(
                uid, "⌛️ Your access has expired and you were removed from the channel.\n"
                     "Renew any time:", reply_markup=menu_kb()
            )
        except TelegramError:
            pass
        log.info("expired uid=%s", uid)

    # 2) remind 24h before
    for r in q("SELECT user_id,expires_at FROM access "
               "WHERE expires_at IS NOT NULL AND reminded=0 AND expires_at BETWEEN ? AND ?",
               (t, t + 86400)):
        uid = r["user_id"]
        x("UPDATE access SET reminded=1 WHERE user_id=?", (uid,))
        try:
            await bot.send_message(
                uid, f"⏰ Access expires {fmt_exp(r['expires_at'])}. Renew to stay in:",
                reply_markup=menu_kb()
            )
        except TelegramError:
            pass

    # 3) clean stale invites
    x("DELETE FROM invites WHERE created_at < ?", (t - 86400,))


# ---------------------------------------------------------------- admin
async def cmd_grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    try:
        uid = int(context.args[0])
        arg = context.args[1]
        plan = Plan("admin", "admin", 0, None if arg == "life" else int(arg), "manual grant")
    except (IndexError, ValueError):
        await update.effective_message.reply_text("Usage: /grant <user_id> <days|life>")
        return
    exp = grant(uid, plan, None)
    if not await in_channel(context.bot, uid):
        link = await context.bot.create_chat_invite_link(
            CHANNEL_ID, name=f"grant{uid}", member_limit=1,
            expire_date=datetime.now(timezone.utc) + timedelta(days=1),
        )
        x("INSERT OR REPLACE INTO invites (link,user_id,created_at) VALUES (?,?,?)",
          (link.invite_link, uid, now()))
        try:
            await context.bot.send_message(uid, f"🎁 Access granted!\n{link.invite_link}")
        except TelegramError:
            pass
    await update.effective_message.reply_text(f"Granted {uid} → {fmt_exp(exp)}")


async def cmd_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    try:
        uid = int(context.args[0])
    except (IndexError, ValueError):
        await update.effective_message.reply_text("Usage: /revoke <user_id>")
        return
    x("DELETE FROM access WHERE user_id=?", (uid,))
    await kick(context.bot, uid)
    await update.effective_message.reply_text(f"Revoked {uid}")


async def cmd_refund(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/refund <user_id> [charge_id] — refunds Stars and revokes access."""
    if not is_admin(update.effective_user.id):
        return
    try:
        uid = int(context.args[0])
    except (IndexError, ValueError):
        await update.effective_message.reply_text("Usage: /refund <user_id> [charge_id]")
        return

    if len(context.args) > 1:
        row = q1("SELECT * FROM payments WHERE charge_id=?", (context.args[1],))
    else:
        row = q1("SELECT * FROM payments WHERE user_id=? AND refunded=0 "
                 "ORDER BY created_at DESC LIMIT 1", (uid,))
    if not row:
        await update.effective_message.reply_text("No refundable payment found.")
        return

    try:
        ok = await context.bot.refund_star_payment(
            user_id=row["user_id"], telegram_payment_charge_id=row["charge_id"]
        )
    except TelegramError as e:
        await update.effective_message.reply_text(f"Refund failed: {e}")
        return

    if ok:
        x("UPDATE payments SET refunded=1 WHERE charge_id=?", (row["charge_id"],))
        x("DELETE FROM access WHERE user_id=?", (row["user_id"],))
        await kick(context.bot, row["user_id"])
        try:
            await context.bot.send_message(
                row["user_id"], f"↩️ Refunded ⭐ {row['stars']}. Access revoked."
            )
        except TelegramError:
            pass
        await update.effective_message.reply_text(f"Refunded ⭐ {row['stars']} to {row['user_id']}")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    active = q1("SELECT COUNT(*) c FROM access")["c"]
    rev = q1("SELECT COALESCE(SUM(stars),0) s, COUNT(*) c FROM payments WHERE refunded=0")
    await update.effective_message.reply_text(
        f"👥 Active: {active}\n💰 Sales: {rev['c']}\n⭐️ Stars earned: {rev['s']}"
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled error", exc_info=context.error)


# ---------------------------------------------------------------- main
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler(["status", "myaccess"], cmd_status))
    app.add_handler(CommandHandler(["paysupport", "support"], cmd_paysupport))
    app.add_handler(CommandHandler("grant", cmd_grant))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("refund", cmd_refund))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(on_buy, pattern=r"^(buy:|status)"))
    app.add_handler(PreCheckoutQueryHandler(on_pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, on_successful_payment))
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_error_handler(on_error)

    app.job_queue.run_repeating(sweep, interval=300, first=15, name="sweep")

    log.info("Bot running. Channel=%s", CHANNEL_ID)
    # chat_member updates are NOT delivered by default — must be requested
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
