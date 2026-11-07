# Revue des conventions Alliance Auth et des prompts

Analyse du 7 novembre 2026. Ce document explique la révision des prompts ; il ne demande pas d'exécuter tous les chantiers. Seuls les prompts et leurs index ont été modifiés pendant cette revue.

## Conclusion

Les huit prompts initiaux décrivaient des améliorations pertinentes, mais ils n'étaient pas optimaux sur quatre points : certains périmètres étaient trop larges, quelques garanties de cycle de vie des tokens étaient insuffisamment précises, deux petits refactorings paraissaient obligatoires et deux chemins d'intégration partagée manquaient.

Les huit fichiers sont conservés et renforcés. Deux prompts supplémentaires traitent l'administration User/Group et les prix publics ESI. Les modifications applicatives restent à réaliser séparément, si elles sont encore nécessaires au moment du lancement.

## Références et méthode

L'ordre de confiance utilisé est : contrat du dépôt et données à préserver, code des versions réellement importées, documentation officielle compatible, puis exemples d'autres modules. Une préférence de style ou un exemple trouvé dans une autre app n'est pas une règle imposée à tous les modules AA.

Références inspectées localement :

| Référence              | Version observée             | Ce qui a été comparé                                                                                                     |
| ---------------------- | ---------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Alliance Auth          | 5.2.0 dans le module importé | Fournisseur ESI, hooks, décorateurs, modèles d'identité, proxys et formulaires admin, notifications.                     |
| django-esi             | 10.1.0                       | Gestionnaire des tokens, scopes, callbacks, signaux disponibles, client OpenAPI, pagination, cache, limites et reprises. |
| allianceauth-app-utils | 1.32.1                       | Validation des réglages et limites des anciens helpers ESI.                                                              |
| django-eveonline-sde   | 0.2.0                        | Modèles de types et d'univers, champs de noms et traduction.                                                             |
| django-solo            | 2.5.1                        | Clé du singleton, `get_solo()`, écritures et cache.                                                                      |
| CharLink               | 1.14.0 dans son code source  | `LoginImport`, callback après OAuth, annotation de couverture et permissions.                                            |
| CorpTools              | 3.2.0 dans son code source   | Fournisseur OpenAPI et appels de tâches avec objet Token.                                                                |
| aa-metenox             | 2.1.0 dans son code source   | Fournisseur filtré, scopes, `.result()`/`.results()`, désactivation d'un propriétaire métier et notification AA.         |

Ces modules ont servi de références en lecture seule ; aucun audit fonctionnel complet de leurs propres défauts n'est revendiqué. Leur présence ne justifie pas une nouvelle dépendance obligatoire d'Indy Hub. Certaines métadonnées de distributions anciennes coexistent dans le venv : les versions et chemins réellement importés doivent être revérifiés, sans nettoyage automatique des paquets.

La documentation officielle recommande un fournisseur réutilisé et filtré et décrit la pagination native, le passage d'un objet Token, le cache et les limites. Elle ne prescrit pas de supprimer toute façade métier. Voir [le client OpenAPI django-esi](https://django-esi.readthedocs.io/en/latest/developer/openapi31.html).

Le guide AA décrit des applications Django autonomes et une séparation de responsabilités adaptable. Indy Hub est une application métier ; il n'a pas à devenir un `ServiceHook` d'authentification externe simplement pour imiter un service Discord ou Mumble. Voir [l'intégration des services](https://allianceauth.readthedocs.io/en/latest/development/custom/integrating-services.html).

L'[ancien exemple d'app consulté](https://apps.allianceauth.org/apps/detail/aa-example-plugin) affiche encore une dépendance AA 4. Ses commandes de création initiale et de suppression de migrations ne sont pas une procédure de refactoring pour Indy Hub déjà installé. Ne pas les appliquer à ce dépôt.

## Comparaison par responsabilité

| Domaine                 | État observé dans Indy Hub                                                                  | Évaluation et conséquence                                                                                                                                                                                                |
| ----------------------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Hooks et montage        | `hooks.register`, `MenuItemHook`, `UrlHook`, namespace `indy_hub`.                          | Déjà cohérent avec AA et les exemples inspectés. Pas de réécriture de hooks à ajouter aux prompts.                                                                                                                       |
| Templates et assets     | Base héritant de `allianceauth/base-bs5.html`, fichiers propres namespacés.                 | Bonne base. Les bundles et partiels AA sont des outils à réutiliser quand ils correspondent au besoin ; ce n'est pas une raison de remplacer toute l'interface ou ses interactions métier.                               |
| Logs et réglages        | `get_extension_logger`, module `app_settings`, validation locale stricte.                   | L'organisation est classique. Le validateur app-utils rabat certaines valeurs ou utilise un défaut, alors qu'Indy Hub lève `ImproperlyConfigured` : pas de remplacement aveugle entre deux contrats différents.          |
| Permissions             | Permissions Django/AA et décorateurs locaux avec suivi d'usage.                             | Les wrappers ne sont pas un doublon purement inutile. Garder les réponses, CSRF, retours et le suivi avant toute extraction.                                                                                             |
| Identités               | Réutilisation de `EveCharacter`, `EveCorporationInfo` et `CharacterOwnership`.              | Correct. Pas de nouveau miroir d'identités à créer. Une conversion générale des FK `User` ne fait pas partie des simplifications démontrées.                                                                             |
| Snapshots               | Blueprints, jobs, assets, rôles, skills, contrats et état industriel propres.               | Ce stockage n'est pas fourni par django-esi. Ne pas supprimer ces tables au nom d'une normalisation AA ni dépendre de CorpTools pour les remplacer.                                                                      |
| Cycle des tokens        | Déduplication et suppressions métier sur `esi.Token` dans les signaux et certaines vues.    | Responsabilités partagées à corriger en priorité ; prompt 01 renforcé.                                                                                                                                                   |
| Fournisseur ESI         | Fournisseur Indy Hub partagé et whitelist d'opérations.                                     | Déjà conforme. Les essais de compatibilité de la façade peuvent être réduits ; prompt 02 recentré.                                                                                                                       |
| Limites et reprises     | Protection native plus budgets, cooldowns et reprises propres.                              | Simplification conditionnée à l'équivalence exacte. Le budget métier n'est pas un bucket ESI ; prompt 03 précisé.                                                                                                        |
| Pagination              | `.results()` natif puis validation complémentaire des dates.                                | Le natif ne démontre pas une garantie identique pour toutes les pages. Prompt 04 conditionnel ; conserver une protection vaut mieux qu'une optimisation non prouvée.                                                     |
| OAuth et CharLink       | Utilisation de `sso_redirect`/callback natifs, profils et vérifications répétés.            | Le transport n'est pas réimplémenté. Centraliser les profils sans élargir les scopes et vérifier la cohérence des deux prédicats de couverture ; prompt 05 renforcé.                                                     |
| Routes historiques      | Quatre contrôleurs redirigent les anciennes simulations.                                    | Aucun défaut de conformité AA démontré. Prompt 06 facultatif, à gain modeste.                                                                                                                                            |
| Singleton               | `MaterialExchangeSettings.get_solo()` utilise la première ligne ou crée une ligne.          | django-solo peut améliorer le contrat. Le coût de compatibilité historique doit justifier le changement ; prompt 07 facultatif.                                                                                          |
| Accès SDE               | SQL et introspection pour maintenir l'anglais et d'anciennes variantes.                     | Un accès ORM explicite peut réduire la complexité. Les modèles supportés, langues et filtres doivent être préservés ; prompt 08 découpé.                                                                                 |
| Administration partagée | Enregistrement des modèles concrets User/Group en plus des proxys AA.                       | Lacune du plan initial et risque d'interférence sur les droits. Nouveau prompt 09 prioritaire.                                                                                                                           |
| Prix publics ESI        | Appel `requests` direct vers l'endpoint des prix CCP.                                       | Seconde voie de transport ESI, hors de la façade partagée. Nouveau prompt 10 ciblé ; Fuzzwork reste un service distinct.                                                                                                 |
| Notifications           | Modèle Notification AA déjà utilisé, routage et digest propres au métier.                   | Ne pas remplacer la logique métier par une simple notification native ni changer les modes `aa_only`/Discord/`both` comme effet de bord d'un refactoring.                                                                |
| Tests et packaging      | Projet de test dédié, tox et outils de qualité ; réglages pouvant utiliser Redis et broker. | Relire l'isolation réelle avant exécution. Les prompts sont des documents de travail ; leur emplacement dans le package ne vaut pas décision de les distribuer. Ne pas lancer une refonte du packaging pour cette revue. |

Les partiels et bundles natifs sont décrits dans [la documentation des templates AA](https://allianceauth.readthedocs.io/en/latest/development/custom/framework/templates.html). Ils constituent une référence d'intégration, pas une obligation de supprimer les composants propres au craft planner.

## Observations déterminantes

### Token créé et scopes finalisés sont deux événements différents

Dans `esi/managers.py`, le gestionnaire crée d'abord le token, ajoute les scopes M2M puis réutilise éventuellement un équivalent. `esi/signals.py` expose des statistiques de requêtes, pas un événement public de finalisation de ce parcours.

Un `post_save`, un `post_add` ou un `on_commit()` ne prouvent pas à eux seuls que tous les scopes sont connus. En autocommit, le callback peut partir entre deux ajouts. Le prompt 01 demande désormais une preuve sur ce scénario et sépare retrait des suppressions métier et adaptation des déclencheurs. Il ne demande pas d'inventer un signal natif.

Cette limite d'`on_commit()` est également décrite par [Django 5.2](https://docs.djangoproject.com/en/5.2/topics/db/transactions/#performing-actions-after-commit). Elle interdit de déduire la fin d'un parcours OAuth de la seule exécution d'un callback de transaction.

### Le cache natif peut éviter le chemin de validation réseau

Dans `esi/openapi_clients.py` inspecté, `.result()` peut utiliser une réponse de cache sans passer par `_make_request()`. Or c'est ce dernier chemin qui appelle les contrôles de scopes et `valid_access_token()`. Le passage d'un objet Token ne démontre donc pas une validation OAuth à chaque restitution depuis le cache.

Le prompt 02 précise maintenant que seuls les contrôles réellement redondants peuvent disparaître. Les filtres user/character/scopes/rôles restent nécessaires ; la politique de validité et d'autorisation doit aussi être caractérisée pour le cache chaud. Il ne faut ni exposer davantage de données par simplification ni transformer un affichage passif en renouvellement OAuth systématique.

### Publication unique et exécution unique ne sont pas synonymes

Les reprises HTTP du transport, les republications Celery et les redélivrances du broker sont trois niveaux. Le prompt 03 demandait une formulation trop absolue sur les doubles reprises. Il cible maintenant une seule intention de republication par exécution et conserve l'idempotence requise par la livraison au moins une fois.

La [documentation Celery](https://docs.celeryq.dev/en/stable/userguide/tasks.html) distingue les tâches idempotentes et les comportements d'acquittement ; les options de l'installation doivent être vérifiées avant de conclure sur ses redélivrances effectives.

### La centralisation des profils n'est pas une union globale des scopes

Les parcours personnels, corporation et Material Exchange ne demandent pas tous les mêmes droits. Le contrôle Python CharLink inspecté peut valider activement un token alors que son annotation SQL affiche une couverture enregistrée. Le prompt 05 demande de caractériser puis résoudre explicitement cet écart, sans mettre de validation OAuth dans un simple affichage.

### Une modification de cohérence des snapshots est une modification fonctionnelle

Le natif gère les pages et les cas ETag, mais la réponse agrégée n'expose pas nécessairement les en-têtes de chaque page. Retirer le contrôle complémentaire peut affaiblir la protection ; le durcir peut retarder la fraîcheur. Le prompt 04 distingue désormais optimisation équivalente, correction fonctionnelle et optimisation différée.

### L'admin concret n'est pas le proxy AA

Le contrôle isolé du registre avec les applications AA et Indy Hub a trouvé :

| Modèle administré               | Contrôleur observé                              | Champs en lecture seule pour un objet existant et un utilisateur non superutilisateur synthétique |
| ------------------------------- | ----------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `authentication.User` — proxy   | `allianceauth.authentication.admin.UserAdmin`   | `date_joined`, `last_login`, `is_staff`, `is_superuser`, `user_permissions`.                      |
| `groupmanagement.Group` — proxy | `allianceauth.groupmanagement.admin.GroupAdmin` | `permissions`.                                                                                    |
| `auth.User` — concret           | `indy_hub.admin.IndyHubUserAdmin`               | Aucun dans ce contrôle.                                                                           |
| `auth.Group` — concret          | `indy_hub.admin.IndyHubGroupAdmin`              | Aucun dans ce contrôle.                                                                           |

Le contrôle n'a utilisé aucun compte réel et n'a exécuté aucune requête en base. Il démontre la coexistence et la différence des restrictions, pas à lui seul un accès effectif exploitable. Le prompt 09 demande une matrice de permissions concrètes/proxy et des tests GET/POST réels sur des objets jetables avant d'annoncer une conclusion de sécurité.

## Évaluation et corrections des prompts

| Prompt        | Évaluation initiale                                               | Amélioration apportée                                                                                                          |
| ------------- | ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| 01 Tokens     | Prioritaire, mais finalisation des scopes insuffisamment précise. | Référence au cycle natif, autocommit explicite, sous-diffs, re-sélection de l'autorisation finale et pas de nouveau scheduler. |
| 02 Client     | Pertinent, mais risquait de devenir une réécriture de façade.     | Exemples AA 5, étapes bornées, préservation des formes de réponses, tests de frontière native, date de compatibilité stable.   |
| 03 Limites    | Pertinent, mais garantie de reprise trop absolue.                 | Distinction HTTP/tâche/broker et conservation de l'idempotence ; mécanismes supprimés uniquement sur preuve.                   |
| 04 Pagination | Très sensible et non nécessairement rentable.                     | Statut conditionnel, séparation des corrections fonctionnelles et possibilité explicite de conserver l'approche actuelle.      |
| 05 Profils    | Utile, mais risque de construire un moteur générique inutile.     | Module de profils minimal, contrat CharLink vérifié, pas de refonte des décorateurs ni d'élargissement implicite.              |
| 06 Legacy     | Valide mais gain faible.                                          | Statut facultatif et conservation possible si une extraction nuit à la lisibilité.                                             |
| 07 Singleton  | Standard possible, mais migration pas automatiquement justifiée.  | Décision fondée sur bénéfice/risque et vérification paquet/app/configuration distincts.                                        |
| 08 SDE        | Utile avec risque de mélanger types et univers.                   | Étape limitée aux noms de types, ancien chemin univers séparé, tests de résultat plutôt que sous-chaînes SQL.                  |
| 09 Admin      | Absent.                                                           | Ajout d'une correction de coexistence et de responsabilité, sans patch du registre ou du formulaire AA.                        |
| 10 Prix ESI   | Absent car explicitement exclu du prompt 02.                      | Ajout d'un transport public ciblé, avec cache, 304, Decimal et contrat timeout préservés.                                      |

Pour le prompt 08, le comportement de réécriture est décrit dans [la documentation du manager multilingue](https://django-modeltranslation.readthedocs.io/en/latest/usage.html#multilingual-manager). Ce mécanisme s'applique aux modèles effectivement enregistrés : le test doit refléter cette configuration et non seulement compiler une requête sur un manager simplifié.

Principaux points d'entrée Indy Hub à relire lors du lancement : [signaux](../signals.py), [client ESI](../services/esi_client.py), [profils utilisateur](../views/user.py), [CharLink](../thirdparty/charlink_hook.py), [administration](../admin.py), [prix publics](../services/market_prices.py), [noms SDE](../utils/eve.py) et [modèles](../models.py).

## Ordre conseillé

1. **09 et 01** : administration partagée, puis tokens partagés ; corrections d'interférence sur les composants communs.
1. **02** : simplification progressive de la façade ESI.
1. **05 et 10** : profils d'autorisation, puis raccordement des prix publics à la façade stabilisée.
1. **03** : consolidation des limites démontrées redondantes.
1. **08** : réduction des accès SQL aux noms de types avec les tests de langue.
1. **04** : seulement si les preuves permettent une optimisation sûre.
1. **06 et 07** : facultatifs, en dernier si le gain de maintenance justifie l'intervention.

Lancer un seul chantier à la fois. Les numéros existants restent stables pour ne pas casser les liens ou les sessions déjà ouvertes. Un chantier déjà corrigé se termine par sa vérification ; aucun diff artificiel n'est nécessaire.

## Vérifications réalisées et limites

- Lecture des huit prompts, des références natives et d'un échantillon de modules AA locaux, avec comparaison des contrats réels.
- Démarrage du projet de test avec les dépendances V5 et inspection du registre admin, sur paramètres isolés, sans base active ni compte réel.
- Contrôle des sources du gestionnaire natif et des signaux disponibles ; absence d'événement de finalisation constatée dans la version examinée.
- Relecture des différences de validation des réglages, formes de réponse, profils CharLink et caches de prix.
- Vérification des documents, liens, chemins et avantages/inconvénients après révision.

La suite applicative complète n'a pas été relancée pour cette modification documentaire. Aucun appel ESI/OAuth/Discord réel, aucune migration, aucune action sur les workers ou le Redis actif n'a été exécuté. Le rapport ne certifie ni tous les modules communautaires ni le fonctionnement de la production. Les preuves de tests rapportées par d'autres travaux dans `docs/codex/TESTING.md` ne sont pas présentées comme de nouveaux résultats de cette revue.

Les prompts sont placés dans le package à la demande de l'utilisateur. Leur inclusion éventuelle dans un wheel ou un sdist n'a pas été qualifiée ici : vérifier le contenu des artefacts sur une construction isolée avant publication, sans déduire qu'un dossier nommé `temporary_prompt` est automatiquement exclu. Aucun réglage Git ou packaging n'a été modifié pour les cacher.
