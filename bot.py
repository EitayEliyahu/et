import csv
import json
import time
import random

from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# === הגדרות בסיס ===
import os
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
DATA_FILE = Path("Chance.csv")      # קובץ הנתונים של הצ'אנס

KNOWN_COMMANDS = {
    "/start",
    "/help",
    "/grant",
    "/subinfo",
    "/myid",
    "/terms",
    "/revoke",
    "/broadcast",
}

# אדמין – את זה להחליף ל-user_id שלך
ADMIN_IDS = [812811431]

# קובץ מנויים (user_id -> expiry_timestamp)
SUBSCRIBERS_FILE = Path("subscribers.json")

# פוטר קבוע לכל הודעה מהמערכת
FOOTER = "\n\nלכל פנייה לגבי המערכת ומנויים שלחו הודעה ליוזר @eitayeliyahu"

# קירור לקלף האוטומטי (user_id -> last_timestamp)
auto_card_cooldowns: dict[int, float] = {}


# === חלק 0: ניהול מנויים יומיים (24 שעות) ===

def load_subscribers() -> dict:
    """
    טוען מנויים מהקובץ בפורמט:
    {
        "123456789": 1732664100.0,  # timestamp של תוקף
        "987654321": 1732667890.0
    }
    """
    if not SUBSCRIBERS_FILE.exists():
        return {}
    try:
        with SUBSCRIBERS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    if isinstance(data, list):
        # אם פעם היה פורמט ישן של רשימה – נתחיל מחדש
        return {}
    return data


def save_subscribers():
    with SUBSCRIBERS_FILE.open("w", encoding="utf-8") as f:
        json.dump(subscribers, f, ensure_ascii=False, indent=2)


def is_subscriber(user_id: int) -> bool:
    """
    בדיקה אם משתמש נחשב מנוי:
    • מנוי רק אם יש רשומה בתוקף בקובץ subscribers.json
    (אין יותר גישת מנוי אוטומטית לאדמין).
    """
    now = time.time()
    uid = str(user_id)

    expiry = subscribers.get(uid)
    if not expiry:
        return False

    if now > expiry:
        del subscribers[uid]
        save_subscribers()
        return False

    return True


subscribers = load_subscribers()


# === חלק 1: עבודה עם נתונים ===

def load_draws(limit: int = 200) -> List[Tuple[str, str, str, str]]:
    """
    קורא קובץ בפורמט:
    date,draw_number,card1,card2,card3,card4,empty
    לדוגמה:
    27/11/2025,52009,8,9,9,Q,
    """
    draws = []

    if not DATA_FILE.exists():
        return []

    with DATA_FILE.open("r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 6:
                continue

            card1 = row[2].strip()
            card2 = row[3].strip()
            card3 = row[4].strip()
            card4 = row[5].strip()

            if card1 and card2 and card3 and card4:
                draws.append((card1, card2, card3, card4))

    # לא הופכים את הרשימה — משאירים כמו בקובץ
    return draws[:limit]


def get_last_10_draws() -> List[Tuple[str, str, str, str]]:
    draws = load_draws(limit=10)
    return draws


def calc_card_stats(draws: List[Tuple[str, str, str, str]]):
    """
    פונקציה שתחשב סטטיסטיקות לכל קלף.
    כרגע – ספירה פשוטה.
    """
    stats = {}
    for draw in draws:
        for card in draw:
            stats.setdefault(card, 0)
            stats[card] += 1
    return stats


def suggest_4_sets(stats, num_sets: int = 3) -> List[List[str]]:
    """
    מחזיר 3 צירופים דומים מאוד של 4 קלפים,
    על בסיס הקלפים הכי חזקים בסטטיסטיקה.
    """
    sorted_cards = [card for card, count in sorted(stats.items(), key=lambda x: x[1], reverse=True)]

    if len(sorted_cards) < 4:
        return [sorted_cards[:4]]

    base = sorted_cards[:4]  # הסט הבסיסי – 4 הקלפים הכי חזקים
    sets: List[List[str]] = []

    # סט 1 – הבסיס
    sets.append(base)

    # סט 2 – מחליף קלף אחד בקלף הבא בתור
    if len(sorted_cards) >= 5:
        alt1 = base.copy()
        alt1[-1] = sorted_cards[4]
        sets.append(alt1)

    # סט 3 – מחליף קלף אחר בקלף הבא אחריו
    if len(sorted_cards) >= 6:
        alt2 = base.copy()
        alt2[-2] = sorted_cards[5]
        sets.append(alt2)

    while len(sets) < num_sets:
        sets.append(base)

    return sets[:num_sets]


def get_hot_cards(stats, top_n: int = 6) -> List[str]:
    sorted_cards = sorted(stats.items(), key=lambda x: x[1], reverse=True)
    return [card for card, count in sorted_cards[:top_n]]


# === חלק 2: תפריט וכפתורים ===

def get_main_keyboard(is_subscriber_flag: bool) -> ReplyKeyboardMarkup:
    """
    חינמי:
      • 10 ההגרלות האחרונות
      • רכישת מנוי
      • שלושה כפתורים שיווקיים
      • איך זה עובד

    מנוי:
      • 10 ההגרלות האחרונות
      • 3 קלפים חמים
      • קלף אוטומטי
      • היסטוריית תחזיות (כרגע סקיצה)
      • טקסטים שיווקיים
      • איך זה עובד
    """
    if is_subscriber_flag:
        keyboard = [
            ["🎰 10 ההגרלות האחרונות"],
            ["📊 3 קלפים חמים להגרלה הבאה"],
            ["🃏 קלף אוטומטי"],
            ["🕒 היסטוריית תחזיות"],
            ["🎯 מה היתרון של הבוט?"],
            ["💰 מה מקבלים במנוי?", "🔥 למה כדאי להיות מנוי?"],
            ["ℹ️ איך זה עובד"],
        ]
    else:
        keyboard = [
            ["🎰 10 ההגרלות האחרונות"],
            ["💳 רכישת מנוי"],
            ["🎯 מה היתרון של הבוט?"],
            ["💰 מה מקבלים במנוי?", "🔥 למה כדאי להיות מנוי?"],
            ["ℹ️ איך זה עובד"],
        ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


# === חלק 3: Handlers של הבוט ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_sub = is_subscriber(user.id)

    status_text = "✅ מנוי יומי פעיל" if is_sub else "❌ ללא מנוי פעיל"

    text = (
        "ברוך הבא ל־*Chance Predictor* 🔮\n\n"
        "⚠️ *הבוט נמצא בגרסת הרצה (Beta)*\n"
        "ייתכנו שדרוגים, עדכונים ופיצ׳רים חדשים שייכנסו בהמשך.\n\n"
        "המערכת מציגה ניתוח סטטיסטי ותחזיות הסתברותיות להגרלות צ׳אנס, "
        "המבוססות על נתונים היסטוריים ואלגוריתם ייעודי.\n\n"
        f"מצב המנוי שלך: {status_text}\n\n"
        "בגרסה החינמית ניתן לצפות בהגרלות האחרונות, לקרוא על המערכת ולקבל מידע כללי.\n"
        "כדי לפתוח גישה מלאה לתחזיות חמות וכלי פרימיום – ניתן לרכוש מנוי יומי.\n\n"
        "© כל הזכויות שמורות – *איתי אליהו*"
    )

    await update.message.reply_text(text + FOOTER, reply_markup=get_main_keyboard(is_sub), parse_mode="Markdown")


async def handle_last_10(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draws = get_last_10_draws()
    if not draws:
        await update.message.reply_text("אין עדיין נתונים של הגרלות." + FOOTER)
        return

    suits = ["♠️", "♥️", "♦️", "♣️"]  # משמאל לימין: עלה, לב, יהלום, תלתן

    lines = []
    for i, draw in enumerate(draws, start=1):
        # draw זה טפל של 4 קלפים: (card1, card2, card3, card4)
        cards_with_suits = [
            f"{card}{suits[idx]}" for idx, card in enumerate(draw)
        ]
        line = f"{i}. {'  |  '.join(cards_with_suits)}"
        lines.append(line)

    text = "🎰 *10 ההגרלות האחרונות:*\n\n" + "\n".join(lines)
    await update.message.reply_text(text + FOOTER, parse_mode="Markdown")


async def handle_predict_4(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    כרגע לא מחובר לכפתור בתפריט, אבל נשמר לפיצ'ר צירופים חמים.
    """
    user = update.effective_user
    is_sub = is_subscriber(user.id)

    if not is_sub:
        text = (
            "🔒 הפיצ׳ר הזה זמין למנויים יומיים בלבד.\n\n"
            "נראה שאין לך מנוי פעיל כרגע או שהוא הסתיים.\n"
            "אפשר לפתוח גישה ל־24 שעות מלאות דרך ״💳 רכישת מנוי״."
        )
        await update.message.reply_text(text + FOOTER, reply_markup=get_main_keyboard(False))
        return

    draws = load_draws()
    if not draws:
        await update.message.reply_text("אין מספיק נתונים לחישוב תחזיות." + FOOTER)
        return

    stats = calc_card_stats(draws)
    sets = suggest_4_sets(stats, num_sets=3)

    suits = ["♠️", "♥️", "♦️", "♣️"]  # משמאל לימין: עלה, לב, יהלום, תלתן

    lines = []
    for i, s in enumerate(sets, start=1):
        # s הוא רשימה של 4 קלפים – נוסיף לכל עמודה את הסמל שלה
        cards_with_suits = [
            f"{card}{suits[idx]}" for idx, card in enumerate(s)
        ]
        cards_str = " | ".join(cards_with_suits)
        lines.append(f"{i}. {cards_str}")

    text = (
        "📊 *3 צירופים חמים להגרלה הקרובה (מתעדכן כל שעתיים אוטומטית):* 🔥\n\n"
        + "\n".join(lines)
        + "\n\n"
        "הצירופים נבנים על בסיס קלפים בעלי הופעה גבוהה יותר,"
        " עם שינויים קלים בין צירוף לצירוף כדי לשמור על גיוון.\n\n"
        "⚠️ הבוט מציג תחזיות סטטיסטיות בלבד ואינו מבטיח זכייה. "
        "השימוש הוא על אחריות המשתמש."
    )

    await update.message.reply_text(text + FOOTER, parse_mode="Markdown")


async def handle_hot_cards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_sub = is_subscriber(user.id)

    if not is_sub:
        text = (
            "🔒 הפיצ׳ר הזה זמין למנויים יומיים בלבד.\n\n"
            "כדי לראות אילו קלפים נחשבים \"חמים\" לפי הנתונים – "
            "אפשר לפתוח מנוי יומי דרך ״💳 רכישת מנוי״."
        )
        await update.message.reply_text(text + FOOTER, reply_markup=get_main_keyboard(False))
        return

    draws = load_draws()
    if not draws:
        await update.message.reply_text("אין מספיק נתונים לחישוב קלפים חמים." + FOOTER)
        return

    stats = calc_card_stats(draws)
    hot = get_hot_cards(stats, top_n=3)

    # אמוג׳ים לפי עמודות: עלה, לב, יהלום, תלתן
    suits = ["♠️", "♥️", "♦️", "♣️"]

    hot_with_suits = [
        f"{card}{suits[idx]}" for idx, card in enumerate(hot)
    ]

    cards_str = " | ".join(hot_with_suits)
    text = (
        "🔥 *3 קלפים חמים לפי הנתונים הקיימים:*\n\n"
        f"{cards_str}\n\n"
        "החום של הקלפים מבוסס על תדירות ההופעה שלהם בתקופה האחרונה.\n\n"
        "⚠️ אין כאן הבטחה לזכייה. זה כלי עזר סטטיסטי בלבד."
    )
    await update.message.reply_text(text + FOOTER, parse_mode="Markdown")


async def handle_auto_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    כפתור קלף אוטומטי – מחזיר קלף רנדומלי (דרגה + צורה), עם קירור של 5 שניות לכל משתמש.
    """
    user = update.effective_user
    uid = user.id

    # רק למנויים
    if not is_subscriber(uid):
        text = (
            "🔒 הפיצ׳ר של קלף אוטומטי זמין למנויים יומיים בלבד.\n\n"
            "כדי לפתוח גישה – השתמש ב״💳 רכישת מנוי״."
        )
        await update.message.reply_text(text + FOOTER, reply_markup=get_main_keyboard(False))
        return

    now = time.time()
    last_ts = auto_card_cooldowns.get(uid, 0)

    # קירור של 5 שניות בין לחיצה ללחיצה
    if now - last_ts < 5:
        await update.message.reply_text(
            "⏳ אפשר לבקש קלף אוטומטי פעם ב־5 שניות. נסה שוב עוד כמה רגעים."
            + FOOTER
        )
        return

    auto_card_cooldowns[uid] = now

    # דרגות הקלפים (7–A, כמו בצ'אנס)
    ranks = ["7", "8", "9", "10", "J", "Q", "K", "A"]
    suits = ["♠️", "♥️", "♦️", "♣️"]  # אותו סדר שקבענו

    rank = random.choice(ranks)
    suit = random.choice(suits)

    text = (
        "🃏 *קלף אוטומטי להגרלה הקרובה:*\n\n"
        f"{rank}{suit}\n\n"
        "שימוש בקלף הוא על אחריות המשתמש בלבד. "
        "זה כלי עזר סטטיסטי/רנדומלי – לא הבטחה לזכייה."
    )

    await update.message.reply_text(text + FOOTER, parse_mode="Markdown")


async def handle_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    סקיצה לפיצ׳ר עתידי – כרגע רק טקסט הסבר.
    """
    user = update.effective_user
    is_sub = is_subscriber(user.id)

    if not is_sub:
        text = (
            "🔒 היסטוריית תחזיות זמינה למנויים יומיים בלבד.\n\n"
            "בקרוב תתווסף כאן היסטוריה של צירופים שנשלחו עבורך.\n"
            "כדי להיות בין הראשונים שמשתמשים בזה, אפשר לפתוח מנוי יומי."
        )
        await update.message.reply_text(text + FOOTER, reply_markup=get_main_keyboard(False))
        return

    text = (
        "🕒 היסטוריית תחזיות\n\n"
        "בגרסה הנוכחית הפיצ׳ר עדיין בבנייה.\n"
        "במהלך תקופת הבטא תתווסף כאן היסטוריה של צירופים שנשלחו עבורך."
    )
    await update.message.reply_text(text + FOOTER)


async def handle_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "ℹ️ *איך זה עובד?*\n\n"
        "Chance Predictor הוא בוט ניתוח סטטיסטי להגרלות צ׳אנס.\n\n"
        "המערכת:\n"
        "• קוראת את תוצאות ההגרלות מתוך קובץ הנתונים\n"
        "• סופרת כמה פעמים כל קלף הופיע\n"
        "• מזהה קלפים עם תדירות גבוהה יותר וקלפים \"שקטים\" לאורך זמן\n"
        "• בונה צירופים וחישובים הסתברותיים על בסיס הנתונים\n\n"
        "המטרה היא לתת למשתמש תמונה סטטיסטית חדה יותר – "
        "ולא להבטיח זכייה או תוצאה כלשהי.\n\n"
        "⚠️ הבוט אינו ייעוץ השקעה או הימורים. כל שימוש במידע הוא באחריות המשתמש בלבד."
    )
    await update.message.reply_text(text + FOOTER, parse_mode="Markdown")


# === טקסטים שיווקיים ===

async def handle_why_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🔥 *למה כדאי להיות מנוי?*\n\n"
        "כי Chance Predictor נותן לך יתרון סטטיסטי על פני משחק אקראי.\n\n"
        "כמנוי יומי אתה:\n"
        "• לא נשען רק על תחושות בטן\n"
        "• מקבל צירופים שהמערכת חישבה עבורך על בסיס נתונים\n"
        "• משחק בצורה יותר מודעת וחכמה\n\n"
        "המנוי היומי נותן לך גישה מלאה ל־24 שעות – ואתה בוחר מתי לנצל אותו."
    )
    await update.message.reply_text(text + FOOTER, parse_mode="Markdown")


async def handle_bot_advantage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎯 *מה היתרון של הבוט?*\n\n"
        "היתרון האמיתי של Chance Predictor הוא בנתונים.\n\n"
        "המערכת:\n"
        "• סורקת את תוצאות הצ׳אנס האחרונות\n"
        "• מחשבת לכל קלף כמה פעמים הופיע וכמה זמן לא יצא\n"
        "• מזהה דפוסים ומגמות שחוזרות על עצמן\n\n"
        "במקום לשחק \"בעיניים עצומות\", הבוט נותן תמונה סטטיסטית חדה "
        "של מה חם, מה קר ואיפה ייתכן שיש הזדמנות."
    )
    await update.message.reply_text(text + FOOTER, parse_mode="Markdown")


async def handle_what_you_get(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "💰 *מה מקבלים במנוי היומי?*\n\n"
        "כשתפתח מנוי יומי ל־Chance Predictor תקבל:\n\n"
        "• 🔥 3 תחזיות חמות בכל לחיצה\n"
        "• 🕒 גישה לפיצ׳רים מתקדמים (כמו היסטוריית תחזיות כשיתווסף)\n"
        "• 📊 סטטיסטיקות מורחבות לפי הנתונים המעודכנים\n"
        "• ⚙️ גישה לכל פיצ׳ר חדש שייכנס במהלך תקופת הבטא\n\n"
        "המטרה: לתת לך יתרון סטטיסטי – לא הבטחה לזכייה, אלא משחק חכם יותר."
    )
    await update.message.reply_text(text + FOOTER, parse_mode="Markdown")


async def handle_subscription_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    מסך רכישת המנוי – נוסח כפי שביקשת + כפתור לפתיחת צ'אט איתך בטלגרם.
    """
    user = update.effective_user

    text = (
        "💳 *מנוי יומי – Chance Predictor*\n\n"
        "המנוי מעניק גישה מלאה לכלי הניתוח והתחזיות למשך 24 שעות מלאות 🔥\n\n"
        "📌 *עלות המנוי:* 50 ₪ בלבד\n\n"
        "איך מצטרפים?\n\n"
        "שלחו הודעה בכפתור למטה!\n\n"
        "✔️ לאחר הפעלת המנוי תקבל גישה מלאה לכל הפיצ׳רים.\n\n"
        "לכל פנייה לגבי המערכת ומנויים שלחו הודעה ליוזר @eitayeliyahu"
    )

    # הודעה אוטומטית שתופיע אצלך בצ'אט
    encoded_text = (
        "Hi%20Eitay,%20I%20want%20to%20purchase%20a%20daily%20subscription%20"
        "to%20the%20Chance%20Predictor%20bot."
    )

    telegram_url = f"https://t.me/eitayeliyahu?text={encoded_text}"

    keyboard = [
        [InlineKeyboardButton("💬 שליחת הודעה לתשלום", url=telegram_url)],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)


# === ניהול כפתורים / תפריט ===

async def handle_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    מטפל בכל לחיצות הכפתורים של התפריט הראשי (ReplyKeyboard).
    בוחר את הפונקציה המתאימה לפי הטקסט שנשלח.
    """
    if not update.message:
        return

    text = (update.message.text or "").strip()
    print("USER CLICKED:", repr(text))

    # משותף - גם למנוי וגם לחינמי
    if "10 ההגרלות האחרונות" in text:
        await handle_last_10(update, context)

    # כפתור 2 – 3 קלפים חמים (סטטיסטיקה)
    elif "3 קלפים חמים להגרלה הבאה" in text:
        await handle_hot_cards(update, context)

    # כפתור 3 – קלף אוטומטי
    elif "קלף אוטומטי" in text:
        await handle_auto_card(update, context)

    elif "היסטוריית תחזיות" in text:
        await handle_history(update, context)

    elif "איך זה עובד" in text:
        await handle_info(update, context)

    elif "רכישת מנוי" in text:
        await handle_subscription_info(update, context)

    elif "למה כדאי להיות מנוי" in text:
        await handle_why_sub(update, context)

    elif "מה היתרון של הבוט" in text:
        await handle_bot_advantage(update, context)

    elif "מה מקבלים במנוי" in text:
        await handle_what_you_get(update, context)

    else:
        # כל טקסט אחר – פולבאק
        await fallback(update, context)


# === תנאי שימוש ===

async def handle_terms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📜 *תנאי שימוש – Chance Predictor*\n\n"
        "1. מהות השירות\n"
        "הבוט Chance Predictor הוא כלי לניתוח סטטיסטי של תוצאות הגרלות צ׳אנס. "
        "המערכת מציגה מידע, דפוסים ותחזיות הסתברותיות בלבד, ואינה מהווה ייעוץ השקעה, ייעוץ הימורים, "
        "או התחייבות לתוצאה כלשהי.\n\n"
        "2. אחריות המשתמש\n"
        "כל החלטה לסמן טופס, להשתתף בהגרלה או להוציא כסף – היא באחריות המשתמש בלבד. "
        "מפעיל הבוט אינו אחראי על רווחים, הפסדים, זכיות או אי־זכיות הנובעים משימוש בבוט.\n\n"
        "3. אין הבטחה לזכייה\n"
        "התחזיות אינן מבטיחות זכייה ואינן מבוססות על ידע פנימי או מידע שאינו ציבורי. "
        "מדובר בכלי אנליטי בלבד, המבוסס על נתונים היסטוריים ואלגוריתמים סטטיסטיים.\n\n"
        "4. שימוש הוגן\n"
        "אין להעביר את הגישה למנוי בתשלום לאחרים ללא אישור מפעיל הבוט. "
        "מפעיל הבוט רשאי לחסום גישה למשתמשים הפועלים בניגוד לתנאים אלו.\n\n"
        "5. שינויים בשירות\n"
        "התוכן, האלגוריתם והפיצ׳רים יכולים להשתנות ולהתעדכן מעת לעת ללא הודעה מראש.\n\n"
        "6. גיל מינימלי\n"
        "השימוש בבוט מיועד לבגירים מעל גיל 18.\n\n"
        "*שימוש בבוט מהווה הסכמה לתנאי השימוש הללו.*"
    )
    await update.message.reply_text(text + FOOTER, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📌 *רשימת פקודות:*\n\n"
        "/start – הפעלת הבוט\n"
        "/help – רשימת הפקודות המלאה\n"
        "/myid – הצגת ה־User ID שלך בטלגרם\n"
        "/grant – הענקת גישה למשתמש (למנהלים בלבד)\n"
        "/revoke – ביטול גישה למשתמש (למנהלים בלבד)\n"
        "/subinfo – בדיקת מצב המנוי שלך\n"
        "/terms – תנאי שימוש\n"
    )
    await update.message.reply_text(help_text + FOOTER, parse_mode="Markdown")


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /myid – מציג למשתמש את ה־User ID שלו בטלגרם.
    """
    user = update.effective_user
    text = (
        "🔑 *ה־User ID שלך:*\n\n"
        f"`{user.id}`\n\n"
        "שמור את המספר הזה במידת הצורך או השתמש בו מול מפעיל הבוט בעת רכישת מנוי."
    )
    await update.message.reply_text(text + FOOTER, parse_mode="Markdown")


# === פקודות אדמין למנויים ===

async def cmd_grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /grant בתגובה להודעה של המשתמש
    או:
    /grant <user_id>
    מפעיל מנוי ל-24 שעות.
    """
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return

    message = update.effective_message
    target_id = None

    # אם הפקודה נשלחה בתגובה על הודעה של משתמש
    if message and message.reply_to_message:
        target_id = message.reply_to_message.from_user.id

    # אם נשלח /grant <user_id>
    elif context.args:
        try:
            target_id = int(context.args[0])
        except ValueError:
            await message.reply_text("שימוש: /grant <user_id> או בתגובה על הודעה של המשתמש." + FOOTER)
            return

    # בלי תגובה ובלי ארגומנט
    else:
        await message.reply_text("שימוש: /grant <user_id> או בתגובה על הודעה של המשתמש." + FOOTER)
        return

    now = time.time()
    expires_at = now + 24 * 60 * 60  # 24 שעות קדימה

    subscribers[str(target_id)] = expires_at
    save_subscribers()

    try:
        await context.bot.send_message(
            target_id,
            "✅ המנוי היומי שלך לבוט Chance Predictor הופעל.\n"
            "יש לך גישה מלאה ל־24 השעות הקרובות 🔮" + FOOTER
        )
    except Exception:
        pass

    await message.reply_text(
        f"מנוי יומי הופעל למשתמש {target_id} ✅\n"
        "הגישה תפקע אוטומטית בעוד 24 שעות." + FOOTER
    )


async def cmd_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /revoke בתגובה להודעה של המשתמש
    או:
    /revoke <user_id>
    מבטל מנוי (גם אם עדיין בתוקף).
    """
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return

    target_id = None

    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
    elif context.args:
        try:
            target_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("שימוש: /revoke <user_id> או בתגובה על הודעה של המשתמש." + FOOTER)
            return
    else:
        await update.message.reply_text("שימוש: /revoke <user_id> או בתגובה על הודעה של המשתמש." + FOOTER)
        return

    uid = str(target_id)
    if uid in subscribers:
        del subscribers[uid]
        save_subscribers()
        await update.message.reply_text(f"הגישה של {target_id} בוטלה." + FOOTER)
        try:
            await context.bot.send_message(
                target_id,
                "הגישה שלך לבוט Chance Predictor בוטלה.\n"
                "אם מדובר בטעות – אפשר לפנות למפעיל." + FOOTER
            )
        except Exception:
            pass
    else:
        await update.message.reply_text("למשתמש הזה לא הייתה גישה פעילה." + FOOTER)


async def cmd_subinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /subinfo – המשתמש יכול לבדוק אם יש לו מנוי פעיל ומתי הוא פג.
    """
    user = update.effective_user
    uid = str(user.id)
    message = update.effective_message

    # אם זה אדמין – אפשר לתת לו תשובה מיוחדת (אבל בלי מנוי אוטומטי)
    if user.id in ADMIN_IDS:
        await message.reply_text(
            "אתה מוגדר כאדמין במערכת.\n"
            "לפני שאתה בודק חוויית משתמש – ודא אם יש לך מנוי פעיל בעזרת /myid ו-/grant לפי הצורך." + FOOTER
        )
        return

    expiry_ts = subscribers.get(uid)

    # אם אין רשומה בכלל – אין מנוי פעיל
    if not expiry_ts:
        await message.reply_text(
            "כרגע אין לך מנוי יומי פעיל.\n\n"
            "אפשר לפתוח גישה ל־24 שעות מלאות דרך ״💳 רכישת מנוי״." + FOOTER
        )
        return

    # כאן אנחנו בטוחים שיש timestamp תקין
    expiry_dt = datetime.fromtimestamp(expiry_ts)
    text = (
        "✅ יש לך מנוי יומי פעיל.\n"
        f"תוקף עד: {expiry_dt.strftime('%d/%m/%Y %H:%M:%S')}\n\n"
        "מומלץ לנצל את המנוי בזמן שהוא פעיל – "
        "התחזיות מתעדכנות לפי הנתונים האחרונים."
    )
    await message.reply_text(text + FOOTER)


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /broadcast <הודעה> – שליחת הודעה לכל המנויים (שעדיין ברשימה).
    """
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return

    if not context.args:
        await update.message.reply_text("שימוש: /broadcast <הודעה לשליחה לכל המנויים>" + FOOTER)
        return

    message_text = " ".join(context.args) + FOOTER

    sent = 0
    failed = 0

    for uid in list(subscribers.keys()):
        try:
            await context.bot.send_message(
                chat_id=int(uid),
                text=message_text
            )
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"הודעה נשלחה ל-{sent} מנויים. נכשלה עבור {failed}." + FOOTER
    )


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    text = update.message.text or ""
    cmd = text.split()[0]  # לוקח רק את הפקודה עצמה (/bla)

    # אם הפקודה *לא* ברשימת הפקודות שלך – נענה "הקש /help"
    if cmd not in KNOWN_COMMANDS:
        await update.message.reply_text("הקש /help לקבלת רשימת פקודות." + FOOTER)


# === fallback ===

async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_sub = is_subscriber(user.id)
    text = "בחר אחת מהאפשרויות בתפריט למטה 👇"
    await update.message.reply_text(text + FOOTER, reply_markup=get_main_keyboard(is_sub))


# === main ===

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # פקודות
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("terms", handle_terms))
    app.add_handler(CommandHandler("subinfo", cmd_subinfo))
    app.add_handler(CommandHandler("grant", cmd_grant))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("myid", cmd_myid))

    # פקודה לא מוכרת
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    # כפתורים / טקסטים + כל טקסט שאינו פקודה
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_buttons))

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
