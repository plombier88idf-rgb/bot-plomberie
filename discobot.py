import base64
import json
import os
import re
import sqlite3
from datetime import datetime, timezone

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

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
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna").strip() or "gpt-5.6-luna"
AI_CLIENT = AsyncOpenAI(api_key=OPENAI_API_KEY) if (AsyncOpenAI and OPENAI_API_KEY) else None

allowed = os.environ.get("ALLOWED_TELEGRAM_USER_IDS", "").strip()
ALLOWED_USER_IDS = {
    int(x.strip()) for x in allowed.split(",") if x.strip().isdigit()
} if allowed else set()

SITE_STEPS = [
    ("client", "Client / organisme", "text", "Qui est le client / organisme ?"),
    ("site", "Site", "text", "Nom du site / bâtiment ?"),
    ("adresse", "Adresse du site", "text", "Adresse complète du site ?"),
    ("contact_nom", "Contact", "text", "Nom et prénom du contact qui suit le dossier ?"),
    ("contact_fonction", "Fonction du contact", "text", "Fonction / service du contact ?"),
    ("contact_email", "E-mail du contact", "text", "E-mail du contact pour devis, rapports et factures ?"),
    ("contact_tel", "Téléphone du contact", "text", "Numéro de téléphone du contact ?"),
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

# Le dossier d'entrée doit rester ultra simple. Les coordonnées de contact détaillées
# sont conservées si elles sont lues sur le document, mais elles ne doivent jamais
# bloquer le passage au contrôle ni être redemandées une par une.
OPTIONAL_SITE_KEYS = {
    "contact_nom",
    "contact_fonction",
    "contact_email",
    "contact_tel",
    "nombre_appareils",
}

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
        "dossier_status": "PROSPECT_A_QUALIFIER",
        "prefill_devices": [],
        "source_photos": [],
        "notes_intake": "",
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
        " | ".join(filter(None, [
            value_text(site.get("contact_nom", "")),
            value_text(site.get("contact_fonction", "")),
            value_text(site.get("contact_email", "")),
            value_text(site.get("contact_tel", "")),
        ])),
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


ADDRESS_RE = re.compile(
    r"\b\d{1,4}(?:\s*(?:bis|ter))?\s+"
    r"(?:rue|avenue|av\.?|boulevard|bd\.?|route|chemin|all[ée]e|impasse|place|quai|cours)\b"
    r".*?\b\d{5}\s+[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\- ]*",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\d)(?:(?:\+33|0)[1-9](?:[ .\-]?\d{2}){4})(?!\d)", re.IGNORECASE)


def clean_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip(" \t\n,;:-")
        return value or None
    return value


def local_smart_extract(text):
    """Extraction prudente sans IA : adresse, e-mail, téléphone, nombre et champs préfixés."""
    text = (text or "").strip()
    out = {"site": {}, "devices": [], "notes": ""}
    if not text:
        return out

    explicit = {
        "client": r"(?:^|\n)\s*client\s*[:=]\s*([^\n;]+)",
        "site": r"(?:^|\n)\s*site\s*[:=]\s*([^\n;]+)",
        "adresse": r"(?:^|\n)\s*adresse\s*[:=]\s*([^\n;]+)",
        "contact_nom": r"(?:^|\n)\s*(?:contact|nom)\s*[:=]\s*([^\n;]+)",
        "contact_fonction": r"(?:^|\n)\s*(?:fonction|service)\s*[:=]\s*([^\n;]+)",
        "contact_email": r"(?:^|\n)\s*(?:e-?mail|mail)\s*[:=]\s*([^\n;]+)",
        "contact_tel": r"(?:^|\n)\s*(?:t[ée]l(?:[ée]phone)?)\s*[:=]\s*([^\n;]+)",
    }
    for key, pattern in explicit.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            out["site"][key] = clean_value(m.group(1))

    m = EMAIL_RE.search(text)
    if m and not out["site"].get("contact_email"):
        out["site"]["contact_email"] = m.group(0)

    m = PHONE_RE.search(text)
    if m and not out["site"].get("contact_tel"):
        out["site"]["contact_tel"] = m.group(0)

    m = ADDRESS_RE.search(text)
    if m:
        out["site"]["adresse"] = clean_value(m.group(0))
        prefix = clean_value(text[:m.start()])
        if prefix and not out["site"].get("site"):
            prefix = re.sub(r"^(?:site\s*[:=]\s*)", "", prefix, flags=re.IGNORECASE)
            if len(prefix) <= 120:
                out["site"]["site"] = prefix

    m = re.search(r"\b(\d{1,2})\s+(?:disconnecteur(?:s)?|appareil(?:s)?|BA)\b", text, re.IGNORECASE)
    if m:
        out["site"]["nombre_appareils"] = int(m.group(1))

    device = {}
    m = re.search(r"\bDN\s*([0-9]{2,3})\b", text, re.IGNORECASE)
    if m:
        device["diametre"] = f"DN{m.group(1)}"
    m = re.search(r"\b(BA|CA|EA|HA|HD|DC)\b", text, re.IGNORECASE)
    if m:
        device["type"] = m.group(1).upper()
    m = re.search(r"\b(?:n[°o]\s*(?:de\s*)?s[ée]rie|s[ée]rie)\s*[:#-]?\s*([A-Z0-9._/-]{4,})", text, re.IGNORECASE)
    if m:
        device["serie"] = m.group(1)
    if device:
        out["devices"].append(device)

    return out


def extraction_has_data(extracted):
    if not extracted:
        return False
    if any(clean_value(v) is not None for v in (extracted.get("site") or {}).values()):
        return True
    return any(any(clean_value(v) is not None for v in d.values()) for d in (extracted.get("devices") or []))


def merge_extractions(base, extra):
    result = {
        "site": dict((base or {}).get("site") or {}),
        "devices": list((base or {}).get("devices") or []),
        "notes": (base or {}).get("notes") or "",
    }
    if not extra:
        return result
    for key, value in (extra.get("site") or {}).items():
        value = clean_value(value)
        if value is not None:
            result["site"][key] = value
    if extra.get("devices"):
        result["devices"] = [
            {k: clean_value(v) for k, v in d.items() if clean_value(v) is not None}
            for d in extra["devices"] if isinstance(d, dict)
        ]
    if clean_value(extra.get("notes")):
        result["notes"] = clean_value(extra.get("notes"))
    return result


async def ai_extract(text=None, image_data_uri=None):
    """Extraction structurée par IA. Une donnée absente reste vide : aucune invention."""
    if not AI_CLIENT:
        return None

    prompt = """
Tu es le module d'extraction de Discobot Aqualeo.
Extrais uniquement les informations réellement présentes dans le texte ou visibles sur l'image.
N'invente jamais un nom, une adresse, un contact, une référence, un diamètre ou une mesure.
Réponds uniquement avec un objet JSON valide, sans markdown, sous cette forme :
{
  "site": {
    "client": null,
    "site": null,
    "adresse": null,
    "contact_nom": null,
    "contact_fonction": null,
    "contact_email": null,
    "contact_tel": null,
    "nombre_appareils": null
  },
  "devices": [
    {
      "emplacement": null,
      "marque": null,
      "modele": null,
      "type": null,
      "serie": null,
      "diametre": null
    }
  ],
  "notes": null
}
Règles d'extraction importantes :
- Lis tout le document : en-tête, donneur d'ordre, établissement, adresse d'intervention, contact, téléphone, e-mail, tableau, remarques et liste d'appareils.
- Distingue le client / donneur d'ordre du site d'intervention quand les deux sont clairement visibles.
- Si un seul établissement ou organisme est clairement visible et qu'aucun client distinct n'est indiqué, tu peux mettre ce même nom dans "client" et "site" : cela évite de redemander deux fois la même chose.
- "contact_nom" = la personne utile pour ce dossier si elle est explicitement présente. Mets aussi son e-mail et son téléphone dans les champs dédiés lorsqu'ils sont visibles.
- Pour nombre_appareils, utilise un entier seulement s'il est explicite ou si le document montre clairement une liste exhaustive.
- Si plusieurs disconnecteurs/appareils sont clairement listés, crée un objet distinct par appareil avec tout ce qui est lisible : emplacement, marque, modèle, type, série et diamètre.
- Une donnée incertaine reste null. Ne transforme jamais une supposition en information certaine.
Pour une photo de document, lis le document. Pour une photo d'installation, relève seulement ce qui est lisible ou visible.
"""
    content = [{"type": "input_text", "text": prompt}]
    if text:
        content.append({"type": "input_text", "text": f"Texte utilisateur :\n{text}"})
    if image_data_uri:
        content.append({"type": "input_image", "image_url": image_data_uri})

    try:
        response = await AI_CLIENT.responses.create(
            model=OPENAI_MODEL,
            input=[{"role": "user", "content": content}],
        )
        raw = (response.output_text or "").strip()
        fence = chr(96) * 3
        if raw.startswith(fence):
            raw = re.sub(r"^.{3}(?:json)?\s*", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"\s*.{3}$", "", raw)
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start:end + 1]
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception as exc:
        print(f"[Discobot] Analyse IA impossible: {exc}")
        return None


async def extract_from_message(update, context, include_photo=False):
    text = (update.effective_message.text or update.effective_message.caption or "").strip()
    local = local_smart_extract(text)
    image_data_uri = None

    if include_photo and update.effective_message.photo:
        try:
            photo = update.effective_message.photo[-1]
            tg_file = await context.bot.get_file(photo.file_id)
            blob = await tg_file.download_as_bytearray()
            encoded = base64.b64encode(bytes(blob)).decode("ascii")
            image_data_uri = f"data:image/jpeg;base64,{encoded}"
        except Exception as exc:
            print(f"[Discobot] Téléchargement photo impossible: {exc}")

    ai = await ai_extract(text=text or None, image_data_uri=image_data_uri) if (text or image_data_uri) else None
    return merge_extractions(local, ai)


def apply_extraction(session, extracted):
    if not extracted:
        return
    data = session["data"]
    site = data.setdefault("site", {})
    valid_site_keys = {x[0] for x in SITE_STEPS}

    for key, value in (extracted.get("site") or {}).items():
        value = clean_value(value)
        if key in valid_site_keys and value is not None:
            site[key] = value

    cleaned_devices = []
    valid_device_keys = {x[0] for x in DEVICE_STEPS}
    for d in (extracted.get("devices") or []):
        if not isinstance(d, dict):
            continue
        item = {}
        for key, value in d.items():
            value = clean_value(value)
            if key in valid_device_keys and value is not None:
                item[key] = value
        if item:
            cleaned_devices.append(item)

    if cleaned_devices:
        data["prefill_devices"] = cleaned_devices
        if not site.get("nombre_appareils"):
            site["nombre_appareils"] = len(cleaned_devices)

    if clean_value(extracted.get("notes")):
        data["notes_intake"] = clean_value(extracted.get("notes"))


def prepare_device_prefill(data):
    if data.get("current_device"):
        return
    idx = max(0, int(data.get("device_index", 1)) - 1)
    prefills = data.get("prefill_devices") or []
    if idx < len(prefills):
        data["current_device"] = dict(prefills[idx])


def has_answer(value):
    if value is None:
        return False
    if isinstance(value, dict):
        return bool(value)
    return bool(str(value).strip())


def normalize_intake_defaults(data):
    """Réduit les répétitions du dossier d'entrée sans inventer de coordonnées."""
    site = data.setdefault("site", {})

    if has_answer(site.get("client")) and not has_answer(site.get("site")):
        site["site"] = site["client"]
    elif has_answer(site.get("site")) and not has_answer(site.get("client")):
        site["client"] = site["site"]

    if not has_answer(site.get("nombre_appareils")):
        prefills = data.get("prefill_devices") or []
        site["nombre_appareils"] = len(prefills) if prefills else 1


def next_missing_step(session, start_index=0):
    data = session["data"]
    normalize_intake_defaults(data)

    for idx in range(max(0, start_index), len(ALL_STEPS)):
        key, _, _, _ = get_step(idx)

        if idx < len(SITE_STEPS):
            if has_answer(data.get("site", {}).get(key)):
                continue
            if key in OPTIONAL_SITE_KEYS:
                continue
            return idx

        prepare_device_prefill(data)
        if has_answer(data.get("current_device", {}).get(key)):
            continue
        return idx

    return len(ALL_STEPS)


def build_intake_preview(data):
    site = data.get("site", {})
    lines = ["🧠 DOSSIER COMPRIS / PRÉREMPLI"]
    labels = [
        ("client", "Client"),
        ("site", "Site"),
        ("adresse", "Adresse"),
        ("contact_nom", "Contact"),
        ("contact_fonction", "Fonction"),
        ("contact_email", "E-mail"),
        ("contact_tel", "Téléphone"),
        ("nombre_appareils", "Disconnecteurs"),
    ]
    for key, label in labels:
        if has_answer(site.get(key)):
            lines.append(f"• {label} : {value_text(site[key])}")
    prefills = data.get("prefill_devices") or []
    for i, device in enumerate(prefills, start=1):
        parts = [
            value_text(device[k]) for k in ("emplacement", "marque", "modele", "type", "diametre", "serie")
            if has_answer(device.get(k))
        ]
        if parts:
            lines.append(f"• Appareil {i} : " + " — ".join(parts))
    lines.append("")
    lines.append("Je garde tout ce qui est déjà lu : je ne te le redemanderai pas.")
    lines.append("S'il faut corriger quelque chose, écris simplement la correction en une phrase.")
    return "\n".join(lines)


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
        f"Contact : {value_text(site.get('contact_nom', '—'))}",
        f"Fonction : {value_text(site.get('contact_fonction', '—'))}",
        f"E-mail : {value_text(site.get('contact_email', '—'))}",
        f"Téléphone : {value_text(site.get('contact_tel', '—'))}",
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
        prepare_device_prefill(data)
        session["current_step"] = next_missing_step(session, len(SITE_STEPS))
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
    session["current_step"] = next_missing_step(session, idx + 1)
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
        "👋 Discobot Aqualeo V0.3 — mode intelligent\n\n"
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
        "🆕 Nouveau dossier / contrôle créé.\n\n"
        "Le plus simple : 📸 envoie directement une photo du dossier / ordre d'intervention.\n"
        "Discobot doit lire le client, le site, l'adresse, le contact utile et les appareils présents.\n\n"
        "Sinon, écris tout en UNE phrase.\n"
        "Exemple : « Lycée Lucie Aubrac, 51 rue Victor Hugo 93500 Pantin, 2 disconnecteurs ».\n\n"
        "Je range les informations et je ne redemande jamais ce qui est déjà connu."
    )


async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id, update.effective_chat.id)
    if not session:
        await update.effective_message.reply_text("Aucun contrôle en cours. Utilise /nouveau.")
        return
    session["current_step"] = next_missing_step(session, session["current_step"])
    save_session(session)
    await update.effective_message.reply_text(build_intake_preview(session["data"]))
    if session["current_step"] < len(ALL_STEPS):
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
    if idx >= len(ALL_STEPS):
        await update.effective_message.reply_text("Ce contrôle est terminé. Utilise /nouveau pour un autre dossier.")
        return

    key, _, kind, _ = get_step(idx)
    msg = update.effective_message

    # Une photo peut être envoyée dès la création du dossier, même si l'étape attend du texte.
    if msg.photo and kind != "photo":
        photo = msg.photo[-1]
        session["data"].setdefault("source_photos", []).append({
            "photo_file_id": photo.file_id,
            "file_unique_id": photo.file_unique_id,
            "width": photo.width,
            "height": photo.height,
            "usage": "DOSSIER_SOURCE",
        })
        await msg.reply_text("🔎 J'analyse la photo et je préremplis le dossier…")
        extracted = await extract_from_message(update, context, include_photo=True)
        if extraction_has_data(extracted):
            apply_extraction(session, extracted)
            session["current_step"] = next_missing_step(session, 0)
            save_session(session)
            await msg.reply_text(build_intake_preview(session["data"]))
            if session["current_step"] < len(ALL_STEPS):
                await send_step(chat_id, session, context)
        else:
            save_session(session)
            if AI_CLIENT is None:
                await msg.reply_text(
                    "📷 Photo enregistrée. La lecture intelligente de la photo n'est pas encore activée sur le serveur.\n\n"
                    "En attendant, envoie-moi UNE seule phrase avec ce que tu as : "
                    "client/établissement + adresse + contact utile. Je répartirai les infos tout seul."
                )
            else:
                await msg.reply_text(
                    "📷 Photo enregistrée, mais je n'ai pas pu lire d'information certaine.\n\n"
                    "Envoie-moi simplement les infos lisibles en UNE phrase ; je ne te ferai pas remplir les champs un par un."
                )
        return

    # Pendant le contrôle, la photo est archivée et peut aussi préremplir marque / modèle / DN / série.
    if kind == "photo":
        if not msg.photo:
            await msg.reply_text("J'attends une photo. Sinon utilise Passer / Impossible / Non vérifiable.")
            return

        photo = msg.photo[-1]
        save_current_value(
            session,
            {
                "photo_file_id": photo.file_id,
                "file_unique_id": photo.file_unique_id,
                "width": photo.width,
                "height": photo.height,
                "archive_status": "A_TELECHARGER_DANS_DOSSIER_APPAREIL",
            },
        )

        extracted = await extract_from_message(update, context, include_photo=True)
        if extraction_has_data(extracted) and extracted.get("devices"):
            valid_device_keys = {x[0] for x in DEVICE_STEPS}
            for k, v in extracted["devices"][0].items():
                if clean_value(v) is not None and k in valid_device_keys:
                    session["data"].setdefault("current_device", {})[k] = clean_value(v)

        session["current_step"] = next_missing_step(session, idx + 1)
        save_session(session)
        if session["current_step"] >= len(ALL_STEPS):
            await complete_current_device(session, chat_id, context)
        else:
            await send_step(chat_id, session, context)
        return

    text = (msg.text or "").strip()
    if not text:
        await msg.reply_text("J'attends une réponse texte, ou utilise un bouton de statut.")
        return

    # Une phrase peut contenir plusieurs informations : Discobot les répartit dans les bons champs.
    extracted = await extract_from_message(update, context, include_photo=False)
    apply_extraction(session, extracted)

    if idx < len(SITE_STEPS):
        bucket = session["data"].setdefault("site", {})
    else:
        bucket = session["data"].setdefault("current_device", {})
        if extracted and extracted.get("devices"):
            valid_device_keys = {x[0] for x in DEVICE_STEPS}
            for k, v in extracted["devices"][0].items():
                if clean_value(v) is not None and k in valid_device_keys:
                    bucket[k] = clean_value(v)

    # Si le message contient clairement d'autres champs (ex. site + adresse),
    # on ne le force jamais dans le champ actuellement demandé.
    smart_found = extraction_has_data(extracted)
    if not has_answer(bucket.get(key)) and not smart_found:
        bucket[key] = text

    if smart_found:
        restart_at = 0 if idx < len(SITE_STEPS) else len(SITE_STEPS)
        session["current_step"] = next_missing_step(session, restart_at)
    else:
        session["current_step"] = next_missing_step(session, idx + 1)
    save_session(session)

    if extraction_has_data(extracted) and idx < len(SITE_STEPS):
        await msg.reply_text(build_intake_preview(session["data"]))

    if session["current_step"] >= len(ALL_STEPS):
        await complete_current_device(session, chat_id, context)
    else:
        await send_step(chat_id, session, context)


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
