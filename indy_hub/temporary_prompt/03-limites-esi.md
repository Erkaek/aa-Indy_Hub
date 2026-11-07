# Prompt 03 — Aligner les limites ESI sans supprimer les budgets métier

Je veux simplifier les mécanismes de limites et de reprise ESI d’Indy Hub en utilisant les fonctions natives de django-esi partout où elles assurent réellement la même protection.

Travaille dans `/home/aadev/indyhub/aa-Indy_Hub`. Réalise les modifications nécessaires dans ce périmètre et vérifie-les en environnement isolé. Je ne demande ni installation, ni intervention sur les services actifs.

## Objectif et hypothèse à vérifier

L’audit a observé un chevauchement entre le suivi natif des buckets/error limits de django-esi et les métadonnées d’en-têtes, cooldowns et conversions d’exceptions maintenus par Indy Hub.

Ce chevauchement n’autorise pas à supprimer toutes les protections du module. Un budget de synchronisations par scope, un cooldown après indisponibilité de Tranquility, un refus de structure privée et une limite protocolaire ESI répondent à des besoins différents.

Le dépôt observé à la rédaction déclarait déjà `django-esi>=10.1,<11`, avec tox aligné sur les métadonnées. Revérifie cet état avant d’agir : je poursuis des mises à jour et le résultat attendu n’est pas de refaire une hausse de version déjà réalisée.

## Référence native et décision de changement

Compare `esi/openapi_clients.py`, `esi/rate_limiting.py`, `esi/exceptions.py` et `esi/decorators.py`, en particulier le contrôle avant requête et les reprises du transport. Aucune convention AA n'impose de supprimer un garde-fou métier local. Prépare des sous-changements par mécanisme, avec une preuve avant/après pour chacun, plutôt qu'une réorganisation globale de toutes les tâches.

## Avantages attendus

- Disposer d’une source claire pour les limites imposées par ESI.
- Éviter les calculs concurrents de délai et les reprises applicatives superposées.
- Simplifier les exceptions, les journaux et les diagnostics d’un blocage.
- Bénéficier du suivi natif des groupes de limites et des corrections de django-esi.
- Conserver des budgets Indy Hub lisibles, distincts des limites externes.

## Inconvénients et risques

- Le comportement natif peut différer selon la version, l’opération et les en-têtes disponibles.
- Une unité de délai mal interprétée peut provoquer une rafale immédiate ou un report excessif.
- Une limite locale peut être une protection supplémentaire intentionnelle, sans équivalent natif.
- Des clés de cache déjà utilisées par les workers peuvent survivre à une mise à jour du code.
- Modifier les reprises sans analyser les couches Celery peut produire des tâches en double ou empêcher une reprise nécessaire.
- Les tests simulés ne démontrent pas le comportement d’une flotte réelle de workers et d’un Redis partagé.

## Préparation et état initial

1. Lis `AGENTS.md`, puis `docs/codex/INDEX.md`.
1. Lis intégralement `ESI.md`, `CELERY.md`, `CACHE.md`, `RISK_AREAS.md`, `TESTING.md` et les réglages ESI de `CONFIGURATION.md`.
1. Relève `git status --short` et les diffs existants. Préserve les changements utilisateur et les chantiers déjà terminés.
1. Identifie la version et les sources réellement importables depuis `/home/aadev/venv-v5/bin/python`, sans modifier cet environnement. Ne suppose pas que le `.venv` du dépôt représente AA V5.
1. Lis les sources publiques pertinentes de django-esi : buckets, suivi des limites, exceptions et décorateurs de retry. Évite de matérialiser un client qui téléchargerait une spécification.
1. Recherche les consommateurs de chaque helper Indy Hub avant de proposer une suppression ou un renommage.

## Périmètre à examiner

- `indy_hub/services/esi_client.py` : en-têtes, cooldowns, exceptions, budgets par scope et contrôle de Tranquility.
- Les tâches qui consomment ces exceptions et les helpers de calcul de délai.
- Les décorateurs django-esi et Celery qui entourent réellement les tâches concernées.
- Les réglages, la documentation et les tests strictement liés aux comportements modifiés.

Ne modifie pas les clés QueueOnce, les verrous de propriétaire, le dispatcher minute, les noms des tâches périodiques, les ETA/countdowns de bulk, les fenêtres métier ou les règles de visibilité. Une refonte de ces mécanismes serait un autre chantier.

## Travail demandé

1. Produis d’abord une matrice avec, pour chaque mécanisme : propriétaire, signal déclencheur, portée, clé, TTL, unité du délai, exception et couche responsable de la reprise.
1. Sépare au minimum les limites globales 420, les buckets/groupes 429, les budgets locaux par scope, les indisponibilités 5xx et les refus d’accès aux structures privées.
1. Vérifie comment le client natif lit la spécification et les en-têtes, met à jour son cache et refuse un appel lorsque le bucket est épuisé.
1. Démontre les doublons exacts avant de les retirer. Conserve les protections pour lesquelles l’équivalence n’est pas établie.
1. Pour chaque exception native, confirme si `reset` représente une durée ou une autre valeur. Ne mélange pas secondes restantes, timestamp et date HTTP.
1. Centralise les conversions encore nécessaires dans un point identifié, sans transformer toutes les erreurs en un retry identique.
1. Définis une seule couche responsable de publier la reprise d’une tâche pour chaque erreur. Vérifie l’interaction entre `rate_limit_retry_task`, `self.retry`, `autoretry_for` et les replanifications explicites existantes.
   Distingue la reprise HTTP interne, la publication d'une nouvelle tâche et la redélivrance par le broker. Ne supprime pas les verrous ou l'idempotence pour masquer une redélivrance possible ; les exceptions et les délais doivent aussi être vérifiés avec une opération native locale simulée.
1. Garde une distinction entre « reporter le travail pour une limite » et « marquer le snapshot complet ». Une interruption de synchronisation ne doit jamais autoriser une suppression des lignes manquantes.
1. Préserve les budgets propres à Indy Hub, leurs paramètres et leurs limites par user/character/corporation/scope lorsque ces dimensions existent.
1. Préserve le traitement non destructif d’un 403 d’endpoint : un accès interdit à une structure n’invalide pas globalement un token partagé.
1. Conserve les protections contre les appels répétés à une structure interdite et le backoff d’indisponibilité quand le natif n’offre pas une garantie équivalente.
1. Traite explicitement les données de cache transitoires d’une ancienne version. N’utilise ni `cache.clear()` ni suppression globale des clés ESI pour simplifier la transition.
1. Garde des journaux utiles qui identifient la raison du report et sa portée sans exposer de tokens, de secrets ou de payloads complets.
1. Mets à jour les notes ESI/cache/Celery uniquement pour le comportement réellement livré.

## Cas de validation requis

Commence par les suites ciblées trouvées dans `TESTING.md` et par recherche dans les tests actuels, puis élargis aux consommateurs modifiés. Utilise une horloge simulée ; les tests ne doivent pas dormir réellement pour attendre un reset.

- Réponse 420 : propagation du délai, une seule publication de reprise et aucun appel anticipé.
- Réponse 429 ou bucket déjà épuisé : même portée de protection, délai borné et absence de double retry.
- En-têtes absents, incomplets ou malformés : comportement explicite et conservateur, sans exception masquée.
- Expiration d’un cooldown : reprise possible au bon moment, sans blocage permanent.
- Épuisement d’un budget métier : protection maintenue même si le bucket natif autorise encore des appels.
- Backend de cache indisponible : comportement prévu par la politique actuelle, sans rafale de secours.
- Refus privé 403 : token partagé conservé et pas de reprise globale inutile.
- 5xx/Tranquility indisponible : backoff conservé et reprise attribuée à une couche unique.
- Deux livraisons d’une même tâche et deux périmètres distincts : isolation et idempotence maintenues.
- Synchronisation interrompue : absence de suppression des anciennes lignes pour un résultat incomplet.
- Clés, signatures et réglages de dispatch hors périmètre : aucun changement involontaire.

Utilise une base jetable, un cache temporaire réservé aux tests et un broker isolé. Vérifie les settings effectifs avant de démarrer Django : le runner du dépôt impose ses propres settings. Ne connecte pas les tests au Redis, au broker ou à la base de l’installation active. Des mocks ciblés doivent rester compatibles avec les tests utilisant `autospec`.

## Limites et exclusions

Ne réécris aucune migration publiée. Ne modifie pas les paquets installés, le V5, les services, les réglages de l’installation active ni les sources de django-esi sous `site-packages`. Ne lance aucun appel ESI/OAuth réel et aucune notification. Aucun redémarrage, commit, push, déploiement ou nettoyage global n’est demandé. Ne lance pas un formatage global sur mes fichiers en cours de modification.

Une difficulté à démontrer l’équivalence n’est pas une autorisation de réduire la protection. Dans ce cas, livre les consolidations démontrées, garde le mécanisme incertain et explique la condition nécessaire pour le simplifier ensuite.

## Critères d’acceptation et compte rendu

- Les limites du protocole et les budgets métier ont des responsabilités explicites.
- Chaque mécanisme supprimé dispose d’une preuve d’équivalence et d’un test de comportement.
- Une même exécution de tâche ne déclenche pas deux intentions de republication concurrentes pour la même erreur. Les redélivrances restent possibles et sont traitées par l'idempotence existante ; aucune garantie d'exécution exactement une fois n'est annoncée.
- Aucun verrou métier, périmètre d’autorisation ou contrôle de snapshot n’a été affaibli.
- Les résultats hors réseau sont présentés comme tels, sans prétendre à une qualification ESI réelle.

Termine par la matrice finale des responsabilités, les fichiers modifiés, les protections retirées ou conservées, les résultats de validation et les limites restantes. Réévalue concrètement les avantages et les inconvénients du changement livré. Si l’état actuel est déjà satisfaisant, explique-le avec des preuves et ne crée pas de modification artificielle.

## Validation des usages dans Chrome

Avant de clore ce chantier, lis et applique `indy_hub/temporary_prompt/VALIDATION-CODE-USAGE-CHROME.md`. En complément des tests du code, exécute dans une vraie session Chrome les parcours consommateurs concernés, avec des comptes synthétiques sur une instance de test isolée. Ne remplace pas les vues locales à qualifier par des réponses simulées ; seules les frontières externes restent simulées. Ne réutilise ni le port ni la session de l'installation active. Si seul Chromium est disponible, indique-le explicitement.

Consigne le navigateur et sa version, les scénarios et rôles exécutés, les résultats, les erreurs console/réseau/serveur et les captures utiles dans un dossier temporaire sans secrets. Un navigateur non fonctionnel, un cas non exécuté ou une assertion en échec doivent être rapportés comme tels : les tests unitaires seuls ne permettent pas de déclarer les usages concernés validés. Aucun appel ESI/OAuth réel ou modification de l'installation active n'est autorisé par cette validation.
