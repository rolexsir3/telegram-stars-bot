#!/usr/bin/env python3
"""Sell private-channel access via Telegram Stars and Crypto (NOWPayments)."""
from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv()

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update,
)
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationBuilder, CallbackQueryHandler, ChatMemberHandler, CommandHandler,
    ContextTypes, MessageHandler, PreCheckoutQueryHandler, filters,
)
from nowpayments import NowPayments

# ---------------------------------------------------------------- CONFIG
TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
DB_PATH = os.getenv("DB_PATH", "access.db")
SUPPORT = os.getenv("SUPPORT_CONTACT", "@your_username")
INVITE_TTL_MIN = int(os.getenv("INVITE_TTL_MIN", "30"))
CHANNEL_NAME = os.getenv("CHANNEL_NAME", "Private Channel")

# Crypto Config
NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY", "")
NOWPAYMENTS_SANDBOX = os.getenv("NOWPAYMENTS_SANDBOX", "true").lower() == "true"
CRYPTO_PRICE_USD = float(os.getenv("CRYPTO_PRICE_USD", "20"))
CRYPTO_CURRENCIES = os.getenv("CRYPTO_CURRENCIES", "btc,eth,usdttrc20,ltc").split(",")

nowpayments = NowPayments(NOWPAYMENTS_API_KEY, sandbox=NOWPAYMENTS_SANDBOX) if NOWPAYMENTS_API_KEY else None

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("stars-bot")

# ---------------------------------------------------------------- PLANS
@dataclass(frozen=True)
class Plan:
    id: str
    title: str
    stars: int
    days: int | None
    description: str
    recurring: bool = False

PLANS: dict[str, Plan] = {
    "life": Plan("life", "Lifetime Access", 1000, None,
                 f"Permanent access to {CHANNEL_NAME}. One payment, forever."),
}

# ---------------------------------------------------------------- STORAGE
DB = sqlite3.connect(DB_PATH, check_same_thread=False)
DB.row_factory = sqlite3.Row
DB.executescript("""
CREATE TABLE IF NOT EXISTS access (
    user_id    INTEGER PRIMARY KEY,
    expires_at INTEGER,
    reminded   INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    charge_id  TEXT PRIMARY KEY,
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
CREATE TABLE IF NOT EXISTS crypto_payments (
    payment_id    TEXT PRIMARY KEY,
    user_id       INTEGER NOT NULL,
    order_id      TEXT NOT NULL UNIQUE,
    pay_currency  TEXT NOT NULL,
    pay_amount    REAL NOT NULL,
    pay_address   TEXT NOT NULL,
    price_usd     REAL NOT NULL,
    status        TEXT NOT NULL DEFAULT 'waiting',
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exp ON access(expires_at);
CREATE INDEX IF NOT EXISTS idx_crypto_status ON crypto_payments(status);
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


# ---------------------------------------------------------------- HELPERS
def menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Pay with Stars — ⭐ 1000", callback_data="buy:stars")],
        [InlineKeyboardButton(f"🪙 Pay with Crypto — ${CRYPTO_PRICE_USD:.0f}", callback_data="buy:crypto")],
        [InlineKeyboardButton("📋 My access", callback_data="status")],
    ])


def is_admin(uid: int) -> bool:
    return uid in ADMINS


async def kick(bot, user_id: int) -> None:
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


# ---------------------------------------------------------------- COMMANDS
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_photo_id = os.getenv("WELCOME_PHOTO_ID", "")

    if welcome_photo_id:
        try:
            await update.effective_message.reply_photo(
                photo=welcome_photo_id,
                caption=(
                    f"👋 <b>Access to {CHANNEL_NAME}</b>\n\n"
                    "Choose your preferred payment method below. Your invite link arrives instantly.\n\n"
                    f"Questions? Contact: {SUPPORT}"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=menu_kb(),
            )
            return
        except TelegramError as e:
            log.warning("Failed to send photo: %s", e)

    await update.effective_message.reply_text(
        f"👋 <b>Access to {CHANNEL_NAME}</b>\n\n"
        "Choose your preferred payment method below. Your invite link arrives instantly.\n\n"
        f"Questions? Contact: {SUPPORT}",
        parse_mode=ParseMode.HTML,
        reply_markup=menu_kb(),
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    row = q1("SELECT expires_at FROM access WHERE user_id=?", (uid,))
    text = (
        f"✅ <b>Access active</b>\nExpires: {fmt_exp(row['expires_at'])}"
        if row else "🔒 You don't have active access."
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=menu_kb())


async def cmd_paysupport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(f"💬 Payment support: {SUPPORT}")


# ---------------------------------------------------------------- BUYING
async def on_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id

    # Handle "My access" button
    if query.data == "status":
        await query.answer()
        row = q1("SELECT expires_at FROM access WHERE user_id=?", (uid,))
        await query.message.reply_text(
            f"✅ Access until {fmt_exp(row['expires_at'])}" if row else "🔒 No active access.",
            reply_markup=menu_kb(),
        )
        return

    # Handle "Back" button
    if query.data == "back":
        await query.answer()
        await query.message.reply_text(
            f"👋 <b>Access to {CHANNEL_NAME}</b>\n\nChoose your payment method:",
            parse_mode=ParseMode.HTML,
            reply_markup=menu_kb(),
        )
        return

    # Handle "buy:stars" or "buy:crypto"
    if ":" not in query.data:
        await query.answer()
        return

    action = query.data.split(":", 1)[1]

    if action == "stars":
        plan = PLANS["life"]
        await query.answer()
        payload = f"life:{uid}:{secrets.token_hex(4)}"

        try:
            await context.bot.send_invoice(
                chat_id=uid,
                title=f"{plan.title} — {CHANNEL_NAME}",
                description=plan.description,
                payload=payload,
                currency="XTR",
                provider_token="",
                prices=[LabeledPrice(plan.title, plan.stars)],
            )
        except TelegramError as e:
            log.error("send_invoice failed: %s", e)
            await query.message.reply_text("⚠️ Couldn't create the invoice. Try again.")

    elif action == "crypto":
        await query.answer()
        if not nowpayments:
            await query.message.reply_text(
                "⚠️ Crypto payments are not configured. Please use Stars instead."
            )
            return

        buttons = []
        currency_names = {
            "btc": "Bitcoin (BTC)",
            "eth": "Ethereum (ETH)",
            "usdttrc20": "USDT (TRC20)",
            "ltc": "Litecoin (LTC)",
            "bnb": "BNB",
            "sol": "Solana (SOL)",
        }
        for curr in CRYPTO_CURRENCIES:
            curr = curr.strip()
            name = currency_names.get(curr, curr.upper())
            buttons.append([InlineKeyboardButton(name, callback_data=f"crypto:{curr}")])

        buttons.append([InlineKeyboardButton("« Back", callback_data="back")])

        await query.message.reply_text(
            f"🪙 <b>Pay with Crypto — ${CRYPTO_PRICE_USD:.0f}</b>\n\n"
            "Select your preferred cryptocurrency:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )


async def on_pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pcq = update.pre_checkout_query
    try:
        plan_id, uid, _nonce = pcq.invoice_payload.split(":")
        plan = PLANS[plan_id]
        if int(uid) != pcq.from_user.id or pcq.currency != "XTR" or pcq.total_amount != plan.stars:
            raise ValueError("Validation failed")
    except Exception:
        await pcq.answer(ok=False, error_message="This payment link is invalid. Start over with /start.")
        return
    await pcq.answer(ok=True)


async def on_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    sp = msg.successful_payment
    uid = update.effective_user.id
    plan_id, payload_uid, _ = sp.invoice_payload.split(":")
    plan = PLANS.get(plan_id)

    if not plan or int(payload_uid) != uid:
        await msg.reply_text(f"⚠️ Payment received but couldn't be matched. Contact {SUPPORT}.")
        return

    first = x(
        "INSERT OR IGNORE INTO payments (charge_id,user_id,plan_id,stars,recurring,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (sp.telegram_payment_charge_id, uid, plan.id, sp.total_amount,
         int(bool(getattr(sp, "is_recurring", False))), now())
    )
    if not first:
        return

    expires_at = grant(uid, plan, sp)
    log.info("PAID uid=%s plan=%s stars=%s exp=%s", uid, plan.id, sp.total_amount, expires_at)

    if await in_channel(context.bot, uid):
        await msg.reply_text(
            f"🎉 <b>Payment received — access extended!</b>\nExpires: {fmt_exp(expires_at)}\n\n"
            "You're already in the channel.",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        link = await context.bot.create_chat_invite_link(
            CHANNEL_ID,
            name=f"u{uid}",
            member_limit=1,
            expire_date=datetime.now(timezone.utc) + timedelta(minutes=INVITE_TTL_MIN),
        )
    except TelegramError:
        await msg.reply_text(
            f"✅ Payment received, but I couldn't create your invite link. Contact {SUPPORT}."
        )
        return

    x("INSERT OR REPLACE INTO invites (link,user_id,created_at) VALUES (?,?,?)",
      (link.invite_link, uid, now()))
    await msg.reply_text(
        f"🎉 <b>Payment received!</b>\n\n"
        f"Expires: <b>{fmt_exp(expires_at)}</b>\n\n"
        f"👉 {link.invite_link}\n\n"
        f"⚠️ This link works <b>once</b> and expires in {INVITE_TTL_MIN} minutes.",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


def grant(uid: int, plan: Plan, sp) -> int | None:
    row = q1("SELECT expires_at FROM access WHERE user_id=?", (uid,))
    current = row["expires_at"] if row else -1

    if current is None or plan.days is None:
        new_exp = None
    else:
        base = max(now(), current) if current not in (-1, None) else now()
        new_exp = base + plan.days * 86400

    if sp is not None:
        sub_exp = getattr(sp, "subscription_expiration_date", None)
        if sub_exp is not None:
            new_exp = int(sub_exp.timestamp())

    x(
        "INSERT INTO access (user_id,expires_at,reminded,updated_at) VALUES (?,?,0,?) "
        "ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at, "
        "reminded=0, updated_at=excluded.updated_at",
        (uid, new_exp, now())
    )
    return new_exp


# ---------------------------------------------------------------- CRYPTO
async def on_crypto_currency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    currency = query.data.split(":", 1)[1]
    await query.answer()

    if not nowpayments:
        await query.message.reply_text("⚠️ Crypto payments not configured.")
        return

    order_id = f"u{uid}_{int(time.time())}_{secrets.token_hex(3)}"
    try:
        payment = nowpayments.create_payment(
            price_usd=CRYPTO_PRICE_USD,
            pay_currency=currency,
            order_id=order_id,
            description=f"Lifetime access to {CHANNEL_NAME}",
        )
    except Exception as e:
        log.error("Failed to create crypto payment: %s", e)
        await query.message.reply_text(
            "⚠️ Couldn't create crypto invoice. Please try again or use Stars."
        )
        return

    x(
        "INSERT INTO crypto_payments "
        "(payment_id,user_id,order_id,pay_currency,pay_amount,pay_address,price_usd,status,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (payment["payment_id"], uid, order_id, currency, payment["pay_amount"],
         payment["pay_address"], CRYPTO_PRICE_USD, "waiting", now(), now())
    )

    await query.message.reply_text(
        f"🪙 <b>Crypto Payment</b>\n\n"
        f"<b>Amount:</b> <code>{payment['pay_amount']}</code> {payment['pay_currency'].upper()}\n"
        f"<b>Address:</b> <code>{payment['pay_address']}</code>\n\n"
        f"⚠️ <b>Send EXACTLY this amount to this address.</b>\n"
        f"⏳ Payment expires in 60 minutes. I'll notify you when confirmed.",
        parse_mode=ParseMode.HTML,
    )


async def poll_crypto_payments(context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot
    pending = q(
        "SELECT * FROM crypto_payments WHERE status IN ('waiting','confirming') AND created_at > ?",
        (now() - 7200,)
    )

    for row in pending:
        try:
            status_data = nowpayments.get_payment_status(row["payment_id"])
            new_status = status_data.get("payment_status", "unknown")
            x(
                "UPDATE crypto_payments SET status=?, updated_at=? WHERE payment_id=?",
                (new_status, now(), row["payment_id"])
            )

            if new_status == "finished":
                uid = row["user_id"]
                plan = PLANS["life"]
                expires_at = grant(uid, plan, None)

                if await in_channel(bot, uid):
                    await bot.send_message(
                        uid,
                        f"🎉 <b>Crypto payment confirmed!</b>\n\nYou already have access.",
                        parse_mode=ParseMode.HTML,
                    )
                else:
                    try:
                        link = await bot.create_chat_invite_link(
                            CHANNEL_ID,
                            name=f"crypto{uid}",
                            member_limit=1,
                            expire_date=datetime.now(timezone.utc) + timedelta(minutes=INVITE_TTL_MIN),
                        )
                        await bot.send_message(
                            uid,
                            f"🎉 <b>Crypto payment confirmed!</b>\n\n"
                            f"👉 {link.invite_link}\n\n"
                            f"⚠️ Works once, expires in {INVITE_TTL_MIN} min.",
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True,
                        )
                    except TelegramError:
                        await bot.send_message(
                            uid,
                            f"✅ Payment confirmed but couldn't create invite link. Contact {SUPPORT}."
                        )
                log.info("CRYPTO PAID uid=%s amount=%s %s", uid, row["pay_amount"], row["pay_currency"])

            elif new_status in ("failed", "expired", "refunded"):
                await bot.send_message(
                    row["user_id"],
                    f"⚠️ Your crypto payment was {new_status}. Please try again or use Stars."
                )
        except Exception as e:
            log.error("Error polling crypto payment %s: %s", row["payment_id"], e)


# ---------------------------------------------------------------- CHAT MEMBER & JOBS
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
        return

    buyer, joiner = row["user_id"], cm.new_chat_member.user.id
    x("DELETE FROM invites WHERE link=?", (link,))
    if buyer != joiner:
        log.warning("link shared: buyer=%s joiner=%s — removing", buyer, joiner)
        await kick(context.bot, joiner)
        try:
            await context.bot.send_message(
                buyer,
                f"⚠️ Your invite link was used by someone else and was revoked. Contact {SUPPORT}."
            )
        except TelegramError:
            pass


async def sweep(context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot
    t = now()
    for r in q("SELECT user_id FROM access WHERE expires_at IS NOT NULL AND expires_at<=?", (t,)):
        uid = r["user_id"]
        x("DELETE FROM access WHERE user_id=?", (uid,))
        await kick(bot, uid)
        try:
            await bot.send_message(uid, "⌛️ Your access has expired. Renew any time:", reply_markup=menu_kb())
        except TelegramError:
            pass

    for r in q(
        "SELECT user_id,expires_at FROM access "
        "WHERE expires_at IS NOT NULL AND reminded=0 AND expires_at BETWEEN ? AND ?",
        (t, t + 86400)
    ):
        uid = r["user_id"]
        x("UPDATE access SET reminded=1 WHERE user_id=?", (uid,))
        try:
            await bot.send_message(
                uid,
                f"⏰ Access expires {fmt_exp(r['expires_at'])}. Renew to stay in:",
                reply_markup=menu_kb()
            )
        except TelegramError:
            pass
    x("DELETE FROM invites WHERE created_at < ?", (t - 86400,))


# ---------------------------------------------------------------- ADMIN
async def cmd_grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    try:
        uid, arg = int(context.args[0]), context.args[1]
        plan = Plan("admin", "admin", 0, None if arg == "life" else int(arg), "manual grant")
    except (IndexError, ValueError):
        await update.effective_message.reply_text("Usage: /grant <user_id> <days|life>")
        return
    exp = grant(uid, plan, None)
    if not await in_channel(context.bot, uid):
        link = await context.bot.create_chat_invite_link(
            CHANNEL_ID,
            name=f"grant{uid}",
            member_limit=1,
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
        row = q1(
            "SELECT * FROM payments WHERE user_id=? AND refunded=0 ORDER BY created_at DESC LIMIT 1",
            (uid,)
        )
    if not row:
        await update.effective_message.reply_text("No refundable payment found.")
        return

    try:
        ok = await context.bot.refund_star_payment(
            user_id=row["user_id"],
            telegram_payment_charge_id=row["charge_id"]
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
                row["user_id"],
                f"↩️ Refunded ⭐ {row['stars']}. Access revoked."
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


async def cmd_cryptostats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    total = q1(
        "SELECT COUNT(*) c, COALESCE(SUM(price_usd),0) s FROM crypto_payments WHERE status='finished'"
    )
    pending = q1(
        "SELECT COUNT(*) c FROM crypto_payments WHERE status IN ('waiting','confirming')"
    )
    await update.effective_message.reply_text(
        f"🪙 <b>Crypto Stats</b>\n\n"
        f"✅ Completed: {total['c']} (${total['s']:.2f})\n"
        f"⏳ Pending: {pending['c']}",
        parse_mode=ParseMode.HTML,
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled error", exc_info=context.error)


# ---------------------------------------------------------------- MAIN
def main():
    if not TOKEN:
        log.error("BOT_TOKEN is not set. Check your .env file.")
        return
    if CHANNEL_ID == 0:
        log.error("CHANNEL_ID is not set. Check your .env file.")
        return

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler(["status", "myaccess"], cmd_status))
    app.add_handler(CommandHandler(["paysupport", "support"], cmd_paysupport))
    app.add_handler(CommandHandler("grant", cmd_grant))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("refund", cmd_refund))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("cryptostats", cmd_cryptostats))

    # Fixed pattern - removed $ to match "buy:stars", "buy:crypto", etc.
    app.add_handler(CallbackQueryHandler(on_buy, pattern=r"^(buy:|status|back)"))
    app.add_handler(CallbackQueryHandler(on_crypto_currency, pattern=r"^crypto:"))
    app.add_handler(PreCheckoutQueryHandler(on_pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, on_successful_payment))
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_error_handler(on_error)

    app.job_queue.run_repeating(sweep, interval=300, first=15, name="sweep")
    if nowpayments:
        app.job_queue.run_repeating(poll_crypto_payments, interval=30, first=30, name="crypto_poll")

    log.info("Bot running. Channel=%s", CHANNEL_ID)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
