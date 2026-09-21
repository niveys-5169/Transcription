"""Profils de capacités NVIDIA NIM : reasoning off, température, préfixe."""
from app.proofread.nim_profiles import (
    DEFAULT_PROFILE,
    build_messages,
    profile_for,
)


def test_nemotron_recoit_detailed_thinking_off_en_tete_avec_temperature_zero():
    profile = profile_for("nvidia/llama-3.3-nemotron-super-49b-v1")

    assert profile.system_prefix == "detailed thinking off"
    assert profile.prefix_placement == "leading_system"
    assert profile.temperature == 0.0

    messages = build_messages(profile, system="RELECTURE", user="Bloc à relire")
    assert messages[0] == {"role": "system", "content": "detailed thinking off"}
    assert messages[1] == {"role": "system", "content": "RELECTURE"}
    assert messages[2] == {"role": "user", "content": "Bloc à relire"}


def test_qwen3_recoit_no_think_en_fin_de_message_utilisateur():
    profile = profile_for("qwen3-235b-a22b")

    assert profile.system_prefix == "/no_think"
    assert profile.prefix_placement == "user_suffix"

    messages = build_messages(profile, system="RELECTURE", user="Bloc")
    assert messages == [
        {"role": "system", "content": "RELECTURE"},
        {"role": "user", "content": "Bloc\n\n/no_think"},
    ]


def test_modele_instruct_simple_ne_recoit_aucun_prefixe_invente():
    profile = profile_for("meta/llama-3.1-8b-instruct")

    assert profile is DEFAULT_PROFILE
    assert profile.system_prefix == ""

    messages = build_messages(profile, system="RELECTURE", user="Bloc")
    assert messages == [
        {"role": "system", "content": "RELECTURE"},
        {"role": "user", "content": "Bloc"},
    ]


def test_modele_inconnu_utilise_le_profil_neutre():
    assert profile_for("un-modele-jamais-vu") is DEFAULT_PROFILE
    assert profile_for("") is DEFAULT_PROFILE
