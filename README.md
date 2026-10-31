# telegram-stars-bot

# 🤖 Telegram Stars Bot - Sell Channel Access

A simple Telegram bot that sells access to your private channel using Telegram Stars (Telegram's built-in virtual currency).

**What it does:**
- Users pay with Telegram Stars (no credit cards or external payment gateways needed)
- Bot automatically creates a private, single-use invite link
- User joins your private channel
- Bot tracks who has access and automatically removes them when it expires
- You can manually grant, revoke, or refund access with simple admin commands

---

## 📋 What You Need Before Starting

1. **A Telegram Bot Token** - Get it from [@BotFather](https://t.me/BotFather)
2. **A Private Telegram Channel** - That you own and manage
3. **Your Telegram User ID** - Get it from [@userinfobot](https://t.me/userinfobot)
4. **An Ubuntu VPS** (recommended for 24/7 hosting) - Or you can run it on your local computer for testing

---

## 🚀 Quick Start (Local Testing)

### Step 1: Get Your Bot Token
1. Open Telegram and search for [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts to name your bot
3. Copy the API token (looks like: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

### Step 2: Set Up Your Channel
1. Create a private channel in Telegram
2. Add your new bot as an **Administrator** with these permissions:
   - ✅ Invite Users via Link
   - ✅ Ban Users
3. Get your channel ID: Forward any message from your channel to [@userinfobot](https://t.me/userinfobot) and copy the ID (looks like: `-1001234567890`)

### Step 3: Get Your User ID
1. Open [@userinfobot](https://t.me/userinfobot) in Telegram
2. Send `/start`
3. Copy your numeric ID (looks like: `123456789`)

### Step 4: Download and Install
1. Download this repository as a ZIP file and extract it.
2. Open your terminal/command prompt in the extracted folder.

**Windows:**
```cmd
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

**Mac/Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 5: Configure the Bot
1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```
2. Open `.env` in a text editor (Notepad, VS Code, nano, etc.) and fill in your details:
   ```env
   BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
   CHANNEL_ID="-1001234567890"
   ADMIN_IDS="123456789"
   SUPPORT_CONTACT="@your_username"
   DB_PATH="access.db"
   INVITE_TTL_MIN="30"
   CHANNEL_NAME="My Premium Channel"
   ```
   *⚠️ Important: Always put quotes around values, especially if they contain spaces!*

### Step 6: Run the Bot
```bash
python bot.py
```
You should see: `INFO:application:Bot running. Channel=-1001234567890`

Open Telegram, find your bot, and send `/start` to test it!

---

## 🖥️ Deploy to Ubuntu VPS (24/7 Hosting)

### Step 1: Connect to Your VPS
```bash
ssh ubuntu@your-vps-ip
```

### Step 2: Install Dependencies
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git
```

### Step 3: Clone the Repository
```bash
cd ~
git clone https://github.com/YOUR_USERNAME/telegram-stars-bot.git
cd telegram-stars-bot
```
*(Replace `YOUR_USERNAME` with your actual GitHub username)*

### Step 4: Set Up Python Environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 5: Configure the Bot
```bash
cp .env.example .env
nano .env
```
Fill in your details (same format as Step 5 above). Save with `Ctrl+O`, `Enter`, then `Ctrl+X`.

### Step 6: Test It Locally First
```bash
python bot.py
```
Press `Ctrl+C` to stop after confirming it works.

### Step 7: Create a Systemd Service (Run in Background)
```bash
sudo nano /etc/systemd/system/telegram-bot.service
```
Paste this configuration:
```ini
[Unit]
Description=Telegram Stars Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/telegram-stars-bot
EnvironmentFile=/home/ubuntu/telegram-stars-bot/.env
Environment="PATH=/home/ubuntu/telegram-stars-bot/venv/bin"
ExecStart=/home/ubuntu/telegram-stars-bot/venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
Save with `Ctrl+O`, `Enter`, then `Ctrl+X`.

### Step 8: Start and Enable the Service
```bash
sudo systemctl daemon-reload
sudo systemctl enable telegram-bot
sudo systemctl start telegram-bot
```

### Step 9: Verify It's Running
```bash
sudo systemctl status telegram-bot
```
Look for `Active: active (running)` in green.

---

## 🎛️ Admin Commands

Send these commands directly to your bot in a private chat. They only work for User IDs listed in `ADMIN_IDS`.

| Command | What It Does | Example |
|---------|--------------|---------|
| `/grant` | Manually give someone access | `/grant 123456789 life` |
| `/revoke` | Remove someone's access and kick them | `/revoke 123456789` |
| `/refund` | Refund a payment and revoke access | `/refund 123456789` |
| `/stats` | View active users and total revenue | `/stats` |

*💡 Tip: To get any user's Telegram ID, forward one of their messages to [@userinfobot](https://t.me/userinfobot).*

---

## ⚙️ Configuration Guide

Edit the `.env` file to customize your bot:

```env
# Your bot token from BotFather
BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrsTUVwxyz"

# Your channel ID (must start with -100)
CHANNEL_ID="-1001234567890"

# Your Telegram user ID (comma-separated for multiple admins)
ADMIN_IDS="123456789,987654321"

# Support contact shown to users if they have issues
SUPPORT_CONTACT="@your_username"

# Database file location (use absolute path on VPS)
DB_PATH="/home/ubuntu/telegram-stars-bot/access.db"

# How long invite links remain valid (in minutes)
INVITE_TTL_MIN="30"

# Your channel name (shown in welcome messages)
CHANNEL_NAME="My Premium Channel"
```

---

## 💰 Changing the Price

Edit `bot.py` to change the price or plan details:

```bash
nano bot.py
```
Find the `PLANS` dictionary (around line 60):
```python
PLANS: dict[str, Plan] = {
    "life": Plan("life", "Lifetime Access", 1000, None,
                 "Permanent access to " + CHANNEL_NAME + ". One payment, forever."),
}
```
Change `1000` to your desired price in Telegram Stars. 

Save and restart the bot:
```bash
sudo systemctl restart telegram-bot
```

---

## 🖼️ Add a Welcome Photo

### Option 1: Use Telegram's Hosting (Recommended & Easiest)
1. Send your desired welcome image to [@RawDataBot](https://t.me/RawDataBot) in Telegram.
2. Copy the long `"file_id"` string from the bot's JSON response.
3. Edit `bot.py` and find the `cmd_start` function. Update it like this:
   ```python
   async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
       welcome_photo_id = "AgACAgIAAxkBAAIC..."  # Paste your file_id here
       
       await update.effective_message.reply_photo(
           photo=welcome_photo_id,
           caption=f"👋 <b>Access to {CHANNEL_NAME}</b>\n\nPay with Telegram Stars for instant access.",
           parse_mode=ParseMode.HTML,
           reply_markup=menu_kb(),
       )
   ```

### Option 2: Upload to Your VPS
1. Upload the image from your computer to the VPS:
   ```bash
   scp welcome.jpg ubuntu@your-vps-ip:~/telegram-stars-bot/
   ```
2. Edit `bot.py` to read the local file:
   ```python
   async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
       with open("welcome.jpg", "rb") as photo_file:
           await update.effective_message.reply_photo(
               photo=photo_file,
               caption=f"👋 <b>Access to {CHANNEL_NAME}</b>\n\nPay with Telegram Stars for instant access.",
               parse_mode=ParseMode.HTML,
               reply_markup=menu_kb(),
           )
   ```
3. Restart the bot: `sudo systemctl restart telegram-bot`

---

## 🔧 Essential Management Commands

```bash
# Start the bot
sudo systemctl start telegram-bot

# Stop the bot
sudo systemctl stop telegram-bot

# Restart the bot (use after editing bot.py or .env)
sudo systemctl restart telegram-bot

# Check if it's running properly
sudo systemctl status telegram-bot

# View live logs (press Ctrl+C to exit)
sudo journalctl -u telegram-bot -f

# View the last 100 log lines
sudo journalctl -u telegram-bot -n 100

# Update the bot code from GitHub
cd ~/telegram-stars-bot
git pull
sudo systemctl restart telegram-bot
```

---

## 🚨 Troubleshooting

### Bot won't start
Check the logs for the exact error:
```bash
sudo journalctl -u telegram-bot -n 50
```

**Common Errors & Fixes:**
1. **`KeyError: 'BOT_TOKEN'`**  
   → Your `.env` file is not being loaded. Ensure `EnvironmentFile=/home/ubuntu/telegram-stars-bot/.env` is in your systemd service file.
2. **`Premium: command not found`**  
   → Your `.env` file has unquoted values with spaces. Fix: `CHANNEL_NAME="Premium Channel"` (add quotes).
3. **`ModuleNotFoundError: No module named 'telegram'`**  
   → Python packages are not installed in the virtual environment. Run: `source venv/bin/activate && pip install -r requirements.txt`
4. **`sqlite3.OperationalError: unable to open database file`**  
   → Database path is incorrect. Use an absolute path: `DB_PATH="/home/ubuntu/telegram-stars-bot/access.db"`

### Bot is running but not responding in Telegram
1. Verify it's running: `sudo systemctl status telegram-bot`
2. Check for API errors in logs: `sudo journalctl -u telegram-bot -f`
3. Double-check that your `BOT_TOKEN` in `.env` is 100% correct.
4. Ensure you actually typed `/start` to the bot in Telegram.

### Payment fails or invoice doesn't appear
1. Ensure your bot is fully set up and verified in @BotFather.
2. Test with a very small amount first (e.g., 1 Star) to verify the flow.
3. Confirm your code uses `currency="XTR"` and `provider_token=""`.

### User pays but can't join the channel
1. Verify the bot is an **Administrator** in the channel.
2. Verify the bot has **"Invite Users via Link"** and **"Ban Users"** permissions.
3. Check if the user was previously banned from the channel (the bot will try to unban them, but manual intervention might be needed).

---

## 📊 How Telegram Stars Payouts Work

1. Users pay with Stars, which are credited to your bot's balance.
2. Stars are held for a ~21-day security period.
3. After the hold period, you can withdraw Stars via [Fragment](https://fragment.com/).
4. Stars are converted to Toncoin (TON), which you can then swap for fiat currency on any major crypto exchange.
5. **Minimum withdrawal:** 1,000 Stars.

---

## 🔒 Security Best Practices

1. **Never share your `BOT_TOKEN`** or commit your `.env` file to GitHub.
2. **Restrict `.env` permissions** on your VPS: `chmod 600 .env`
3. **Limit admin access** strictly to your own User ID in the `ADMIN_IDS` variable.
4. **Back up your database** regularly: `cp ~/telegram-stars-bot/access.db ~/backup/access.db.$(date +\%Y\%m\%d)`
5. **Monitor your logs** weekly for any unusual activity or repeated failed payment attempts.

---

## 🎉 You're Done!

Your bot is now fully configured and running. 

**Final Checklist:**
- [ ] Bot responds to `/start`
- [ ] Payment invoice generates correctly
- [ ] Invite link is sent after a test payment
- [ ] You can successfully join the channel via the link
- [ ] `/stats` command shows accurate data

Good luck with your channel! 🚀
