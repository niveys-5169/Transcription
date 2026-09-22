"""Contrat léger du front sans dépendance de navigateur.

Les interactions complètes sont consignées dans docs/RECETTE_PHASE6.md ; ces
assertions empêchent les régressions structurelles les plus coûteuses.
"""
from pathlib import Path


STATIC = Path(__file__).parents[1] / "app" / "static"


def test_interface_propose_des_sous_titres_professionnels():
    html = (STATIC / "index.html").read_text(encoding="utf-8").upper()
    assert "SRT" in html
    assert "VTT" in html


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
    assert 'id="nim_model"' in html
    assert 'id="nim_fallback_model_1"' in html
    assert 'id="nim_fallback_model_2"' in html
    assert "populateNimModels" in script
    assert "nimModelsDetail" in script
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


def test_outils_de_fusion_et_scission_sont_exposes():
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'data-action="split"' in script
    assert 'data-action="merge"' in script
    assert "/split`" in script and "/merge`" in script


def test_rechercher_remplacer_est_expose_dans_l_editeur():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'id="editor-replace-input"' in html
    assert 'id="editor-replace-all-btn"' in html
    assert "replaceEditorMatches" in script


def test_undo_redo_editeur_sont_exposes_au_clavier():
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "editorUndoStack" in script
    assert "undoEditor()" in script and "redoEditor()" in script
    assert 'event.key.toLowerCase() === "z"' in script


def test_lexique_est_editable_depuis_l_interface():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'id="lexicon-form"' in html
    assert 'id="verify-lexicon-all"' in html
    assert 'id="lexicon-verification-progress"' in html
    assert 'data-lexicon-action="verify"' in script
    assert 'data-lexicon-action="edit"' in script
    assert 'data-lexicon-action="delete"' in script
    assert "/api/lexicon/verification" in script
    assert "valider-lexique" in script


def test_horodatage_de_bloc_est_editable():
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'data-action="timing"' in script
    assert "editBlockTiming" in script


def test_exports_srt_et_vtt_sont_a_nouveau_proposes():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'data-fmt="srt"' in html
    assert 'data-fmt="vtt"' in html
    assert 'data-fmt="docx"' in html


def test_suivi_audio_est_borne_par_animation_frame():
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "requestAnimationFrame" in script
    assert 'addEventListener("timeupdate", schedulePlayerTimeDisplay)' in script
