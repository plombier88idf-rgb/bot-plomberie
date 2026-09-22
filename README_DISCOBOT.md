# Discobot Aqualeo

Branche dédiée au bot Telegram de contrôle des disconnecteurs.

## Démarrage Railway

Commande de démarrage :

```
python discobot.py
```

Variable obligatoire :

- `BOT_TOKEN` : token Telegram BotFather.

Variable optionnelle :

- `ALLOWED_TELEGRAM_USER_IDS` : identifiant Telegram autorisé. Plusieurs IDs peuvent être séparés par des virgules.
- `DB_PATH` : chemin SQLite.
- `OPENAI_API_KEY` : active l'analyse intelligente des photos et du texte libre.
- `OPENAI_MODEL` : modèle d'analyse, par défaut `gpt-5.6-luna`.

## Principes

Discobot est guidé mais non bloquant :

- une étape peut être renseignée ;
- passée ;
- marquée impossible ;
- marquée non vérifiable ;
- aucune mesure n'est inventée ;
- le bot ne déclare pas automatiquement un appareil conforme.

La V0.1 stocke les contrôles en SQLite. Une version suivante pourra utiliser Supabase pour les clients, sites, appareils, photos, historiques et rapports.


## V0.3 — dossier intelligent

- Une photo de dossier peut être envoyée dès `/nouveau`.
- Une phrase contenant site + adresse + contact est répartie dans les bons champs.
- Les données déjà comprises ne sont pas redemandées.
- Les photos de plaques peuvent préremplir marque, modèle, type, DN et série.
- Toute donnée incertaine reste vide : aucune invention.
