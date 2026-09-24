"""Configuration de l'application.

Les réglages viennent de trois sources, par priorité décroissante :

1. les variables d'environnement (pratique pour un déploiement) ;
2. le fichier ``data/config.json`` (écrit par l'écran Réglages de l'app) ;
3. les valeurs par défaut ci-dessous.

Les clés API (RunPod, Anthropic, NVIDIA, Hugging Face) ne quittent jamais la
machine : elles sont stockées dans ``secrets.json`` (voir ``SECRETS_PATH``),
commun à l'exe et à ``lancer.bat`` sous Windows, avec une copie dans
``config.json`` ; ni l'un ni l'autre n'est versionné.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path


def _default_data_dir() -> Path:
    """Racine des données par défaut, hors variable d'environnement.

    En mode "frozen" (exécutable PyInstaller), ``Path.cwd()`` est le dossier
    depuis lequel l'utilisateur a double-cliqué l'exe (Bureau,
    Téléchargements...), pas un endroit où écrire des données applicatives —
    on utilise alors le dossier de données local Windows. En mode source
    (développement), on garde ``./data`` à côté du dépôt.
    """
    if getattr(sys, "frozen", False):
        return Path(_localappdata_root()) / "Transcription"
    return Path.cwd() / "data"


def _localappdata_root() -> str:
    return os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")


# Racine des données : médias extraits, base SQLite, config.
DATA_DIR = Path(os.environ.get("TRANSCRIPTION_DATA_DIR", _default_data_dir())).resolve()
MEDIA_DIR = DATA_DIR / "media"
CONFIG_PATH = DATA_DIR / "config.json"


def _default_secrets_path() -> Path:
    """Fichier des clés API, commun à l'exe et au lancement depuis le code.

    L'exe range ses données dans ``%LOCALAPPDATA%\\Transcription`` et
    ``lancer.bat`` dans ``data\\`` : deux ``config.json`` distincts. Les clés,
    elles, doivent être saisies une seule fois pour la machine — d'où un
    fichier à part, toujours au même endroit sous Windows, quel que soit le
    dossier de données. Ailleurs (pas d'exe), il reste dans ce dossier.
    """
    if sys.platform == "win32":
        return Path(_localappdata_root()) / "Transcription" / "secrets.json"
    return DATA_DIR / "secrets.json"


SECRETS_PATH = Path(os.environ.get("TRANSCRIPTION_SECRETS_PATH") or _default_secrets_path()).resolve()
DB_PATH = DATA_DIR / "transcription.db"
# Un .md finalisé par cours (relu, vérifié ou publié) : la matière première du
# Doc maître NotebookLM (voir app/notebooklm_sync.py). Indépendant du coffre
# Obsidian, qui reste optionnel — ce dossier existe dès qu'un cours est relu.
COURSES_DIR = DATA_DIR / "cours"

# Modèles Whisper acceptés, du plus rapide au plus précis.
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]

# Moteurs de transcription disponibles.
ENGINES = ["local", "runpod"]


# Modes de relecture. NVIDIA NIM reste un repli opt-in de Claude ; il n'est
# pas proposé comme moteur principal afin de ne pas basculer silencieusement
# sur un service facturé.
# « nim » permet une relance explicite sur NVIDIA, indépendamment du repli
# automatique configuré pour Claude.
PROOFREAD_MODES = ["claude", "nim", "basic", "none"]

# Comment l'application appelle Claude : le CLI (abonnement) ou l'API (clé).
CLAUDE_BACKENDS = ["cli", "api"]

# Champs considérés comme secrets : jamais renvoyés en clair par l'API.
SECRET_FIELDS = {"runpod_api_key", "anthropic_api_key", "nim_api_key", "hf_token"}


def obsidian_domain_defaults(domain_label: str) -> dict[str, str]:
    """Chemins Obsidian proposés pour un domaine, sans imposer l'organisation."""
    label = (domain_label or "MJPM").strip() or "MJPM"
    return {
        "obsidian_entities_folder": f"Formation/{label}/Entités",
        "obsidian_index_note": f"Formation/{label}/MOC Formation.md",
        "obsidian_glossary_note": f"Formation/{label}/Glossaire {label}.md",
        "obsidian_tags": f"formation/{label}",
    }

_lock = threading.Lock()


@dataclass
class Settings:
    """Réglages persistés de l'application."""

    # --- Moteur de transcription ---
    default_engine: str = "local"
    default_model: str = "large-v3"
    language: str = "fr"  # "auto" laisse Whisper détecter la langue.

    # --- RunPod (GPU cloud, optionnel) ---
    runpod_api_key: str = ""
    # Jeton lecture seule requis par pyannote, jamais renvoyé au navigateur.
    hf_token: str = ""
    diarization_enabled: bool = True
    # Taille des tronçons envoyés à RunPod. L'API /run plafonne la charge utile
    # à ~10 Mo ; un WAV 16 kHz mono 16 bits encodé en base64 pèse ~42 ko/s, donc
    # un cours d'une heure ne peut pas partir en un seul appel.
    # 180 s ≈ 7,3 Mo encodés : une vraie marge sous la limite. 240 s (l'ancien
    # défaut) donnait ≈ 9,8 Mo — trop près du plafond une fois l'enveloppe
    # JSON ajoutée, avec un risque d'échec intermittent selon les tronçons.
    runpod_chunk_seconds: int = 180

    # --- RunPod : pod (optionnel) ---
    # Une machine GPU louée à la minute (pas à la requête comme le
    # serverless), créée seulement quand runpod_pod_mode en a besoin et
    # détruite (« terminate », pas juste « stop ») dès la transcription
    # finie — pour ne jamais payer un GPU qui ne fait rien. Voir
    # RUNPOD_POD_MODES ci-dessus pour le choix entre serverless et pod.
    # Contrairement au serverless, un pod n'a pas de build automatique depuis
    # ce dépôt : il faut construire et pousser l'image vous-même (voir le
    # README) et renseigner sa référence ici.
    runpod_pod_image: str = ""
    runpod_pod_gpu_type_id: str = "NVIDIA L4"
    # Volume reseau RunPod (optionnel) : persiste le cache Hugging Face
    # (modele Whisper) entre deux pods, pour que seul le tout premier
    # telechargement le paie — sans l'embarquer dans l'image Docker
    # elle-meme, ce que RunPod doit retelecharger en entier a chaque pod
    # cree sur un hote qui ne l'a pas deja en cache local. Contrepartie : un
    # volume reseau est epingle a un datacenter precis, ce qui restreint la
    # disponibilite GPU du pod de secours a ce seul datacenter (voir le
    # README, section « Pod : volume reseau »).
    runpod_pod_network_volume_id: str = ""
    runpod_pod_container_disk_gb: int = 20
    runpod_pod_port: int = 8000
    # Démarrage d'un pod : tirage de l'image + démarrage CUDA + chargement du
    # modèle Whisper. Généreux à dessein — plus lent qu'un worker serverless
    # déjà chaud, mais ça ne se produit qu'en secours.
    runpod_pod_boot_timeout_seconds: int = 600
    # Durée maximale d'une transcription sur le pod, du dépôt du fichier à
    # la réponse. Le pod calcule dans un thread et l'application sonde
    # l'état du travail (voir pod_server.py, /jobs) : ce délai borne cette
    # attente, il n'est pas un délai de lecture HTTP. Généreux à dessein :
    # plusieurs heures d'audio en large-v3 avec diarisation prennent
    # longtemps, et abandonner en cours de route revient à payer le GPU
    # pour rien (voir le travail 4ec65ffcebb5 : deux gros fichiers
    # abandonnés après 120 s alors que le pod travaillait encore).
    runpod_pod_job_timeout_seconds: int = 4 * 3600
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
    # « medium » : une relecture fidèle corrige et ponctue, elle ne raisonne
    # pas ; l'effort élevé allonge chaque bloc sans gain mesurable, alors que
    # les garde-fous (anti-résumé, règles, vérification ciblée) restent actifs.
    proofread_effort: str = "medium"
    # Couple rapide, réservé aux passes mécaniques (repérage des affirmations
    # à vérifier, comparaison brut/relu) : jamais pour la relecture elle-même
    # ni pour les verdicts de fact-check, qui exigent le jugement de
    # `proofread_model`/`proofread_effort` — notamment sur les références
    # juridiques, où une erreur coûte plus cher que le temps économisé.
    proofread_model_fast: str = "claude-haiku-4-5-20251001"
    proofread_effort_fast: str = "low"
    # Taille (en caractères) d'un bloc de texte envoyé en relecture.
    proofread_chunk_chars: int = 6000
    # Blocs relus (et vérifiés) de front, Claude comme NIM : chaque bloc est
    # un appel indépendant, le temps est surtout de l'attente réseau.
    proofread_workers: int = 4
    # Ajouter titre, intertitres et résumé au texte relu.
    structure_output: bool = True

    # --- Repli NVIDIA NIM (optionnel) ---
    # NIM expose une API compatible OpenAI. Cette clé n'est jamais envoyée au
    # navigateur : seul le serveur l'utilise si Claude est indisponible ou en
    # erreur et que le repli est explicitement activé.
    nim_api_key: str = ""
    nim_fallback_enabled: bool = False
    nim_base_url: str = "https://integrate.api.nvidia.com/v1/chat/completions"
    nim_model: str = "nvidia/llama-3.3-nemotron-super-49b-v1"
    # Deux alternatives, utilisées dans cet ordre si le modèle principal est
    # saturé, indisponible ou rend une réponse inutilisable.
    nim_fallback_model_1: str = ""
    nim_fallback_model_2: str = ""
    # Modèle des passes légères NIM (sommaire, vérification ciblée) ; la
    # relecture reste sur ``nim_model``. En cas d'échec, la chaîne principale
    # prend le relais. Vide : ``nim_model`` partout.
    nim_model_fast: str = "meta/llama-3.1-8b-instruct"
    nim_timeout: int = 300

    # --- Vérification externe (recherche web) ---
    factcheck: bool = True
    # Recherches web autorisées par affirmation à vérifier.
    factcheck_max_searches: int = 8
    # Catégories d'affirmations qui déclenchent une recherche web, séparées
    # par des virgules — les autres (nom_propre, rapport, statistique...)
    # sont repérées mais jamais vérifiées : c'est le juridique qui expose à
    # un risque en cas d'erreur, pas une statistique approximative.
    factcheck_priority_types: str = "reference_juridique,date,organisme"
    # Verdicts menés de front : ils sont indépendants les uns des autres,
    # rien n'empêche de paralléliser plutôt que d'attendre en séquence.
    factcheck_workers: int = 4
    # Durée de validité d'un verdict mémorisé, en jours — un texte de loi
    # peut être modifié, la mémorisation ne doit pas être éternelle.
    factcheck_cache_days: int = 90

    # --- Domaine et lexique ---
    domain_label: str = "MJPM"
    lexicon_enabled: bool = True
    # Amorcer Whisper avec les sigles et noms propres du lexique
    # (`initial_prompt`) — voir app/lexicon/.
    lexicon_whisper_prompt: bool = True

    # --- Coffre Obsidian (optionnel : vide = étape « fiche » inactive) ---
    obsidian_vault_path: str = ""
    obsidian_notes_folder: str = "Formation/Transcriptions"
    obsidian_verbatim_folder: str = "Formation/Transcriptions/Verbatim"
    obsidian_write_verbatim: bool = True
    obsidian_revision_folder: str = "Formation/Transcriptions/Révisions"
    obsidian_concepts_folder: str = "Formation/Concepts"
    obsidian_themes_folder: str = "Formation/Synthèses"
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
    # La compilation globale reste disponible pour les usages existants, mais
    # chaque cours possède désormais son propre Doc Google.
    notebooklm_master_doc_enabled: bool = True
    # Identifiants OAuth « application de bureau » (Google Cloud Console) et
    # jeton obtenu après le premier consentement — chemins relatifs à la
    # racine du projet, ou absolus. Le jeton est réutilisé et rafraîchi tout
    # seul ; --init relance le consentement s'il manque ou n'est plus valide.
    notebooklm_credentials_path: str = str(DATA_DIR / "google_credentials.json")
    notebooklm_token_path: str = str(DATA_DIR / "google_token.json")

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
        "anthropic_api_key": "ANTHROPIC_API_KEY",
        "nim_api_key": "NIM_API_KEY",
        "nim_base_url": "NIM_BASE_URL",
        "nim_model": "NIM_MODEL",
        "nim_fallback_model_1": "NIM_FALLBACK_MODEL_1",
        "nim_fallback_model_2": "NIM_FALLBACK_MODEL_2",
        "nim_model_fast": "NIM_MODEL_FAST",
        "nim_timeout": "NIM_TIMEOUT",
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

        # Les clés partagées priment : c'est la dernière saisie, où qu'elle
        # ait été faite (exe ou lancer.bat).
        for key, value in _load_shared_secrets().items():
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
        requested_domain = str(updates.get("domain_label", settings.domain_label)).strip() or "MJPM"
        if requested_domain != settings.domain_label:
            old_defaults = obsidian_domain_defaults(settings.domain_label)
            new_defaults = obsidian_domain_defaults(requested_domain)
            for key, old_value in old_defaults.items():
                if updates.get(key, getattr(settings, key)) == old_value:
                    updates = {**updates, key: new_defaults[key]}
        cleared: set[str] = set()
        for key, value in updates.items():
            if key not in known:
                continue
            if key in SECRET_FIELDS:
                if value == "__clear__":
                    setattr(settings, key, "")
                    cleared.add(key)
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
            else:
                setattr(settings, key, value)

        # config.json garde aussi une copie des clés, pour qu'une ancienne
        # version de l'exe les retrouve ; le fichier partagé fait foi.
        _write_private_json(CONFIG_PATH, asdict(settings))
        # Une clé vide n'est écrite que si elle a été effacée : un simple
        # enregistrement sans cette clé ne doit pas bloquer sa reprise depuis
        # l'autre installation (voir _load_shared_secrets).
        shared = {key: value for key, value in _read_json(SECRETS_PATH).items() if key in SECRET_FIELDS}
        for key in SECRET_FIELDS:
            if getattr(settings, key) or key in cleared:
                shared[key] = getattr(settings, key)
        _write_private_json(SECRETS_PATH, dict(sorted(shared.items())))

    return settings


def _write_private_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    # Restreindre les permissions : le fichier contient des clés API.
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Sans effet sur Windows, sans conséquence.


def _read_json(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _load_shared_secrets() -> dict[str, str]:
    """Clés du fichier partagé, complétées une fois depuis les anciens emplacements.

    Une clé absente du fichier partagé est reprise du ``config.json`` du
    dossier de données courant, sinon de celui de l'exe — là où elle avait
    été saisie avant ce fichier commun. Une clé présente mais vide (effacée
    volontairement) n'est jamais réimportée.
    """
    shared = _read_json(SECRETS_PATH)
    secrets = {key: str(value) for key, value in shared.items()
               if key in SECRET_FIELDS and value is not None}
    legacy_sources = [CONFIG_PATH]
    if sys.platform == "win32":
        legacy_sources.append(Path(_localappdata_root()) / "Transcription" / "config.json")
    imported = False
    for source in legacy_sources:
        raw = _read_json(source)
        for key in SECRET_FIELDS - secrets.keys():
            value = str(raw.get(key) or "").strip()
            if value:
                secrets[key] = value
                imported = True
    if imported:
        try:
            _write_private_json(SECRETS_PATH, dict(sorted(secrets.items())))
        except OSError:
            pass  # Relu depuis config.json au prochain démarrage.
    return secrets


def ensure_dirs() -> None:
    """Crée l'arborescence de données si besoin."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    COURSES_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)
