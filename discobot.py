import json
import os
import sqlite3
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, BotCommand
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

TOKEN = os.environ.get("BOT_TOKEN")
DB_PATH = os.environ.get("DB_PATH", "discobot.db")

allowed = os.environ.get("ALLOWED_TELEGRAM_USER_IDS", "").strip()
ALLOWED_USER_IDS = {
    int(x.strip()) for x in allowed.split(",") if x.strip().isdigit()
} if allowed else set()

STEPS = [
    ("client", "Client", "text", "Nom du client ?"),
    ("site", "Site", "text", "Nom du site / bâtiment ?"),
    ("emplacement", "Emplacement", "text", "Où se trouve le disconnecteur ? (chaufferie, regard, local technique...)"),
    ("photo_loin", "Photo de loin", "photo", "Envoie une photo générale montrant l'installation et son environnement."),
    ("photo_pres", "Photo de près", "photo", "Envoie une photo rapprochée du disconnecteur et de sa plaque si possible."),
    ("type", "Type / modèle", "text", "Type, marque et référence du disconnecteur ?"),
    ("serie", "N° de série", "text", "Numéro de série ? Écris « illisible » s'il n'est pas lisible."),
    ("diametre", "Diamètre", "text", "Diamètre nominal ? Ex. DN20, DN50, DN80."),
    ("vanne_amont", "Vanne amont", "text", "Présence et état de la vanne amont ?"),
    ("vanne_aval", "Vanne aval", "text", "Présence et état de la vanne aval ?"),
    ("clapets", "Clapets / décharge", "text", "État visuel des clapets et de la soupape de décharge ?"),
    ("pression_amont", "Pression amont", "text", "Pression amont mesurée, avec unité ?"),
    ("pression_zone", "Pression zone intermédiaire", "text", "Pression de la zone intermédiaire mesurée, avec unité ?"),
    ("pression_aval", "Pression aval", "text", "Pression aval mesurée, avec unité ?"),
    ("differentiel", "Différentiel", "text", "Différentiel mesuré, avec unité ?"),
    ("essais", "Essais", "text", "Résultat des essais d'étanchéité / mise à décharge et observations ?"),
    ("observations", "Observations", "text", "Anomalies, remarques ou travaux à prévoir ?"),
    ("intervenant", "Intervenant", "text", "Nom de l'intervenant ?"),
]

STATUS_LABELS = {
    "passed": "PASSÉ",
    "impossible": "IMPOSSIBLE",
    "non_verifiable": "NON VÉRIFIABLE",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_db():
    con = sqlite3.connect(DB_PATH)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            current_step INTEGER NOT NULL DEFAULT 0,
            data TEXT NOT NULL DEFAULT '{}',
            completed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    con.commit()
    return con


def get_session(user_id, chat_id):
    con = connect_db()
    row = con.execute(
        """
        SELECT id, current_step, data
        FROM sessions
        WHERE user_id=? AND chat_id=? AND completed=0
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id, chat_id),
    ).fetchone()
    con.close()
    if not row:
        return None
    return {"id": row[0], "current_step": row[1], "data": json.loads(row[2])}


def create_session(user_id, chat_id):
    con = connect_db()
    con.execute(
        """
        INSERT INTO sessions(user_id, chat_id, current_step, data, completed, created_at, updated_at)
        VALUES (?, ?, 0, '{}', 0, ?, ?)
        """,
        (user_id, chat_id, now_iso(), now_iso()),
    )
    con.commit()
    con.close()


def save_session(session, completed=False):
    con = connect_db()
    con.execute(
        """
        UPDATE sessions
        SET current_step=?, data=?, completed=?, updated_at=?
        WHERE id=?
        """,
        (
            session["current_step"],
            json.dumps(session["data"], ensure_ascii=False),
            1 if completed else 0,
            now_iso(),
            session["id"],
        ),
    )
    con.commit()
    con.close()


def allowed_user(user_id):
    return not ALLOWED_USER_IDS or user_id in ALLOWED_USER_IDS


def status_keyboard():
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("⏭ Passer", callback_data="status:passed"),
            InlineKeyboardButton("⚠️ Impossible", callback_data="status:impossible"),
        ], [
            InlineKeyboardButton("❔ Non vérifiable", callback_data="status:non_verifiable"),
        ]]
    )


async def send_step(chat_id, step_index, context):
    if step_index >= len(STEPS):
        return
    _, label, _, prompt = STEPS[step_index]
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Étape {step_index + 1}/{len(STEPS)} — {label}\n\n{prompt}",
        reply_markup=status_keyboard(),
    )


def value_text(value):
    if isinstance(value, dict) and value.get("status"):
        return STATUS_LABELS.get(value["status"], value["status"])
    if isinstance(value, dict) and value.get("photo_file_id"):
        return "📷 photo enregistrée"
    return str(value)


def build_summary(data):
    lines = ["✅ CONTRÔLE TERMINÉ — RÉCAPITULATIF", ""]
    for key, label, _, _ in STEPS:
        lines.append(f"• {label} : {value_text(data.get(key, '—'))}")
    lines.extend([
        "",
        "⚠️ Discobot consigne uniquement les informations réellement saisies.",
        "Il n'invente aucune mesure et ne déclare pas automatiquement l'appareil conforme.",
    ])
    return "\n".join(lines)


async def finish_session(session, chat_id, context):
    session["current_step"] = len(STEPS)
    save_session(session, completed=True)
    summary = build_summary(session["data"])
    for i in range(0, len(summary), 3900):
        await context.bot.send_message(chat_id=chat_id, text=summary[i:i + 3900])
    await context.bot.send_message(chat_id=chat_id, text="Pour un nouveau contrôle : /nouveau")


async def advance(session, chat_id, value, context):
    idx = session["current_step"]
    if idx >= len(STEPS):
        await finish_session(session, chat_id, context)
        return
    key = STEPS[idx][0]
    session["data"][key] = value
    session["current_step"] += 1
    save_session(session)
    if session["current_step"] >= len(STEPS):
        await finish_session(session, chat_id, context)
    else:
        await send_step(chat_id, session["current_step"], context)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not allowed_user(user_id):
        await update.effective_message.reply_text("Accès non autorisé.")
        return
    await update.effective_message.reply_text(
        "👋 Bienvenue dans Discobot Aqualeo.\n\n"
        "Je guide le contrôle d'un disconnecteur étape par étape. "
        "Chaque étape peut être renseignée, passée, marquée impossible ou non vérifiable. "
        "Aucune mesure n'est inventée.\n\n"
        f"Ton identifiant Telegram : {user_id}\n\n"
        "/nouveau — démarrer un contrôle\n"
        "/resume — reprendre le contrôle en cours\n"
        "/annuler — clôturer le contrôle en cours"
    )


async def nouveau(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if not allowed_user(user_id):
        await update.effective_message.reply_text("Accès non autorisé.")
        return

    existing = get_session(user_id, chat_id)
    if existing:
        await update.effective_message.reply_text(
            "Un contrôle est déjà en cours. Utilise /resume ou /annuler."
        )
        return

    create_session(user_id, chat_id)
    session = get_session(user_id, chat_id)
    await update.effective_message.reply_text("🆕 Nouveau contrôle créé.")
    await send_step(chat_id, session["current_step"], context)


async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    session = get_session(user_id, chat_id)
    if not session:
        await update.effective_message.reply_text("Aucun contrôle en cours. Utilise /nouveau.")
        return
    await send_step(chat_id, session["current_step"], context)


async def annuler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    session = get_session(user_id, chat_id)
    if not session:
        await update.effective_message.reply_text("Aucun contrôle en cours.")
        return
    save_session(session, completed=True)
    await update.effective_message.reply_text(
        "Contrôle clôturé sans validation. Tu peux recommencer avec /nouveau."
    )


async def callback_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    chat_id = query.message.chat.id

    if not allowed_user(user_id):
        await query.answer("Accès non autorisé", show_alert=True)
        return

    status = query.data.split(":", 1)[1]
    if status not in STATUS_LABELS:
        return

    session = get_session(user_id, chat_id)
    if not session:
        await query.answer("Aucun contrôle en cours", show_alert=True)
        return

    await advance(session, chat_id, {"status": status}, context)


async def receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if not allowed_user(user_id):
        await update.effective_message.reply_text("Accès non autorisé.")
        return

    session = get_session(user_id, chat_id)
    if not session:
        await update.effective_message.reply_text("Aucun contrôle en cours. Utilise /nouveau.")
        return

    idx = session["current_step"]
    if idx >= len(STEPS):
        await finish_session(session, chat_id, context)
        return

    _, _, kind, _ = STEPS[idx]

    if kind == "photo":
        if not update.effective_message.photo:
            await update.effective_message.reply_text(
                "J'attends une photo pour cette étape. "
                "Sinon utilise Passer / Impossible / Non vérifiable."
            )
            return
        photo = update.effective_message.photo[-1]
        await advance(
            session,
            chat_id,
            {
                "photo_file_id": photo.file_id,
                "file_unique_id": photo.file_unique_id,
                "width": photo.width,
                "height": photo.height,
            },
            context,
        )
        return

    text = (update.effective_message.text or "").strip()
    if not text:
        await update.effective_message.reply_text(
            "J'attends une réponse texte, ou utilise un bouton de statut."
        )
        return
    await advance(session, chat_id, text, context)


async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("start", "Accueil Discobot"),
        BotCommand("nouveau", "Nouveau contrôle"),
        BotCommand("resume", "Reprendre le contrôle"),
        BotCommand("annuler", "Annuler le contrôle en cours"),
    ])


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN n'est pas configuré.")

    connect_db().close()

    app = (
        Application.builder()
        .token(TOKEN)
        .concurrent_updates(False)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("nouveau", nouveau))
    app.add_handler(CommandHandler("resume", resume))
    app.add_handler(CommandHandler("annuler", annuler))
    app.add_handler(CallbackQueryHandler(callback_status, pattern=r"^status:"))
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, receive))

    app.run_polling()


if __name__ == "__main__":
    main()
