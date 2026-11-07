# Prompt 09 — Respecter l'administration partagée d'Alliance Auth

Travaille dans `/home/aadev/indyhub/aa-Indy_Hub`. Je veux supprimer les interférences d'Indy Hub avec l'administration globale des utilisateurs et groupes d'Alliance Auth, tout en gardant ses permissions compréhensibles. Réalise une correction ciblée dans le dépôt et qualifie-la sur une installation de test isolée.

## Problème à revérifier

L'examen du 7 novembre 2026 a trouvé dans `indy_hub/admin.py` un enregistrement des modèles Django concrets `auth.User` et `auth.Group` pour personnaliser les libellés de trois permissions Indy Hub. Le code utilise `admin.site._registry`, `unregister()` et de nouveaux formulaires.

Alliance Auth 5.2.0 enregistre déjà des proxys pour ces mêmes tables : `authentication.User` et `groupmanagement.Group`. Dans le projet de test inspecté, les quatre entrées coexistent. Le chemin Indy Hub pour les modèles concrets n'a pas les mêmes champs en lecture seule que les écrans AA pour un administrateur non superutilisateur.

Ce constat n'est pas une preuve de contournement exploitable par n'importe quel utilisateur : les droits effectifs des modèles/proxys et les routes admin doivent être testés. Vérifie l'état actuel avant toute correction ; mes autres mises à jour peuvent déjà l'avoir changé.

## Avantages attendus

- Éviter des chemins d'administration supplémentaires pour les tables communes d'authentification.
- Laisser Alliance Auth posséder les formulaires, restrictions, inlines et actions de ses écrans.
- Éliminer la dépendance à un registre privé et à l'ordre d'import des applications.
- Réduire le risque d'un second formulaire moins restrictif pour les groupes, permissions ou attributs sensibles des utilisateurs.
- Garder l'administration Indy Hub limitée à ses modèles et capacités métier.

## Inconvénients et risques

- Les libellés des permissions peuvent revenir à leur présentation native ; ce changement d'affichage doit être expliqué.
- Un favori pointant vers le modèle concret peut disparaître. Il faut identifier ces routes et documenter l'écran AA correspondant.
- Un mauvais retrait peut désenregistrer par erreur l'admin natif ou une personnalisation du projet hôte.
- Les permissions Django du modèle concret et du proxy ne sont pas toujours interchangeables ; une simple assertion sur le nom de classe admin est insuffisante.
- Ajouter un monkeypatch des formulaires AA pour sauver un libellé recréerait le couplage que l'on souhaite retirer.

## Préparation et sources

1. Lis `AGENTS.md`, puis `docs/codex/INDEX.md` et les notes `PERMISSIONS.md`, `ARCHITECTURE.md`, `DATABASE.md`, `TESTING.md` et `RISK_AREAS.md`.
1. Relève `git status --short` et les différences des fichiers touchés. Préserve mes changements, sans reset, remplacement global ou reformatage hors périmètre.
1. Vérifie versions et chemins avec `/home/aadev/venv-v5/bin/python`, sans modifier ce venv. Les anciennes versions trouvées dans des métadonnées résiduelles ne remplacent pas le contrôle du module réellement importé.
1. Lis `indy_hub/admin.py`, les déclarations des permissions et leurs tests.
1. Lis les sources installées `allianceauth/authentication/admin.py`, `allianceauth/groupmanagement/admin.py`, leurs modèles proxys et les API publiques Django `ModelAdmin` et `AdminSite`.
1. Compare la situation avec et sans Indy Hub sur deux configurations de test distinctes. Ne réinitialise pas le registre admin du processus actif.

## Périmètre et exclusions

Modifie uniquement l'intégration admin d'Indy Hub, les tests utiles et la documentation directement liée. Préserve ses trois codenames de permissions, leurs affectations, les actions admin propres aux modèles industriels et la console métier `settings_admin_users`.

Ne modifie pas l'administration d'Alliance Auth ou d'un autre module, les fichiers `site-packages`, les permissions stockées sur l'installation active, les groupes existants ou le modèle utilisateur. Ne réécris aucune migration publiée et n'en crée pas uniquement pour changer le rendu d'un libellé.

## Travail demandé

1. Inventorie les modèles concrets et proxys concernés, les entrées admin, les URL résolubles et les classes/formulaires réellement sélectionnés.
1. Compare leurs droits de lecture, ajout, modification, suppression, actions et champs sensibles pour chaque type d'administrateur. Vérifie les appels réels à `has_*_permission` et les conditions de lecture seule sur un objet existant.
1. Confirme si les seules raisons des enregistrements Indy Hub sont les libellés. Identifie toute autre fonctionnalité utile avant de retirer le code.
1. Retire l'enregistrement supplémentaire des modèles d'authentification par Indy Hub lorsqu'il est bien à l'origine de la duplication. Laisse les enregistrements du projet hôte et d'AA intacts ; ne désenregistre pas leurs proxys.
1. Conserve les libellés natifs si aucune extension publique simple et sûre n'existe. Une légère différence de présentation vaut mieux qu'une modification globale du formulaire AA. Ne mute ni `_registry` ni les méthodes d'un admin appartenant à une autre app pour simuler une intégration.
1. Si un besoin de présentation demeure, limite-le à un écran Indy Hub déjà existant et pertinent ; ne crée pas un nouveau panneau de gestion des permissions.
1. Documente les anciennes URL concrètes retirées, les destinations AA correspondantes et le caractère inchangé des permissions stockées. Ne redirige pas un POST d'édition vers un autre modèle avec ses données sans analyse de sécurité.
1. Conserve le diff petit. Pas de conversion générale des modèles `User`, de renommage de permissions ou de réorganisation complète de l'admin.

## Tests de comportement

Exécute les tests uniquement avec une base temporaire, un cache et un broker isolés, les connexions externes bloquées et les effets de démarrage examinés. Ne te connecte pas au projet installé ni à ses comptes réels.

- L'ajout d'Indy Hub ne crée plus un second écran User/Group pour la même table et n'altère pas l'admin AA attendu.
- Utilisateur anonyme, utilisateur sans `is_staff`, staff sans permission pertinente, staff avec permissions concrètes seules, staff avec permissions proxy pertinentes et superutilisateur : vérifie l'accès effectif à chaque route concernée.
- Le staff non superutilisateur ne gagne pas un accès aux champs `is_staff`, `is_superuser`, `user_permissions` ou aux permissions des groupes par un chemin alternatif introduit par Indy Hub.
- Un POST fabriqué ne permet pas d'écrire un champ que le formulaire AA réserve en lecture seule. Teste sur des objets jetables et vérifie l'état en base.
- Les restrictions, formulaires, inlines et actions propres à AA restent disponibles, en utilisant le vrai admin du projet de test.
- Les permissions Indy Hub existantes restent attribuées aux mêmes utilisateurs/groupes et continuent de gouverner ses vues.
- Les admins des modèles industriels restent enregistrés et fonctionnels.
- Les changements de libellés éventuels sont décrits ; aucune migration ou modification de données n'est exécutée pour les masquer.

Commence par les régressions de registre, de routes et de permissions pertinentes, puis les contrôles plus larges prescrits par `TESTING.md`. Les tests sur Django isolé doivent inclure les applications AA qui enregistrent les proxys ; un registre sans AA n'est pas une validation de cette intégration.

## Limites et critères d'acceptation

Aucun appel ESI/OAuth/Discord, changement du V5, accès à la base active, flush Redis, restart, commit, push ou déploiement. Les données et migrations publiées sont préservées.

Le résultat doit laisser l'administration commune sous la responsabilité de son propriétaire, sans élargir les droits ni créer un nouveau mécanisme de personnalisation globale. S'il existe un besoin fonctionnel qui ne peut pas être conservé avec les API publiques, expose-le et garde un comportement sûr plutôt qu'un patch implicite du noyau.

## Compte rendu final

Présente les entrées et routes admin avant/après, les fichiers modifiés, la matrice de droits réellement testée, les avantages obtenus et les inconvénients restants. Distingue une différence de restrictions constatée d'un contournement démontré par un test. Indique les tests exécutés, leurs résultats et les limites, sans prétendre avoir validé l'installation active.

## Validation des usages dans Chrome

Avant de clore ce chantier, lis et applique `indy_hub/temporary_prompt/VALIDATION-CODE-USAGE-CHROME.md`. En complément des tests du code, exécute dans une vraie session Chrome les parcours consommateurs concernés, avec des comptes synthétiques sur une instance de test isolée. Ne remplace pas les vues locales à qualifier par des réponses simulées ; seules les frontières externes restent simulées. Ne réutilise ni le port ni la session de l'installation active. Si seul Chromium est disponible, indique-le explicitement.

Consigne le navigateur et sa version, les scénarios et rôles exécutés, les résultats, les erreurs console/réseau/serveur et les captures utiles dans un dossier temporaire sans secrets. Un navigateur non fonctionnel, un cas non exécuté ou une assertion en échec doivent être rapportés comme tels : les tests unitaires seuls ne permettent pas de déclarer les usages concernés validés. Aucun appel ESI/OAuth réel ou modification de l'installation active n'est autorisé par cette validation.
