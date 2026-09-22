"""Lexique du domaine MJPM : mesures, acteurs, actes, prestations, textes.

Livré non vérifié (``verifie: false`` sur chaque entrée) : c'est un point de
départ écrit à la main, pas une source d'autorité. Il ne le devient qu'après
avoir été confronté à une recherche web — voir ``verify_lexicon()``, qui
utilise la même machinerie que le fact-check des transcriptions
(``app.proofread.factcheck``). Tant qu'une entrée n'est pas vérifiée, elle
sert à amorcer Whisper (``whisper_prompt``, le pire risque y est un mot de
vocabulaire inutile) mais n'est jamais opposée comme référence au
fact-checking (``lookup``, appelé uniquement sur les entrées vérifiées) ni
recopiée dans une fiche d'entité du coffre — voir ``app/obsidian/entities.py``.
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

LEXICON_PATH = (
    Path(getattr(sys, "_MEIPASS")) / "app" / "lexicon" / "mjpm.json"
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None)
    else Path(__file__).parent / "mjpm.json"
)

# Amorce Whisper (``initial_prompt``) : la fenêtre de prompt du modèle fait
# environ 224 jetons ; on reste large en dessous pour laisser de la place à
# un contexte éventuel côté moteur.
WHISPER_PROMPT_MAX_CHARS = 800


@dataclass
class Term:
    """Une entrée du lexique."""

    terme: str
    categorie: str = "autre"
    sigles: list[str] = field(default_factory=list)
    variantes: list[str] = field(default_factory=list)
    definition: str = ""
    reference: str = ""
    wikilink: str = ""
    sources: list[dict] = field(default_factory=list)
    verifie: bool = False
    verifie_le: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def noms(self) -> list[str]:
        """Toutes les graphies connues du terme : le terme, ses sigles, ses variantes."""
        return [self.terme, *self.sigles, *self.variantes]


def _user_lexicon_path() -> Path:
    from .. import config

    return config.DATA_DIR / "lexique_utilisateur.json"


def _load_file(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Lexique illisible, ignoré : %s", path)
        return []
    return raw if isinstance(raw, list) else []


def load_lexicon(refresh: bool = False) -> list[Term]:
    """Charge le lexique livré, complété par les ajouts de l'utilisateur.

    Un terme du lexique utilisateur qui reprend le même ``terme`` qu'une
    entrée livrée la remplace ; sinon il s'ajoute.
    """
    global _cache
    if _cache is not None and not refresh:
        return _cache

    entries: dict[str, Term] = {}
    for raw in _load_file(LEXICON_PATH):
        term = _from_dict(raw)
        if term:
            entries[term.terme] = term
    for raw in _load_file(_user_lexicon_path()):
        term = _from_dict(raw)
        if term:
            entries[term.terme] = term

    _cache = list(entries.values())
    return _cache


_cache: list[Term] | None = None


def save_user_term(term: Term) -> None:
    """Ajoute ou remplace une entrée dans le lexique de l'utilisateur.

    Écrit dans ``data/lexique_utilisateur.json`` (hors dépôt), jamais dans le
    lexique livré (``mjpm.json``) : une entrée acceptée depuis un fact-check
    est une proposition retenue par l'utilisateur, pas un ajout au lexique de
    référence de l'application.
    """
    path = _user_lexicon_path()
    entries = _load_file(path)
    entries = [e for e in entries if str(e.get("terme") or "") != term.terme]
    entries.append(term.to_dict())

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)

    load_lexicon(refresh=True)


def update_term(terme: str, **changements) -> Term | None:
    """Modifie seulement les champs demandés d'une entrée existante.

    La copie locale ainsi créée conserve tous les autres champs de l'entrée
    livrée (sigles, variantes, référence et sources notamment).
    """
    current = next((item for item in load_lexicon() if item.terme == terme), None)
    if current is None:
        return None
    allowed = set(Term.__dataclass_fields__) - {"terme"}
    values = current.to_dict()
    for key, value in changements.items():
        if key in allowed:
            values[key] = value
    updated = _from_dict(values)
    if updated is None:
        return None
    save_user_term(updated)
    return updated


def delete_user_term(terme: str) -> bool:
    """Supprime un ajout local sans toucher au lexique livré.

    Si le terme remplaçait une entrée livrée, celle-ci redevient simplement
    visible après suppression : le jeu de référence reste donc immuable.
    """
    terme = str(terme or "").strip()
    if not terme:
        return False
    path = _user_lexicon_path()
    entries = _load_file(path)
    kept = [entry for entry in entries if str(entry.get("terme") or "") != terme]
    if len(kept) == len(entries):
        return False
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(kept, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    load_lexicon(refresh=True)
    return True


def user_term_names() -> set[str]:
    """Noms modifiables depuis l'interface (fichier utilisateur seulement)."""
    return {str(entry.get("terme") or "") for entry in _load_file(_user_lexicon_path())}


def _from_dict(raw: dict) -> Term | None:
    terme = str(raw.get("terme") or "").strip()
    if not terme:
        return None
    return Term(
        terme=terme,
        categorie=str(raw.get("categorie") or "autre"),
        sigles=[str(s) for s in raw.get("sigles") or []],
        variantes=[str(v) for v in raw.get("variantes") or []],
        definition=str(raw.get("definition") or ""),
        reference=str(raw.get("reference") or ""),
        wikilink=str(raw.get("wikilink") or terme),
        sources=list(raw.get("sources") or []),
        verifie=bool(raw.get("verifie", False)),
        verifie_le=raw.get("verifie_le"),
    )


def lookup(citation: str, *, verified_only: bool = True) -> Term | None:
    """Un terme dont une graphie connue apparaît dans ``citation``.

    ``verified_only`` (par défaut) exclut les entrées non vérifiées : ce
    n'est délibérément pas la fonction à appeler pour amorcer Whisper (voir
    ``whisper_prompt``, qui n'a pas ce garde-fou car le risque y est nul).
    """
    citation_lower = citation.lower()
    best: Term | None = None
    for term in load_lexicon():
        if verified_only and not term.verifie:
            continue
        for nom in term.noms():
            if nom and nom.lower() in citation_lower:
                if best is None or len(nom) > len(best.terme):
                    best = term
                break
    return best


def whisper_prompt() -> str:
    """Amorce de vocabulaire pour Whisper : sigles et noms propres du lexique.

    Toutes les entrées y contribuent, vérifiées ou non — une entrée fausse
    n'y fait au pire perdre un mot de vocabulaire inutile, contrairement au
    fact-check ou aux fiches d'entités où une erreur s'installerait comme
    référence. Plafonnée pour rester sous la fenêtre de prompt du modèle.
    """
    pieces: list[str] = []
    length = 0
    for term in load_lexicon():
        for nom in [term.terme, *term.sigles]:
            if not nom or nom in pieces:
                continue
            if length + len(nom) + 2 > WHISPER_PROMPT_MAX_CHARS:
                return ", ".join(pieces)
            pieces.append(nom)
            length += len(nom) + 2
    return ", ".join(pieces)


def glossary_block(settings=None) -> str:
    """Bloc à ajouter au prompt système de la relecture.

    Consigne délibérément prudente : ce sont des graphies correctes
    *possibles*, pas une liste à imposer — la relecture ne doit jamais
    remplacer un mot par un terme du lexique sans que l'audio le suggère.
    """
    terms = load_lexicon()
    if not terms:
        return ""
    lignes = [
        f"- {term.terme}" + (f" ({', '.join(term.sigles)})" if term.sigles else "")
        for term in terms
    ]
    return (
        "Lexique du domaine (MJPM — protection juridique des majeurs) : ce "
        "sont des graphies correctes du domaine. Si l'audio suggère l'un de "
        "ces termes ou sigles, écris-le sous cette graphie plutôt que sous "
        "une variante phonétique. Ne remplace jamais un mot par un terme de "
        "cette liste si l'audio ne le suggère pas clairement, et n'en "
        "invente jamais de graphie voisine.\n\n" + "\n".join(lignes)
    )


def _edit_distance_at_most(a: str, b: str, limit: int) -> bool:
    """Distance de Levenshtein bornée, sans calculer la matrice complète au-delà."""
    a, b = a.lower(), b.lower()
    if abs(len(a) - len(b)) > limit:
        return False
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            current[j] = min(
                previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost
            )
        previous = current
    return previous[-1] <= limit


def near_misses(text: str, *, max_distance: int = 2, min_length: int = 4) -> list[tuple[str, Term]]:
    """Mots de ``text`` proches d'un terme du lexique sans lui être identiques.

    Repère une graphie déformée (« DIPEM » pour « DIPM ») sans appel réseau :
    comparaison lettre à lettre contre chaque sigle et variante connus.
    """
    import re

    words = {w for w in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", text) if len(w) >= min_length}
    results: list[tuple[str, Term]] = []
    for word in words:
        for term in load_lexicon():
            for nom in term.noms():
                if not nom or len(nom) < min_length:
                    continue
                if word.lower() == nom.lower():
                    continue  # identique : pas une quasi-erreur
                if _edit_distance_at_most(word, nom, max_distance):
                    results.append((word, term))
                    break
            else:
                continue
            break
    return results
