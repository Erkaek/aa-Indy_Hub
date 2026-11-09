# Prompt 04 — Simplifier la pagination en préservant les snapshots complets

Je veux examiner puis réduire les traitements de pagination redondants d’Indy Hub en m’appuyant sur django-esi, sans perdre la garantie qu’une réconciliation destructive utilise un snapshot complet et cohérent.

Travaille dans `/home/aadev/indyhub/aa-Indy_Hub`. Ce prompt autorise une réalisation ciblée dans le dépôt, ses tests isolés et sa documentation. Il ne demande ni modification de l’installation active ni appel ESI réel.

## Objectif et point essentiel

L’audit a observé que `_fetch_paginated()` appelle `.results()`, puis que `_validate_paginated_last_modified()` parcourt à nouveau les pages pour comparer leurs dates. Cette seconde passe peut utiliser le cache, mais peut aussi provoquer des requêtes supplémentaires.

django-esi gère déjà la pagination et les réponses ETag partielles. Cela ne démontre pas qu’il fournit la même garantie de cohérence `Last-Modified` entre toutes les pages. N’assimile pas ces deux propriétés.

Je veux une simplification démontrée, pas la suppression d’un contrôle au motif que `.results()` existe. Si l’API publique native ne permet pas de préserver la garantie à coût inférieur, conserve le contrôle et livre une amélioration limitée ou un diagnostic précis de ce qui empêche l’optimisation.

## Statut et référence de ce chantier

Ce chantier est conditionnel et vient après la stabilisation de la façade. Compare les implémentations publiques de `.result()` et `.results()` dans `esi/openapi_clients.py`. L'absence d'une API publique exposant les en-têtes de chaque page peut justifier de conserver l'approche actuelle. Ne crée pas une seconde boucle de pagination uniquement pour annoncer une réduction de code.

Une compilation, un comptage de mocks ou des en-têtes de dernière page ne prouvent pas la cohérence de l'ensemble. La conclusion « optimisation non justifiée » est un résultat valide, sans diff artificiel. Si l'analyse révèle un défaut indépendant de complétude, identifie-le comme correction fonctionnelle distincte, avec son impact sur la fraîcheur et les reprises ; ne le mélange pas silencieusement à une refactorisation supposée équivalente.

## Avantages attendus

- Réduire les relectures de pages et les éventuels appels réseau supplémentaires.
- Clarifier la responsabilité de django-esi pour le transport paginé.
- Rendre explicites les conditions autorisant la réconciliation des lignes locales.
- Réduire les adaptations de pagination maintenues par Indy Hub.
- Mieux tester les erreurs de pages, les caches et les réponses mixtes avant suppression de données.

## Inconvénients et risques

- Un snapshot incomplet accepté comme complet peut entraîner la suppression de blueprints, jobs, assets ou structures valides.
- Une liste reçue sans erreur n’est pas à elle seule une preuve de cohérence temporelle.
- La réponse renvoyée par `.results(return_response=True)` peut ne représenter que la dernière page.
- Une seconde passe peut être économique quand le cache est chaud ; supprimer du code ne garantit donc pas un gain réseau mesurable.
- Recourir à des hooks privés de django-esi pour observer chaque page créerait un nouveau couplage fragile.
- Le refus conservateur d’un résultat douteux peut retarder la fraîcheur des données et demander une reprise contrôlée.

## Préparation obligatoire

1. Lis `AGENTS.md`, puis `docs/codex/INDEX.md`.
1. Lis `ESI.md`, `DATABASE.md`, `CELERY.md`, `CACHE.md`, `RISK_AREAS.md` et `TESTING.md` sous `docs/codex/`.
1. Relève `git status --short` et les diffs existants. Je travaille en parallèle : respecte les changements en cours, les nouvelles signatures et les correctifs déjà présents.
1. Vérifie les versions avec `/home/aadev/venv-v5/bin/python`, sans modifier le V5. Le `.venv` du dépôt n’est pas une preuve de l’environnement actif.
1. Relis les métadonnées actuelles ; à la préparation de ce prompt, django-esi était déjà borné à `>=10.1,<11`.
1. Lis le code natif de `.result()` et `.results()` dans la version supportée, y compris la gestion du cache, des 304 et des nombres de pages. Utilise des sources et des spécifications locales, sans initialisation distante du fournisseur.

## Périmètre

Examine d’abord `indy_hub/services/esi_client.py`, notamment `_fetch_paginated()` et `_validate_paginated_last_modified()`, puis les synchronisations qui réconcilient les résultats avec les tables locales.

Repère tous les consommateurs réellement concernés : blueprints, jobs, assets, contrats et structures selon leur chemin actuel. N’affirme pas qu’une modification de cette méthode couvre un consommateur qui suit un autre chemin.

Ne change pas les règles d’ownership, les permissions, les scopes, les clés de verrouillage, les identités QueueOnce, les budgets, les schedules ou les règles de notification. Ne fusionne pas les tables de snapshots et ne réécris aucune migration publiée.

## Diagnostic et décision demandés

1. Décris le flux actuel : sélection du token, collecte des pages, cache/ETag, validation, transaction d’écriture et suppression des lignes absentes.
1. Identifie pour chaque consommateur la preuve exacte utilisée pour déclarer le résultat complet. Repère les sorties partielles, les erreurs converties en liste vide et les erreurs de sonde ignorées.
1. Construis une matrice comparant les garanties natives et applicatives : toutes les pages récupérées, cache mixte, 304 intégral, date cohérente, total de pages stable et interruption par une limite.
1. Mesure hors réseau le nombre d’appels logiques, de lectures de cache et de demandes de page pour les scénarios actuels. Ne présente pas tous les accès à `.result()` comme des requêtes réseau réelles.
1. Évalue une solution fondée sur les API publiques prises en charge. Ne duplique pas la boucle de pagination native et n’intercepte pas ses méthodes privées par monkeypatch en production.
1. Sépare les contrôles réellement redondants des protections non couvertes par le natif. Documente la preuve de chaque suppression envisagée.
1. Si une simplification sûre est possible, réalise-la dans un diff limité, en préservant les contrats des consommateurs et les exceptions utiles.
1. Si elle ne l’est pas, conserve la validation de cohérence, réduis seulement les répétitions démontrées et expose les limites de l’optimisation. Une conclusion « optimisation différée » doit être accompagnée de preuves précises.

## Invariants à maintenir dans la réalisation

- Une erreur de récupération ne doit jamais être remplacée par une liste vide prétendument complète.
- Une réponse 304 signifie réutiliser le snapshot pertinent, pas remplacer la table par zéro ligne.
- Une interruption, un token absent, un scope insuffisant, un 403 ou une limite de taux ne doivent pas déclencher la suppression des lignes absentes.
- Un échec de sonde n’est pas une preuve positive de cohérence. Fais remonter l’incertitude au niveau qui décide d’une réconciliation destructive.
- Les différences entre résultat réellement vide, données non modifiées et résultat incomplet doivent rester explicites.
- Les écritures et les suppressions restent bornées au bon user/character/corporation et à la bonne catégorie de snapshot.
- Les chemins `force_refresh=True` et cache désactivé doivent être examinés séparément : ne leur attribue pas une garantie qu’ils n’ont pas.
- Si un résultat est refusé, les anciennes données restent disponibles et le mécanisme existant de reprise contrôlée est respecté.
- Les logs n’exposent ni tokens ni payloads complets et distinguent le refus de cohérence d’une panne de transport.

## Tests de comportement attendus

Crée ou adapte uniquement des tests utiles à ces garanties, avec réponses, horloge et cache simulés. Commence par les suites ciblées existantes, puis élargis aux synchronisations modifiées selon `TESTING.md`.

1. Une page et plusieurs pages complètes : résultat correct, sans seconde collecte redondante démontrée.
1. Toutes les pages répondent 304 : aucune suppression de données et maintien du snapshot existant.
1. Mélange de pages modifiées et non modifiées : traitement natif respecté, sans liste tronquée acceptée.
1. Dates `Last-Modified` différentes : refus explicite avant réconciliation destructive.
1. En-tête absent, nombre de pages invalide ou changeant : contrat actuel caractérisé et politique documentée et testée. Toute modification de ce contrat doit être présentée comme une correction fonctionnelle, sans promettre une preuve que les données ESI ne permettent pas d'établir.
1. Exception sur une page intermédiaire, une sonde ou la dernière page : aucune suppression des anciennes lignes.
1. Limites 420/429 et erreur 5xx : résultat non complet et reprise sans duplication applicative.
1. Résultat réellement vide et complet : suppression autorisée seulement dans le périmètre attendu.
1. Cache chaud, cache expiré, cache désactivé et force refresh : comptages distingués et garanties explicites.
1. Deux corporations ou deux personnages : aucun effacement croisé et aucun mélange de réponses.
1. Snapshot refusé après une précédente réussite : données et drapeaux de succès précédents préservés correctement.

Fais vérifier au moins un comportement de suppression sur des modèles en base de test : un mock de retour de liste ne suffit pas à prouver la préservation des données.

## Isolation et limites d’intervention

Vérifie les settings effectifs avant toute exécution ; `runtests.py` force ses settings. Utilise uniquement une base jetable, un cache de test et un broker isolé. Les signaux de migration et de sauvegarde ne doivent pas publier sur les services actifs. Les fixtures OpenAPI doivent être locales et les connexions ESI/OAuth/notifications réelles bloquées.

Ne modifie ni les paquets installés ni les sources sous `site-packages`, et n’effectue aucun nettoyage global de Redis ou du worktree. Ne lance pas les tâches de réparation sur l’installation active. Aucun redémarrage, commit, push ou déploiement n’est demandé. N’exécute pas un formatage global qui modifierait mes travaux existants.

## Critères d’acceptation et compte rendu

- Toute réduction de contrôle est justifiée par une garantie native équivalente et un test.
- Aucune réconciliation destructive ne considère une réponse interrompue ou douteuse comme un snapshot complet.
- Les gains sont exprimés par scénario mesuré, sans promettre une réduction universelle des appels ESI.
- Le code ne dépend pas d’un hook privé fragile ajouté uniquement pour ce chantier.
- Les contrôles hors réseau sont distingués d’une qualification réelle de pagination ESI non exécutée.

Termine par le flux final, les fichiers modifiés, les garanties conservées, les mesures avant/après, les résultats des tests et les limites restantes. Réévalue les avantages et les inconvénients du résultat livré. Si la simplification doit attendre un contrat natif supplémentaire, indique exactement lequel et conserve la protection existante.

## Validation des usages dans Chrome

Avant de clore ce chantier, lis et applique `indy_hub/temporary_prompt/VALIDATION-CODE-USAGE-CHROME.md`. En complément des tests du code, exécute dans une vraie session Chrome les parcours consommateurs concernés, avec des comptes synthétiques sur une instance de test isolée. Ne remplace pas les vues locales à qualifier par des réponses simulées ; seules les frontières externes restent simulées. Ne réutilise ni le port ni la session de l'installation active. Si seul Chromium est disponible, indique-le explicitement.

Consigne le navigateur et sa version, les scénarios et rôles exécutés, les résultats, les erreurs console/réseau/serveur et les captures utiles dans un dossier temporaire sans secrets. Un navigateur non fonctionnel, un cas non exécuté ou une assertion en échec doivent être rapportés comme tels : les tests unitaires seuls ne permettent pas de déclarer les usages concernés validés. Aucun appel ESI/OAuth réel ou modification de l'installation active n'est autorisé par cette validation.
