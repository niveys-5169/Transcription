"""Profils de capacités des modèles NVIDIA NIM.

Tous les modèles hébergés derrière une API NIM (compatible OpenAI) ne
partagent pas la même convention pour désactiver leur raisonnement interne.
``nvidia/llama-3.3-nemotron-super-49b-v1`` exige un premier message système
littéral ``detailed thinking off`` ; d'autres familles (Qwen3, DeepSeek-R1,
GLM-4.5, ...) utilisent la balise ``/no_think`` dans le message utilisateur.
Un modèle instruct « simple », sans mode de raisonnement, ne doit recevoir
aucun préfixe inventé — cela ne ferait qu'ajouter du bruit inutile.

Ce module centralise cette connaissance dans un seul endroit : un profil par
famille de modèles, choisi par un motif appliqué à l'identifiant du modèle.
Le même mécanisme s'applique au modèle principal et à ses secours : un
fallback n'est jamais envoyé « nu ».
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Emplacement de la consigne de raisonnement dans les messages envoyés.
PLACEMENT_LEADING_SYSTEM = "leading_system"  # un message système à part, en tête
PLACEMENT_USER_SUFFIX = "user_suffix"  # ajoutée à la fin du message utilisateur
PLACEMENT_NONE = "none"


@dataclass(frozen=True)
class NimModelProfile:
    """Comment parler à une famille de modèles NIM donnée."""

    pattern: str
    reasoning_mode: str  # "detailed_thinking_off" | "no_think" | "none"
    system_prefix: str = ""
    prefix_placement: str = PLACEMENT_LEADING_SYSTEM
    temperature: float = 0.0
    supports_structured_output: bool = False


# Profil neutre : aucun préfixe inventé, température basse par défaut pour
# une tâche de relecture (pas de créativité recherchée). C'est le choix pour
# tout modèle qui ne correspond à aucun motif connu ci-dessous — mieux vaut
# ne rien ajouter que de désactiver un mode qui n'existe pas.
DEFAULT_PROFILE = NimModelProfile(
    pattern="",
    reasoning_mode="none",
    system_prefix="",
    prefix_placement=PLACEMENT_NONE,
    temperature=0.0,
)

# Ordre de test important : le motif le plus spécifique d'abord.
_PROFILES: tuple[NimModelProfile, ...] = (
    # Nemotron (famille "super"/"nano"/"ultra") : documentation NVIDIA —
    # premier message système littéral, température 0 recommandée.
    NimModelProfile(
        pattern=r"nemotron",
        reasoning_mode="detailed_thinking_off",
        system_prefix="detailed thinking off",
        prefix_placement=PLACEMENT_LEADING_SYSTEM,
        temperature=0.0,
    ),
    # Qwen3, DeepSeek-R1 distillés, GLM-4.5 : convention "/no_think" en fin
    # de message utilisateur. Best effort — certaines déclinaisons NIM
    # documentent des variantes légèrement différentes ; à ajuster au cas
    # par cas si un modèle précis s'avère non conforme.
    NimModelProfile(
        pattern=r"qwen3|deepseek-r1|glm-4",
        reasoning_mode="no_think",
        system_prefix="/no_think",
        prefix_placement=PLACEMENT_USER_SUFFIX,
        temperature=0.0,
    ),
)


def profile_for(model: str) -> NimModelProfile:
    """Profil du modèle NIM ``model``, ou le profil neutre si inconnu."""
    name = (model or "").strip()
    for profile in _PROFILES:
        if re.search(profile.pattern, name, re.IGNORECASE):
            return profile
    return DEFAULT_PROFILE


def build_messages(profile: NimModelProfile, system: str, user: str) -> list[dict]:
    """Assemble les messages à envoyer, selon la convention du profil.

    Le préfixe de raisonnement (quand il existe) reste toujours distinct des
    consignes métier (``system``) : jamais fusionné dans la même chaîne, pour
    rester fidèle à ce que documente chaque fournisseur de modèle.
    """
    if profile.prefix_placement == PLACEMENT_LEADING_SYSTEM and profile.system_prefix:
        return [
            {"role": "system", "content": profile.system_prefix},
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    if profile.prefix_placement == PLACEMENT_USER_SUFFIX and profile.system_prefix:
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": f"{user}\n\n{profile.system_prefix}"},
        ]
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
