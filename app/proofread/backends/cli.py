"""Back-end CLI : appelle le binaire ``claude``, sur l'abonnement.

C'est le back-end par défaut de l'application (``claude_backend: "cli"``).
Contrairement au back-end API (``api.py``), il ne débite pas de compte API
facturé au jeton : il s'authentifie comme n'importe quelle session Claude
Code, via ``claude setup-token`` ou ``claude auth login`` — l'abonnement
suffit.

Chaque appel est un processus jetable, isolé de la configuration personnelle
de la machine (pas de CLAUDE.md, pas de skills, pas de serveurs MCP, aucune
persistance de session) et sans la moindre possibilité d'attendre une
confirmation : un travail de transcription tourne sans personne devant
l'écran. Voir les commentaires sur chaque option de ``_build_command``.

Le prompt système passe en argument (``--system-prompt``), le texte à traiter
par l'entrée standard — jamais en argument : un bloc de plusieurs milliers de
caractères dépasserait vite la taille maximale d'une ligne de commande.

Format d'échange : ``--output-format stream-json``, en JSON Lines, plutôt que
le ``json`` simple. Deux raisons : ça permet de vérifier ponctuellement
``should_cancel()`` au fil des lignes plutôt que d'attendre en bloc (même
principe que ``media.extract_wav``), et surtout — pour le fact-check — c'est
la seule façon de constater qu'une recherche web a *effectivement* eu lieu,
plutôt que de croire le modèle sur parole (voir ``factcheck.py``).

Le détail exact de l'enveloppe JSON n'est pas garanti par une documentation
figée ici : l'analyse ci-dessous est délibérément tolérante (elle ignore tout
ce qu'elle ne reconnaît pas) et se rabat sur un message d'erreur clair plutôt
que de planter si le format observé diffère.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import threading
from pathlib import Path

from ...config import Settings, load_settings
from ...process import hidden_console_flags
from ..base import ProofreadError
from ..structure import parse_json_array, parse_json_object
from .base import BackendResult

logger = logging.getLogger(__name__)

# Généreux à dessein : effort élevé et recherche web font des tours longs.
# Nom conservé pour compatibilité (tests, appelants existants) : c'est le
# délai par défaut, utilisé quand ni ``web_search`` ni ``fast`` ne
# s'appliquent (relecture : les blocs sont longs, sans recherche web).
CALL_TIMEOUT_SECONDS = 900
# Un verdict avec recherche web peut enchaîner plusieurs tours de
# recherche : même délai généreux que le cas par défaut.
WEB_CALL_TIMEOUT_SECONDS = 900
# Une extraction mécanique (repérage des affirmations, comparaison
# brut/relu) est un texte court, sans recherche web : pas besoin d'un
# délai pensé pour un verdict juridique qui explore le web.
FAST_CALL_TIMEOUT_SECONDS = 180

_URL_RE = re.compile(r"https?://[^\s\)\]\"'>]+")

# Motifs qui trahissent une limite d'usage de l'abonnement atteinte, à
# distinguer de toute autre erreur : voir QuotaExhausted plus bas.
_QUOTA_PATTERNS = (
    "usage limit",
    "rate limit",
    "quota",
    "limite d'usage",
    "limite de débit",
)


class QuotaExhausted(ProofreadError):
    """La limite d'usage de l'abonnement est atteinte pour l'instant.

    Distinguée des autres échecs pour que les appelants — le fact-check en
    premier lieu — ne confondent jamais « je n'ai pas pu vérifier » avec
    « c'est vérifié ». Voir ``factcheck.py:apply_verdicts``.
    """


class CliBackend:
    """Appelle ``claude -p`` en processus jetable, un par requête."""

    name = "cli"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()

    # ------------------------------------------------------------- disponibilité

    def _executable(self) -> str | None:
        configured = (self.settings.claude_cli_path or "").strip()
        if configured:
            if shutil.which(configured):
                return configured
            path = Path(configured)
            return str(path) if path.exists() else None
        return shutil.which("claude")

    def is_available(self) -> tuple[bool, str]:
        exe = self._executable()
        if not exe:
            return False, (
                "Le CLI « claude » est introuvable. Installez Claude Code, ou "
                "renseignez son chemin dans les réglages."
            )
        try:
            result = subprocess.run(
                [exe, "auth", "status", "--json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                creationflags=hidden_console_flags(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"Le CLI « claude » n'a pas répondu ({exc})."

        try:
            status = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            status = {}

        if not status.get("loggedIn"):
            return False, (
                "Le CLI « claude » n'est pas connecté : lancez "
                "« claude setup-token » (abonnement) ou « claude auth login »."
            )
        if status.get("authMethod") == "api_key":
            # Le but même de ce back-end est d'utiliser l'abonnement plutôt
            # qu'un compte API : un CLI configuré avec une clé API le
            # contredirait silencieusement.
            return False, (
                "Le CLI « claude » est configuré avec une clé API, pas avec "
                "l'abonnement. Utilisez plutôt le back-end « api » dans les "
                "réglages, ou reconnectez le CLI avec « claude setup-token »."
            )
        return True, f"Abonnement connecté ({status.get('authMethod') or 'oauth'})."

    # ------------------------------------------------------------------ appel

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        schema: dict | None = None,
        web_search: bool = False,
        fast: bool = False,
    ) -> BackendResult:
        available, detail = self.is_available()
        if not available:
            raise ProofreadError(detail)
        exe = self._executable()
        assert exe is not None  # garanti par is_available() ci-dessus

        cmd = self._build_command(exe, system=system, schema=schema, web_search=web_search, fast=fast)
        if web_search:
            timeout = WEB_CALL_TIMEOUT_SECONDS
        elif fast:
            timeout = FAST_CALL_TIMEOUT_SECONDS
        else:
            timeout = CALL_TIMEOUT_SECONDS
        return self._run(cmd, user, schema, timeout=timeout, web_search=web_search)

    def _build_command(
        self, exe: str, *, system: str, schema: dict | None, web_search: bool, fast: bool = False
    ) -> list[str]:
        model = self.settings.proofread_model_fast if fast else self.settings.proofread_model
        effort = self.settings.proofread_effort_fast if fast else self.settings.proofread_effort
        cmd = [
            exe,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            model,
            "--effort",
            effort,
            "--system-prompt",
            system,
            # "" désactive tous les outils (documenté par --help) ; un seul
            # nommé, ici, jamais Bash ni l'accès aux fichiers — l'app donne à
            # lire à Claude une transcription dont elle ne maîtrise pas le
            # contenu. ``--tools`` ne fait que restreindre l'ensemble
            # *disponible* : sans ``--allowedTools`` en plus, WebSearch reste
            # soumis à une confirmation qu'aucune session sans écran ne peut
            # donner, et se retrouve refusé d'office par
            # ``--permission-prompts none`` ci-dessous.
            "--tools",
            "WebSearch" if web_search else "",
        ]
        if web_search:
            cmd += ["--allowedTools", "WebSearch"]
        cmd += [
            # Rien ne doit pouvoir demander confirmation : le thread unique
            # de la file resterait bloqué indéfiniment sans personne pour
            # répondre. La liste ci-dessus pré-approuve WebSearch ; tout le
            # reste qui tenterait de demander une confirmation est refusé.
            "--permission-prompts",
            "none",
            "--no-session-persistence",
            # Étanche à la configuration de la machine : ni CLAUDE.md, ni
            # skills, ni hooks, ni serveurs MCP personnels n'ont leur place
            # dans un travail de transcription.
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--setting-sources",
            "",
        ]
        if schema is not None:
            cmd += ["--json-schema", json.dumps(_cli_schema(schema), ensure_ascii=False)]
        return cmd

    # ------------------------------------------------------------- exécution

    def _run(
        self,
        cmd: list[str],
        user_prompt: str,
        schema: dict | None,
        *,
        timeout: int = CALL_TIMEOUT_SECONDS,
        web_search: bool = False,
    ) -> BackendResult:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=hidden_console_flags(),
        )
        assert process.stdin is not None and process.stdout is not None

        timed_out = threading.Event()
        watchdog = threading.Timer(timeout, lambda: (timed_out.set(), process.kill()))
        watchdog.daemon = True
        watchdog.start()

        # Plafond de recherches (chantier 7) : sur le CLI, rien d'autre ne
        # borne le nombre de recherches web qu'une affirmation ambiguë peut
        # déclencher — contrairement au back-end API, où
        # ``factcheck_max_searches`` est un ``max_uses`` transmis au modèle
        # (voir ``api.py``). Même mécanique que le chien de garde de délai
        # ci-dessus (``threading.Event`` + ``process.kill()``) : chaque
        # recherche web constatée dans le flux incrémente le compteur, et le
        # dépassement du plafond tue le processus sur-le-champ.
        search_cap = self.settings.factcheck_max_searches
        capped = threading.Event()

        try:
            process.stdin.write(user_prompt)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass  # le processus a peut-être déjà quitté ; on lit stderr plus bas

        result_event: dict | None = None
        last_assistant_text = ""
        web_searches = 0
        sources: list[str] = []
        websearch_tool_ids: set[str] = set()

        try:
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type")
                if event_type == "assistant":
                    text, ids = _scan_assistant(event)
                    if text:
                        last_assistant_text = text
                    for tool_id in ids:
                        websearch_tool_ids.add(tool_id)
                        web_searches += 1
                    if web_search and search_cap and web_searches > search_cap:
                        # Le plafond est franchi : tuer le processus tout de
                        # suite plutôt que d'attendre une recherche de plus,
                        # potentiellement longue.
                        capped.set()
                        process.kill()
                elif event_type == "user":
                    sources.extend(_scan_tool_results(event, websearch_tool_ids))
                elif event_type == "result":
                    result_event = event
        finally:
            watchdog.cancel()
            process.stdout.close()
            stderr = process.stderr.read() if process.stderr else ""
            if process.stderr:
                process.stderr.close()
            process.wait()

        if capped.is_set():
            # Un arrêt sur plafond n'est pas une erreur, c'est une borne :
            # le texte déjà reçu est exploité normalement. S'il n'y a rien
            # d'exploitable, le comportement d'erreur habituel s'applique
            # plus bas (result_event est None et last_assistant_text vide).
            logger.info(
                "Plafond de recherches web atteint (%d), arrêt du CLI « claude ».",
                search_cap,
            )

        if timed_out.is_set() and not capped.is_set():
            raise ProofreadError(
                "Le CLI « claude » n'a pas répondu dans le délai imparti."
            )

        if result_event is None:
            # Rien d'exploitable n'est jamais arrivé sur stdout — repli sur le
            # dernier texte assistant vu, sinon échec explicite.
            if last_assistant_text:
                text = last_assistant_text
            else:
                raise ProofreadError(
                    "Le CLI « claude » n'a produit aucun résultat."
                    + (f"\n{stderr.strip()[:800]}" if stderr.strip() else "")
                )
        else:
            if result_event.get("is_error"):
                message = str(result_event.get("result") or result_event.get("subtype") or "")
                haystack = f"{message}\n{stderr}".lower()
                if any(pattern in haystack for pattern in _QUOTA_PATTERNS):
                    raise QuotaExhausted(
                        "Limite d'usage de l'abonnement Claude atteinte. "
                        "Réessayez plus tard, ou basculez temporairement sur "
                        "le back-end « api »."
                    )
                raise ProofreadError(
                    f"Le CLI « claude » a échoué : {message.strip() or 'erreur inconnue'}"
                )
            text = str(result_event.get("result") or "")

        parsed = _parse_schema(text, schema) if schema is not None else None

        return BackendResult(
            text=text, web_searches=web_searches, sources=sources, parsed=parsed
        )


def _scan_assistant(event: dict) -> tuple[str, list[str]]:
    """Texte et identifiants des appels à WebSearch dans un message assistant."""
    message = event.get("message") or {}
    content = message.get("content") or []
    text_parts: list[str] = []
    tool_ids: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and block.get("text"):
            text_parts.append(block["text"])
        elif block.get("type") == "tool_use" and block.get("name") == "WebSearch":
            tool_id = block.get("id")
            if tool_id:
                tool_ids.append(tool_id)
    return "".join(text_parts), tool_ids


def _scan_tool_results(event: dict, websearch_ids: set[str]) -> list[str]:
    """URLs trouvées dans les résultats des appels à WebSearch de ``websearch_ids``."""
    message = event.get("message") or {}
    content = message.get("content") or []
    urls: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        if block.get("tool_use_id") not in websearch_ids:
            continue
        payload = block.get("content")
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        urls.extend(_URL_RE.findall(text))
    # Sans doublons, en gardant l'ordre d'apparition.
    return list(dict.fromkeys(urls))


def _cli_schema(schema: dict) -> dict:
    """Schéma effectivement transmis à ``--json-schema``.

    L'API Anthropic exige qu'un outil personnalisé — ce que devient
    ``--json-schema`` sous le capot du CLI — déclare un ``input_schema`` de
    type « object » ; un schéma de type « array » nu fait échouer l'appel
    (« tools.0.custom.input_schema.type: Input should be 'object' »). On
    l'enveloppe donc dans un objet ; ``_parse_schema`` défait l'enveloppe
    côté lecture.
    """
    if schema.get("type") == "array":
        return {"type": "object", "properties": {"items": schema}, "required": ["items"]}
    return schema


def _parse_schema(text: str, schema: dict) -> dict | list | None:
    is_array = schema.get("type") == "array"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    if parsed is not None:
        if is_array:
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and isinstance(parsed.get("items"), list):
                return parsed["items"]
        elif isinstance(parsed, dict):
            return parsed

    if is_array:
        return parse_json_array(text) or None
    return parse_json_object(text) or None
