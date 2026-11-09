# Prompt 08 — Simplifier les accès SDE avec l'ORM et préserver les noms anglais

Travaille dans `/home/aadev/indyhub/aa-Indy_Hub`. Je veux simplifier les accès aux noms d'objets SDE dans `indy_hub/utils/eve.py`, en remplaçant les requêtes SQL et l'introspection devenues inutiles par l'ORM de django-eveonline-sde et les mécanismes de traduction appropriés. Réalise ce chantier dans le dépôt, sans modifier le SDE installé ni les données de l'installation.

## Objectif et état à revérifier

L'audit a repéré `_get_table_columns()`, `_sde_english_name_expression()`, `get_type_name()` et `batch_cache_type_names()`, qui interrogent directement `eve_sde_itemtype` pour obtenir un nom anglais. Un autre chemin, `_resolve_sde_location_name()`, cherche une table `eve_sde_mapdenormalize` par introspection.

Ces fonctions peuvent encore contenir des compatibilités nécessaires. Vérifie le code courant, la plage de versions SDE déclarée et les consommateurs avant de supprimer un chemin. Préserve les résultats métier et les signatures publiques.

## Référence native et réalisation en étapes

Compare les modèles `eve_sde.models.ItemType` et les API publiques du manager modeltranslation dans les versions prises en charge. Le test existant qui vérifie une sous-chaîne SQL dans `test_utils_eve.py` n'est pas le contrat métier : remplace les assertions trop liées à l'implémentation par des vérifications de résultats et de sélection des types publiés, sans affaiblir ces garanties.

Livre d'abord le remplacement des accès aux noms de types. Traite le chemin historique `mapdenormalize` séparément, seulement après une preuve de son usage ou de son obsolescence. Le SDE moderne peut modéliser séparément stations, systèmes et astres ; cela ne justifie ni une recherche sur toutes les tables à chaque affichage ni une extension des appels ESI.

## Avantages attendus

- Réduire le couplage aux noms physiques de tables et aux variantes de colonnes.
- Confier les requêtes, paramètres et différences de moteurs à Django.
- Rendre les accès plus faciles à lire et à tester pour un développeur AA/Django.
- Mutualiser le choix de langue et les replis dans un chemin local clairement documenté.
- Conserver des résolutions en lot au lieu de multiplier les requêtes par objet.

## Inconvénients et risques à traiter

- Le champ `.name` peut suivre la langue active. Un remplacement ORM naïf peut faire apparaître des noms français ou allemands là où le module attend des noms anglais.
- Le manager multilingue et `.rewrite(False)` peuvent être absents dans un environnement de test sans modeltranslation ; ne déduis pas le comportement installé d'un tel test incomplet.
- Les anciennes versions SDE autorisées par les métadonnées peuvent avoir des modèles ou champs différents de la version locale. Supprimer un fallback peut devenir une rupture de compatibilité.
- `name_en` vide et `name_en` nul ne sont pas équivalents. Ne modifie pas implicitement le résultat de `COALESCE(name_en, name)` ni les replis actuels.
- Une optimisation ORM peut modifier la sélection des types publiés, les valeurs de repli ou le nombre de requêtes.
- Un cache de noms incorrect peut conserver le mauvais résultat après le changement de langue ou le chargement du SDE.
- Retirer un ancien chemin de noms publics sans vérifier les cas station, système, astre et structure privée peut dégrader l'affichage ou provoquer davantage d'appels ESI.

## Lecture préalable et préservation

1. Lis `AGENTS.md`, puis `docs/codex/INDEX.md`.
1. Lis les notes `DATABASE.md`, `ESI.md`, `CACHE.md`, `TESTING.md`, `CONFIGURATION.md` et `QUIRKS.md` sous `docs/codex/`.
1. Examine `git status --short` et les différences de `utils/eve.py` et des tests. Préserve mes modifications en cours et celles des autres chantiers. Ne lance pas de reformatage global.
1. Vérifie les versions, modules réellement importés et métadonnées des dépendances. `/home/aadev/venv-v5/bin/python` est l'interpréteur V5 connu, à confirmer ; le `.venv` du dépôt n'est pas automatiquement la référence.
1. Les constats sont historiques : si un chemin est déjà simplifié correctement, vérifie-le sans le réécrire.

## Périmètre autorisé

- Les helpers de noms SDE et leurs petits adaptateurs dans `indy_hub/utils/eve.py`.
- Les consommateurs directs uniquement si l'ancienne API doit être conservée par un adaptateur.
- Les tests ciblés et la documentation des versions/langues prises en charge.

Préserve les caches de noms de structures privées et les protections contre les 403, les budgets de recherche et les snapshots d'assets. Ne change pas les calculs industriels, les imports SDE, les routes, les modèles métier ou les permissions. N'ajoute pas de dépendance obligatoire à un module optionnel pour résoudre les noms.

Ne réécris aucune migration existante. Ne lance aucun import ni rechargement du SDE réel, aucune migration sur la base active, aucun appel ESI/OAuth réel, aucun flush Redis et aucun redémarrage. Ne modifie ni les paquets du V5 ni sa configuration. Ne crée ni commit, ni push, ni déploiement.

## Travail demandé

1. Dresse la carte des fonctions visées et de leurs consommateurs, notamment les pages blueprints, craft, listes Material Exchange et commandes de maintenance.
1. Inspecte `ItemType`, ses champs et ses managers dans les versions effectivement prises en charge. Vérifie comment modeltranslation enregistre les champs et réécrit les requêtes.
1. Décris les contrats actuels : entrée absente ou invalide, identifiant inconnu, anglais disponible, anglais nul ou vide, filtre `published`, SDE absent en test, cache déjà rempli et résolution par lot.
1. Remplace les requêtes aux types par une requête ORM explicite. Une piste à vérifier est `rewrite(False)` avec une annotation `Coalesce("name_en", "name")`. Ce n'est pas une solution à copier sans contrôle : vérifie la disponibilité du manager, le SQL produit et les résultats pour la plage de versions supportées.
1. Préserve la différence éventuelle entre la résolution unitaire et celle en lot concernant les types non publiés. Ne rajoute pas un filtre pour rendre les deux fonctions artificiellement identiques.
1. Conserve une lecture en lot avec `values_list()` ou une construction équivalente. Évite une requête par identifiant et découpe les grandes listes si les limites de paramètres du moteur le demandent.
1. Supprime l'introspection et les helpers SQL uniquement lorsqu'ils n'ont plus de consommateur utile. Ne supprime pas une compatibilité indispensable à une version encore autorisée sans résoudre explicitement ce contrat.
1. Pour `_resolve_sde_location_name()`, vérifie d'abord si la table et le chemin sont encore fournis par le SDE supporté. Si ce chemin est réellement obsolète, remplace-le par les modèles publics adaptés ou documente son retrait. Ne remplace jamais la résolution d'une structure privée par une recherche SDE publique.
1. Préserve les caches utiles. Vérifie que les noms anglais et les replis ne restent pas bloqués sur un identifiant brut après l'apparition des données.
1. Mets à jour les notes concernées. Si une version minimale différente devient nécessaire, explique-la avec des preuves et n'élargis pas ce chantier en mise à jour générale des dépendances. Ne modifie pas l'environnement actif.

## Vérifications attendues

Utilise une base jetable, des objets SDE synthétiques locaux et un cache isolé. Aucun appel externe n'est nécessaire pour qualifier cette modification. Teste les résultats et les requêtes réellement produites, pas uniquement des mocks reproduisant le nouveau code.

- Sous langues `en`, `fr` et `de`, le même type conserve le nom anglais attendu.
- Active modeltranslation dans au moins un scénario pour tester le comportement qui a motivé le contournement SQL.
- Vérifie aussi l'environnement de test sans manager multilingue si le dépôt le prend en charge.
- Noms anglais présents, nuls et vides ; nom de base présent ou absent ; identifiant inconnu et entrée absente : mêmes replis que le contrat existant.
- Types publiés/non publiés : pas de changement involontaire de catalogue ou de noms.
- Liste vide, identifiants répétés et grande liste : bon résultat, nombre de requêtes borné et absence de requête par ligne.
- Cache froid et chaud, données nouvellement disponibles : résultat correct sans contamination par la langue active.
- Résolution des noms publics et privés : pas de nouvelle requête ESI, pas de suppression des cooldowns et pas de placeholder incorrect introduit.
- Préserve les tests de `indy_hub.tests.core.test_utils_eve` et les tests existants de langue/résolution repérés dans le dépôt.
- Lance d'abord la suite ciblée, puis les suites craft/Material Exchange affectées et les contrôles plus larges prescrits par `TESTING.md`, sur des ressources isolées.

Un SQL compilé correct est une preuve partielle, pas une preuve de résultat. Un test SQLite n'établit pas à lui seul la compatibilité MySQL/MariaDB ; distingue ce qui est testé, inspecté et restant à qualifier.

## Critères d'acceptation

- API des helpers conservée et sorties métier inchangées hors correction explicitement expliquée.
- Anglais garanti dans le contexte réellement multilingue, avec les replis préservés.
- SQL/introspection supprimés seulement là où leur utilité a disparu.
- Aucun appel ESI supplémentaire et aucun changement du schéma SDE.
- Nombre de requêtes en lot conservé ou réduit avec preuve appropriée.
- Tests ciblés pertinents réussis, limites de versions et de moteurs clairement indiquées.

## Compte rendu final attendu

Présente les fichiers modifiés, les compatibilités conservées ou retirées et pourquoi, les avantages réellement obtenus, les inconvénients restants, puis les contrôles et leurs résultats. Donne des exemples avant/après pour un nom anglais et un identifiant inconnu. Indique les moteurs et configurations de traduction réellement testés, sans annoncer une validation en production.

## Validation des usages dans Chrome

Avant de clore ce chantier, lis et applique `indy_hub/temporary_prompt/VALIDATION-CODE-USAGE-CHROME.md`. En complément des tests du code, exécute dans une vraie session Chrome les parcours consommateurs concernés, avec des comptes synthétiques sur une instance de test isolée. Ne remplace pas les vues locales à qualifier par des réponses simulées ; seules les frontières externes restent simulées. Ne réutilise ni le port ni la session de l'installation active. Si seul Chromium est disponible, indique-le explicitement.

Consigne le navigateur et sa version, les scénarios et rôles exécutés, les résultats, les erreurs console/réseau/serveur et les captures utiles dans un dossier temporaire sans secrets. Un navigateur non fonctionnel, un cas non exécuté ou une assertion en échec doivent être rapportés comme tels : les tests unitaires seuls ne permettent pas de déclarer les usages concernés validés. Aucun appel ESI/OAuth réel ou modification de l'installation active n'est autorisé par cette validation.
