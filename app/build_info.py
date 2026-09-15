"""Identifiant du build embarqué dans l'exécutable.

Le workflow GitHub Actions remplace cette valeur dans son espace de travail
juste avant PyInstaller. Le fichier de développement reste volontairement
stable et ne déclenche jamais de mise à jour automatique.
"""

BUILD_ID = "development"
