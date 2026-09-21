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

from pricing import ensure_pricing_tables, find_prices

TOKEN = os.environ.get("BOT_TOKEN")
DB_PATH = os.environ.get("DB_PATH", "discobot.db")

allowed = os.environ.get("ALLOWED_TELEGRAM_USER_IDS", "").strip()
ALLOWED_USER_IDS = {
    int(x.strip()) for x in allowed.split(",") if x.strip().isdigit()
} if allowed else set()

SITE_STEPS = [
    ("client", "Client", "text", "Nom du client / organisme ?"),
    ("site", "Site", "text", "Nom du site / bâtiment ?"),
    ("adresse", "Adresse du site", "text", "Adresse complète du site ?"),
    ("contact_site", "Contact sur place", "text", "Nom / fonction / téléphone du contact sur place, si connu ?"),
    ("nombre_appareils", "Nombre d'appareils", "text", "Combien de disconnecteurs sont présents sur ce site ?"),
]

DEVICE_STEPS = [
    ("emplacement", "Emplacement précis", "text", "Emplacement exact de l'appareil ? Ex. chaufferie, regard extérieur, local incendie..."),
    ("photo_loin", "Photo générale", "photo", "Envoie une photo générale de l'installation et de son environnement."),
    ("photo_pres", "Photo rapprochée", "photo", "Envoie une photo rapprochée du disconnecteur et de sa plaque si possible."),
    ("marque", "Marque", "text", "Marque du disconnecteur ?"),
    ("modele", "Modèle / référence", "text", "Modèle ou référence fabricant ?"),
    ("type", "Type", "text", "Type du disconnecteur ? Ex. BA."),
    ("serie", "N° de série", "text", "Numéro de série ? Écris « illisible » s'il n'est pas lisible."),
    ("diametre", "Diamètre", "text", "Diamètre nominal ? Ex. DN20, DN50, DN80."),
    ("annee_pose", "Année de pose", "text", "Année de pose ou âge estimé de l'appareil ? Si inconnu, indique « inconnu »."),
    ("vanne_amont", "Vanne amont", "text", "Présence et état de la vanne amont ?"),
    ("vanne_aval", "Vanne aval", "text", "Présence et état de la vanne aval ?"),
    ("clapets", "Clapets / décharge", "text", "État visuel des clapets et de la soupape de décharge ?"),
    ("pression_amont", "Pression amont", "text", "Pression amont réellement mesurée, avec unité ?"),
    ("pression_zone", "Pression zone intermédiaire", "text", "Pression de la zone intermédiaire réellement mesurée, avec unité ?"),
    ("pression_aval", "Pression aval", "text", "Pression aval réellement mesurée, avec unité ?"),
    ("differentiel", "Différentiel", "text", "Différentiel réellement mesuré, avec unité ?"),
    ("essais", "Essais", "text", "Résultat des essais d'étanchéité / mise à décharge ?"),
    ("diagnostic", "Diagnostic", "text", "Diagnostic technique : conforme au contrôle, nettoyage, clapet, soupape, joints, kit interne, appareil à remplacer, autre ?"),
    ("recommandation", "Suite à prévoir", "text", "Suite proposée : aucune / surveillance / réparation ciblée / kit complet / remplacement complet ?"),
    ("intervenant", "Intervenant", "text", "Nom de l'intervenant ?"),
]

ALL_STEPS = SITE_STEPS + DEVICE_STEPS

STATUS_LABELS = {
    "passed": "PASSÉ",
    "impossible": "IMPOSSIBLE",
    "non_verifiable": "NON VÉRIFIABLE",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
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
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS controls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            client TEXT,
            site TEXT,
            address TEXT,
            site_contact TEXT,
            device_count INTEGER,
            data TEXT NOT NULL,
            visit_type TEXT NOT NULL DEFAULT 'CONTROL_DIAGNOSTIC',
            created_at TEXT NOT NULL
        )
    """)
    ensure_pricing_tables(con)
    con.commit()
    return con


def blank_data():
    return {
        "site": {},
        "devices": [],
        "current_device": {},
        "device_index": 1,
        "visit_type": "CONTROL_DIAGNOSTIC",
    }


def get_session(user_id, chat_id):
    con = connect_db()
    row = con.execute("""
        SELECT id, current_step, data
        FROM sessions
        WHERE user_id=? AND chat_id=? AND completed=0
        ORDER BY id DESC LIMIT 1
    """, (user_id, chat_id)).fetchone()
    con.close()
    if not row:
        return None
    return {"id": row[0], "current_step": row[1], "data": json.loads(row[2])}


def create_session(user_id, chat_id):
    data = json.dumps(blank_data(), ensure_ascii=False)
    con = connect_db()
    con.execute("""
        INSERT INTO sessions(user_id, chat_id, current_step, data, completed, created_at, updated_at)
        VALUES (?, ?, 0, ?, 0, ?, ?)
    """, (user_id, chat_id, data, now_iso(), now_iso()))
    con.commit()
    con.close()


def save_session(session, completed=False):
    con = connect_db()
    con.execute("""
        UPDATE sessions
        SET current_step=?, data=?, completed=?, updated_at=?
        WHERE id=?
    """, (
        session["current_step"],
        json.dumps(session["data"], ensure_ascii=False),
        1 if completed else 0,
        now_iso(),
        session["id"],
    ))
    con.commit()
    con.close()


def persist_control(session):
    data = session["data"]
    site = data.get("site", {})
    con = connect_db()
    con.execute("""
        INSERT INTO controls(
            session_id, client, site, address, site_contact, device_count,
            data, visit_type, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        session["id"],
        value_text(site.get("client", "")),
        value_text(site.get("site", "")),
        value_text(site.get("adresse", "")),
        value_text(site.get("contact_site", "")),
        total_devices(data),
        json.dumps(data, ensure_ascii=False),
        data.get("visit_type", "CONTROL_DIAGNOSTIC"),
        now_iso(),
    ))
    con.commit()
    con.close()


def allowed_user(user_id):
    return not ALLOWED_USER_IDS or user_id in ALLOWED_USER_IDS


def status_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏭ Passer", callback_data="status:passed"),
            InlineKeyboardButton("⚠️ Impossible", callback_data="status:impossible"),
        ],
        [InlineKeyboardButton("❔ Non vérifiable", callback_data="status:non_verifiable")],
    ])


def get_step(step_index):
    return ALL_STEPS[step_index]


def total_devices(data):
    value = data.get("site", {}).get("nombre_appareils", 1)
    if isinstance(value, dict):
        return 1
    try:
        total = int(str(value).strip())
        return max(1, total)
    except Exception:
        return 1


def device_position_text(data):
    return f"Appareil {data.get('device_index', 1)}/{total_devices(data)}"


async def send_step(chat_id, session, context):
    idx = session["current_step"]
    if idx >= len(ALL_STEPS):
        return

    key, label, _, prompt = get_step(idx)
    data = session["data"]

    if idx >= len(SITE_STEPS):
        heading = f"{device_position_text(data)} — {label}"
    else:
        heading = f"Site — {label}"

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"{heading}\n\n{prompt}",
        reply_markup=status_keyboard(),
    )


def value_text(value):
    if isinstance(value, dict) and value.get("status"):
        return STATUS_LABELS.get(value["status"], value["status"])
    if isinstance(value, dict) and value.get("photo_file_id"):
        return "📷 photo enregistrée"
    return str(value)


def build_summary(data):
    site = data.get("site", {})
    lines = [
        "✅ 1er PASSAGE TERMINÉ — CONTRÔLE / DIAGNOSTIC",
        "",
        f"Client : {value_text(site.get('client', '—'))}",
        f"Site : {value_text(site.get('site', '—'))}",
        f"Adresse : {value_text(site.get('adresse', '—'))}",
        f"Nombre d'appareils annoncé : {value_text(site.get('nombre_appareils', '—'))}",
        "",
    ]

    for n, device in enumerate(data.get("devices", []), start=1):
        lines.append(f"--- APPAREIL {n}/{len(data.get('devices', []))} ---")
        for key, label, _, _ in DEVICE_STEPS:
            lines.append(f"• {label} : {value_text(device.get(key, '—'))}")
        lines.append("")

    lines.extend([
        "RÈGLE DEVIS : aucun prix de pièce ne doit être inventé.",
        "Un devis de réparation/remplacement ne pourra utiliser qu'un prix fournisseur avec source et date de vérification.",
        "Les tarifs de plus de 31 jours sont considérés à actualiser avant simulation automatique.",
        "",
        "Si une intervention est nécessaire : 2e passage = réparation ciblée, kit interne ou remplacement selon diagnostic et comparaison économique.",
    ])
    return "\n".join(lines)


def save_current_value(session, value):
    idx = session["current_step"]
    key, _, _, _ = get_step(idx)

    if idx < len(SITE_STEPS):
        session["data"]["site"][key] = value
    else:
        session["data"]["current_device"][key] = value


async def complete_current_device(session, chat_id, context):
    data = session["data"]
    device = dict(data.get("current_device", {}))
    device["device_number"] = data.get("device_index", 1)
    data.setdefault("devices", []).append(device)

    if data["device_index"] < total_devices(data):
        data["device_index"] += 1
        data["current_device"] = {}
        session["current_step"] = len(SITE_STEPS)
        save_session(session)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ Appareil {data['device_index'] - 1}/{total_devices(data)} enregistré.\n\n"
                 f"On passe à l'appareil {data['device_index']}/{total_devices(data)}."
        )
        await send_step(chat_id, session, context)
        return False

    persist_control(session)
    session["current_step"] = len(ALL_STEPS)
    save_session(session, completed=True)
    summary = build_summary(data)
    for i in range(0, len(summary), 3900):
        await context.bot.send_message(chat_id=chat_id, text=summary[i:i + 3900])
    await context.bot.send_message(
        chat_id=chat_id,
        text="Le contrôle est archivé. Si une réparation est nécessaire, le 2e passage sera rattaché au même site/appareil."
    )
    return True


async def advance(session, chat_id, value, context):
    idx = session["current_step"]
    save_current_value(session, value)
    session["current_step"] += 1
    save_session(session)

    if session["current_step"] >= len(ALL_STEPS):
        await complete_current_device(session, chat_id, context)
    else:
        await send_step(chat_id, session, context)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not allowed_user(user_id):
        await update.effective_message.reply_text("Accès non autorisé.")
        return

    await update.effective_message.reply_text(
        "👋 Discobot Aqualeo V0.2\n\n"
        "1er passage : contrôle + diagnostic.\n"
        "2e passage : intervention uniquement si nécessaire.\n\n"
        "Prix : aucune estimation fournisseur inventée. "
        "Chaque tarif utilisé pour un devis devra avoir une source et une date de vérification, "
        "avec alerte de mise à jour au-delà de 31 jours.\n\n"
        f"Ton identifiant Telegram : {user_id}\n\n"
        "/nouveau — nouveau contrôle\n"
        "/resume — reprendre\n"
        "/annuler — clôturer sans validation\n"
        "/tarifs — état de la base prix"
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
    await update.effective_message.reply_text(
        "🆕 Nouveau contrôle créé. On commence par le dossier client/site."
    )
    await send_step(chat_id, session, context)


async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id, update.effective_chat.id)
    if not session:
        await update.effective_message.reply_text("Aucun contrôle en cours. Utilise /nouveau.")
        return
    await send_step(update.effective_chat.id, session, context)


async def annuler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id, update.effective_chat.id)
    if not session:
        await update.effective_message.reply_text("Aucun contrôle en cours.")
        return
    save_session(session, completed=True)
    await update.effective_message.reply_text(
        "Contrôle clôturé sans validation. Aucune donnée manquante n'a été inventée."
    )


async def tarifs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed_user(update.effective_user.id):
        await update.effective_message.reply_text("Accès non autorisé.")
        return

    con = connect_db()
    prices = find_prices(con)
    con.close()

    if not prices:
        await update.effective_message.reply_text(
            "💶 Base tarifs vide pour le moment.\n\n"
            "Aucun devis automatique ne sera calculé avec un prix inventé. "
            "Les tarifs devront venir d'une source vérifiée : compte fournisseur, export tarif, facture d'achat ou prix confirmé."
        )
        return

    fresh = sum(1 for p in prices if p["fresh"])
    stale = len(prices) - fresh
    await update.effective_message.reply_text(
        f"💶 Base tarifs\n\n"
        f"Références enregistrées : {len(prices)}\n"
        f"Tarifs à jour (≤31 jours) : {fresh}\n"
        f"Tarifs à actualiser : {stale}\n\n"
        "Les tarifs périmés ne seront pas utilisés automatiquement pour établir un devis."
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
    _, _, kind, _ = get_step(idx)

    if kind == "photo":
        if not update.effective_message.photo:
            await update.effective_message.reply_text(
                "J'attends une photo. Sinon utilise Passer / Impossible / Non vérifiable."
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
                "archive_status": "A_TELECHARGER_DANS_DOSSIER_APPAREIL",
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
        BotCommand("annuler", "Annuler le contrôle"),
        BotCommand("tarifs", "État des prix fournisseurs"),
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
    app.add_handler(CommandHandler("tarifs", tarifs))
    app.add_handler(CallbackQueryHandler(callback_status, pattern=r"^status:"))
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, receive))

    app.run_polling()


if __name__ == "__main__":
    main()
