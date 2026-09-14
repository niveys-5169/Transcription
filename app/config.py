"""Configuration de l'application.

Les réglages viennent de trois sources, par priorité décroissante :

1. les variables d'environnement (pratique pour un déploiement) ;
2. le fichier ``data/config.json`` (écrit par l'écran Réglages de l'app) ;
3. les valeurs par défaut ci-dessous.

Les clés API (RunPod, Anthropic) ne quittent jamais la machine : elles sont
stockées dans ``data/config.json``, qui est exclu du dépôt par ``.gitignore``.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path

# Racine des données : médias extraits, base SQLite, config.
DATA_DIR = Path(os.environ.get("TRANSCRIPTION_DATA_DIR", Path.cwd() / "data")).resolve()
MEDIA_DIR = DATA_DIR / "media"
CONFIG_PATH = DATA_DIR / "config.json"
DB_PATH = DATA_DIR / "transcription.db"
# Un .md finalisé par cours (relu, vérifié ou publié) : la matière première du
# Doc maître NotebookLM (voir app/notebooklm_sync.py). Indépendant du coffre
# Obsidian, qui reste optionnel — ce dossier existe dès qu'un cours est relu.
COURSES_DIR = DATA_DIR / "cours"

# Modèles Whisper acceptés, du plus rapide au plus précis.
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]

# Moteurs de transcription disponibles.
ENGINES = ["local", "runpod"]

# Comment le moteur RunPod démarre le calcul :
# - "off" : uniquement le serverless. Si aucun worker ne le prend en charge,
#   la transcription échoue avec un message explicite.
# - "fallback" : le serverless d'abord ; si aucun worker ne prend en charge
#   le premier tronçon avant runpod_launch_timeout_seconds, bascule sur un
#   pod créé à ce moment-là.
# - "always" : pod dès le premier tronçon, sans jamais essayer le
#   serverless — utile quand on sait déjà qu'il n'a pas de capacité, pour ne
#   pas perdre runpod_launch_timeout_seconds à l'attendre pour rien.
RUNPOD_POD_MODES = ["off", "fallback", "always"]

# Modes de relecture.
PROOFREAD_MODES = ["claude", "basic", "none"]

# Comment l'application appelle Claude : le CLI (abonnement) ou l'API (clé).
CLAUDE_BACKENDS = ["cli", "api"]

# Champs considérés comme secrets : jamais renvoyés en clair par l'API.
SECRET_FIELDS = {"runpod_api_key", "anthropic_api_key"}

_lock = threading.Lock()


@dataclass
class Settings:
    """Réglages persistés de l'application."""

    # --- Moteur de transcription ---
    default_engine: str = "local"
    default_model: str = "large-v3"
    language: str = "fr"

    # --- RunPod (GPU cloud, optionnel) ---
    runpod_api_key: str = ""
    runpod_endpoint_id: str = ""
    # Taille des tronçons envoyés à RunPod. L'API /run plafonne la charge utile
    # à ~10 Mo ; un WAV 16 kHz mono 16 bits encodé en base64 pèse ~42 ko/s, donc
    # un cours d'une heure ne peut pas partir en un seul appel.
    # 180 s ≈ 7,3 Mo encodés : une vraie marge sous la limite. 240 s (l'ancien
    # défaut) donnait ≈ 9,8 Mo — trop près du plafond une fois l'enveloppe
    # JSON ajoutée, avec un risque d'échec intermittent selon les tronçons.
    runpod_chunk_seconds: int = 180
    # Combien de temps un premier tronçon peut rester « en file » avant de
    # conclure qu'aucun worker serverless ne va démarrer (voir pod de secours
    # ci-dessous). Sans rapport avec MAX_WAIT_PER_CHUNK, qui borne l'attente
    # totale une fois qu'un worker a effectivement pris le job.
    runpod_launch_timeout_seconds: int = 90

    # --- RunPod : pod (optionnel) ---
    # Une machine GPU louée à la minute (pas à la requête comme le
    # serverless), créée seulement quand runpod_pod_mode en a besoin et
    # détruite (« terminate », pas juste « stop ») dès la transcription
    # finie — pour ne jamais payer un GPU qui ne fait rien. Voir
    # RUNPOD_POD_MODES ci-dessus pour le choix entre serverless et pod.
    # Contrairement au serverless, un pod n'a pas de build automatique depuis
    # ce dépôt : il faut construire et pousser l'image vous-même (voir le
    # README) et renseigner sa référence ici.
    runpod_pod_mode: str = "off"
    runpod_pod_image: str = ""
    runpod_pod_gpu_type_id: str = "NVIDIA L4"
    runpod_pod_container_disk_gb: int = 20
    runpod_pod_port: int = 8000
    # Démarrage d'un pod : tirage de l'image + démarrage CUDA + chargement du
    # modèle Whisper. Généreux à dessein — plus lent qu'un worker serverless
    # déjà chaud, mais ça ne se produit qu'en secours.
    runpod_pod_boot_timeout_seconds: int = 600
    # Un seul thread traite la file de travaux (voir pipeline.py) : quand
    # plusieurs fichiers s'enchaînent, le pod créé pour le premier reste donc
    # disponible pour les suivants au lieu d'être détruit puis recréé à
    # chaque fois (ce qui rechargerait l'image et le modèle Whisper à
    # chaque fichier). Il n'est détruit que si personne n'en a eu besoin
    # pendant ce délai — jamais laissé vivre indéfiniment. Court par défaut :
    # après le tout dernier fichier, ce délai est du temps GPU facturé pour
    # rien puisque personne ne le redemandera ; il ne sert qu'à couvrir
    # l'écart entre deux dépôts manuels rapprochés.
    runpod_pod_idle_timeout_seconds: int = 90

    # --- Accès à Claude ---
    # "cli" (par défaut) : le binaire `claude`, authentifié sur l'abonnement
    # (`claude setup-token`) — aucun compte API facturé au jeton. "api" : la
    # clé API Anthropic ci-dessous, comme dans les versions précédentes.
    claude_backend: str = "cli"
    # Chemin du binaire `claude`. Vide : cherché dans le PATH.
    claude_cli_path: str = ""

    # --- Relecture (optionnelle) ---
    default_proofread: str = "claude"
    anthropic_api_key: str = ""
    # Modèle de base de tous les appels à Claude (relecture, sommaire,
    # vérification, extraction des affirmations, fact-check) : un seul
    # réglage, pas de modèle différent par étape.
    proofread_model: str = "claude-sonnet-5"
    # Relevé par défaut : le coût n'est plus un critère de conception ici.
    proofread_effort: str = "high"
    # Taille (en caractères) d'un bloc de texte envoyé en relecture.
    proofread_chunk_chars: int = 6000
    # Ajouter titre, intertitres et résumé au texte relu.
    structure_output: bool = True

    # --- Vérification externe (recherche web) ---
    factcheck: bool = True
    # Recherches web autorisées par affirmation à vérifier.
    factcheck_max_searches: int = 8

    # --- Lexique MJPM ---
    lexicon_enabled: bool = True
    # Amorcer Whisper avec les sigles et noms propres du lexique
    # (`initial_prompt`) — voir app/lexicon/.
    lexicon_whisper_prompt: bool = True

    # --- Coffre Obsidian (optionnel : vide = étape « fiche » inactive) ---
    obsidian_vault_path: str = ""
    obsidian_notes_folder: str = "Formation/Transcriptions"
    obsidian_entities_folder: str = "Formation/MJPM/Entités"
    obsidian_index_note: str = "Formation/MJPM/MOC Formation.md"
    obsidian_glossary_note: str = "Formation/MJPM/Glossaire MJPM.md"
    obsidian_create_entities: bool = True
    obsidian_tags: str = "formation/MJPM"
    obsidian_filename_template: str = "{date} — {titre}"

    # --- Divers ---
    keep_media: bool = True

    # --- NotebookLM / Google Drive (optionnel) ---
    # Synchronise un Google Doc maître (compilation de tous les cours relus)
    # après chaque fin de chaîne, pour que NotebookLM le retrouve à jour via
    # sa synchro automatique Drive→NotebookLM. Désactivé par défaut : marche
    # en local sans rien configurer, et la suite de tests ne fait aucun appel
    # réseau tant que ce réglage n'est pas activé explicitement.
    notebooklm_sync_enabled: bool = False
    # Dossier Drive où créer le Doc maître (python -m app.notebooklm_sync
    # --init). Vide : créé à la racine de « Mon Drive ».
    notebooklm_drive_folder_id: str = ""
    # Identifiant du Doc maître, rempli une fois par --init puis réutilisé :
    # chaque sync fait un files().update() dessus, jamais un nouveau fichier.
    notebooklm_master_doc_id: str = ""
    # Identifiants OAuth « application de bureau » (Google Cloud Console) et
    # jeton obtenu après le premier consentement — chemins relatifs à la
    # racine du projet, ou absolus. Le jeton est réutilisé et rafraîchi tout
    # seul ; --init relance le consentement s'il manque ou n'est plus valide.
    notebooklm_credentials_path: str = "data/google_credentials.json"
    notebooklm_token_path: str = "data/google_token.json"

    def public_dict(self) -> dict:
        """Version sérialisable pour l'UI : les secrets sont masqués."""
        out: dict = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in SECRET_FIELDS:
                out[f.name] = ""
                out[f"{f.name}_set"] = bool(value)
            else:
                out[f.name] = value
        return out


_settings: Settings | None = None


def _normalize_runpod_endpoint_id(value: str) -> str:
    """Nettoie l'identifiant de endpoint RunPod.

    Erreur fréquente : coller l'URL affichée dans la console RunPod
    (« https://api.runpod.ai/v2/<id>/run ») plutôt que le seul identifiant.
    Le code construit alors une URL avec l'identifiant à l'intérieur d'une
    autre URL, que RunPod renvoie en 404 sans indice sur la cause. On
    retrouve donc le segment utile, et on retire au passage guillemets et
    espaces qu'un copier-coller laisse parfois.
    """
    cleaned = value.strip().strip("'\"").strip()
    if "api.runpod.ai/v2/" in cleaned:
        cleaned = cleaned.split("api.runpod.ai/v2/", 1)[1]
    cleaned = cleaned.strip("/")
    for suffix in ("/run", "/runsync", "/health", "/status", "/cancel"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
    return cleaned.split("/", 1)[0]


# Valeurs acceptées pour un réglage booléen venant de l'environnement — les
# variables d'environnement n'ont que des chaînes, contrairement à
# data/config.json qui garde le vrai type JSON.
_TRUE_STRINGS = {"1", "true", "vrai", "oui", "yes", "on"}


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in _TRUE_STRINGS


def _from_env(settings: Settings) -> Settings:
    """Applique les variables d'environnement par-dessus les réglages."""
    env_map = {
        "runpod_api_key": "RUNPOD_API_KEY",
        "runpod_endpoint_id": "RUNPOD_ENDPOINT_ID",
        "anthropic_api_key": "ANTHROPIC_API_KEY",
        "proofread_model": "TRANSCRIPTION_PROOFREAD_MODEL",
        "default_engine": "TRANSCRIPTION_ENGINE",
        "default_model": "TRANSCRIPTION_MODEL",
        "claude_backend": "CLAUDE_BACKEND",
        "obsidian_vault_path": "TRANSCRIPTION_OBSIDIAN_VAULT",
        "notebooklm_drive_folder_id": "NOTEBOOKLM_DRIVE_FOLDER_ID",
        "notebooklm_master_doc_id": "NOTEBOOKLM_MASTER_DOC_ID",
        "notebooklm_credentials_path": "NOTEBOOKLM_CREDENTIALS_PATH",
        "notebooklm_token_path": "NOTEBOOKLM_TOKEN_PATH",
    }
    for attr, env_name in env_map.items():
        value = os.environ.get(env_name)
        if value:
            value = value.strip()
            if attr == "runpod_endpoint_id":
                value = _normalize_runpod_endpoint_id(value)
            setattr(settings, attr, value)

    # Seul réglage booléen venant de l'environnement pour l'instant : traité
    # à part plutôt que d'élargir env_map à des types mixtes.
    sync_enabled = os.environ.get("NOTEBOOKLM_SYNC_ENABLED")
    if sync_enabled:
        settings.notebooklm_sync_enabled = _parse_bool(sync_enabled)

    return settings


def load_settings(refresh: bool = False) -> Settings:
    """Charge (et met en cache) les réglages."""
    global _settings
    with _lock:
        if _settings is not None and not refresh:
            return _settings

        settings = Settings()
        if CONFIG_PATH.exists():
            try:
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raw = {}
            known = {f.name for f in fields(Settings)}
            for key, value in raw.items():
                if key in known and value is not None:
                    setattr(settings, key, value)

        _settings = _from_env(settings)
        return _settings


def save_settings(updates: dict) -> Settings:
    """Met à jour les réglages sur disque et renvoie la version à jour.

    Une valeur vide pour un champ secret veut dire « ne change pas » : l'UI
    n'affiche jamais les clés existantes, elle ne peut donc pas les renvoyer.
    Pour effacer une clé, l'UI envoie explicitement la chaîne ``"__clear__"``.
    """
    settings = load_settings()
    known = {f.name for f in fields(Settings)}

    with _lock:
        for key, value in updates.items():
            if key not in known:
                continue
            if key in SECRET_FIELDS:
                if value == "__clear__":
                    setattr(settings, key, "")
                elif value:
                    setattr(settings, key, str(value).strip())
                continue
            current = getattr(settings, key)
            if isinstance(current, bool):
                setattr(settings, key, bool(value))
            elif isinstance(current, int):
                try:
                    setattr(settings, key, int(value))
                except (TypeError, ValueError):
                    pass
            elif key == "runpod_endpoint_id" and value:
                setattr(settings, key, _normalize_runpod_endpoint_id(str(value)))
            else:
                setattr(settings, key, value)

        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(asdict(settings), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(CONFIG_PATH)
        # Restreindre les permissions : le fichier contient des clés API.
        try:
            CONFIG_PATH.chmod(0o600)
        except OSError:
            pass  # Sans effet sur Windows, sans conséquence.

    return settings


def ensure_dirs() -> None:
    """Crée l'arborescence de données si besoin."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    COURSES_DIR.mkdir(parents=True, exist_ok=True)
