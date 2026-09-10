from app.proofread import chunking

from conftest import segment


def _long_segments(count: int, text: str = "Une phrase de longueur moyenne. "):
    return [segment(i * 5.0, i * 5.0 + 5.0, text) for i in range(count)]


def test_segments_to_text_concatene_et_ignore_le_vide():
    segments = [segment(0, 1, "Bonjour"), segment(1, 2, "   "), segment(2, 3, "tout le monde")]
    assert chunking.segments_to_text(segments) == "Bonjour tout le monde"


def test_build_chunks_respecte_la_taille_maximale():
    chunks = chunking.build_chunks(_long_segments(60), max_chars=600)
    assert len(chunks) > 1
    # La limite basse est de 500 caractères ; on tolère un dépassement d'un
    # segment, puisqu'on ne coupe jamais à l'intérieur d'un segment.
    assert all(len(chunk.text) <= 600 + 40 for chunk in chunks)


def test_build_chunks_conserve_tout_le_texte():
    segments = _long_segments(40)
    chunks = chunking.build_chunks(segments, max_chars=800)
    recompose = " ".join(chunk.text for chunk in chunks)
    assert recompose == chunking.segments_to_text(segments)


def test_build_chunks_horodate_chaque_bloc():
    chunks = chunking.build_chunks(_long_segments(30), max_chars=700)
    assert chunks[0].start == 0.0
    for previous, current in zip(chunks, chunks[1:]):
        assert current.start >= previous.end
        assert current.index == previous.index + 1


def test_build_chunks_coupe_de_preference_en_fin_de_phrase():
    segments = [
        segment(0, 2, "a" * 400),
        segment(2, 4, "Fin de phrase."),
        segment(4, 6, "b" * 100),
    ]
    chunks = chunking.build_chunks(segments, max_chars=560)
    assert chunks[0].text.endswith("Fin de phrase.")
    assert chunks[1].text.startswith("b")


def test_build_chunks_sur_liste_vide():
    assert chunking.build_chunks([]) == []
    assert chunking.build_chunks([segment(0, 1, "  ")]) == []


def test_tail_coupe_sur_un_mot_entier():
    text = "Le premier principe de la thermodynamique énonce la conservation."
    extrait = chunking.tail(text, 20)
    assert len(extrait) <= 20
    assert text.endswith(extrait)
    assert not extrait.startswith(" ")
    assert chunking.tail("court", 100) == "court"
