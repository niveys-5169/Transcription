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

# Modèles Whisper acceptés, du plus rapide au plus précis.
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]

# Moteurs de transcription disponibles.
ENGINES = ["local", "runpod"]

# Modes de relecture.
PROOFREAD_MODES = ["claude", "basic", "none"]

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

    # --- RunPod : pod de secours (optionnel) ---
    # Le serverless RunPod peut rester bloqué en file si aucun worker ne
    # dispose de capacité (GPU rare, quota atteint...). Le pod de secours est
    # une machine GPU louée à la minute, créée seulement à ce moment-là et
    # détruite (« terminate », pas juste « stop ») dès la transcription finie
    # — pour ne payer que si le serverless a vraiment échoué à démarrer.
    # Contrairement au serverless, un pod n'a pas de build automatique depuis
    # ce dépôt : il faut construire et pousser l'image vous-même (voir le
    # README) et renseigner sa référence ici.
    runpod_pod_enabled: bool = False
    runpod_pod_image: str = ""
    runpod_pod_gpu_type_id: str = "NVIDIA L4"
    runpod_pod_container_disk_gb: int = 20
    runpod_pod_port: int = 8000
    # Démarrage d'un pod : tirage de l'image + démarrage CUDA + chargement du
    # modèle Whisper. Généreux à dessein — plus lent qu'un worker serverless
    # déjà chaud, mais ça ne se produit qu'en secours.
    runpod_pod_boot_timeout_seconds: int = 600

    # --- Relecture (optionnelle) ---
    default_proofread: str = "claude"
    anthropic_api_key: str = ""
    proofread_model: str = "claude-opus-5"
    proofread_effort: str = "medium"
    # Taille (en caractères) d'un bloc de texte envoyé en relecture.
    proofread_chunk_chars: int = 6000
    # Ajouter titre, intertitres et résumé au texte relu.
    structure_output: bool = True

    # --- Divers ---
    keep_media: bool = True

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


def _from_env(settings: Settings) -> Settings:
    """Applique les variables d'environnement par-dessus les réglages."""
    env_map = {
        "runpod_api_key": "RUNPOD_API_KEY",
        "runpod_endpoint_id": "RUNPOD_ENDPOINT_ID",
        "anthropic_api_key": "ANTHROPIC_API_KEY",
        "proofread_model": "TRANSCRIPTION_PROOFREAD_MODEL",
        "default_engine": "TRANSCRIPTION_ENGINE",
        "default_model": "TRANSCRIPTION_MODEL",
    }
    for attr, env_name in env_map.items():
        value = os.environ.get(env_name)
        if value:
            value = value.strip()
            if attr == "runpod_endpoint_id":
                value = _normalize_runpod_endpoint_id(value)
            setattr(settings, attr, value)
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
