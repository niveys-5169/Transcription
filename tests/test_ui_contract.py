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


def test_confiance_et_repli_nim_sont_visibles_sans_exposer_la_cle():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")

    assert 'id="nim_api_key"' in html
    assert 'type="password" id="nim_api_key"' in html
    assert "timeline-marker.needs-review" in css
    assert "confidence-badge" in css
    assert "markerLabel" in script
    assert 'id="retry-claude-btn"' in html
    assert 'id="retry-nim-btn"' in html


def test_zone_memoire_reste_en_proposition_avant_validation():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'id="knowledge-zone"' in html
    assert 'id="knowledge-btn"' in html
    assert "renderKnowledge(job)" in script
    assert "/knowledge`" in script
