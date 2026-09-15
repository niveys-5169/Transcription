"""Contrat léger du front sans dépendance de navigateur.

Les interactions complètes sont consignées dans docs/RECETTE_PHASE6.md ; ces
assertions empêchent les régressions structurelles les plus coûteuses.
"""
from pathlib import Path


STATIC = Path(__file__).parents[1] / "app" / "static"


def test_interface_ne_propose_plus_de_sous_titres():
    html = (STATIC / "index.html").read_text(encoding="utf-8").upper()
    assert "SRT" not in html
    assert "VTT" not in html


def test_etats_vides_et_longues_transcriptions_ont_leurs_points_d_ancrage():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")

    for marker in ("cancel-btn", "retry-btn", "player-unavailable", "blocks-more-btn"):
        assert f'id="{marker}"' in html
    assert "blocksRenderLimit: 250" in script
    assert "[hidden] { display: none !important; }" in css
