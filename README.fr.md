# Resource Steward — Guide d’utilisation

**Langues :** [English](README.md) · [العربية](README.ar.md) · Français · [Español](README.es.md)

Resource Steward est un utilitaire Python léger qui surveille la pression sur les ressources dans des sessions Linux de type Arena, vérifie les conditions avant les tâches coûteuses, supervise les commandes qu’il lance et organise la vérification des fichiers volumineux.

> **Portée et sécurité :** il s’agit d’un projet indépendant et non officiel, et non d’une fonction intégrée à Arena ou Google Drive. L’outil ne téléverse ni ne supprime automatiquement les fichiers. Examinez les commandes et les permissions avant toute exécution.

## Fonctionnalités

- Lit les informations mémoire cgroup v2 visibles, la mémoire disponible de l’hôte et l’espace disque libre.
- Estime le nombre et la taille des fichiers susceptibles d’entrer dans un instantané de session.
- Effectue des contrôles préalables et signale la pression sur les ressources ou le retour à la normale.
- Peut superviser une commande enfant et arrêter son groupe de processus si les limites configurées sont atteintes ou si la surveillance échoue.
- Conserve une file locale SQLite pour examiner les fichiers volumineux.
- Peut vérifier la taille et la somme MD5 d’un fichier Google Drive déjà présent, avec un jeton de lecture configuré.
- Fournit un exemple de points de reprise et de restauration, si l’application sait elle-même enregistrer et restaurer son état.

## Prérequis

- Linux avec `/proc` et cgroup v2.
- Python 3.10 ou version ultérieure.
- Un shell compatible POSIX et Git.
- Aucune dépendance Python tierce n’est nécessaire pour l’utilitaire principal.

Le projet vise les environnements Linux de type Arena ; il n’est pas documenté comme un outil générique pour Android/Termux, macOS ou Windows.

## Démarrage

```bash
git clone https://github.com/supermorkat/Resource-Steward.git
cd Resource-Steward
python --version
python -m pytest
```

Consultez la documentation du dépôt et l’aide de la ligne de commande pour connaître la syntaxe exacte de la version utilisée. La surveillance ne garantit pas qu’une tâche puisse être arrêtée ou reprise sans risque.

## Précautions

- Les mesures dépendent des informations exposées par l’environnement et restent des estimations.
- Une commande supervisée peut être arrêtée lorsque les seuils sont atteints ou que les mesures deviennent indisponibles.
- Les points de reprise doivent être implémentés par l’application elle-même.
- La vérification Google Drive concerne un fichier déjà téléversé ; elle n’effectue pas le téléversement.
- Ne placez jamais les jetons d’accès dans le code ou le dépôt. Utilisez le mécanisme de variables d’environnement documenté.
- Examinez les éléments de la file des fichiers volumineux avant d’agir.

## Tests et contribution

Depuis la racine du dépôt :

```bash
python -m pytest
```

Mettez les tests à jour si vous modifiez les seuils de ressources, la supervision des processus, le stockage ou la vérification Drive.

## Licence

Aucune licence n’est indiquée dans la documentation au moment de la rédaction. N’assumez pas que la réutilisation, la modification ou la redistribution sont autorisées avant l’ajout d’une licence explicite.

[Dépôt GitHub](https://github.com/supermorkat/Resource-Steward)
