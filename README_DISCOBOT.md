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

## Principes

Discobot est guidé mais non bloquant :

- une étape peut être renseignée ;
- passée ;
- marquée impossible ;
- marquée non vérifiable ;
- aucune mesure n'est inventée ;
- le bot ne déclare pas automatiquement un appareil conforme.

La V0.1 stocke les contrôles en SQLite. Une version suivante pourra utiliser Supabase pour les clients, sites, appareils, photos, historiques et rapports.
