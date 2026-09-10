# Transcription — RunPod Serverless endpoint

Point de terminaison RunPod Serverless qui transcrit de l'audio avec
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) sur GPU
(`device="cuda"`, `compute_type="float16"`). Conçu pour être appelé
depuis la page locale `transcription-locale.html` (moteur « RunPod »
dans le choix du moteur de transcription), en complément du serveur
local CPU et du moteur navigateur (WASM) — les trois renvoient le même
format de résultat.

## Contenu du dépôt

| Fichier | Rôle |
|---|---|
| `handler.py` | Le handler RunPod Serverless (point d'entrée du worker) |
| `Dockerfile` | Image du worker (base CUDA + dépendances + handler) |
| `requirements.txt` | Dépendances Python épinglées et testées |
| `.dockerignore` | Exclut la doc et les fichiers non nécessaires de l'image |

## Contrat de l'API

**Entrée** (`job["input"]`) :

```json
{
  "audio_base64": "<fichier WAV encodé en base64>",
  "model": "large-v3",
  "language": "fr"
}
```

`model` accepte : `tiny`, `base`, `small`, `medium`, `large-v3` (tout
autre valeur retombe sur `large-v3`). `language` est optionnel
(détection automatique si omis).

**Sortie** (`job["output"]`, succès) :

```json
{
  "text": "texte complet transcrit",
  "segments": [{"start": 0.0, "end": 2.5, "text": "..."}],
  "language": "fr"
}
```

**Sortie** (erreur) : `{"error": "message"}`.

## Déployer sur RunPod

1. Poussez ce dépôt sur GitHub (déjà fait si vous lisez ceci depuis là-bas).
2. RunPod → **Serverless** → **New Endpoint** → source **GitHub Repo**,
   sélectionnez ce dépôt, branche `main`, chemin du Dockerfile `/Dockerfile`.
3. GPU recommandé : **L4** (meilleur rapport prix/vitesse mesuré pour ce
   type de calcul — voir la comparaison de coûts faite avec Claude).
4. Workers : min 0 (scale-to-zero), max selon vos besoins.
5. Récupérez l'**Endpoint ID** et une **clé API** (Settings → API Keys)
   une fois déployé, à coller dans `transcription-locale.html`.

## Ce qui a été vérifié avant déploiement (côté logique applicative)

Testé en local avec un modèle `faster_whisper.WhisperModel` simulé
(pas de GPU dans l'environnement de dev) :
- décodage audio base64 → fichier temporaire → transcription → JSON de
  sortie conforme au contrat ci-dessus ;
- gestion des erreurs : `audio_base64` manquant, base64 invalide, nom
  de modèle inconnu (retombe sur `large-v3`) ;
- `device="cuda"` et `compute_type="float16"` bien transmis à
  `WhisperModel` ;
- `runpod.serverless.start(...)` appelé de façon inconditionnelle au
  niveau module (requis par le scanner de dépôt de RunPod — un appel
  conditionnel dans `if __name__ == "__main__":` n'est pas détecté).

**Non vérifiable depuis l'environnement de développement** (pas
d'accès GPU ni à Hugging Face) : le build Docker réel, le
téléchargement du modèle sur le worker, et le temps de transcription
réel. À confirmer avec un premier job réel sur RunPod.

## Choix de conception notables

- **`vad_filter=False`** : le filtre de détection de voix (VAD) peut
  classer à tort de l'audio avec musique de fond ou parole discrète
  comme « silence » et faire disparaître du contenu réel sans erreur
  (bug reproduit et diagnostiqué sur le serveur local — voir
  historique du projet). Désactivé ici pour la même raison :
  traiter un peu plus lentement plutôt que perdre du contenu en
  silence.
- **`beam_size=5`** : compromis qualité/vitesse standard de Whisper ;
  à réduire (ex. 1) si la vitesse prime sur la précision.
