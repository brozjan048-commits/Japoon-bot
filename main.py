# main.py — ShogunBot with Duel subsystem (sealed picks: private choices)
import os
import json
import random
import time
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

# ---------------- Config ----------------
DATA_FILE = "shogun_data.json"

# ---------------- load / save ----------------
def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_data(d):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

data = load_data()
# structure: { "<user_id>": {"name": "...", "points": 0, "rank": "...", "wins":0, "losses":0, "first_seen":"YYYY-MM-DD", ...} }

# ---------------- Rank table (every 5 points) ----------------
rank_table = [
    (0,  "ولگرد بی‌خانمان",   "浮浪者"),
    (5,  "شاگرد شمشیرزن",     "弟子"),
    (10, "سامورایی جوان",     "若侍"),
    (15, "رونین سرگردان",    "浪人"),
    (20, "جنگجوی بوشیدو",    "武士道の戦士"),
    (25, "نگهبان شوگان",     "将軍の守護者"),
    (30, "فرمانده میدان",     "指揮官"),
    (35, "استاد شمشیر",       "剣の師匠"),
    (40, "سامورایی بزرگ",     "大侍"),
    (45, "شوگان",            "将軍"),
    (50, "شوگان اعظم",       "大将軍"),
]

def get_rank_by_points(points):
    chosen_fa, chosen_jp = rank_table[0][1], rank_table[0][2]
    for threshold, fa, jp in rank_table:
        if points >= threshold:
            chosen_fa, chosen_jp = fa, jp
    return chosen_fa, chosen_jp

def get_rank_index_from_points(points):
    idx = 0
    for i,(thr,fa,jp) in enumerate(rank_table):
        if points >= thr:
            idx = i
    return idx

# ---------------- helpers ----------------
import datetime
def ensure_user_obj(uid, name):
    uid = str(uid)
    if uid not in data:
        data[uid] = {
            "name": name,
            "points": 0.0,
            "rank": get_rank_by_points(0)[0],
            "wins": 0,
            "losses": 0,
            "streak": 0,
            "class": "",          # class: "سامورایی", "رونین", "راهب", "نینجا"
            "blades": 0,          # for samurai
            "class_xp": 0,
            "first_seen": datetime.date.today().isoformat()
        }
        save_data(data)

def check_rank_change_and_message(uid):
    uid = str(uid)
    user = data[uid]
    old_rank = user.get("rank", get_rank_by_points(0)[0])
    new_rank_fa, new_rank_jp = get_rank_by_points(user["points"])
    if old_rank != new_rank_fa:
        # compare index
        old_idx = get_rank_index_from_points(get_rank_threshold_by_name(old_rank))
        new_idx = get_rank_index_from_points(get_rank_threshold_by_name(new_rank_fa))
        user["rank"] = new_rank_fa
        save_data(data)
        if new_idx > old_idx:
            return f"🌅 {user['name']} به مقام «{new_rank_fa}» ارتقا یافت.\nافتخار همراهت باد。\n（{user['name']}は「{new_rank_jp}」に昇進した。）"
        else:
            return f"🌑 {user['name']} سقوط کرد و اکنون در «{new_rank_fa}» است。\n（{user['name']}は「{new_rank_jp}」に降格した。）"
    return None

def get_rank_threshold_by_name(rank_name):
    for thr,fa,jp in rank_table:
        if fa == rank_name:
            return thr
    return 0

# ---------------- Messaging templates ----------------
HONOR_MSGS = [
    "🌸 افتخارِ {name} افزوده شد. راه بوشیدو با توست.",
    "🏵 نامِ {name} در دفترِ افتخار حک شد؛ عزیمت به سوی شرف.",
    "🕯 افتخارت بالا رفت، {name}. شمشیرت روشن بماند."
]
SEPPOKU_MSGS = [
    "🩸 {name} سپوکو برگزید؛ شعلهٔ ناموس اندکی فرونشست.",
    "⚔️ {name} راه سخت سپوکو را برگزید؛ نامش جاودان نشد.",
    "🖤 سپوکوِ {name} ثبت شد؛ درنگی برای بازنگری شرافت."
]
WELCOME_SLOGAN_4 = [
    "شجاعت: ستونِ نخستِ وجود.",
    "وفاداری: پیوندِ نیرومندِ دلها.",
    "راستی: شمشیری بی‌پیرایه.",
    "افتخار: ثمرِ هر عملِ راستین."
]

# ---------------- Duel subsystem (sealed private picks) ----------------
active_duels = {}  # key: "minid:maxid" -> duel dict

MOVE_MAP = {
    "سنگ": "strike", "stone": "strike", "strike": "strike",
    "کاغذ": "parry", "paper": "parry", "parry": "parry",
    "قیچی": "feint", "scissors": "feint", "feint": "feint"
}

BEATS = {
    "strike": "feint",   # strike > feint
    "feint": "parry",    # feint > parry
    "parry": "strike"    # parry > strike
}

def duel_key(a, b):
    a, b = str(a), str(b)
    return ":".join(sorted([a, b]))

async def start_duel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("برای شروع دوئل، پیام کسی را ریپلای کن و 'دوئل' بفرست.")
        return

    challenger = update.message.from_user
    target = update.message.reply_to_message.from_user
    chat_id = update.effective_chat.id

    if challenger.id == target.id:
        await update.message.reply_text("نمی‌توانی با خودت دوئل کنی.")
        return

    key = duel_key(challenger.id, target.id)
    if key in active_duels:
        await update.message.reply_text("در حال حاضر یک دوئل یا دعوت معلق بین این دو نفر وجود دارد. قبل از دعوت مجدد صبر کنید.")
        return

    # create duel entry
    active_duels[key] = {
        "chat_id": chat_id,
        "challenger": str(challenger.id),
        "target": str(target.id),
        "choices": {},
        "created_at": time.time(),
        "expires_at": time.time() + 60
    }

    await update.message.reply_text(
        f"⚔️ دوئل ثبت شد: {challenger.first_name} vs {target.first_name}.\n"
        "هر دو در پیام خصوصی به من حرکت خود را انتخاب کنید: سنگ / کاغذ / قیچی.\n"
        "لطفاً تا 60 ثانیه انتخاب کنید."
    )

    # DM both
    try:
        await context.bot.send_message(challenger.id, f"دوئل با {target.first_name} ثبت شد — لطفاً یکی از: سنگ / کاغذ / قیچی را اینجا بفرست (60s).")
        await context.bot.send_message(target.id, f"دوئل با {challenger.first_name} ثبت شد — لطفاً یکی از: سنگ / کاغذ / قیچی را اینجا بفرست (60s).")
    except Exception:
        # cleanup and inform group
        active_duels.pop(key, None)
        await update.message.reply_text(
            "خطا: یکی از طرفین پیام خصوصی با بات را باز نکرده است. از هر دو بخواهید ابتدا در چت خصوصی /start بزنند و دوباره تلاش کنید."
        )
        return

    # schedule timeout watcher
    asyncio.create_task(_duel_timeout_watcher(key, context, 60))

async def _duel_timeout_watcher(key, context, wait_seconds):
    await asyncio.sleep(wait_seconds)
    duel = active_duels.get(key)
    if not duel:
        return
    missing = []
    for uid in (duel["challenger"], duel["target"]):
        if uid not in duel["choices"]:
            missing.append(uid)
    for uid in missing:
        duel["choices"][uid] = random.choice(["strike", "parry", "feint"])
        try:
            await context.bot.send_message(int(uid), "زمان انتخاب شما تمام شد؛ حرکتی تصادفی برای شما انتخاب شد.")
        except:
            pass
    await _resolve_duel(key, context)

async def private_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # only private chats
    if update.effective_chat.type != "private":
        return
    user = update.message.from_user
    text = (update.message.text or "").strip().lower()
    move = MOVE_MAP.get(text)
    if not move:
        parts = text.split()
        if len(parts) >= 2:
            move = MOVE_MAP.get(parts[1])
    if not move:
        await update.message.reply_text("لطفاً یکی از: سنگ / کاغذ / قیچی (یا strike / parry / feint) را بفرست.")
        return

    # find duel where this user participates and hasn't chosen yet
    found = None
    for key, duel in active_duels.items():
        if str(user.id) in (duel["challenger"], duel["target"]):
            if str(user.id) in duel["choices"]:
                await update.message.reply_text("شما قبلاً انتخاب کرده‌اید؛ ثبت شده است.")
                return
            found = (key, duel)
            break

    if not found:
        await update.message.reply_text("دوئل فعالی برای شما پیدا نشد. ابتدا در گروه روی پیام فردی ریپلای کن و 'دوئل' بفرست.")
        return

    key, duel = found
    duel["choices"][str(user.id)] = move
    await update.message.reply_text(f"انتخاب ثبت شد: {move} — منتظر انتخاب حریف...")
    if len(duel["choices"]) == 2:
        await _resolve_duel(key, context)

async def _resolve_duel(key, context: ContextTypes.DEFAULT_TYPE):
    duel = active_duels.get(key)
    if not duel:
        return
    chat_id = duel["chat_id"]
    a_id = duel["challenger"]
    b_id = duel["target"]
    a_move = duel["choices"].get(a_id)
    b_move = duel["choices"].get(b_id)

    if not a_move or not b_move:
        await context.bot.send_message(chat_id, "دوئل ناقص بود؛ لغو می‌شود.")
        active_duels.pop(key, None)
        return

    # ensure users exist
    ensure_user_obj(a_id, data.get(a_id, {}).get("name", "ناشناس"))
    ensure_user_obj(b_id, data.get(b_id, {}).get("name", "ناشناس"))
    A = data[a_id]
    B = data[b_id]

    # tie
    if a_move == b_move:
        await context.bot.send_message(chat_id,
            f"⚖️ تساوی بین {A['name']} و {B['name']} — هر دو {a_move} انتخاب کردند. هیچ امتیازی تغییر نکرد.")
        active_duels.pop(key, None)
        save_data(data)
        return

    # determine winner by RPS
    if BEATS[a_move] == b_move:
        winner_id, loser_id = a_id, b_id
        winner_move, loser_move = a_move, b_move
    elif BEATS[b_move] == a_move:
        winner_id, loser_id = b_id, a_id
        winner_move, loser_move = b_move, a_move
    else:
        await context.bot.send_message(chat_id, "خطا در محاسبه دوئل؛ مساوی اعلام شد.")
        active_duels.pop(key, None)
        return

    # snapshot pre points
    winner = data[winner_id]
    loser = data[loser_id]
    winner_class = winner.get("class", "")
    loser_class = loser.get("class", "")
    pre_w_points = float(winner.get("points", 0))
    pre_l_points = float(loser.get("points", 0))

    # base gains/losses
    winner_gain = 1.0
    loser_deduct = 1.0

    # ---------- class rules (as specified) ----------
    # Samurai: blades -> every win gives a blade; 3 blades -> +1 extra; if 5+ streak, extra *1.5
    if winner_class == "سامورایی":
        blades = int(winner.get("blades", 0)) + 1
        winner["blades"] = blades
        extra = 0.0
        if blades >= 3:
            winner["blades"] = blades - 3
            extra = 1.0
            if winner.get("streak", 0) >= 5:
                extra *= 1.5
        winner_gain += extra

    # Ronin: if opponent has >= +2 points -> +0.5 ; else +0.6
    if winner_class == "رونین":
        if pre_l_points >= pre_w_points + 2:
            winner_gain += 0.5
        else:
            winner_gain += 0.6

    # Ninja: wins give 0.8 instead of 1
    if winner_class == "نینجا":
        winner_gain = 0.8

    # Monk: no special on win

    # apply winner updates
    winner["points"] = float(winner.get("points", 0)) + float(winner_gain)
    winner["wins"] = winner.get("wins", 0) + 1
    winner["streak"] = winner.get("streak", 0) + 1
    # class XP (simple): +1 xp per win
    winner["class_xp"] = winner.get("class_xp", 0) + 1

    # apply loser deduction rules
    # Ninja: 30% chance to ignore loss (no deduction)
    if loser_class == "نینجا":
        if random.random() < 0.30:
            loser_deduct = 0.0
    # Monk: deduct only 0.6 on loss
    if loser_class == "راهب":
        loser_deduct = 0.6

    loser["points"] = max(0.0, float(loser.get("points", 0)) - float(loser_deduct))
    loser["losses"] = loser.get("losses", 0) + 1
    loser["streak"] = 0

    save_data(data)

    # check rank changes
    win_msg = check_rank_change_and_message(winner_id)
    lose_msg = check_rank_change_and_message(loser_id)

    # announcement
    summary = (
        f"⚔️ دوئل بین {data[winner_id]['name']} و {data[loser_id]['name']} به پایان رسید!\n"
        f"🧭 انتخاب‌ها: {data[winner_id]['name']} ⇢ {winner_move} — {data[loser_id]['name']} ⇢ {loser_move}\n\n"
        f"🏆 برنده: {data[winner_id]['name']} → +{round(winner_gain,2)} امتیاز\n"
        f"💀 بازنده: {data[loser_id]['name']} → −{round(loser_deduct,2)} امتیاز\n\n"
        f"🎖 امتیازهای جدید: {data[winner_id]['name']}: {round(data[winner_id]['points'],2)}  |  {data[loser_id]['name']}: {round(data[loser_id]['points'],2)}"
    )
    if win_msg:
        summary += "\n\n" + win_msg
    if lose_msg:
        summary += "\n\n" + lose_msg

    await context.bot.send_message(chat_id, summary)

    # cleanup
    active_duels.pop(key, None)
    save_data(data)

# ---------------- Core handlers (welcome, profile, commands) ----------------
async def welcome_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.new_chat_members:
        for member in update.message.new_chat_members:
            line1 = random.choice(["👑 من شوگان این دوجو هستم؛ نظم، افتخار، و سکوت حکم ماست.",
                                   "🎎 بر تو درود که قدم در این دوجو نهادی؛ راه بوشیدو در پیش روست.",
                                   "⚠️ هر که شرافت را نیافریند، تیغ پاسخ خواهد گرفت."])
            slogan = "\n".join(WELCOME_SLOGAN_4)
            jp = "（将軍ボットが起動しました。秩序と名誉を守れ。）"
            await update.message.reply_text(f"{line1}\n{slogan}\n{jp}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    sender = update.message.from_user
    replied = update.message.reply_to_message
    chat_id = update.effective_chat.id

    # profile
    if text in ["من", "پروفایل", "پروفایل من"]:
        uid = str(sender.id)
        ensure_user_obj(uid, sender.first_name)
        user = data[uid]
        rank_fa, rank_jp = get_rank_by_points(user["points"])
        next_idx = get_rank_index_from_points(user["points"]) + 1
        next_thr = rank_table[next_idx][0] if next_idx < len(rank_table) else None
        need = (next_thr - user["points"]) if next_thr is not None else 0
        reply = (
            f"👤 پروفایل {user['name']}\n"
            f"🎖 امتیاز: {round(user['points'],2)}\n"
            f"🔱 رتبه: {rank_fa} ({rank_jp})\n"
            f"⚔️ بردها: {user.get('wins',0)} — باخت‌ها: {user.get('losses',0)}\n"
            f"📅 عضو از: {user.get('first_seen','-')}\n"
            f"🈶 کلاس: {user.get('class','-')} (XP: {user.get('class_xp',0)})\n"
        )
        if next_thr:
            reply += f"⬆️ تا رتبهٔ بعدی {round(need,2)} امتیاز باقی است.\n"
        else:
            reply += "👑 تو در اوجِ شوگان ایستاده‌ای.\n"
        reply += f"（プロフィール：{user['name']}、名誉：{user['points']}）"
        await update.message.reply_text(reply)
        return

    # class commands
    if text.startswith("/class") or text.startswith("کلاس") or text.startswith("class"):
        parts = text.split()
        if len(parts) == 1:
            # show instructions / current
            uid = str(sender.id)
            ensure_user_obj(uid, sender.first_name)
            cl = data[uid].get("class","-")
            await update.message.reply_text(f"کلاس شما: {cl}\nبرای انتخاب: /class سامورایی  یا /class نینجا  یا /class راهب  یا /class رونین")
            return
        # choose class
        chosen = parts[1].strip()
        uid = str(sender.id)
        ensure_user_obj(uid, sender.first_name)
        if chosen in ["سامورایی","نینجا","راهب","رونین"]:
            data[uid]["class"] = chosen
            save_data(data)
            await update.message.reply_text(f"✅ انتخاب شد: شما اکنون کلاس «{chosen}» هستید.")
        else:
            await update.message.reply_text("کلاس نامعتبر است. انتخاب‌های معتبر: سامورایی، نینجا، راهب، رونین.")
        return

    # duel trigger (replaces old duel block)
    if text == "دوئل":
        await start_duel(update, context)
        return

    # honor (same as before)
    if text == "افتخار":
        if not replied:
            await update.message.reply_text("برای دادن افتخار، باید پیام کسی را ریپلای کنی。\n（返信が必要です。）")
            return
        target = replied.from_user
        if target.id == sender.id:
            await update.message.reply_text("نمی‌توانی به خودت افتخار بدهی؛ شرافت باید از دیگران دیده شود。\n（自分自身に名誉を与えることはできません。）")
            return
        rid = str(target.id)
        ensure_user_obj(rid, target.first_name)
        data[rid]["points"] = float(data[rid].get("points",0)) + 1.0
        data[rid]["name"] = target.first_name
        save_data(data)
        txt = random.choice(HONOR_MSGS).format(name=target.first_name)
        jp = "（{name}の名誉が増しました。武士道の道を歩め。）".format(name=target.first_name)
        change_msg = check_rank_change_and_message(rid)
        reply = f"{txt}\n{jp}\n🎖 امتیاز اکنون: {round(data[rid]['points'],2)}"
        if change_msg:
            reply += "\n\n" + change_msg
        await update.message.reply_text(reply)
        return

    # seppuku
    if text == "سپوکو":
        if not replied:
            await update.message.reply_text("برای سپوکو باید پیام کسی را ریپلای کنی。\n（返信が必要です。）")
            return
        target = replied.from_user
        rid = str(target.id)
        ensure_user_obj(rid, target.first_name)
        data[rid]["points"] = max(0.0, float(data[rid].get("points",0)) - 1.0)
        data[rid]["name"] = target.first_name
        save_data(data)
        txt = random.choice(SEPPOKU_MSGS).format(name=target.first_name)
        jp = "（{name}は切腹をした。名誉が失われた。）".format(name=target.first_name)
        change_msg = check_rank_change_and_message(rid)
        reply = f"{txt}\n{jp}\n🎖 امتیاز اکنون: {round(data[rid]['points'],2)}"
        if change_msg:
            reply += "\n\n" + change_msg
        await update.message.reply_text(reply)
        return

    # leaderboard
    if text == "افتخارات":
        if not data:
            await update.message.reply_text("هنوز افتخاری ثبت نشده است。\n（名誉の記録はまだありません。）")
            return
        board = sorted(data.items(), key=lambda x: x[1]["points"], reverse=True)
        out = "🏯 جدول افتخارات شوگان‌بات:\n\n"
        for i,(uid,info) in enumerate(board, start=1):
            out += f"{i}. {info['name']} — {round(info['points'],2)} امتیاز | {info.get('rank', get_rank_by_points(info['points'])[0])}\n"
        await update.message.reply_text(out)
        return

    # tea / spirit / laws
    if text == "چای":
        await update.message.reply_text(random.choice(["🍵 چای دم شد؛ بگذار بخار آن دل را پاک کند。","🍵 یک جرعه چای، بسان سکوتِ پیش از جنگ؛ دنیا را بازشناس。"]))
        return
    if text == "روح":
        await update.message.reply_text(random.choice(["🕊 روحِ جنگجو آرام، پایدار و بی‌هیاهوست。","🔥 درونت را صیقل کن؛ فولادِ روح را بیافرین。"]))
        return
    if text == "قوانین":
        await update.message.reply_text("📜 قوانین بوشیدو: احترام، شجاعت، راستی، وفاداری.\n（武士道の掟を守れ。）")
        return

# ---------------- Run / bootstrap ----------------
async def main():
    TOKEN = os.getenv("TOKEN") or "8493668083:AAEpTpiPlCCiek1hmcTVfO1mDEfrLibt5t8"
    app = ApplicationBuilder().token(TOKEN).build()

    # private choice handler MUST be added before global message handler
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, private_choice_handler))

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("👑 شوگان‌بات فعال شد (دوئل سیستم ادغام شد).")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
