"""Extraction et découpage audio, via ffmpeg.

Le prototype HTML faisait ce travail dans le navigateur (Web Audio API,
capture d'un élément ``<video>``, parsing WAV maison), ce qui a produit
l'essentiel des bugs du projet : signal coupé à la source par
``video.volume = 0``, en-têtes RIFF non standards, échec de
``decodeAudioData`` au-delà de ~180 Mo.

Ici l'extraction est faite côté serveur par ffmpeg, qui gère nativement tous
ces cas. Si ffmpeg n'est pas installé sur la machine, on utilise le binaire
embarqué par le paquet ``imageio-ffmpeg`` — aucune installation manuelle
n'est donc nécessaire.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

from .process import hidden_console_flags

SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16 bits

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")
_OUT_TIME_RE = re.compile(r"out_time=(\d+):(\d\d):(\d\d(?:\.\d+)?)")


class MediaError(RuntimeError):
    """Erreur d'extraction ou de découpage audio."""


@dataclass
class AudioChunk:
    """Un tronçon de WAV, avec son décalage dans le fichier d'origine."""

    path: Path
    offset: float
    duration: float


def ffmpeg_exe() -> str:
    """Chemin vers ffmpeg : celui du système, sinon celui d'imageio-ffmpeg."""
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - dépend de l'installation
        raise MediaError(
            "ffmpeg est introuvable. Installez-le (https://ffmpeg.org) ou "
            "installez le paquet Python « imageio-ffmpeg » "
            "(pip install imageio-ffmpeg)."
        ) from exc


def ffmpeg_available() -> bool:
    try:
        ffmpeg_exe()
        return True
    except MediaError:
        return False


def _ffmpeg_process_options() -> dict[str, int]:
    """Options de lancement de ffmpeg adaptées à la plate-forme.

    Sous Windows, ``ffmpeg.exe`` ne doit pas créer de fenêtre de console :
    l'application est graphique et l'extraction se déroule en arrière-plan.
    La constante n'existe pas sur les autres systèmes, où la valeur 0 reste
    explicitement sans effet.
    """
    return {"creationflags": hidden_console_flags()}


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        **_ffmpeg_process_options(),
    )


def probe_duration(path: Path) -> float:
    """Durée du média en secondes (0.0 si indéterminable)."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        result = _run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ]
        )
        try:
            return float(result.stdout.strip())
        except ValueError:
            pass

    # Sans ffprobe (cas du binaire embarqué par imageio-ffmpeg) : ffmpeg
    # affiche la durée dans son en-tête puis sort en erreur, faute de sortie
    # demandée. C'est instantané, il ne décode rien.
    result = _run([ffmpeg_exe(), "-hide_banner", "-i", str(path)])
    match = _DURATION_RE.search(result.stderr)
    if match:
        hours, minutes, seconds = match.groups()
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return 0.0


def extract_wav(
    src: Path,
    dst: Path,
    *,
    duration: float = 0.0,
    on_progress: Callable[[float], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> Path:
    """Convertit n'importe quel média en WAV PCM 16 bits, mono, 16 kHz.

    C'est exactement le format attendu par Whisper. ``on_progress`` reçoit une
    fraction entre 0 et 1 quand la durée totale est connue.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_exe(),
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-vn",  # ignorer la piste vidéo
        # Normalisation de volume (EBU R128) : remet à niveau les
        # enregistrements trop bas ou aux écarts de volume importants, sans
        # risque de coupure (le paramètre TP plafonne les crêtes). Un seul
        # passage (pas de mesure préalable) : moins précis qu'un loudnorm en
        # deux temps, mais suffisant ici et sans coût de traitement supplémentaire.
        "-af",
        "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-ac",
        str(CHANNELS),
        "-ar",
        str(SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        "-progress",
        "pipe:1",
        str(dst),
    ]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        **_ffmpeg_process_options(),
    )
    assert process.stdout is not None

    try:
        for line in process.stdout:
            if should_cancel is not None and should_cancel():
                process.kill()
                raise MediaError("Extraction annulée.")
            match = _OUT_TIME_RE.search(line)
            if match and duration > 0 and on_progress is not None:
                hours, minutes, seconds = match.groups()
                elapsed = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
                on_progress(min(elapsed / duration, 1.0))
    finally:
        process.stdout.close()
        stderr = process.stderr.read() if process.stderr else ""
        if process.stderr:
            process.stderr.close()
        returncode = process.wait()

    if returncode != 0:
        raise MediaError(
            f"ffmpeg n'a pas pu extraire l'audio de « {src.name} ».\n"
            f"{stderr.strip()[:800]}"
        )
    if not dst.exists() or dst.stat().st_size <= 44:
        raise MediaError(
            f"Aucun audio n'a été extrait de « {src.name} » : le fichier "
            "contient-il bien une piste sonore ?"
        )
    return dst


def extract_wav_clip(
    src: Path,
    dst: Path,
    *,
    start: float,
    end: float,
    should_cancel: Callable[[], bool] | None = None,
) -> Path:
    """Extrait une plage avec le format Whisper existant : mono, 16 kHz PCM."""
    if end <= start:
        raise MediaError("La plage audio à extraire est vide.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{end - start:.3f}",
        "-vn", "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE),
        "-c:a", "pcm_s16le", "-progress", "pipe:1", str(dst),
    ]
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        errors="replace", **_ffmpeg_process_options(),
    )
    assert process.stdout is not None
    try:
        for _line in process.stdout:
            if should_cancel is not None and should_cancel():
                process.kill()
                raise MediaError("Extraction annulée.")
    finally:
        process.stdout.close()
        stderr = process.stderr.read() if process.stderr else ""
        if process.stderr:
            process.stderr.close()
        returncode = process.wait()
    if returncode != 0:
        raise MediaError(f"ffmpeg n'a pas pu extraire le clip audio.\n{stderr.strip()[:800]}")
    if not dst.exists() or dst.stat().st_size <= 44:
        raise MediaError("Le clip audio extrait est vide.")
    return dst


def open_wav(path: Path) -> wave.Wave_read:
    """Ouvre un WAV en vérifiant qu'il est au format attendu."""
    handle = wave.open(str(path), "rb")
    if (
        handle.getnchannels() != CHANNELS
        or handle.getsampwidth() != SAMPLE_WIDTH
        or handle.getframerate() != SAMPLE_RATE
    ):
        params = (
            f"{handle.getnchannels()} canal(aux), "
            f"{handle.getsampwidth() * 8} bits, {handle.getframerate()} Hz"
        )
        handle.close()
        raise MediaError(
            f"Le WAV « {path.name} » est en {params} ; attendu : mono, 16 bits, "
            f"{SAMPLE_RATE} Hz."
        )
    return handle


def wav_duration(path: Path) -> float:
    handle = open_wav(path)
    try:
        return handle.getnframes() / float(handle.getframerate())
    finally:
        handle.close()


def _rms_profile(path: Path, window_seconds: float = 0.1) -> np.ndarray:
    """Énergie (RMS) du signal, fenêtre par fenêtre, sans tout charger en RAM."""
    window = int(SAMPLE_RATE * window_seconds)
    values: list[float] = []
    handle = open_wav(path)
    try:
        while True:
            raw = handle.readframes(window)
            if not raw:
                break
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
            values.append(float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0)
    finally:
        handle.close()
    return np.asarray(values, dtype=np.float32)


def find_split_points(
    path: Path,
    target_seconds: float,
    *,
    search_seconds: float = 20.0,
    window_seconds: float = 0.1,
) -> list[float]:
    """Points de découpe (en secondes) placés dans les passages les plus calmes.

    Découper à un instant fixe couperait au milieu d'un mot et ferait perdre
    du contenu à la jonction. On vise ``target_seconds`` puis on cherche, dans
    une fenêtre de ``search_seconds`` autour de cette cible, le moment le plus
    silencieux — typiquement une respiration entre deux phrases.
    """
    total = wav_duration(path)
    if total <= target_seconds:
        return []

    profile = _rms_profile(path, window_seconds)
    if profile.size == 0:
        return []

    points: list[float] = []
    cursor = target_seconds
    # On arrête de couper quand le reste ne ferait plus qu'un moignon : un
    # dernier tronçon d'une seconde coûterait un appel réseau pour rien.
    while cursor < total - target_seconds * 0.5:
        low = max(int((cursor - search_seconds / 2) / window_seconds), 0)
        high = min(int((cursor + search_seconds / 2) / window_seconds), profile.size - 1)
        if high <= low:
            candidate = cursor
        else:
            candidate = (low + int(np.argmin(profile[low : high + 1]))) * window_seconds
        # Ne jamais reculer : garantit des tronçons strictement croissants.
        if points and candidate <= points[-1] + 1.0:
            candidate = cursor
        points.append(round(candidate, 3))
        cursor = candidate + target_seconds

    return [p for p in points if 0 < p < total]


def split_wav(
    path: Path,
    target_seconds: float,
    out_dir: Path,
    *,
    search_seconds: float = 20.0,
) -> list[AudioChunk]:
    """Découpe un WAV en tronçons, sur des silences quand c'est possible.

    Renvoie une liste d'un seul élément (le fichier d'origine) si le fichier
    est déjà assez court.
    """
    total = wav_duration(path)
    if total <= target_seconds:
        return [AudioChunk(path=path, offset=0.0, duration=total)]

    cuts = find_split_points(path, target_seconds, search_seconds=search_seconds)
    boundaries = [0.0, *cuts, total]
    out_dir.mkdir(parents=True, exist_ok=True)

    chunks: list[AudioChunk] = []
    source = open_wav(path)
    try:
        for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            start_frame = int(start * SAMPLE_RATE)
            frame_count = int((end - start) * SAMPLE_RATE)
            if frame_count <= 0:
                continue
            source.setpos(start_frame)
            payload = source.readframes(frame_count)

            chunk_path = out_dir / f"{path.stem}.part{index:03d}.wav"
            with wave.open(str(chunk_path), "wb") as out:
                out.setnchannels(CHANNELS)
                out.setsampwidth(SAMPLE_WIDTH)
                out.setframerate(SAMPLE_RATE)
                out.writeframes(payload)

            chunks.append(
                AudioChunk(
                    path=chunk_path,
                    offset=round(start, 3),
                    duration=len(payload) / (SAMPLE_RATE * SAMPLE_WIDTH),
                )
            )
    finally:
        source.close()

    return chunks


def iter_wav_bytes(path: Path, block_size: int = 1 << 20) -> Iterator[bytes]:
    with path.open("rb") as handle:
        while True:
            block = handle.read(block_size)
            if not block:
                return
            yield block
