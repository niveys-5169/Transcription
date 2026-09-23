# Règles du dépôt

## Intégrité du texte des transcriptions

Pour toute modification qui lit, écrit ou transforme une transcription (sortie de processus, réponse API, base de données, export ou interface) :

1. Décoder et encoder explicitement en UTF-8. À la frontière avec un processus ou un fichier, traiter une erreur de décodage comme un échec visible ; ne pas la masquer avec `errors="replace"` ou `errors="ignore"`.
2. Avant de persister ou d'afficher un texte produit par la chaîne de transcription/relecture, vérifier qu'il ne contient pas le point de code U+FFFD (caractère de remplacement Unicode). S'il apparaît, arrêter l'étape avec un message d'erreur et conserver la version précédente du texte. Ne pas remplacer automatiquement les caractères corrompus : l'original n'est plus récupérable de façon fiable.
3. Pour chaque changement de ce chemin, vérifier un aller-retour avec une phrase française contenant `é`, `è`, `ê`, `à`, `ç` et `œ`, puis contrôler le diff et le texte rendu. Le changement est terminé seulement si ces caractères sont identiques à l'entrée et si aucun U+FFFD n'a été introduit.
