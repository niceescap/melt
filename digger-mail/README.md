# DiggerMail

Application Python synchrone et autonome qui se connecte en IMAP IDLE à une
boîte mail dédiée, détecte les nouveaux messages, filtre, et répond
automatiquement :

- expéditeur **whitelisté** → réponse générée par un LLM (outil `read_kb` pour
  la base de connaissances DiggerRadio) ;
- expéditeur **hors whitelist** → réponse de refus (template fixe).

Tout est tracé dans une base SQLite locale. MVP de test sur
`mailtest@diggerradio.com`, à adapter pour DiggerRadio en production.

## Installation

```bash
cd digger-mail
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Deux fichiers, à copier depuis les exemples :

```bash
cp .env.example .env          # secrets uniquement
cp config.yaml.example config.yaml
```

### `.env` (secrets)

| Variable | Rôle |
| --- | --- |
| `OVH_MAILTEST_USER` | adresse de la boîte dédiée |
| `OVH_MAILTEST_PASSWORD` | mot de passe IMAP/SMTP |
| `LLM_API_KEY` | clé API du LLM |

### `config.yaml`

Tout le reste : hôtes/ports IMAP et SMTP, compte OVH, domaine pour les
`Message-ID`, whitelist d'adresses, bloc `llm` (endpoint, modèle, température,
prompt système, `tools`), bloc `limits`, chemins des templates, niveau de log.

Points clés :

- `llm.tools.enabled` : `false` désactive l'outil `read_kb` (aucun tool_call,
  `tool_trace` reste `null`).
- `llm.tools.max_iterations` : nombre max d'allers-retours d'outils (3 par défaut).
- `limits.fresh_message_seconds` : fenêtre de fraîcheur utilisée uniquement au
  seed initial.
- `limits.throttle_hours` : fenêtre anti-doublon pour les expéditeurs inconnus.

### Base de connaissances

Copier `knowledge_base.md.example` vers `knowledge_base.md` (ou le chemin
déclaré dans `llm.tools.kb_path`). Le fichier est relu à **chaque** appel de
l'outil (pas de cache).

## Lancement manuel

```bash
# Mode normal
python3 main.py --config config.yaml --env .env

# Mode dry-run : se connecte, idle, logge, n'envoie rien
python3 main.py --config config.yaml --env .env --dry-run
```

Au premier démarrage, `last_uid_seen` est initialisé avec le `MAX(UID)` de la
boîte (seed) : les anciens messages ne sont pas traités.

## Installation systemd

```bash
sudo cp digger-mail.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now digger-mail
```

Le service lance `main.py` en tant que `User=nicee` depuis
`/srv/vrac-tests`, avec `EnvironmentFile=/srv/vrac-tests/.env` et
`Restart=on-failure`.

## Statistiques

```bash
# Décompte par statut (aujourd'hui, 7 jours, 30 jours)
python3 -c "from state import stats; print(stats())"

# Ligne par ligne
sqlite3 digger_mail.sqlite3 \
  "SELECT uid, from_addr, status, substr(reply_body,1,40) FROM received ORDER BY uid DESC LIMIT 20;"
```

Statuts possibles : `pending`, `processing`, `replied`, `filtered`, `failed`,
`throttled`.

## Dépannage

| Symptôme | Piste |
| --- | --- |
| `Échec appel LLM` dans les logs | vérifier `LLM_API_KEY`, l'endpoint, le modèle, le timeout |
| `Échec SMTP` | vérifier `OVH_MAILTEST_PASSWORD`, l'hôte/port SMTP, les quotas |
| `KB inaccessible` | le chemin `llm.tools.kb_path` est faux ; le LLM répond quand même |
| `max_tool_iterations atteint` | le LLM boucle sur l'outil ; vérifier le prompt ou la KB |
| Aucun message traité | vérifier `last_uid_seen` en base et le seed initial |
| `Throttle uid=...` | normal : réponse déjà envoyée dans la fenêtre de 24 h |
| Déconnexions répétées | backoff exponentiel plafonné à 30 s ; consulter `journalctl -u digger-mail` |

Logs : `journalctl -u digger-mail -f` (stdout/stderr → journald).

## Tests

```bash
cd digger-mail
pytest tests/
```

Aucun test ne touche au réseau : `call_llm` est mocké, les fixtures `.eml`
sont dans `tests/fixtures/`.

## Ce que ce MVP ne fait pas

- Pas de reconstruction de conversation côté serveur (l'historique est dans
  les citations `>` du corps, transmis tel quel au LLM).
- Pas de mémoire conversationnelle côté LLM (stateless).
- Pas de retry LLM/SMTP, pas de cache KB, pas de serveur HTTP, pas d'asyncio.