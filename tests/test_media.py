"""Tests du découpage audio — sans ffmpeg : ils n'utilisent que des WAV."""
import pytest

from app import media


def test_wav_duration(wav_factory):
    path = wav_factory([(2.5, 0.5)])
    assert media.wav_duration(path) == pytest.approx(2.5, abs=0.01)


def test_open_wav_refuse_un_format_inattendu(tmp_path):
    import wave

    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(44_100)
        handle.writeframes(b"\x00\x00\x00\x00" * 100)

    with pytest.raises(media.MediaError, match="mono"):
        media.open_wav(path)


def test_find_split_points_vise_les_silences(wav_factory):
    # 10 s de son, 2 s de silence, 10 s de son : la coupe doit tomber dans
    # le silence, pas à la seconde 10 pile.
    path = wav_factory([(10.0, 0.6), (2.0, 0.0), (10.0, 0.6)])
    points = media.find_split_points(path, target_seconds=11.0, search_seconds=6.0)

    assert len(points) == 1
    assert 10.0 <= points[0] <= 12.0


def test_find_split_points_rien_a_couper(wav_factory):
    path = wav_factory([(5.0, 0.5)])
    assert media.find_split_points(path, target_seconds=60.0) == []


def test_split_wav_rend_le_fichier_tel_quel_si_court(wav_factory):
    path = wav_factory([(4.0, 0.5)])
    chunks = media.split_wav(path, 60.0, path.parent / "out")
    assert len(chunks) == 1
    assert chunks[0].path == path
    assert chunks[0].offset == 0.0


def test_split_wav_produit_des_troncons_contigus(wav_factory, tmp_path):
    path = wav_factory([(10.0, 0.6), (1.5, 0.0), (10.0, 0.6), (1.5, 0.0), (8.0, 0.6)])
    total = media.wav_duration(path)
    chunks = media.split_wav(path, 11.0, tmp_path / "chunks", search_seconds=5.0)

    assert len(chunks) >= 2
    assert chunks[0].offset == 0.0
    # Chaque tronçon reprend exactement là où le précédent s'arrête.
    for previous, current in zip(chunks, chunks[1:]):
        assert current.offset == pytest.approx(
            previous.offset + previous.duration, abs=0.02
        )
    assert sum(chunk.duration for chunk in chunks) == pytest.approx(total, abs=0.05)


def test_split_wav_ecrit_des_wav_valides(wav_factory, tmp_path):
    path = wav_factory([(10.0, 0.6), (1.0, 0.0), (10.0, 0.6)])
    chunks = media.split_wav(path, 10.0, tmp_path / "chunks", search_seconds=4.0)

    for chunk in chunks:
        handle = media.open_wav(chunk.path)  # lève si le format est mauvais
        try:
            assert handle.getnframes() > 0
        finally:
            handle.close()


def test_ffmpeg_manquant_donne_un_message_utile(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda _: None)
    monkeypatch.setitem(
        __import__("sys").modules, "imageio_ffmpeg", None
    )
    with pytest.raises(media.MediaError, match="ffmpeg est introuvable"):
        media.ffmpeg_exe()
