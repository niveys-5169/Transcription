"""Politiques explicites d'acceptation des candidats de relecture."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ProofreadAcceptanceContract:
    min_length_ratio: float
    max_expansion_ratio: float
    max_expansion_absolute_margin: int
    max_added_word_ratio: float
    max_added_span_words: int

    check_language: bool = True
    check_prefix_alignment: bool = True
    check_entity_grounding: bool = True
    preserve_numbers: bool = True
    preserve_acronyms: bool = True


FAITHFUL_PROOFREAD_CONTRACT = ProofreadAcceptanceContract(
    min_length_ratio=0.55,
    max_expansion_ratio=1.6,
    max_expansion_absolute_margin=40,
    max_added_word_ratio=0.40,
    max_added_span_words=25,
)
