import os
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")

CATEGORY, SUBSERVICE, QUESTIONS, MEDIA, CITY, PHONE, AVAILABILITY, CONFIRM = range(8)


SERVICES = {

    "🚨 Astreinte / Urgence": {
        "💦 Fuite importante": [
            "Où se situe la fuite ?",
            "L'eau coule-t-elle encore actuellement ?",
            "Pouvez-vous couper l'arrivée d'eau ?",
            "Y a-t-il de l'eau à proximité d'une installation électrique ?",
        ],
        "🌊 Dégât des eaux": [
            "Où apparaissent l'eau ou les dégâts ?",
            "Connaissez-vous l'origine de la fuite ?",
            "L'arrivée d'eau a-t-elle été coupée ?",
            "La fuite est-elle toujours active ?",
        ],
        "🚽 WC totalement bouché": [
            "L'eau monte-t-elle lorsque vous tirez la chasse ?",
            "Avez-vous un autre WC utilisable dans le logement ?",
            "D'autres évacuations sont-elles également bouchées ?",
        ],
        "🔥 Plus de chauffage": [
            "Quel appareil produit votre chauffage ?",
            "Quelle est sa marque ?",
            "Un code défaut est-il affiché ?",
            "Avez-vous encore de l'eau chaude ?",
        ],
        "🚿 Plus d'eau chaude": [
            "Quel appareil produit l'eau chaude ? Chaudière, chauffe-eau ou autre ?",
            "Quelle est sa marque ?",
            "Voyez-vous un défaut, une fuite ou un voyant inhabituel ?",
        ],
        "❗ Autre urgence": [
            "Décrivez précisément la situation et ce qui vous semble urgent.",
        ],
    },

    "🔧 Plomberie": {
        "🚰 Robinetterie": [
            "Quel équipement est concerné ?",
            "Souhaitez-vous une réparation ou un remplacement ?",
            "Connaissez-vous la marque du matériel ?",
        ],
        "🚽 WC": [
            "Quel est le problème avec le WC ?",
            "Est-il suspendu ou posé au sol ?",
            "Souhaitez-vous une réparation ou un remplacement ?",
        ],
        "🧺 Machine à laver / lave-vaisselle": [
            "Souhaitez-vous créer, déplacer ou réparer l'alimentation ?",
            "Une évacuation existe-t-elle déjà à proximité ?",
        ],
        "🚰 Création arrivée / évacuation": [
            "Quel équipement souhaitez-vous raccorder ?",
            "Une alimentation ou une évacuation existe-t-elle à proximité ?",
        ],
        "🔧 Autre plomberie": [
            "Décrivez les travaux ou le problème rencontré.",
        ],
    },

    "💧 Fuite / Réparation": {
        "🚰 Robinet / mitigeur": [
            "Quel équipement fuit ? Cuisine, lavabo, douche, baignoire...",
            "La fuite est-elle permanente ?",
            "Pouvez-vous couper ou isoler l'arrivée d'eau concernée ?",
        ],
        "🔩 Raccord / flexible": [
            "Où se situe le raccord ou flexible qui fuit ?",
            "La fuite est-elle importante ou seulement goutte à goutte ?",
            "Pouvez-vous couper l'eau ?",
        ],
        "🚽 Fuite WC": [
            "Où voyez-vous la fuite ? Cuvette, réservoir, alimentation ou sol ?",
            "Le WC est-il suspendu ou posé au sol ?",
            "Pouvez-vous utiliser le WC actuellement ?",
        ],
        "🚿 Douche / baignoire": [
            "Où voyez-vous la fuite ? Robinetterie, évacuation, joints ou dessous ?",
            "La fuite apparaît-elle uniquement pendant l'utilisation ?",
        ],
        "🚰 Évier / lavabo": [
            "La fuite vient-elle du robinet, siphon, évacuation ou alimentation ?",
            "La fuite apparaît-elle uniquement pendant l'utilisation ?",
        ],
        "🚿 Chauffe-eau": [
            "Où voyez-vous la fuite ? Groupe de sécurité, raccord, cuve ou dessous du ballon ?",
            "La fuite est-elle permanente ?",
            "Pouvez-vous couper l'arrivée d'eau du chauffe-eau ?",
        ],
        "♨️ Radiateur / chauffage": [
            "Où voyez-vous la fuite ? Robinet, purgeur, raccord ou radiateur ?",
            "La pression de la chaudière baisse-t-elle ?",
            "Pouvez-vous isoler le radiateur ?",
        ],
        "🔧 Canalisation visible": [
            "Où se trouve la canalisation qui fuit ?",
            "Savez-vous s'il s'agit d'eau chaude ou d'eau froide ?",
            "Pouvez-vous couper l'eau ?",
        ],
        "❓ Autre fuite visible": [
            "Décrivez précisément où vous voyez l'eau et ce qui semble fuir.",
            "La fuite est-elle toujours active ?",
            "Pouvez-vous couper l'eau ?",
        ],
    },

    "🔥 Chaudière / Chauffage": {
        "🧹 Entretien chaudière": [
            "Quelle est la marque de la chaudière ?",
            "Quel est son modèle si vous le connaissez ?",
            "Est-ce une chaudière gaz, fioul ou autre ?",
            "Fonctionne-t-elle normalement actuellement ?",
        ],
        "🛠 Dépannage chaudière": [
            "Quelle est la marque et le modèle de la chaudière ?",
            "Décrivez le problème rencontré.",
            "Un code défaut est-il affiché ? Si oui, lequel ?",
            "Avez-vous encore du chauffage ?",
            "Avez-vous encore de l'eau chaude ?",
            "Avez-vous essayé un reset/réarmement ?",
        ],
        "🔄 Remplacement chaudière": [
            "Quelle chaudière possédez-vous actuellement ?",
            "Maison ou appartement ?",
            "Quelle est approximativement la surface du logement ?",
            "Combien de personnes occupent le logement ?",
            "Souhaitez-vous chauffage seul ou chauffage + eau chaude ?",
        ],
        "🥶 Radiateur froid": [
            "Un seul radiateur est concerné ou plusieurs ?",
            "Le radiateur chauffe-t-il partiellement ?",
            "Quel type de chauffage possédez-vous ?",
        ],
        "💧 Radiateur qui fuit": [
            "La fuite vient-elle du robinet, purgeur, raccord ou radiateur ?",
            "Pouvez-vous isoler le radiateur ?",
        ],
        "🔄 Remplacement radiateur": [
            "Combien de radiateurs souhaitez-vous remplacer ?",
            "Indiquez approximativement leurs dimensions.",
            "Quel type de chauffage possédez-vous ?",
        ],
    },

    "🚿 Chauffe-eau": {
        "🛠 Dépannage chauffe-eau": [
            "Quel problème rencontrez-vous ?",
            "Quelle est approximativement sa capacité ?",
            "Quelle est la marque et le modèle si vous les connaissez ?",
            "Quel âge approximatif a le chauffe-eau ?",
            "Disjoncte-t-il au tableau électrique ?",
        ],
        "🔄 Remplacement chauffe-eau": [
            "Quelle est la capacité actuelle ?",
            "Est-il vertical, horizontal ou extra-plat ?",
            "Combien de personnes utilisent l'eau chaude ?",
            "Souhaitez-vous conserver la même capacité ?",
        ],
        "💦 Chauffe-eau qui fuit": [
            "D'où semble provenir la fuite ?",
            "La fuite est-elle permanente ?",
            "Pouvez-vous couper l'arrivée d'eau du chauffe-eau ?",
        ],
        "🔧 Groupe de sécurité": [
            "Le groupe de sécurité coule-t-il ?",
            "Coule-t-il seulement pendant la chauffe ou en permanence ?",
            "Connaissez-vous l'âge du chauffe-eau ?",
        ],
    },

    "🔥 Gaz / Mise aux normes": {
        "🛡 Mise en conformité gaz": [
            "Pourquoi souhaitez-vous une mise en conformité ?",
            "Disposez-vous d'un diagnostic ou rapport mentionnant des anomalies ?",
            "Quel appareil est alimenté au gaz ?",
        ],
        "🔧 Modification canalisation gaz": [
            "Que souhaitez-vous modifier sur l'installation gaz ?",
            "Quel appareil est concerné ?",
            "Connaissez-vous le type de gaz utilisé ?",
        ],
        "🟡 PC gaz / point de coupure": [
            "Souhaitez-vous créer, déplacer ou remplacer le point de coupure gaz ?",
            "Quel appareil est concerné ?",
            "Où se trouve actuellement l'arrivée gaz ?",
        ],
        "🍳 Plaque / cuisinière gaz": [
            "Souhaitez-vous raccorder, déplacer ou supprimer l'alimentation ?",
            "L'arrivée gaz existe-t-elle déjà à proximité ?",
        ],
        "🔥 Raccordement chaudière gaz": [
            "Quelle est la marque et le modèle de la chaudière ?",
            "L'alimentation gaz existe-t-elle déjà ?",
            "S'agit-il d'un remplacement ou d'une nouvelle installation ?",
        ],
        "🚫 Condamnation gaz": [
            "Quelle alimentation souhaitez-vous condamner ?",
            "Quel appareil était raccordé dessus ?",
        ],
        "⚠️ Odeur / suspicion de gaz": [
            "Décrivez brièvement où et quand vous avez constaté l'odeur.",
        ],
    },

    "🌀 Débouchage": {
        "🚰 Évier / lavabo": [
            "L'eau est-elle bloquée ou s'écoule-t-elle lentement ?",
            "D'autres équipements sont-ils également bouchés ?",
            "Avez-vous déjà essayé de déboucher ?",
        ],
        "🚿 Douche / baignoire": [
            "L'eau est-elle bloquée ou s'écoule-t-elle lentement ?",
            "L'eau remonte-t-elle dans un autre équipement ?",
        ],
        "🚽 WC": [
            "L'eau monte-t-elle dans la cuvette ?",
            "Le WC est-il totalement inutilisable ?",
            "D'autres évacuations sont-elles touchées ?",
        ],
        "🏠 Canalisation générale": [
            "Quels équipements sont touchés ?",
            "Disposez-vous d'un regard ou d'un accès à la canalisation ?",
            "Le problème concerne-t-il toute l'habitation ?",
        ],
        "🏢 Colonne": [
            "S'agit-il d'un immeuble ?",
            "Plusieurs logements sont-ils concernés ?",
            "Où se situe l'accès à la colonne ?",
        ],
        "🌧 Gouttière / descente EP": [
            "Le bouchon concerne-t-il une gouttière ou une descente ?",
            "À quelle hauteur approximative se situe l'installation ?",
            "L'accès est-il possible avec une échelle ?",
        ],
    },

    "📹 Inspection caméra": {
        "🔎 Recherche de bouchon": [
            "Quelle canalisation souhaitez-vous inspecter ?",
            "Quel problème constatez-vous ?",
            "Existe-t-il un accès à la canalisation ?",
        ],
        "🌀 Contrôle après débouchage": [
            "Quel réseau a été débouché ?",
            "Le problème revient-il régulièrement ?",
        ],
        "💥 Recherche casse / défaut": [
            "Pourquoi suspectez-vous une casse ou un défaut ?",
            "La canalisation est-elle intérieure ou extérieure ?",
        ],
        "🏠 Contrôle canalisation": [
            "Pourquoi souhaitez-vous contrôler la canalisation ?",
            "Savez-vous approximativement son diamètre ?",
        ],
    },

    "🔎 Recherche de fuite": {
        "🧱 Fuite encastrée": [
            "Où pensez-vous que la fuite se situe ?",
            "Voyez-vous des traces d'humidité ?",
            "Le compteur tourne-t-il lorsque tout est fermé ?",
        ],
        "🏠 Plafond / mur humide": [
            "Où se trouvent les traces ?",
            "Y a-t-il une salle d'eau ou un logement au-dessus ?",
            "Depuis quand les traces sont-elles apparues ?",
        ],
        "🔥 Réseau chauffage": [
            "La pression de la chaudière baisse-t-elle régulièrement ?",
            "Voyez-vous une fuite sur un radiateur ou une canalisation ?",
            "À quelle fréquence devez-vous remettre de l'eau ?",
        ],
        "📋 Recherche pour assurance": [
            "Disposez-vous d'une demande de votre assurance ?",
            "Où les dégâts sont-ils visibles ?",
            "Connaissez-vous l'origine supposée de la fuite ?",
        ],
        "❓ Origine inconnue": [
            "Décrivez les signes qui vous font penser à une fuite.",
            "Le compteur tourne-t-il lorsque tous les robinets sont fermés ?",
        ],
    },

    "♨️ Désembouage": {
        "🔥 Radiateurs": [
            "Combien de radiateurs possède l'installation ?",
            "Combien chauffent mal ou restent froids ?",
            "Quel type de chaudière possédez-vous ?",
            "Quel âge approximatif a l'installation ?",
        ],
        "🏠 Installation complète": [
            "Maison ou appartement ?",
            "Combien de radiateurs possède l'installation ?",
            "Quelle est la surface approximative ?",
            "Quel générateur de chauffage possédez-vous ?",
        ],
        "♨️ Plancher chauffant": [
            "Quelle surface approximative est chauffée au sol ?",
            "Certaines zones chauffent-elles moins que d'autres ?",
            "Quel est le générateur de chauffage ?",
        ],
    },

    "🌧 Gouttières": {
        "🌀 Débouchage gouttière": [
            "La gouttière déborde-t-elle lorsqu'il pleut ?",
            "Le problème vient-il de la gouttière ou de la descente ?",
            "À quelle hauteur approximative se trouve-t-elle ?",
        ],
        "🧹 Nettoyage gouttières": [
            "Combien de mètres environ sont à nettoyer ?",
            "Maison de plain-pied ou avec étage ?",
            "L'accès est-il dégagé ?",
        ],
        "🔧 Réparation gouttière": [
            "Quel problème constatez-vous ? Fuite, raccord, pente, fixation...",
            "Quel est le matériau de la gouttière si vous le connaissez ?",
        ],
    },

    "🧹 Ramonage": {
        "🔥 Conduit chaudière": [
            "Quel appareil est raccordé au conduit ?",
            "Quel combustible utilisez-vous ?",
            "Quand a eu lieu le dernier ramonage ?",
        ],
        "🪵 Poêle / cheminée": [
            "S'agit-il d'une cheminée, d'un insert ou d'un poêle ?",
            "Bois, granulés ou autre combustible ?",
            "Quand a eu lieu le dernier ramonage ?",
            "Quelle est approximativement la hauteur du conduit ?",
        ],
    },

    "🛁 Salle de bain": {
        "🏗 Rénovation complète": [
            "Décrivez votre projet de salle de bain.",
            "Quelles sont approximativement les dimensions de la pièce ?",
            "Souhaitez-vous modifier l'emplacement des équipements ?",
            "Les équipements sont-ils déjà choisis ?",
        ],
        "🛁 Baignoire → douche": [
            "Quelles sont les dimensions approximatives de la baignoire ?",
            "Souhaitez-vous une douche classique, extra-plate ou PMR ?",
            "Souhaitez-vous également refaire la faïence ?",
        ],
        "🚿 Création / remplacement douche": [
            "Décrivez l'installation actuelle.",
            "Quelles dimensions souhaitez-vous ?",
            "Souhaitez-vous modifier les arrivées ou évacuations ?",
        ],
        "🚰 Meuble vasque": [
            "Simple ou double vasque ?",
            "Avez-vous déjà acheté le meuble ?",
            "Faut-il déplacer les alimentations ou l'évacuation ?",
        ],
        "🚽 WC": [
            "Souhaitez-vous poser, remplacer ou déplacer le WC ?",
            "WC suspendu ou posé au sol ?",
        ],
    },

    "🧱 Carrelage / Faïence": {
        "🛁 Salle de bain": [
            "Souhaitez-vous carreler le sol, les murs ou les deux ?",
            "Quelle est approximativement la surface ?",
            "L'ancien carrelage doit-il être déposé ?",
        ],
        "🚿 Douche": [
            "Quelle zone souhaitez-vous carreler ou reprendre ?",
            "Y a-t-il actuellement un problème d'étanchéité ?",
        ],
        "🍳 Crédence cuisine": [
            "Quelle est approximativement la surface de la crédence ?",
            "L'ancien revêtement doit-il être retiré ?",
        ],
        "🔧 Réparation / reprise": [
            "Décrivez la réparation nécessaire.",
            "Disposez-vous de carreaux identiques ?",
        ],
    },

    "🎨 Peinture": {
        "🏠 Murs": [
            "Quelle pièce souhaitez-vous repeindre ?",
            "Quelle est approximativement la surface ?",
            "Quel est l'état actuel des murs ?",
        ],
        "⬆️ Plafond": [
            "Quelle est approximativement la surface du plafond ?",
            "Y a-t-il des fissures, taches ou dégâts des eaux ?",
        ],
        "💦 Après dégât des eaux": [
            "La cause du dégât des eaux est-elle réparée ?",
            "Les supports sont-ils actuellement secs ?",
            "Quelles zones sont endommagées ?",
        ],
        "🛁 Salle de bain": [
            "Quelles parties souhaitez-vous repeindre ?",
            "Y a-t-il actuellement des traces d'humidité ou moisissures ?",
        ],
    },

    "📝 Devis / Autres travaux": {
        "🏠 Installation plomberie": [
            "Décrivez précisément votre projet.",
            "Construction, rénovation ou modification d'une installation existante ?",
        ],
        "🍳 Cuisine": [
            "Quels travaux souhaitez-vous réaliser ?",
            "Faut-il créer ou déplacer des arrivées et évacuations ?",
        ],
        "🏗 Rénovation": [
            "Décrivez les travaux envisagés.",
            "Quelles pièces sont concernées ?",
        ],
        "📋 Autre demande": [
            "Décrivez précisément les travaux souhaités.",
        ],
    },
}


def make_keyboard(items, columns=2, extra=None):
    rows = []
    for i in range(0, len(items), columns):
        rows.append(items[i:i + columns])

    if extra:
        for item in extra:
            rows.append([item])

    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def category_keyboard():
    return make_keyboard(list(SERVICES.keys()), 2)


def subservice_keyboard(category):
    return make_keyboard(
        list(SERVICES[category].keys()),
        1,
        ["🏠 Accueil"],
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "👋 Bonjour et bienvenue chez Plomberie Aqualeo.\n\n"
        "🔧 Plomberie • Chauffage • Dépannage • Rénovation\n\n"
        "Quelques questions rapides vont nous permettre de comprendre "
        "votre demande et de prévoir le matériel nécessaire avant l'intervention.\n\n"
        "📸 Vous pourrez envoyer plusieurs photos ou vidéos.\n\n"
        "👇 Sélectionnez votre besoin :",
        reply_markup=category_keyboard(),
    )

    return CATEGORY


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Votre identifiant Telegram est :\n\n{update.effective_chat.id}"
    )


async def choose_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text not in SERVICES:
        await update.message.reply_text(
            "👇 Sélectionnez votre besoin avec les boutons.",
            reply_markup=category_keyboard(),
        )
        return CATEGORY

    context.user_data["category"] = text

    if text == "🔥 Gaz / Mise aux normes":
        await update.message.reply_text(
            "⚠️ SÉCURITÉ GAZ\n\n"
            "En cas d'odeur importante ou de danger immédiat, "
            "n'utilisez pas de flamme, évitez les interrupteurs électriques, "
            "aérez si cela peut être fait sans danger, fermez l'arrivée de gaz "
            "si possible et contactez le service d'urgence gaz compétent."
        )

    await update.message.reply_text(
        f"{text}\n\n👇 Sélectionnez votre situation :",
        reply_markup=subservice_keyboard(text),
    )

    return SUBSERVICE


async def choose_subservice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "🏠 Accueil":
        return await start(update, context)

    category = context.user_data.get("category")

    if not category or text not in SERVICES[category]:
        await update.message.reply_text("Merci d'utiliser les boutons proposés.")
        return SUBSERVICE

    context.user_data["subservice"] = text
    context.user_data["answers"] = []
    context.user_data["question_index"] = 0
    context.user_data["media"] = []

    if text == "⚠️ Odeur / suspicion de gaz":
        await update.message.reply_text(
            "🚨 Une suspicion de fuite de gaz peut présenter un danger immédiat.\n\n"
            "En cas d'odeur importante, éloignez-vous si nécessaire et contactez "
            "le service d'urgence gaz compétent depuis un endroit sûr.\n\n"
            "Cette messagerie ne remplace pas un service d'urgence."
        )

    questions = SERVICES[category][text]

    await update.message.reply_text(
        f"Question 1/{len(questions)}\n\n{questions[0]}",
        reply_markup=make_keyboard([], extra=["🏠 Accueil"]),
    )

    return QUESTIONS


async def answer_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "🏠 Accueil":
        return await start(update, context)

    category = context.user_data["category"]
    subservice = context.user_data["subservice"]
    questions = SERVICES[category][subservice]

    index = context.user_data["question_index"]

    context.user_data["answers"].append({
        "question": questions[index],
        "answer": text,
    })

    index += 1
    context.user_data["question_index"] = index

    if index < len(questions):
        await update.message.reply_text(
            f"Question {index + 1}/{len(questions)}\n\n{questions[index]}"
        )
        return QUESTIONS

    await update.message.reply_text(
        "📸 PHOTOS / VIDÉOS\n\n"
        "Envoyez une ou plusieurs photos ou vidéos afin de nous aider "
        "à préparer l'intervention.\n\n"
        "Si possible :\n"
        "• vue générale\n"
        "• vue rapprochée du problème\n"
        "• plaque signalétique / référence\n"
        "• raccordements ou canalisations concernés\n\n"
        "Quand vous avez terminé, appuyez sur « ✅ Terminé ».",
        reply_markup=make_keyboard(
            [],
            extra=["✅ Terminé", "⏭ Continuer sans photo", "🏠 Accueil"],
        ),
    )

    return MEDIA


async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["media"].append({
        "type": "photo",
        "file_id": update.message.photo[-1].file_id,
    })

    await update.message.reply_text(
        f"✅ Photo reçue ({len(context.user_data['media'])} fichier(s)).\n"
        "Vous pouvez en envoyer d'autres ou appuyer sur « ✅ Terminé »."
    )
    return MEDIA


async def receive_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["media"].append({
        "type": "video",
        "file_id": update.message.video.file_id,
    })

    await update.message.reply_text(
        f"✅ Vidéo reçue ({len(context.user_data['media'])} fichier(s)).\n"
        "Vous pouvez en envoyer d'autres ou appuyer sur « ✅ Terminé »."
    )
    return MEDIA


async def finish_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "🏠 Accueil":
        return await start(update, context)

    if text not in ["✅ Terminé", "⏭ Continuer sans photo"]:
        await update.message.reply_text(
            "📸 Envoyez une photo/vidéo ou utilisez un des boutons."
        )
        return MEDIA

    await update.message.reply_text(
        "📍 Dans quelle ville doit avoir lieu l'intervention ?",
        reply_markup=make_keyboard([], extra=["🏠 Accueil"]),
    )

    return CITY


async def save_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🏠 Accueil":
        return await start(update, context)

    context.user_data["city"] = update.message.text

    contact_button = KeyboardButton(
        "📱 Partager mon numéro",
        request_contact=True,
    )

    await update.message.reply_text(
        "📞 Quel numéro pouvons-nous utiliser pour vous contacter ?",
        reply_markup=ReplyKeyboardMarkup(
            [[contact_button], ["🏠 Accueil"]],
            resize_keyboard=True,
        ),
    )

    return PHONE


async def save_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.contact.phone_number
    await ask_availability(update)
    return AVAILABILITY


async def save_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🏠 Accueil":
        return await start(update, context)

    context.user_data["phone"] = update.message.text
    await ask_availability(update)
    return AVAILABILITY


async def ask_availability(update: Update):
    await update.message.reply_text(
        "🗓 Quand souhaitez-vous une intervention ?\n\n"
        "Exemple : dès que possible, aujourd'hui après 17h, demain matin...",
        reply_markup=make_keyboard(
            ["⚡ Dès que possible"],
            1,
            ["🏠 Accueil"],
        ),
    )


async def save_availability(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🏠 Accueil":
        return await start(update, context)

    context.user_data["availability"] = update.message.text

    await update.message.reply_text(
        "📋 RÉCAPITULATIF\n\n"
        + customer_summary(context.user_data)
        + "\n\nVérifiez les informations avant l'envoi.",
        reply_markup=make_keyboard(
            [],
            extra=["✅ Envoyer ma demande", "🔄 Recommencer"],
        ),
    )

    return CONFIRM


def customer_summary(data):
    lines = [
        data.get("category", ""),
        f"➡️ {data.get('subservice', '')}",
        "",
    ]

    for item in data.get("answers", []):
        lines.append(f"❓ {item['question']}")
        lines.append(f"➡️ {item['answer']}")
        lines.append("")

    photos = sum(
        1 for item in data.get("media", [])
        if item["type"] == "photo"
    )
    videos = sum(
        1 for item in data.get("media", [])
        if item["type"] == "video"
    )

    lines.extend([
        f"📍 Ville : {data.get('city', '')}",
        f"📞 Téléphone : {data.get('phone', '')}",
        f"🗓 Disponibilité : {data.get('availability', '')}",
        f"📸 Photos : {photos}",
        f"🎥 Vidéos : {videos}",
    ])

    return "\n".join(lines)


def admin_summary(update, data):
    user = update.effective_user
    username = f"@{user.username}" if user.username else "Non renseigné"

    if data.get("category") == "🚨 Astreinte / Urgence":
        header = "🚨🚨 ASTREINTE / URGENCE AQUALEO 🚨🚨"
    else:
        header = "🔔 NOUVELLE DEMANDE AQUALEO"

    return (
        f"{header}\n\n"
        f"👤 Client : {user.full_name}\n"
        f"✈️ Telegram : {username}\n\n"
        + customer_summary(data)
    )


async def confirm_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "🔄 Recommencer":
        return await start(update, context)

    if text != "✅ Envoyer ma demande":
        return CONFIRM

    data = context.user_data

    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text=admin_summary(update, data),
            )

            for media in data.get("media", []):
                if media["type"] == "photo":
                    await context.bot.send_photo(
                        chat_id=int(ADMIN_CHAT_ID),
                        photo=media["file_id"],
                    )
                elif media["type"] == "video":
                    await context.bot.send_video(
                        chat_id=int(ADMIN_CHAT_ID),
                        video=media["file_id"],
                    )

        except Exception as error:
            print(f"Erreur envoi Aqualeo : {error}")

    await update.message.reply_text(
        "✅ Votre demande a bien été enregistrée.\n\n"
        "Les informations et médias transmis permettront à Plomberie Aqualeo "
        "de mieux préparer l'intervention et le matériel nécessaire.\n\n"
        "🔧 Plomberie Aqualeo",
        reply_markup=make_keyboard(["🏠 Nouvelle demande"], 1),
    )

    context.user_data.clear()
    return CATEGORY


async def new_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await start(update, context)


async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("start", "Accueil Aqualeo"),
        BotCommand("myid", "Mon identifiant Telegram"),
    ])


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN n'est pas configuré.")

    application = (
        Application.builder()
        .token(TOKEN)
        .concurrent_updates(False)
        .post_init(post_init)
        .build()
    )

    conversation = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(
                filters.Regex("^🏠 Nouvelle demande$"),
                new_request,
            ),
        ],
        states={
            CATEGORY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, choose_category)
            ],
            SUBSERVICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, choose_subservice)
            ],
            QUESTIONS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, answer_question)
            ],
            MEDIA: [
                MessageHandler(filters.PHOTO, receive_photo),
                MessageHandler(filters.VIDEO, receive_video),
                MessageHandler(filters.TEXT & ~filters.COMMAND, finish_media),
            ],
            CITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_city)
            ],
            PHONE: [
                MessageHandler(filters.CONTACT, save_contact),
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_phone),
            ],
            AVAILABILITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_availability)
            ],
            CONFIRM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_request)
            ],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )

    application.add_handler(CommandHandler("myid", myid))
    application.add_handler(conversation)

    application.run_polling()


if __name__ == "__main__":
    main()
