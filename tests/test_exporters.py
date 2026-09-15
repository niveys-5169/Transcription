import json

from app import exporters


SEGMENTS = [
    {"start": 0.0, "end": 2.5, "text": "Bonjour à tous."},
    {"start": 2.5, "end": 6.25, "text": "Nous commençons le cours."},
    {"start": 6.25, "end": 7.0, "text": "   "},  # segment vide : ignoré
]


def test_timecode_srt_et_vtt():
    assert exporters.timecode(0) == "00:00:00,000"
    assert exporters.timecode(3661.5) == "01:01:01,500"
    assert exporters.timecode(3661.5, ".") == "01:01:01.500"
    assert exporters.timecode(-4) == "00:00:00,000"


def test_srt_numerote_et_saute_les_segments_vides():
    srt = exporters.to_srt(SEGMENTS)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:02,500\nBonjour à tous.")
    assert "2\n00:00:02,500 --> 00:00:06,250" in srt
    assert srt.count("-->") == 2


def test_vtt_commence_par_l_entete():
    vtt = exporters.to_vtt(SEGMENTS)
    assert vtt.splitlines()[0] == "WEBVTT"
    assert "00:00:00.000 --> 00:00:02.500" in vtt


def test_render_txt_prefere_le_texte_relu():
    job = {"clean_text": "Texte relu.", "raw_text": "texte brut"}
    assert exporters.render(job, "txt") == "Texte relu.\n"
    assert exporters.render({"raw_text": "texte brut"}, "txt") == "texte brut\n"


def test_exports_utilisent_les_blocs_humains_des_qu_ils_sont_corriges():
    job = {
        "raw_text": "brut", "clean_text": "Version IA.",
        "segments": [{"text": "Version IA."}],
        "review_blocks": [{"id": "segment-1", "text": "Version humaine."}],
    }
    assert exporters.render(job, "txt") == "Version humaine.\n"
    assert "Version humaine." in exporters.render(job, "md")
    payload = json.loads(exporters.render(job, "json"))
    assert payload["texte_relu"] == "Version humaine."
    assert payload["texte_brut"] == "brut"


def test_render_md_contient_titre_resume_et_corps():
    job = {
        "filename": "cours.mp4",
        "title": "Introduction à la thermodynamique",
        "summary": json.dumps(["Premier principe", "Second principe"]),
        "clean_text": "## Le premier principe\n\nL'énergie se conserve.",
    }
    markdown = exporters.render(job, "md")
    assert markdown.startswith("# Introduction à la thermodynamique")
    assert "- Premier principe" in markdown
    assert "L'énergie se conserve." in markdown


def test_course_markdown_ne_garde_que_le_resume_si_disponible():
    job = {
        "filename": "cours.mp4",
        "title": "Introduction à la thermodynamique",
        "summary": json.dumps(["Premier principe", "Second principe"]),
        "clean_text": "## Le premier principe\n\nL'énergie se conserve. " * 50,
    }
    markdown = exporters.course_markdown(job)
    assert markdown.startswith("# Introduction à la thermodynamique")
    assert "- Premier principe" in markdown
    assert "- Second principe" in markdown
    assert "L'énergie se conserve" not in markdown


def test_course_markdown_retombe_sur_le_texte_complet_sans_resume():
    job = {"title": "Cours sans relecture", "clean_text": "Texte intégral du cours."}
    markdown = exporters.course_markdown(job)
    assert "Texte intégral du cours." in markdown


def test_render_json_est_relisable():
    job = {
        "filename": "cours.mp4",
        "duration": 12.5,
        "summary": json.dumps(["un point"]),
        "segments": SEGMENTS,
        "clean_text": "relu",
        "raw_text": "brut",
    }
    payload = json.loads(exporters.render(job, "json"))
    assert payload["fichier"] == "cours.mp4"
    assert payload["resume"] == ["un point"]
    assert len(payload["segments"]) == 3


def test_render_json_tolere_un_resume_illisible():
    payload = json.loads(exporters.render({"summary": "pas du json"}, "json"))
    assert payload["resume"] == []


def test_safe_filename_neutralise_les_caracteres_genants():
    assert exporters.safe_filename("Cours n°3 : l'oral.mp4", "txt") == "Cours-n3-loral.txt"
    assert exporters.safe_filename("", "srt") == "transcription.srt"
    assert exporters.safe_filename("../../etc/passwd", "txt") == "passwd.txt"


def test_safe_filename_ne_casse_pas_l_entete_http():
    # Ni guillemet ni saut de ligne ne doivent survivre : le nom part dans
    # un en-tete Content-Disposition.
    for hostile in ['cours"; rm -rf /.mp4', "cours\r\nX-Injection: 1.mp4", "…‮.mp4"]:
        nom = exporters.safe_filename(hostile, "txt")
        assert not set(nom) & set('"\r\n/\\;')
        assert nom.endswith(".txt")
