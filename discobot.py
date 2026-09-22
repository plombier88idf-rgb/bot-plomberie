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
    ("preparation_controle", "Préparation / branchement", "text", "Préparation du contrôle et raccordement de la mallette."),
    ("vanne_amont", "1 — Vanne amont", "text", "Contrôle d'étanchéité de la vanne d'arrêt amont."),
    ("clapet_amont", "2 — Clapet amont", "text", "Contrôle du clapet amont."),
    ("soupape_decharge", "3 — Soupape / décharge", "text", "Contrôle de la soupape de décharge."),
    ("vanne_aval", "4 — Vanne aval", "text", "Contrôle d'étanchéité de la vanne d'arrêt aval."),
    ("clapet_aval", "5 — Clapet aval", "text", "Contrôle du clapet aval."),
    ("pression_amont", "Mesure P1 — Amont", "text", "Pression amont réellement mesurée, avec unité ?"),
    ("pression_zone", "Mesure P2 — Zone intermédiaire", "text", "Pression de la zone intermédiaire réellement mesurée, avec unité ?"),
    ("pression_aval", "Mesure P3 — Aval", "text", "Pression aval réellement mesurée, avec unité ?"),
    ("differentiel", "6 — Différentiel / ouverture décharge", "text", "Différentiel réellement mesuré au déclenchement, avec unité ?"),
    ("essais", "Bilan essais", "text", "Résultat final des essais et comportement de la décharge ?"),
    ("diagnostic", "Diagnostic", "text", "Diagnostic calculé par Discobot."),
    ("recommandation", "Suite à prévoir", "text", "Suite calculée par Discobot."),
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
        [InlineKeyboardButton("🟡 Non vérifiable", callback_data="status:non_verifiable")],
    ])


def step_keyboard(key):
    if key == "preparation_controle":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Prêt / mallette raccordée", callback_data="result:preparation_controle:ok")],
            [InlineKeyboardButton("🟡 Non vérifiable", callback_data="result:preparation_controle:nv")],
        ])
    if key in {"vanne_amont", "vanne_aval"}:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🟢 Ferme bien", callback_data=f"result:{key}:ok"),
                InlineKeyboardButton("🔴 Laisse passer", callback_data=f"result:{key}:leak"),
            ],
            [InlineKeyboardButton("🟡 Non vérifiable", callback_data=f"result:{key}:nv")],
        ])
    if key in {"clapet_amont", "soupape_decharge", "clapet_aval"}:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🟢 RAS", callback_data=f"result:{key}:ok"),
                InlineKeyboardButton("🔴 Anomalie", callback_data=f"result:{key}:anomaly"),
            ],
            [InlineKeyboardButton("🟡 Non vérifiable", callback_data=f"result:{key}:nv")],
        ])
    return status_keyboard()


def device_identity(data):
    device = data.get("current_device", {})
    parts = [
        str(device.get("marque", "")).strip(),
        str(device.get("modele", "")).strip(),
        str(device.get("type", "")).strip(),
        str(device.get("diametre", "")).strip(),
    ]
    return " ".join(p for p in parts if p)


def is_sferaco_ba574(data):
    ident = device_identity(data).lower()
    return ("sferaco" in ident or "scudo" in ident) and ("ba574" in ident or "950" in ident)


def parse_pressure_bar(value, differential=False):
    """Convertit une saisie bar/mbar en bar. Pour un différentiel nu, 120/380 = mbar."""
    if value is None or isinstance(value, dict):
        return None
    text = str(value).lower().replace(",", ".").strip()
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(mbar|bar)?", text)
    if not m:
        return None
    number = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if unit == "mbar":
        return number / 1000.0
    if unit == "bar":
        return number
    if differential and abs(number) >= 10:
        return number / 1000.0
    return number


def contains_any(value, words):
    if value is None:
        return False
    text = str(value).lower()
    return any(w in text for w in words)


def positive_leak(value):
    """Détecte une fuite sans transformer 'pas de fuite' en défaut."""
    if value is None:
        return False
    text = str(value).lower()
    negatives = [
        "pas de fuite", "aucune fuite", "sans fuite", "ne fuit pas",
        "pas d'écoulement", "pas d ecoulement", "aucun écoulement",
        "aucun ecoulement", "sec", "ras"
    ]
    if any(n in text for n in negatives):
        return False
    return any(w in text for w in [
        "fuite", "coule", "écoulement", "ecoulement", "pisse",
        "goutte", "décharge permanente", "decharge permanente"
    ])


def is_help_request(text):
    t = (text or "").strip().lower()
    compact = re.sub(r"[^a-zà-ÿ0-9? ]+", " ", t)
    compact = re.sub(r"\s+", " ", compact).strip()
    if compact in {"?", "aide", "help", "comment", "quoi", "je fais quoi", "quoi faire",
                   "etape a suivre", "étape a suivre", "etape à suivre", "étape à suivre",
                   "explique", "explique moi"}:
        return True
    return any(x in compact for x in ["comment je fais", "quelle etape", "quelle étape", "tu peux expliquer"])


def is_technical_question(text):
    t = (text or "").strip().lower()
    if not t:
        return False
    if "?" in t:
        return True
    compact = re.sub(r"[^a-zà-ÿ0-9 ]+", " ", t)
    compact = re.sub(r"\s+", " ", compact).strip()
    starters = ("donc ", "est ce que ", "est-ce que ", "je dois ", "faut il ", "faut-il ",
                "je rouvre ", "je referme ", "je ferme ", "j ouvre ", "j'ouvre ",
                "ensuite ", "apres ", "après ", "comment ", "pourquoi ", "quoi faire",
                "quelle etape", "quelle étape", "etape a suivre", "étape à suivre")
    return compact.startswith(starters)


def normalize_measurement_value(key, raw):
    text = str(raw).strip().replace(",", ".")
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        number = float(text)
        if key == "differentiel":
            if abs(number) >= 10:
                return f"{text} mbar"
            return f"{text} bar"
        return f"{text} bar"
    return str(raw).strip()


def parse_measurement_bundle(text, current_key):
    """Accepte P1/P2/P3/ΔP en une fois, ou 4 lignes numériques dans cet ordre."""
    raw = (text or "").strip()
    out = {}
    patterns = {
        "pression_amont": r"(?:\bP1\b|pression\s*amont)\s*[:=\-]?\s*(-?\d+(?:[.,]\d+)?)\s*(mbar|bar)?",
        "pression_zone": r"(?:\bP2\b|zone\s*interm[ée]diaire)\s*[:=\-]?\s*(-?\d+(?:[.,]\d+)?)\s*(mbar|bar)?",
        "pression_aval": r"(?:\bP3\b|pression\s*aval)\s*[:=\-]?\s*(-?\d+(?:[.,]\d+)?)\s*(mbar|bar)?",
        "differentiel": r"(?:diff[ée]rentiel|ΔP|delta\s*p|P1\s*[-–]\s*P2)\s*[:=\-]?\s*(-?\d+(?:[.,]\d+)?)\s*(mbar|bar)?",
    }
    for key, pat in patterns.items():
        m = re.search(pat, raw, re.IGNORECASE)
        if m:
            val = m.group(1).replace(",", ".")
            unit = (m.group(2) or "").lower()
            out[key] = normalize_measurement_value(key, val + (f" {unit}" if unit else ""))

    if out:
        return out

    lines = [x.strip() for x in raw.splitlines() if x.strip()]
    if current_key == "pression_amont" and len(lines) == 4 and all(re.fullmatch(r"-?\d+(?:[.,]\d+)?", x) for x in lines):
        return {
            "pression_amont": normalize_measurement_value("pression_amont", lines[0]),
            "pression_zone": normalize_measurement_value("pression_zone", lines[1]),
            "pression_aval": normalize_measurement_value("pression_aval", lines[2]),
            "differentiel": normalize_measurement_value("differentiel", lines[3]),
        }
    return {}


def canonical_identity_value(key, value):
    if value is None:
        return ""
    s = str(value).lower().replace(",", ".").strip()
    if key == "modele":
        return re.sub(r"[^a-z0-9]", "", s)
    if key == "diametre":
        compact = re.sub(r"\s+", "", s).replace('"', "")
        dn = re.search(r"dn\s*(\d+)", s)
        if dn:
            return "dn" + dn.group(1)
        inch_map = {
            "1/2":"dn15","3/4":"dn20","1":"dn25",
            "11/4":"dn32","1.1/4":"dn32","1-1/4":"dn32",
            "11/2":"dn40","1.1/2":"dn40","1-1/2":"dn40",
            "2":"dn50","21/2":"dn65","2.1/2":"dn65","2-1/2":"dn65",
            "3":"dn80","4":"dn100"
        }
        return inch_map.get(compact, re.sub(r"[^a-z0-9/]", "", compact))
    return re.sub(r"\s+", " ", s)


def merge_photo_device_data(data, extracted_device):
    target = data.setdefault("current_device", {})
    valid = {x[0] for x in DEVICE_STEPS}
    identity_keys = {"marque", "modele", "type", "serie", "diametre"}
    conflicts = []
    for k, v in (extracted_device or {}).items():
        v = clean_value(v)
        if v is None or k not in valid:
            continue
        if not has_answer(target.get(k)):
            target[k] = v
        elif k in identity_keys and canonical_identity_value(k, target.get(k)) != canonical_identity_value(k, v):
            conflicts.append((k, target.get(k), v))
    return conflicts


def valve_leaks(value):
    return contains_any(value, ["laisse passer", "non étanche", "non etanche", "fuite"])


def live_diagnostic(data):
    """Lecture provisoire : sépare symptôme, preuve, hypothèse et prochain test."""
    d = data.get("current_device", {})
    lines = []

    vanne_amont = d.get("vanne_amont")
    vanne_aval = d.get("vanne_aval")
    essais = d.get("essais")
    clapets = d.get("clapets")

    p1 = parse_pressure_bar(d.get("pression_amont"))
    p2 = parse_pressure_bar(d.get("pression_zone"))
    p3 = parse_pressure_bar(d.get("pression_aval"))
    dp = parse_pressure_bar(d.get("differentiel"), differential=True)
    if dp is None and p1 is not None and p2 is not None:
        dp = p1 - p2

    aval_leaks = valve_leaks(vanne_aval)
    amont_leaks = valve_leaks(vanne_amont)
    discharge_leak = positive_leak(essais)
    no_discharge_leak = contains_any(essais, ["pas de fuite", "aucune fuite", "sans fuite", "pas d'écoulement", "pas d ecoulement"])
    pressure_rises = contains_any(essais, ["remonte", "remontée", "remontee", "réalimente", "realimente"])
    clapet_anomaly = contains_any(clapets, ["anomalie", "défaut", "defaut"]) or positive_leak(clapets)

    if aval_leaks:
        lines.append(
            "🔴 Défaut CONFIRMÉ sur la VANNE AVAL extérieure : elle ne tient pas l'isolement. "
            "Cela ne prouve pas que le clapet aval interne du disconnecteur est défectueux."
        )
    if amont_leaks:
        lines.append(
            "🔴 Défaut CONFIRMÉ sur la VANNE AMONT extérieure : elle ne tient pas l'isolement. "
            "Les essais internes doivent être interprétés avec prudence tant que l'isolement amont n'est pas fiable."
        )

    if dp is not None:
        mbar = round(dp * 1000)
        if is_sferaco_ba574(data):
            if dp < 0.14:
                lines.append(
                    f"🔴 P1-P2 = {mbar} mbar : sous le seuil de 140 mbar utilisé uniquement pour ce BA574 identifié. "
                    "La mise à décharge peut être cohérente avec sa fonction de sécurité, mais la cause reste à isoler."
                )
            else:
                lines.append(
                    f"🟢 P1-P2 = {mbar} mbar : au-dessus du seuil de 140 mbar pour ce BA574 identifié."
                )
        else:
            lines.append(
                f"🔵 Différentiel mesuré P1-P2 = {mbar} mbar. "
                "Discobot ne déclare pas conforme/non conforme sur cette seule valeur tant que le critère constructeur du modèle n'est pas identifié."
            )

    if discharge_leak:
        lines.append(
            "💧 Écoulement à la décharge CONFIRMÉ comme symptôme. Il ne désigne pas à lui seul le clapet fautif."
        )
        lines.append(
            "➡️ Suite logique : vérifier les deux vannes d'isolement, observer quelle pression remonte après isolement, "
            "puis seulement attribuer le défaut à la vanne extérieure, au clapet amont, au clapet aval ou au dispositif de décharge."
        )
    elif no_discharge_leak:
        lines.append("🟢 Aucun écoulement permanent à la décharge n'a été constaté pendant l'essai saisi.")

    if p3 is not None and p2 is not None and p3 > p2:
        lines.append(
            "🔵 P3 est supérieure à P2 : l'aval peut solliciter le clapet aval. "
            "Il faut constater un transfert réel de pression vers P2 avant d'accuser ce clapet."
        )

    if pressure_rises:
        lines.append(
            "🔎 Une remontée de pression est signalée. Il faut identifier sa provenance : "
            "derrière une vanne extérieure fermée = vanne suspecte ; transfert P3→P2 avec isolement fiable = clapet aval suspect ; "
            "reconstitution P1→P2 lors du test amont = clapet amont suspect."
        )

    if clapet_anomaly and not discharge_leak:
        lines.append(
            "🟡 Anomalie clapet/décharge notée, mais aucune pièce ne doit être condamnée sans mesure ou essai d'isolement qui l'identifie."
        )

    if not lines and any(v is not None for v in (p1, p2, p3, dp)):
        lines.append("🔵 Mesures enregistrées. Aucun défaut n'est encore suffisamment isolé pour désigner une pièce.")

    return "\n\n".join(lines)


def component_anomaly(value):
    if value is None or isinstance(value, dict):
        return False
    return contains_any(value, ["anomalie", "défaut", "defaut", "non étanche", "non etanche", "laisse passer"])


def component_non_verifiable(value):
    return isinstance(value, dict) and value.get("status") in {"non_verifiable", "impossible"}


def auto_diagnosis(data):
    """Conclusion calculée à partir des essais réellement enregistrés."""
    d = data.get("current_device", {})
    confirmed = []
    uncertain = []
    good = []

    if valve_leaks(d.get("vanne_amont")):
        confirmed.append("vanne d'arrêt amont extérieure non étanche")
    elif has_answer(d.get("vanne_amont")) and not component_non_verifiable(d.get("vanne_amont")):
        good.append("vanne amont étanche")

    if component_anomaly(d.get("clapet_amont")):
        confirmed.append("anomalie du clapet amont constatée pendant son essai dédié")
    elif has_answer(d.get("clapet_amont")) and not component_non_verifiable(d.get("clapet_amont")):
        good.append("clapet amont RAS")

    if component_anomaly(d.get("soupape_decharge")):
        confirmed.append("anomalie de fonctionnement de la soupape / décharge constatée pendant son essai dédié")
    elif has_answer(d.get("soupape_decharge")) and not component_non_verifiable(d.get("soupape_decharge")):
        good.append("soupape / décharge RAS")

    if valve_leaks(d.get("vanne_aval")):
        confirmed.append("vanne d'arrêt aval extérieure non étanche")
    elif has_answer(d.get("vanne_aval")) and not component_non_verifiable(d.get("vanne_aval")):
        good.append("vanne aval étanche")

    if component_anomaly(d.get("clapet_aval")):
        confirmed.append("anomalie du clapet aval constatée pendant son essai dédié")
    elif has_answer(d.get("clapet_aval")) and not component_non_verifiable(d.get("clapet_aval")):
        good.append("clapet aval RAS")

    p1 = parse_pressure_bar(d.get("pression_amont"))
    p2 = parse_pressure_bar(d.get("pression_zone"))
    p3 = parse_pressure_bar(d.get("pression_aval"))
    dp = parse_pressure_bar(d.get("differentiel"), differential=True)
    if dp is None and p1 is not None and p2 is not None:
        dp = p1 - p2

    if is_sferaco_ba574(data) and dp is not None:
        if dp < 0.14:
            confirmed.append(f"différentiel d'ouverture mesuré à {round(dp*1000)} mbar, inférieur au critère de 140 mbar chargé pour ce modèle")
        else:
            good.append(f"différentiel d'ouverture {round(dp*1000)} mbar")

    # Pour les autres modèles, une valeur seule ne reçoit jamais un seuil inventé.
    if not is_sferaco_ba574(data) and dp is not None:
        uncertain.append(
            f"ΔP relevé à {round(dp*1000)} mbar : valeur archivée, conformité non décidée sans critère constructeur chargé pour ce modèle"
        )

    if positive_leak(d.get("essais")) and not component_anomaly(d.get("soupape_decharge")):
        uncertain.append(
            "écoulement persistant signalé au bilan alors que l'organe responsable n'est pas isolé par les essais dédiés"
        )

    unverifiable = []
    for key, label in [
        ("vanne_amont","vanne amont"), ("clapet_amont","clapet amont"),
        ("soupape_decharge","soupape/décharge"), ("vanne_aval","vanne aval"),
        ("clapet_aval","clapet aval")
    ]:
        if component_non_verifiable(d.get(key)):
            unverifiable.append(label)
    if unverifiable:
        uncertain.append("non vérifiable : " + ", ".join(unverifiable))

    if confirmed:
        result = "DÉFAUT(S) CONFIRMÉ(S) : " + " ; ".join(confirmed) + "."
        if uncertain:
            result += " POINT(S) À CONFIRMER : " + " ; ".join(uncertain) + "."
        return result

    if uncertain:
        return (
            "Aucun organe n'est condamné automatiquement. "
            "POINT(S) À CONFIRMER : " + " ; ".join(uncertain) + "."
        )

    if good:
        return "Contrôle sans anomalie mise en évidence sur les organes vérifiés : " + " ; ".join(good) + "."

    return "Contrôle incomplet : données insuffisantes pour établir une conclusion technique."


def auto_recommendation(data):
    d = data.get("current_device", {})
    actions = []

    if valve_leaks(d.get("vanne_amont")):
        actions.append("devis possible pour réparation/remplacement de la vanne amont extérieure")
    if component_anomaly(d.get("clapet_amont")):
        actions.append("rechercher la référence exacte du kit/clapet amont et son tarif fournisseur avant devis")
    if component_anomaly(d.get("soupape_decharge")):
        actions.append("rechercher la référence exacte du kit/soupape de décharge et son tarif fournisseur avant devis")
    if valve_leaks(d.get("vanne_aval")):
        actions.append("devis possible pour réparation/remplacement de la vanne aval extérieure")
    if component_anomaly(d.get("clapet_aval")):
        actions.append("rechercher la référence exacte du kit/clapet aval et son tarif fournisseur avant devis")

    dp = parse_pressure_bar(d.get("differentiel"), differential=True)
    if is_sferaco_ba574(data) and dp is not None and dp < 0.14:
        actions.append("contrôle/réparation interne à cibler avant remise en conformité du différentiel")

    if not actions:
        if positive_leak(d.get("essais")):
            return "Contrôle complémentaire nécessaire avant tout devis de pièce : la cause de l'écoulement n'est pas suffisamment isolée."
        return "Aucune réparation proposée automatiquement sur les éléments contrôlés."

    return " ; ".join(actions) + ". Aucun prix de pièce n'est utilisé sans référence et tarif fournisseur vérifiés."


def autofill_diagnosis_and_recommendation(data):
    d = data.setdefault("current_device", {})
    d["diagnostic"] = auto_diagnosis(data)
    d["recommandation"] = auto_recommendation(data)


def guided_prompt(session, key, base_prompt):
    data = session["data"]
    ident = device_identity(data)
    prefix = f"Appareil identifié : {ident}\n\n" if ident else ""

    guides = {
        "emplacement": (
            "📍 Regarde où se trouve physiquement l'appareil. "
            "Réponds simplement par exemple : « chaufferie », « regard extérieur », « local incendie ». "
            "Pas besoin d'une phrase complète."
        ),
        "photo_loin": (
            "📸 Recule assez pour prendre l'ensemble : disconnecteur + vannes amont/aval + filtre + évacuation. "
            "La photo doit permettre de comprendre le sens de circulation."
        ),
        "photo_pres": (
            "📸 Prends maintenant une photo rapprochée de la plaque et des prises de contrôle. "
            "Essaie d'avoir marque, modèle, DN, n° de série et flèche de sens dans la photo."
        ),
        "marque": "🔎 Si la marque n'est pas lisible, envoie une photo plus proche au lieu de deviner.",
        "modele": "🔎 Lis la référence sur la plaque. Si elle est illisible, réponds « illisible ».",
        "type": "🔎 Vérifie le marquage du type (par ex. BA). Ne le déduis pas uniquement à la forme de l'appareil.",
        "serie": "🔎 Recopie uniquement le numéro réellement lisible. Sinon réponds « illisible ».",
        "diametre": "🔎 Lis le DN sur le corps ou la plaque. Exemple : DN20, DN50, DN80.",
        "preparation_controle": (
            "🧰 AVANT LE PREMIER ESSAI — on prépare tout une fois.\n"
            "1. Demande au contact du site l'autorisation d'interrompre l'alimentation et confirme qu'aucun équipement/process/réseau sensible n'a besoin d'eau.\n"
            "2. Repère la flèche et identifie P1 = amont, P2 = zone intermédiaire, P3 = aval. Si un repère est douteux : photo/notice, jamais au hasard.\n"
            "3. Identifie les voies de la mallette. Les couleurs ne sont utilisées que si elles sont confirmées sur la mallette/notice.\n"
            "4. Robinets de prises de contrôle fermés avant raccordement. Raccorde les flexibles sur les prises identifiées selon le mode opératoire de la mallette.\n"
            "5. Ouvre ensuite les robinets de contrôle progressivement, purge les flexibles selon la mallette et vérifie l'absence de fuite.\n"
            "6. Laisse les vannes d'arrêt amont et aval en position normale de service jusqu'au début du premier essai.\n"
            "👉 Quand la mallette est raccordée, purgée et stable : appuie sur Prêt."
        ),
        "vanne_amont": (
            "🟠 ÉTAPE 1/6 — VANNE AMONT\n"
            "Départ : mallette déjà raccordée/purgée, amont OUVERT, aval OUVERT.\n"
            "1. Ferme LENTEMENT la vanne amont.\n"
            "2. Fais chuter la pression côté disconnecteur après cette vanne par la prise/purge prévue dans le mode opératoire.\n"
            "3. Referme la purge et observe 30 à 60 s.\n"
            "4. Pression qui remonte = vanne amont non étanche. Pression basse/stable = elle isole.\n"
            "👉 Choisis le résultat. Discobot te donnera ensuite la transition exacte."
        ),
        "clapet_amont": (
            "🟠 ÉTAPE 2/6 — CLAPET AMONT\n"
            "1. Remets l'appareil dans l'état demandé par le mode opératoire après l'essai de vanne amont.\n"
            "2. Le but est de vérifier que P1 ne réalimente pas anormalement P2 à travers le 1er clapet.\n"
            "3. Utilise les prises P1/P2 et les robinets de la mallette selon sa procédure, puis observe P1, P2 et la décharge.\n"
            "4. Si P2 se reconstitue depuis P1 alors que le clapet doit isoler, note Anomalie.\n"
            "👉 Si tu ne sais pas quelle vanne/prise manœuvrer, demande-le : Discobot reste sur cette étape."
        ),
        "soupape_decharge": (
            "🟠 ÉTAPE 3/6 — SOUPAPE / DÉCHARGE\n"
            "1. Garde la mallette raccordée.\n"
            "2. Fais évoluer progressivement P1-P2 selon le mode opératoire de la mallette.\n"
            "3. Observe l'apparition des premières gouttes et vérifie ensuite que la décharge se referme quand les conditions normales reviennent.\n"
            "4. Ne conclus pas 'clapet HS' sur le seul écoulement.\n"
            "👉 Choisis RAS / Anomalie / Non vérifiable."
        ),
        "vanne_aval": (
            "🟠 ÉTAPE 4/6 — VANNE AVAL\n"
            "Transition : si l'amont a été fermé, ROUVRE-LE LENTEMENT et attends la stabilisation avant de tester l'aval.\n"
            "1. Ferme LENTEMENT la vanne aval.\n"
            "2. Fais chuter la pression après la vanne aval par un point de purge sûr si l'installation/procédure le permet.\n"
            "3. Referme ce point et observe 30 à 60 s.\n"
            "4. Pression qui remonte = vanne aval non étanche. Pression basse/stable = elle isole.\n"
            "👉 Choisis le résultat."
        ),
        "clapet_aval": (
            "🟠 ÉTAPE 5/6 — CLAPET AVAL\n"
            "1. On distingue ici la vanne aval extérieure du clapet aval interne.\n"
            "2. Mets l'appareil dans l'état prévu par le mode opératoire pour observer si une pression P3 se transmet vers P2.\n"
            "3. Si P3 réalimente P2 alors que les isolements extérieurs sont fiables, le clapet aval devient suspect/défectueux.\n"
            "4. Une simple fuite à la décharge ne suffit pas à condamner ce clapet.\n"
            "👉 Choisis RAS / Anomalie / Non vérifiable."
        ),
        "pression_amont": (
            "📏 Branche le manomètre AMONT sur la prise amont identifiée par le constructeur. "
            "Purge le flexible, stabilise la lecture puis écris la valeur réelle avec l'unité, par exemple « 4,2 bar »."
        ),
        "pression_zone": (
            "📏 Relève la pression de la ZONE INTERMÉDIAIRE sur la prise correspondante. "
            "Purge le flexible et attends une lecture stable. Écris uniquement la mesure réelle."
        ),
        "pression_aval": (
            "📏 Relève la pression AVAL sur la prise aval. "
            "Écris la valeur réelle avec l'unité. Si aucune prise n'est identifiable, choisis « Non vérifiable » plutôt que d'inventer."
        ),
        "differentiel": (
            "🟠 ÉTAPE 6/6 — DIFFÉRENTIEL D'OUVERTURE DE LA DÉCHARGE\n"
            "1. La mallette doit déjà être raccordée et purgée : ne rebranche rien ici.\n"
            "2. Fais varier progressivement ΔP = P1-P2 selon le mode opératoire.\n"
            "3. Regarde la décharge en même temps. Dès les PREMIÈRES GOUTTES, relève immédiatement ΔP.\n"
            "4. Saisis la valeur avec son unité.\n"
            "5. Discobot n'applique un seuil constructeur que si le modèle/critère est identifié avec certitude."
        ),
        "essais": (
            "🧪 Fais les essais d'ouverture/fermeture de la décharge et d'étanchéité selon la procédure du modèle. "
            "Décris seulement ce qui se passe réellement : « décharge s'ouvre », « fuite continue », « pas de fuite », etc."
        ),
        "diagnostic": (
            "🧠 DIAGNOSTIC GUIDÉ — on ne part jamais de « ça coule donc c'est X ».\n"
            "Discobot sépare toujours : SYMPTÔME → MESURE → ISOLEMENT → NOUVELLE MESURE → CAUSE PROBABLE.\n\n"
            + (live_diagnostic(data) or
               "Aucune conclusion certaine pour le moment. Décris ce qui se passe réellement pendant l'essai.")
            + "\n\n"
            "👉 Si le défaut n'est pas isolé avec certitude, écris « à confirmer ». "
            "Ne démonte ni ne remplace un clapet uniquement sur une supposition."
        ),
        "recommandation": (
            "🔧 Choisis la suite la plus simple correspondant au diagnostic : aucune action, surveillance, nettoyage, réparation ciblée, kit interne ou remplacement. "
            "Si le diagnostic n'est pas certain, marque-le comme à confirmer."
        ),
        "intervenant": "✍️ Indique simplement le prénom/nom de l'intervenant qui a réellement fait le contrôle.",
    }

    guide = guides.get(key, base_prompt)

    if key == "differentiel" and is_sferaco_ba574(data):
        guide += (
            "\n\n📚 Pour le SFERACO/SCUDO BA574 série 950, la notice indique qu'en fonctionnement normal "
            "la pression de la zone intermédiaire est inférieure à la pression amont d'au moins 140 mbar. "
            "Discobot utilisera ce seuil seulement pour ce modèle identifié."
        )

    if key in {"preparation_controle", "vanne_amont", "clapet_amont", "soupape_decharge", "vanne_aval", "clapet_aval", "pression_amont", "pression_zone", "pression_aval", "differentiel", "essais"}:
        guide += (
            "\n\n⚠️ Manipulation hydraulique : procédure destinée à un adulte/technicien autorisé. "
            "Le texte doit être simple à comprendre, mais on ne fait pas manipuler un réseau sous pression à un enfant."
        )

    return prefix + guide


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
        text=f"{heading}\n\n{guided_prompt(session, key, prompt)}",
        reply_markup=step_keyboard(key),
    )


def value_text(value):
    if isinstance(value, dict) and value.get("status"):
        return STATUS_LABELS.get(value["status"], value["status"])
    if isinstance(value, dict) and value.get("photo_file_id"):
        return "📷 photo enregistrée"
    return str(value)


def build_summary(data):
    site = data.get("site", {})
    devices = data.get("devices", [])
    lines = [
        "📄 RAPPORT DE CONTRÔLE — DISCONNECTEUR(S) BA",
        "",
        f"Client : {value_text(site.get('client', '—'))}",
        f"Site : {value_text(site.get('site', '—'))}",
        f"Adresse : {value_text(site.get('adresse', '—'))}",
        f"Contact : {value_text(site.get('contact_nom', '—'))}",
        "",
    ]

    for n, d in enumerate(devices, start=1):
        identity = " — ".join(
            str(d.get(k)).strip() for k in ("marque","modele","type","diametre","serie")
            if has_answer(d.get(k))
        ) or "identité incomplète"

        lines.extend([
            f"━━━━━━━━ APPAREIL {n}/{len(devices)} ━━━━━━━━",
            f"Emplacement : {value_text(d.get('emplacement','—'))}",
            f"Identification : {identity}",
            f"Année / âge déclaré : {value_text(d.get('annee_pose','—'))}",
            "",
            "ESSAIS DES ORGANES",
            f"• Vanne amont : {value_text(d.get('vanne_amont','—'))}",
            f"• Clapet amont : {value_text(d.get('clapet_amont','—'))}",
            f"• Soupape / décharge : {value_text(d.get('soupape_decharge','—'))}",
            f"• Vanne aval : {value_text(d.get('vanne_aval','—'))}",
            f"• Clapet aval : {value_text(d.get('clapet_aval','—'))}",
            "",
            "MESURES",
            f"• P1 amont : {value_text(d.get('pression_amont','—'))}",
            f"• P2 zone intermédiaire : {value_text(d.get('pression_zone','—'))}",
            f"• P3 aval : {value_text(d.get('pression_aval','—'))}",
            f"• ΔP ouverture décharge : {value_text(d.get('differentiel','—'))}",
            f"• Bilan essai : {value_text(d.get('essais','—'))}",
            "",
            "🧠 DIAGNOSTIC AUTOMATIQUE",
            value_text(d.get("diagnostic","—")),
            "",
            "🔧 SUITE À PRÉVOIR",
            value_text(d.get("recommandation","—")),
            "",
            f"Intervenant : {value_text(d.get('intervenant','—'))}",
            "",
        ])

    lines.extend([
        "RÈGLE DE DEVIS : une pièce interne n'est proposée que si le contrôle l'a isolée avec suffisamment de certitude.",
        "Référence et tarif fournisseur doivent être vérifiés avant génération d'un devis.",
        "Aucun e-mail ni devis n'est envoyé automatiquement dans cette version de test.",
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
    answered_key, _, _, _ = get_step(idx)
    save_current_value(session, value)

    if answered_key in {"vanne_amont", "clapet_amont", "soupape_decharge", "vanne_aval", "clapet_aval", "differentiel", "essais"}:
        autofill_diagnosis_and_recommendation(session["data"])

    session["current_step"] = next_missing_step(session, idx + 1)
    save_session(session)

    if answered_key in {"vanne_amont", "clapet_amont", "soupape_decharge", "vanne_aval", "clapet_aval", "differentiel", "essais"}:
        note = live_diagnostic(session["data"])
        if note:
            await context.bot.send_message(
                chat_id=chat_id,
                text="🧠 LECTURE PROVISOIRE\n\n" + note,
            )

    if answered_key == "essais":
        d = session["data"].get("current_device", {})
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🧾 DIAGNOSTIC AUTOMATIQUE\n\n"
                + d.get("diagnostic", "—")
                + "\n\n🔧 SUITE PROPOSÉE\n"
                + d.get("recommandation", "—")
            ),
        )

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
        "👋 Discobot Aqualeo V0.8 — protocole + diagnostic auto\n\n"
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


async def callback_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    chat_id = query.message.chat.id
    if not allowed_user(user_id):
        await query.answer("Accès non autorisé", show_alert=True)
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        return
    _, key, result = parts

    session = get_session(user_id, chat_id)
    if not session:
        await query.answer("Aucun contrôle en cours", show_alert=True)
        return

    idx = session["current_step"]
    current_key, _, _, _ = get_step(idx)
    if current_key != key:
        await query.answer("Cette étape n'est plus active.", show_alert=True)
        return

    if key == "preparation_controle":
        labels = {
            "ok": "préparation terminée / mallette raccordée et purgée",
            "nv": {"status": "non_verifiable"},
        }
    elif key in {"clapet_amont", "soupape_decharge", "clapet_aval"}:
        labels = {
            "ok": "RAS au contrôle",
            "anomaly": "anomalie constatée",
            "nv": {"status": "non_verifiable"},
        }
    else:
        labels = {
            "ok": "fermeture correcte / étanche au contrôle",
            "leak": "laisse passer / fermeture non étanche",
            "nv": {"status": "non_verifiable"},
            "anomaly": "anomalie constatée",
        }
    value = labels.get(result, result)
    await advance(session, chat_id, value, context)


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


async def answer_technical_question(session, key, question):
    data = session["data"]
    ident = device_identity(data) or "modèle non confirmé"
    q = (question or "").strip().lower()

    if key == "vanne_aval" and ("amont" in q or "rouvr" in q or "ouvrir" in q):
        return (
            "✅ Oui.\n"
            "1. Vérifie que la purge précédente est refermée.\n"
            "2. ROUVRE LENTEMENT la vanne amont.\n"
            "3. Attends la stabilisation.\n"
            "4. FERME ensuite LENTEMENT la vanne aval.\n"
            "5. Fais chuter la pression après la vanne aval par un point sûr, referme, puis observe 30 à 60 s.\n"
            "➡️ Remontée = vanne aval non étanche ; stable = vanne étanche.\n\n"
            "Je reste sur l'étape vanne aval jusqu'à ton résultat."
        )

    if any(w in q for w in ["branch", "mallette", "flexible", "p1", "p2", "p3", "prise"]):
        return (
            "🧰 Branchement : P1=amont, P2=zone intermédiaire, P3=aval. "
            "Robinets de prises fermés avant raccordement ; branche chaque voie sur la prise identifiée, "
            "puis ouvre progressivement et purge selon la notice de la mallette. "
            "Je n'utilise une couleur que si elle est confirmée par la mallette/notice."
        )

    if AI_CLIENT:
        guide = guided_prompt(session, key, get_step(session["current_step"])[3])
        prompt = f"""Tu es Discobot, assistant terrain pour contrôle BA.
Question du technicien pendant l'étape {key}, appareil {ident}.
Réponds à la question sans avancer l'étape.
Guide actuel: {guide}
Question: {question}
Réponse française simple, 2 à 7 actions. Dis clairement quelles vannes ouvrir/fermer et quoi observer si pertinent.
N'invente jamais couleurs, prises, seuils ou séquence constructeur non confirmés. Si ça dépend de la notice de la mallette/modèle, dis-le."""
        try:
            response = await AI_CLIENT.responses.create(
                model=OPENAI_MODEL,
                input=[{"role":"user","content":[{"type":"input_text","text":prompt}]}],
            )
            ans=(response.output_text or "").strip()
            if ans:
                return ans
        except Exception as exc:
            print(f"[Discobot] Réponse terrain IA impossible: {exc}")
    return "Je reste sur cette étape.\n\n" + guided_prompt(session, key, get_step(session["current_step"])[3])


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

    # Photo de dossier au tout début : extraction globale autorisée.
    if msg.photo and kind != "photo" and idx < len(SITE_STEPS):
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
            await msg.reply_text(
                "📷 Photo enregistrée, mais aucune information certaine n'a pu être extraite. "
                "Envoie client/site/adresse en une seule phrase."
            )
        return

    # Photo terrain : elle appartient UNIQUEMENT à l'appareil courant.
    if kind == "photo":
        if not msg.photo:
            await msg.reply_text("J'attends une photo. Sinon utilise Passer / Impossible / Non vérifiable.")
            return

        photo = msg.photo[-1]
        save_current_value(session, {
            "photo_file_id": photo.file_id,
            "file_unique_id": photo.file_unique_id,
            "width": photo.width,
            "height": photo.height,
            "archive_status": "A_TELECHARGER_DANS_DOSSIER_APPAREIL",
        })

        extracted = await extract_from_message(update, context, include_photo=True)
        conflicts = []
        if extraction_has_data(extracted) and extracted.get("devices"):
            conflicts = merge_photo_device_data(session["data"], extracted["devices"][0])

        session["current_step"] = next_missing_step(session, idx + 1)
        save_session(session)

        if conflicts:
            labels = {"marque":"marque","modele":"modèle","type":"type","serie":"n° série","diametre":"DN"}
            txt = ["⚠️ La photo suggère une identité différente du dossier. Je N'ÉCRASE rien automatiquement :"]
            for k, old, new in conflicts:
                txt.append(f"• {labels.get(k,k)} : dossier = {old} / photo = {new}")
            txt.append("Si la photo est la bonne référence, écris simplement la correction quand je te demande ce champ.")
            await msg.reply_text("\n".join(txt))

        if session["current_step"] >= len(ALL_STEPS):
            await complete_current_device(session, chat_id, context)
        else:
            await send_step(chat_id, session, context)
        return

    text = (msg.text or "").strip()
    if not text:
        await msg.reply_text("J'attends une réponse texte, ou utilise un bouton de statut.")
        return

    if idx >= len(SITE_STEPS) and (is_help_request(text) or is_technical_question(text)):
        answer = await answer_technical_question(session, key, text)
        await msg.reply_text("🧠 " + answer)
        return

    if is_help_request(text):
        await msg.reply_text("👍 Je reste sur cette étape. Voici exactement quoi faire :")
        await send_step(chat_id, session, context)
        return

    # Partie dossier : l'IA peut répartir une phrase dans plusieurs champs.
    if idx < len(SITE_STEPS):
        extracted = await extract_from_message(update, context, include_photo=False)
        apply_extraction(session, extracted)
        bucket = session["data"].setdefault("site", {})
        smart_found = extraction_has_data(extracted)
        if not has_answer(bucket.get(key)) and not smart_found:
            bucket[key] = text
        session["current_step"] = next_missing_step(session, 0 if smart_found else idx + 1)
        save_session(session)
        if smart_found:
            await msg.reply_text(build_intake_preview(session["data"]))
        if session["current_step"] >= len(ALL_STEPS):
            await complete_current_device(session, chat_id, context)
        else:
            await send_step(chat_id, session, context)
        return

    # Partie technique : PAS d'extraction globale. Le texte ne peut plus modifier le client/contact ni un autre appareil.
    device = session["data"].setdefault("current_device", {})

    if key in {"pression_amont", "pression_zone", "pression_aval", "differentiel"}:
        bundle = parse_measurement_bundle(text, key)
        if bundle:
            for k, v in bundle.items():
                device[k] = v
            session["current_step"] = next_missing_step(session, idx + 1)
            save_session(session)
            await msg.reply_text(
                "📏 Mesures réparties : "
                + " | ".join(f"{k.replace('pression_','P ').replace('differentiel','ΔP')} = {v}" for k, v in bundle.items())
            )
            if session["current_step"] >= len(ALL_STEPS):
                await complete_current_device(session, chat_id, context)
            else:
                await send_step(chat_id, session, context)
            return
        text = normalize_measurement_value(key, text)

    # Tout le reste est enregistré exactement dans le champ demandé.
    device[key] = text
    if key == "essais":
        autofill_diagnosis_and_recommendation(session["data"])

    session["current_step"] = next_missing_step(session, idx + 1)
    save_session(session)

    if key == "essais":
        note = live_diagnostic(session["data"])
        if note:
            await msg.reply_text("🧠 LECTURE PROVISOIRE\n\n" + note)
        await msg.reply_text(
            "🧾 DIAGNOSTIC AUTOMATIQUE\n\n"
            + device.get("diagnostic", "—")
            + "\n\n🔧 SUITE PROPOSÉE\n"
            + device.get("recommandation", "—")
        )

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
    app.add_handler(CallbackQueryHandler(callback_result, pattern=r"^result:"))
    app.add_handler(CallbackQueryHandler(callback_status, pattern=r"^status:"))
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, receive))

    app.run_polling()


if __name__ == "__main__":
    main()
