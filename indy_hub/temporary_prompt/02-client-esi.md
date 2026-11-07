# Prompt 02 — Simplifier le client ESI autour de django-esi

Je veux simplifier la couche de compatibilité ESI d’Indy Hub et l’aligner sur les API publiques de django-esi, en conservant les comportements métier et les contrats utilisés par les vues et les tâches.

Travaille dans `/home/aadev/indyhub/aa-Indy_Hub`. Ce prompt autorise l’analyse, les modifications ciblées du dépôt et leur validation isolée. Il ne demande aucune installation ni aucun déploiement.

## Objectif et point de départ à revérifier

L’audit a relevé dans `indy_hub/services/esi_client.py` une résolution dynamique des noms d’opérations, des essais avec objet Token puis chaîne d’access token, deux essais très proches avec cette chaîne et un rattrapage large de `TypeError` qui peut relancer l’exécution.

Ce constat n’est pas une description garantie de l’état actuel : je réalise d’autres mises à jour en parallèle. Le dépôt observé lors de la préparation de ce prompt exigeait déjà `django-esi>=10.1,<11`. N’applique pas une nouvelle hausse de version pour refaire ce correctif et ne réintroduis pas une ancienne contrainte.

La cible est une façade métier courte : sélection du token et du périmètre, contrôles propres à Indy Hub, appel OpenAPI explicite, puis normalisation utile du résultat et des erreurs.

## Référence AA et limites de la simplification

Compare le fournisseur d'`allianceauth/eveonline/providers.py` et, comme exemples facultatifs, `metenox/esi/providers.py` et `corptools/task_helpers/char_tasks.py`. Les points communs utiles sont un fournisseur partagé filtré, des opérations explicites, un objet Token et des conversions métier localisées. Ces exemples ne justifient ni de dépendre de ces modules ni de recopier leurs choix de reprise.

Le fournisseur propre à Indy Hub est déjà une convention correcte. Le nombre de lignes n'est pas un critère d'acceptation. Livre d'abord le retrait des essais redondants, puis l'alignement des seules opérations concernées ; une réécriture de toute la façade n'est pas demandée.

## Avantages attendus

- Réduire les branches de compatibilité qui doivent être comprises et maintenues.
- Utiliser directement les contrôles de scopes et le renouvellement fournis par django-esi quand un objet `Token` lui est transmis.
- Préserver la vérification d'éligibilité avant de restituer des données sensibles, y compris lorsque le transport sert une réponse depuis son cache.
- Éviter qu’une erreur interne ou de signature déclenche un second essai applicatif imprévu.
- Rendre les opérations, les paramètres et les dépendances de version plus faciles à vérifier.
- Faciliter les tests et les futures mises à jour du transport sans multiplier les clients.

## Inconvénients et risques

- Des tests ou consommateurs internes peuvent dépendre de signatures ou d’exceptions historiques de la façade.
- Les réponses Pydantic ne sont pas nécessairement identiques aux dictionnaires attendus par les services métier.
- Une suppression de fallback sans preuve peut casser une version encore déclarée compatible.
- Une refonte trop large peut mélanger ce chantier avec les limites, la pagination ou la sélection des rôles.
- Moins de code ne prouve pas moins de requêtes réseau : les reprises natives du transport doivent être distinguées des essais ajoutés par Indy Hub.

## Préparation obligatoire

1. Lis `AGENTS.md`, puis `docs/codex/INDEX.md`.
1. Lis `ESI.md`, `PERMISSIONS.md`, `CELERY.md`, `CACHE.md`, `RISK_AREAS.md`, `TESTING.md` et `CONFIGURATION.md` sous `docs/codex/`.
1. Relève `git status --short` et les diffs existants. Préserve les modifications utilisateur, y compris dans les fichiers concernés.
1. Relis les métadonnées, la version de django-esi et les chemins de modules avec `/home/aadev/venv-v5/bin/python`, après avoir confirmé que cet interpréteur existe.
1. Ne prends pas le `.venv` du dépôt comme référence : il a historiquement contenu Alliance Auth 4. Ne modifie ni cet environnement ni le V5 actif.
1. Compare le code réellement présent avec les sources locales de django-esi. Une introspection ne doit pas matérialiser un client qui téléchargerait une spécification OpenAPI distante.
1. Si le correctif est déjà présent, vérifie ses garanties et réalise uniquement ce qui manque ; ne réécris pas un changement équivalent.

## Périmètre

Examine en priorité :

- `indy_hub/services/esi_client.py`, notamment `_call_authed`, `_resolve_operation`, les adaptations de payload et les méthodes de récupération ;
- `indy_hub/services/providers.py` et sa liste d’opérations ;
- les consommateurs et tests correspondant aux méthodes réellement modifiées ;
- les notes ESI et les notes de compatibilité concernées.

Conserve l’API publique utile de la façade et son singleton partagé. Utilise le fournisseur filtré propre à Indy Hub ; ne remplace pas celui-ci par le fournisseur global d’Alliance Auth et ne crée pas un fournisseur par opération.

Les budgets métier, la cohérence des snapshots, les verrous, les clés QueueOnce, les règles de corporation et la déduplication des tâches restent inchangés dans ce chantier. N’implémente pas ici une refonte de `market_prices.py`, de Fuzzwork, des profils OAuth ou des notifications.

## Travail demandé

1. Dresse un tableau des branches de compatibilité existantes : utilité, versions qui les nécessitent, consommateurs et tests.
1. Vérifie le chemin natif de transmission d’un objet `Token`, l’authentification et la validation des scopes dans la version prise en charge.
1. Retire les accès redondants à `valid_access_token()` uniquement quand le contrat natif assure la même garantie au bon moment.
   Point à vérifier explicitement : dans la version 10.1 inspectée, `_validate_token_scopes()` et `valid_access_token()` se trouvent sur le chemin de requête réseau ; une réponse de cache peut éviter ce chemin. Le simple passage de l'objet Token n'est donc pas une preuve de validation à chaque lecture. Conserve les sélections et contrôles nécessaires avant cache, et documente la différence entre autorisation locale, validité OAuth et présence de données en cache. N'ajoute pas une validation OAuth aux écrans passifs pour compenser.
1. Supprime les essais répétés avec la chaîne brute lorsque leur inutilité est démontrée. Ne contourne pas la validation native des scopes.
1. Remplace les récupérations d’opération spéculatives par les noms OpenAPI explicites supportés. Vérifie chaque opération dans la whitelist ; ne l’élargis pas à des tags entiers.
   Partage le fournisseur, pas une instance d'opération mutable conservant les paramètres d'un autre appel. Ne crée pas un nouvel état global d'opération ou de token pour simplifier les méthodes.
1. Aligne les paramètres de corps et de chemin sur les signatures réelles, notamment pour `PostUniverseNames`. Évite le couple « essayer `ids`, puis essayer `body` après toute exception ».
1. Utilise `.result()` pour une réponse unique et `.results()` pour les ressources paginées selon le contrat natif. Préserve les en-têtes nécessaires aux consommateurs.
   Si le code actuel attend une liste à un élément pour une réponse unique, conserve cette API via un adaptateur explicite ou ajuste uniquement ses consommateurs identifiés. Le passage de `.results()` à `.result()` ne doit pas changer silencieusement la forme retournée.
1. Ne capture pas un `TypeError` général pour réexécuter une opération : une erreur levée après le début de l’opération doit remonter sans nouvel essai applicatif caché.
1. Conserve les exceptions métier effectivement consommées et documente leurs correspondances avec les exceptions natives, sans masquer les causes.
1. Préserve les conversions nécessaires pour les snapshots et les types numériques ; évite les adaptateurs génériques sans consommateur démontré.
1. Préserve les décisions de token les plus récentes, les filtres user/character/corporation, les rôles et les listes de personnages autorisés. Un helper natif qui choisit simplement le premier token n’est pas automatiquement équivalent.
1. Actualise seulement la documentation touchée par la réalisation finale.

## Validation attendue

Commence par les tests les plus ciblés décrits dans `TESTING.md`, puis exécute les vérifications plus larges justifiées par le périmètre. Retrouve les noms actuels avec `rg` ; ne suppose pas que les labels de tests de l’audit sont encore exacts.

Vérifie au minimum :

- transmission de l’objet `Token` et maintien des contrôles natifs ;
- token absent, scopes manquants et renouvellement simulé en réussite ou en erreur ;
- cache natif chaud/froid, token expiré ou scopes insuffisants : les contrôles nécessaires ne disparaissent pas avec la requête réseau ;
- résultat unique, liste paginée et normalisation des objets de réponse ;
- 304, 401, 403, 420, 429 et 5xx sans modification involontaire de leur signification ;
- une erreur interne de type `TypeError` ne provoque pas un second appel logique ;
- un appel logique normal n’utilise pas une seconde voie avec chaîne brute ;
- les opérations déclarées restent présentes après filtrage d’une spécification locale minimale ;
- les tests métier des consommateurs impactés continuent de passer.

Les assertions de nombre d’appels doivent viser la couche applicative pertinente. Ne désactive pas les reprises documentées du transport natif pour obtenir artificiellement un compteur égal à un.

Ne considère pas un test qui remplace toute la façade par un mock comme une qualification du contrat django-esi. Couvre au moins la frontière native avec une spécification locale minimale ou les outils de test compatibles avec la version installée. Ne recopie pas les anciens helpers `app_utils.esi` : la version examinée les réserve au client Bravado et ne les prend pas en charge pour AA 5/OpenAPI. La date de compatibilité ESI testée reste stable ; ne l'aligne pas automatiquement sur celle de la dépendance ou sur la date du jour dans ce chantier.

Tous les tests doivent utiliser une base jetable, un cache isolé et un broker sans accès aux services actifs. Lis la configuration du runner avant son lancement : `runtests.py` impose ses settings. Ne lance pas les tests avec les settings de l’installation active. Bloque les appels ESI/OAuth et les notifications réelles ; emploie des fixtures locales et des mocks aux frontières. Évite un mock global de `Task.apply_async` incompatible avec les tests qui utilisent `autospec`.

## Limites d’intervention

Ne modifie pas les migrations publiées, les paquets installés, les fichiers sous `site-packages`, la base active, Redis actif ou les processus web/Celery. Ne lance ni OAuth, ni appel ESI réel, ni redémarrage. Aucun commit, push, déploiement ou nettoyage global du worktree n’est demandé. N’exécute pas de commande de formatage global qui modifierait mes travaux en cours.

## Critères d’acceptation et compte rendu

- Les branches retirées ont une justification fondée sur le contrat supporté, pas seulement sur leur ancienneté apparente.
- Les consommateurs conservent leurs données, leurs erreurs utiles et leurs garanties d’autorisation.
- Aucun budget, verrou ou contrôle de snapshot n’a été supprimé dans ce chantier.
- Les contrôles exécutés et leurs limites sont distingués des validations ESI réelles non exécutées.

Termine par les fichiers modifiés, les simplifications réalisées, les résultats des tests et les points restant à qualifier. Réévalue les avantages et les inconvénients à partir du changement réel. Si une branche doit être conservée, explique précisément pourquoi. Si aucune modification n’est nécessaire, fournis les preuves de cette conclusion sans fabriquer un diff.

## Validation des usages dans Chrome

Avant de clore ce chantier, lis et applique `indy_hub/temporary_prompt/VALIDATION-CODE-USAGE-CHROME.md`. En complément des tests du code, exécute dans une vraie session Chrome les parcours consommateurs concernés, avec des comptes synthétiques sur une instance de test isolée. Ne remplace pas les vues locales à qualifier par des réponses simulées ; seules les frontières externes restent simulées. Ne réutilise ni le port ni la session de l'installation active. Si seul Chromium est disponible, indique-le explicitement.

Consigne le navigateur et sa version, les scénarios et rôles exécutés, les résultats, les erreurs console/réseau/serveur et les captures utiles dans un dossier temporaire sans secrets. Un navigateur non fonctionnel, un cas non exécuté ou une assertion en échec doivent être rapportés comme tels : les tests unitaires seuls ne permettent pas de déclarer les usages concernés validés. Aucun appel ESI/OAuth réel ou modification de l'installation active n'est autorisé par cette validation.
