# Prompt 01 — Respecter le cycle de vie des tokens partagés

Commence par lire `AGENTS.md` et `docs/codex/INDEX.md`, puis inspecte `git status --short` et les diffs des fichiers concernés. Je travaille peut-être encore sur ce dépôt : les constats ci-dessous sont des pistes à revérifier, pas une description garantie de son état actuel. Préserve intégralement mes modifications et adapte ton intervention au code présent.

Je veux que tu implémentes dans Indy Hub une gestion non destructive de l'éligibilité de ses tokens, en laissant django-esi assurer leur cycle de vie natif. Va jusqu'à une modification locale vérifiée et reviewable ; ne te limite pas à un plan. Si le chantier est déjà réalisé, vérifie-le et explique les écarts éventuels sans refaire le travail.

## Référence AA et découpage du chantier

Compare `esi/managers.py` et `esi/signals.py` dans la version réellement utilisée. Dans django-esi 10.1 inspecté, aucun signal public « token finalisé avec tous ses scopes » n'est fourni : ne l'invente pas. CharLink appelle `LoginImport.add_character(request, token)` après son parcours OAuth, mais il reste optionnel. Le modèle `metenox.Owner.disable()` illustre un refus métier sans suppression de la ligne Token ; son API n'est pas une dépendance à ajouter à Indy Hub.

Commence par retirer les suppressions métier et préserver les filtres d'éligibilité. Traite ensuite les déclencheurs de synchronisation dans un second diff identifiable si leur adaptation est nécessaire. Ne construis pas un ordonnanceur ou une nouvelle table d'état pour remplacer quelques signaux sans nécessité démontrée.

## Résultat attendu

- La création, le renouvellement et la réutilisation des tokens restent assurés par django-esi.
- Un rôle corporation insuffisant pour Indy Hub rend le token inéligible à cette opération, sans supprimer la ligne partagée `esi.Token`.
- Les synchronisations et invalidations Indy Hub partent avec des scopes effectivement disponibles, après validation de la transaction, sans multiplication de tâches.
- Aucun droit métier n'est élargi et aucune autorisation n'est déduite de la simple présence d'un token.

## Avantages recherchés

- Éviter de supprimer une autorisation encore utile à d'autres fonctionnalités d'Alliance Auth.
- Supprimer une seconde implémentation de la déduplication des tokens et ses divergences avec django-esi.
- Réduire les effets de bord des signaux pendant la création d'un token et l'ajout de ses scopes.
- Rendre les refus d'accès Indy Hub explicites, testables et indépendants de la validité OAuth globale.
- Respecter le réglage natif `ESI_ALWAYS_CREATE_TOKEN` au lieu de le contredire implicitement.

## Inconvénients et risques à traiter

- Certains parcours peuvent avoir compté sur la suppression physique pour masquer une autorisation : il faut remplacer cette conséquence par un état d'éligibilité cohérent.
- Les scopes sont une relation M2M : un `post_save` initial ne signifie pas que leur ajout est terminé.
- Les ajouts successifs de scopes peuvent produire plusieurs événements et plusieurs callbacks `on_commit` ; changer seulement le signal ne garantit pas une déduplication.
- En autocommit, `on_commit()` peut s'exécuter immédiatement entre deux ajouts de scopes. Il ne signifie ni « fin du callback OAuth » ni « jeu final de scopes connu ». Vérifie aussi ce cas, sans changer le gestionnaire natif.
- Une nouvelle autorisation peut être remplacée par un ancien token équivalent dans le gestionnaire natif ; une tâche ne doit pas dépendre d'une PK temporaire supprimée par django-esi.
- Les statuts administratifs et caches peuvent conserver un ancien état si leurs invalidations ne suivent pas le nouveau parcours.
- Un cache de rôles vide, périmé ou indisponible ne doit pas être interprété comme une validation positive.

## Périmètre et sources à lire

Lis `docs/codex/ESI.md`, `PERMISSIONS.md`, `CELERY.md`, `CACHE.md`, `RISK_AREAS.md` et `TESTING.md` avant de modifier les signaux ou les publications de tâches.

Inspecte au minimum :

- `indy_hub/signals.py`, notamment `remove_duplicate_tokens`, `enforce_corporation_role_tokens`, les synchronisations et invalidations sur tokens/scopes ;
- `indy_hub/views/user.py`, notamment `_revoke_corporation_tokens` et les statuts corporation ;
- `indy_hub/utils/token_status.py`, `services/admin_user_status.py` et les helpers de sélection des tokens utilisés par les tâches ;
- `indy_hub/thirdparty/charlink_hook.py` et les suites de tests token, scopes, rôles et CharLink existantes ;
- dans la version installée de django-esi : `TokenManager.create_from_code`, `TokenQueryset.equivalent_to`, `require_scopes_exact`, `require_valid`, les signaux et le parcours callback.

L'audit avait identifié une déduplication par `post_save` Indy Hub alors que django-esi crée la ligne avant d'ajouter ses scopes. Reproduis le comportement actuel sur des données temporaires avant d'en tirer une conclusion.

## Contraintes de travail

- Travaille dans `/home/aadev/indyhub/aa-Indy_Hub` ; aucun changement dans un autre module ou dans `site-packages`.
- Relève les métadonnées du Python `/home/aadev/venv-v5/bin/python` et les chemins importés. Le `.venv` du dépôt n'est pas une preuve de l'environnement V5 actif ; ne suppose aucune version d'après un ancien audit.
- Ne mets pas à jour le venv, les dépendances, l'installation déployée, les services ou les processus.
- Aucun vrai échange OAuth, appel ESI, envoi Discord, accès à la base active, publication vers le broker actif ou modification du Redis actif.
- Pour les tests, utilise des paramètres dédiés, une base temporaire, un cache isolé et des publications Celery neutralisées ou dirigées vers un broker de test. Vérifie les effets de `AppConfig.ready()` et des signaux avant le lancement.
- Ne fais ni `git reset`, ni nettoyage global, ni commit, push, déploiement ou redémarrage.
- Ne modifie aucune migration publiée. Ce chantier n'exige normalement pas de changement de schéma.
- Ne désactive pas les protections natives : `require_valid()` peut légitimement renouveler ou supprimer un token invalide sous la responsabilité de django-esi.

## Travail à réaliser

1. Dresse la liste exhaustive des suppressions directes ou indirectes de `Token` déclenchées par Indy Hub ; distingue nettoyage métier, refus de rôle et suppression native après échec OAuth.
1. Cartographie le parcours réel : création de ligne, ajout de chaque scope, résolution des équivalences, callback, transaction puis lancement des tâches.
1. Supprime la déduplication maison si son rôle est intégralement couvert par le mécanisme natif vérifié. N'ajoute pas une autre suppression globale sous une nouvelle forme.
1. Remplace les suppressions liées aux règles Indy Hub par une sélection et des réponses non destructives. Conserve les vérifications de propriétaire, corporation, permission, rôle, scopes et liste de personnages autorisés.
1. Fais suivre les synchronisations par un mécanisme compatible avec le cycle natif et les scopes complets. Réévalue les objets au bon moment plutôt que de retenir un token temporaire.
   Une capacité peut être prête avant les autres : n'exige pas artificiellement tous les scopes de tous les profils. Si le signal ne connaît pas la fin du parcours, re-sélectionne l'autorisation et revérifie les scopes requis dans le consommateur approprié. Ne traite pas un simple `post_add` comme un événement de finalisation globale.
1. Garantis qu'un rollback ne publie rien et que des événements M2M répétés ne multiplient pas les tâches. Réutilise les mécanismes d'idempotence existants sans modifier leurs clés par commodité.
1. Préserve les invalidations des badges, tableaux de bord, pages tokens et `AdminUserStatus`, y compris sur suppression native et modification M2M.
1. Garde les écrans passifs : l'affichage des scopes enregistrés ne doit pas provoquer de renouvellement OAuth.
1. Mets à jour les notes du sous-système et les messages devenus inexacts, sans lancer une refonte du client ESI ou des profils de scopes dans ce même changement.

## Vérifications attendues

Réutilise les tests existants et ajoute les régressions nécessaires au changement de comportement :

- Création d'un nouveau token avant ajout de scopes : aucun ancien token n'est supprimé par Indy Hub.
- Scopes ajoutés un par un puis par lot : traitement correct, absence de publications indues et prise en compte du jeu final.
- Token équivalent réutilisé par django-esi : synchronisation portant sur l'autorisation finale, sans dépendance à une PK supprimée.
- Modes natifs de création des tokens, lorsque pris en charge par les versions déclarées, respectés.
- Mauvais rôle, absence de rôle, rôle périmé ou ESI indisponible : refus ou report conservateur, token conservé.
- Token contenant des scopes pour plusieurs usages : le refus d'une opération Indy Hub ne retire pas ses autres autorisations.
- Utilisateur étranger, mauvaise corporation, personnage non autorisé : accès refusé même si le token existe.
- Transaction annulée : aucune publication ; transaction validée : publication dédupliquée et données visibles.
- Création native hors `atomic()` englobant : les ajouts M2M successifs et la réutilisation d'un équivalent ne donnent pas de fausse garantie de finalisation. Teste également la frontière réelle du gestionnaire natif avec l'échange OAuth simulé, pas seulement un faux signal.
- Échec de publication : aucune exécution réseau de secours dans la requête et aucun faux état de réussite.
- Renouvellement natif invalide : le comportement de django-esi et les invalidations restent fonctionnels, sans réseau réel.
- Listing passif : aucun `require_valid`, renouvellement ou appel ESI ajouté par le changement.

Commence par les suites ciblées identifiées dans le dépôt, puis applique les contrôles plus larges pertinents de `TESTING.md`. Ne contourne pas une erreur en affaiblissant les assertions de permission ou de déduplication.

## Critères d'acceptation

- Aucun chemin Indy Hub ne supprime un token partagé pour déduplication ou inéligibilité à son métier.
- Le cycle de vie natif demeure opérationnel et les règles d'autorisation sont au moins aussi strictes qu'avant.
- Les synchronisations ne dépendent pas de scopes incomplets et restent idempotentes après commit.
- L'idempotence est formulée pour des livraisons Celery au moins une fois : une publication n'est pas une preuve d'exécution exactement une fois. Aucun nouveau token n'est supposé appartenir exclusivement à Indy Hub.
- L'installation active et les données historiques n'ont pas été touchées.
- Le diff contient seulement les changements nécessaires à ce chantier, ses tests et sa documentation.

## Bilan final demandé

Donne les fichiers modifiés, le problème avant/après, les responsabilités laissées à django-esi, les vérifications exactes et leurs résultats. Sépare les réussites, les échecs, les contrôles non exécutés et les limites du dispositif isolé. Réévalue les avantages et inconvénients de cette modification à partir du résultat obtenu. Mentionne les éventuelles étapes de déploiement à prévoir, sans les exécuter.

## Validation des usages dans Chrome

Avant de clore ce chantier, lis et applique `indy_hub/temporary_prompt/VALIDATION-CODE-USAGE-CHROME.md`. En complément des tests du code, exécute dans une vraie session Chrome les parcours consommateurs concernés, avec des comptes synthétiques sur une instance de test isolée. Ne remplace pas les vues locales à qualifier par des réponses simulées ; seules les frontières externes restent simulées. Ne réutilise ni le port ni la session de l'installation active. Si seul Chromium est disponible, indique-le explicitement.

Consigne le navigateur et sa version, les scénarios et rôles exécutés, les résultats, les erreurs console/réseau/serveur et les captures utiles dans un dossier temporaire sans secrets. Un navigateur non fonctionnel, un cas non exécuté ou une assertion en échec doivent être rapportés comme tels : les tests unitaires seuls ne permettent pas de déclarer les usages concernés validés. Aucun appel ESI/OAuth réel ou modification de l'installation active n'est autorisé par cette validation.
