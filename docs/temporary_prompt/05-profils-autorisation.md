# Prompt 05 — Centraliser les profils d'autorisation et leurs scopes

Commence par lire `AGENTS.md` et `docs/codex/INDEX.md`, puis inspecte `git status --short` et les diffs concernés. Je travaille peut-être encore sur les autorisations : vérifie l'état actuel au lieu de rejouer mécaniquement un ancien audit. Préserve mes changements et ceux issus des autres prompts.

Je veux que tu implémentes un référentiel unique, simple et explicite des profils d'autorisation Indy Hub, utilisé par les vues d'autorisation et par CharLink. Réalise le changement local et sa vérification, sans toucher au parcours OAuth fourni par django-esi. Si la centralisation est déjà faite, contrôle sa cohérence et limite-toi aux défauts encore présents.

## Référence AA et niveau d'abstraction

Lis `charlink/app_imports/utils.py` et le parcours `charlink/views.py` installés : `LoginImport` attend une liste de scopes, des contrôles de permission, un callback `(request, token)` et éventuellement une annotation SQL de couverture. Un module de constantes/profils sans effet de bord suffit généralement. N'ajoute ni moteur de politiques, ni endpoint acceptant des scopes arbitraires, ni dépendance obligatoire à CharLink.

Les décorateurs Django/django-esi sont des outils de référence, mais les wrappers Indy Hub ajoutent le suivi d'usage et un contrat de réponse particulier. Leur remplacement général n'appartient pas à ce chantier. Préserve notamment `next`, les réponses 403, la CSRF et les différences entre affichage passif et autorisation active.

## Résultat attendu

- Les jeux de scopes, permissions et propriétés des profils ont une source clairement identifiée.
- Les vues et CharLink utilisent cette source sans recopier des unions divergentes.
- Chaque point d'entrée conserve son contrat actuel : URL, permission, scopes exacts, retour, sélection des personnages et confirmation utilisateur.
- L'abstraction distingue un profil d'autorisation, une capacité métier et la couverture existante ; elle ne transforme pas tout en un profil global trop large.

## Avantages recherchés

- Réduire les oublis quand une opération métier demande un scope nouveau ou différent.
- Expliquer facilement quels accès un bouton d'autorisation demande.
- Comparer et tester les profils personnels, corporation et Material Exchange à partir d'une source commune.
- Réduire la duplication des vues en laissant django-esi gérer redirection SSO et callback.
- Éviter les divergences entre l'intégration CharLink et les parcours natifs Indy Hub.

## Inconvénients et risques à traiter

- Des noms proches ne signifient pas des contrats identiques : l'audit avait relevé un profil `authorize_all` incluant les rôles, différent du profil personnel CharLink.
- Une union aveugle peut augmenter les autorisations demandées à tous les utilisateurs.
- `tokens_required` accepte l'existence de tokens éligibles ; certains écrans Indy Hub vérifient la couverture de tous les personnages. Ces comportements ne sont pas interchangeables.
- Un token contenant seulement une partie des scopes n'est pas nécessairement équivalent à un token couvrant le jeu requis ; respecte la sémantique réelle de chaque consommateur.
- Un registre trop générique peut masquer les permissions ou créer des imports circulaires entre vues, tâches et CharLink.
- Les profils corporation donnent des accès sensibles et doivent conserver leur sélection initiale explicite.

## Sources et périmètre

Lis `docs/codex/PERMISSIONS.md`, `ESI.md`, `FRONTEND.md`, `CACHE.md`, `RISK_AREAS.md` et `TESTING.md`.

Inspecte notamment :

- `indy_hub/views/user.py` : constantes, `authorize_*`, couverture des personnages, `force`, `next` et retour après callback ;
- `indy_hub/views/material_exchange_config.py` : demandes de tokens partiels et de tous les scopes ;
- `indy_hub/thirdparty/charlink_hook.py` : scopes, contrôles de permission, annotations de couverture et choix initiaux ;
- `indy_hub/tasks/industry.py`, `services/industry_skills.py` et les services qui définissent déjà des scopes nécessaires ;
- `indy_hub/urls.py`, les templates et scripts qui construisent les URL d'autorisation ;
- `esi.decorators.tokens_required`, `esi.views.sso_redirect` et le contrat CharLink effectivement installé, en lecture seule.

Ce chantier ne doit pas modifier les règles de partage des catalogues corporation, la politique d'éligibilité des rôles ou les paramètres par défaut de notification.

## Contraintes de travail

- Les modifications sont autorisées uniquement dans le dépôt Indy Hub, avec les tests et notes nécessaires à ce chantier.
- Vérifie les versions et chemins réels avec `/home/aadev/venv-v5/bin/python`. Ne te base pas sur le `.venv` historique du dépôt ni sur des versions citées dans une ancienne conversation.
- Aucun changement dans le venv, les autres modules, les services, la base active ou le Redis actif.
- Aucun échange OAuth réel, appel ESI, publication de tâche active ou envoi de notification.
- Fais les tests avec base temporaire, cache dédié et réseau/broker neutralisés. Inspecte les effets de démarrage Django et des signaux avant de lancer le runner.
- Ne réécris aucune migration publiée ; une centralisation de constantes ne justifie pas une migration.
- Aucun commit, push, déploiement, redémarrage ou nettoyage global du worktree.
- Ne demande pas d'approbation pour un choix d'implémentation réversible dans ce périmètre. Si une différence de profils exige un élargissement d'accès, conserve les comportements actuels et documente ce point comme décision produit distincte.

## Diagnostic à produire avant modification

Construis une matrice à partir du code actuel avec une ligne par point d'entrée :

- nom de route et URL publique ;
- ensemble exact des scopes demandés ;
- permission Indy Hub et prérequis de rôle/propriété ;
- objectif personnel, corporation ou Material Exchange ;
- règle de couverture : au moins un personnage, tous les personnages, token unique ou autre contrat vérifié ;
- destination de retour et prise en charge de `next` ;
- comportement `force`, nouvelle autorisation et sélection initiale CharLink.

Conserve cette matrice comme référence de non-régression. Ne présente pas comme anomalie un écart qui répond à un contrat métier différent.

## Travail à réaliser

1. Choisis un petit module de constantes ou de profils sans dépendance aux vues et sans effet de bord réseau/import de tâches inutile.
1. Centralise les composants réellement communs, puis définis explicitement les profils distincts. Utilise des ensembles immuables ou des copies pour éviter une mutation partagée entre consommateurs.
1. Conserve les éventuels noms importés ailleurs par des alias de compatibilité lorsque nécessaire ; évite un changement massif d'imports sans rapport avec l'objectif.
1. Remplace les recopies des vues et de CharLink par le référentiel, avec tri déterministe au moment où une liste ou une chaîne est requise.
1. Mutualise seulement la partie commune des vues : résolution du profil connu, vérification de ses permissions, retour sûr et appel à `sso_redirect`.
1. N'accepte jamais des scopes arbitraires ou une URL de retour non validée provenant du navigateur. Un identifiant de profil inconnu doit être refusé proprement.
1. Garde les wrappers et noms de routes existants afin de préserver templates, favoris, liens et retours OAuth.
1. Préserve les profils sensibles CharLink non sélectionnés initialement et l'absence d'élargissement automatique des scopes personnels.
1. Préserve la distinction affichage passif/validation active. Filtre les scopes avant toute validation active ; ne mets pas un renouvellement dans un listing.
1. Mets à jour la documentation des permissions et scopes pour qu'elle corresponde au registre livré.

## Vérifications attendues

- Pour chaque point d'entrée, compare les scopes avant/après par égalité d'ensembles et vérifie un ordre de sérialisation stable.
- Vérifie les profils personnel, corporation, Material Exchange et les autorisations partielles sans les fusionner implicitement.
- Couvre utilisateur anonyme, accès de base absent, permission de gestion absente, utilisateur autorisé et superutilisateur.
- Vérifie qu'un seul personnage couvert ne satisfait pas une règle « tous les personnages » lorsque plusieurs sont possédés.
- Vérifie personnage sans token, token partiel, token couvrant le profil, personnages multiples et utilisateur sans personnage.
- Vérifie que les règles interdisant l'utilisation d'un token d'un autre utilisateur sont préservées.
- Vérifie `force`, retour interne valide, retour externe refusé, absence de `next`, retour après callback et contrôle des profils inconnus.
- Vérifie les chemins avec CharLink installé et absent, en utilisant les conventions existantes du projet.
- Vérifie les annotations de couverture CharLink sans collisions `GROUP BY`/`OuterRef` et sans appels OAuth sur un affichage passif.
- Compare le prédicat Python `is_character_added` et son annotation SQL pour un même utilisateur, personnage et jeu de scopes. S'ils ne veulent pas dire la même chose aujourd'hui, documente cet écart et corrige-le dans un sous-changement explicite ; n'appelle pas `require_valid()` avant le filtrage de scopes et ne fais pas de validation OAuth implicite pour obtenir une simple égalité d'affichage.
- Utilise des doubles aux frontières SSO ; aucun appel réel n'est nécessaire pour valider les scopes et les redirections.

Commence par les suites d'autorisation, scopes et CharLink existantes, puis les contrôles plus larges pertinents documentés dans `TESTING.md`. Si des scripts/templates changent, exécute leurs vérifications documentées.

## Critères d'acceptation et bilan

Le registre est la source commune sans union excessive, les règles de couverture restent distinctes, toutes les URL publiques restent résolubles et aucun accès supplémentaire n'est demandé silencieusement. La modification n'introduit ni deuxième callback OAuth ni nouvelle dépendance à CharLink pour un déploiement qui ne l'utilise pas.

Dans ton bilan final, donne la matrice avant/après, les fichiers modifiés, les vérifications exactes, les résultats et les contrôles non exécutés. Réévalue explicitement les avantages et inconvénients constatés, puis indique les éventuelles décisions produit restantes sans les transformer en changements implicites.

## Validation des usages dans Chrome

Avant de clore ce chantier, lis et applique `indy_hub/temporary_prompt/VALIDATION-CODE-USAGE-CHROME.md`. En complément des tests du code, exécute dans une vraie session Chrome les parcours consommateurs concernés, avec des comptes synthétiques sur une instance de test isolée. Ne remplace pas les vues locales à qualifier par des réponses simulées ; seules les frontières externes restent simulées. Ne réutilise ni le port ni la session de l'installation active. Si seul Chromium est disponible, indique-le explicitement.

Consigne le navigateur et sa version, les scénarios et rôles exécutés, les résultats, les erreurs console/réseau/serveur et les captures utiles dans un dossier temporaire sans secrets. Un navigateur non fonctionnel, un cas non exécuté ou une assertion en échec doivent être rapportés comme tels : les tests unitaires seuls ne permettent pas de déclarer les usages concernés validés. Aucun appel ESI/OAuth réel ou modification de l'installation active n'est autorisé par cette validation.
