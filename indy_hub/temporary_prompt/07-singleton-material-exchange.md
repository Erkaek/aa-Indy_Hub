# Prompt 07 — Aligner le singleton Material Exchange sur django-solo

Travaille dans `/home/aadev/indyhub/aa-Indy_Hub`. Je veux remplacer, si la transition est sûre, le singleton artisanal de `MaterialExchangeSettings` par le mécanisme `SingletonModel` de django-solo, déjà employé dans l'écosystème Alliance Auth. Réalise ce chantier uniquement dans le dépôt et vérifie-le sur des données de test isolées.

## Statut facultatif et référence native

django-solo est une possibilité standard, pas une obligation imposée par Alliance Auth à chaque réglage global. Compare `solo/models.py`, le contrat actuel et le coût de transition avant de choisir. Si l'adoption impose une migration risquée pour un gain faible, livre le diagnostic et recommande explicitement le report ; ne migre pas uniquement pour correspondre à un exemple.

La présence du paquet et la présence de l'application `solo` dans le projet sont deux informations distinctes. Vérifie les dépendances déclarées et, si la solution en dépend, les exigences `INSTALLED_APPS`. Ne modifie pas les réglages du projet actif et n'ajoute pas d'admin SingletonModelAdmin sans besoin démontré.

## Objectif et état à revérifier

Le constat de départ est que `MaterialExchangeSettings.get_solo()` retourne la première ligne ou en crée une. Cette convention ne garantit pas un singleton en cas de créations concurrentes et accepte silencieusement plusieurs lignes. Le code a pu évoluer depuis cet audit : confirme ce constat avant de modifier quoi que ce soit.

Préserve la valeur effective de `is_enabled`, les données existantes et la compatibilité des appels actuels à `get_solo()`. Le but est d'utiliser le comportement standard de django-solo, sans refondre la configuration métier ni modifier le workflow des commandes Material Exchange.

## Avantages attendus

- Utiliser une abstraction maintenue au lieu d'une convention locale `first()` puis `create()`.
- Obtenir une identité de singleton déterministe et limiter les créations concurrentes par l'API normale.
- Bénéficier des mécanismes de cache et d'invalidation fournis par django-solo lorsqu'ils sont configurés.
- Faciliter la compréhension et la maintenance pour un développeur Alliance Auth.

## Inconvénients et risques à traiter

- Le gain de code est modeste face au travail de compatibilité avec les données installées.
- Le singleton natif utilise normalement une clé fixe ; une ligne existante dont la clé diffère ne doit pas être ignorée ou remplacée par les valeurs par défaut.
- Plusieurs lignes existantes peuvent porter des réglages contradictoires. Choisir arbitrairement une ligne ou les supprimer ferait perdre une décision de configuration.
- Un nouveau cache peut conserver un ancien état si des écritures passent par `QuerySet.update()`, `bulk_create()` ou un autre chemin qui contourne `save()`.
- `SingletonModel` ne constitue pas à lui seul une garantie universelle contre du SQL direct ou toutes les écritures ORM contournant son API.
- Une migration de données éventuelle doit tenir compte des installations MySQL/MariaDB et des liens vers la clé actuelle. Ne présente pas un test SQLite comme une validation de ces moteurs.

## Lecture préalable et préservation

1. Lis `AGENTS.md`, puis `docs/codex/INDEX.md`.
1. Lis les notes `DATABASE.md`, `MIGRATIONS.md`, `CACHE.md`, `CELERY.md`, `RISK_AREAS.md`, `TESTING.md` et `CONFIGURATION.md` sous `docs/codex/`.
1. Examine `git status --short` et les différences des fichiers concernés. Mes modifications en cours doivent rester intactes. Ne réinitialise, ne remplace et ne reformate aucun travail sans rapport avec ce chantier.
1. Vérifie les versions et le code de django-solo et d'Alliance Auth réellement disponibles. `/home/aadev/venv-v5/bin/python` est le point de départ connu ; ne prends pas automatiquement le `.venv` du dépôt comme environnement actif.
1. Traite les constats et versions de l'audit comme des indications historiques. Si le problème est déjà corrigé, valide la correction et évite une réécriture inutile.

## Périmètre autorisé

- `MaterialExchangeSettings` dans `indy_hub/models.py`.
- Ses usages directs, son enregistrement admin éventuel et ses tests.
- Une nouvelle migration seulement si elle est nécessaire à une transition compatible.
- Les notes techniques et les instructions de mise à jour directement affectées.

N'étends pas ce chantier à `MaterialExchangeConfig`, aux ordres, aux prix, aux structures, aux notifications ou aux réglages d'un autre module. N'ajoute pas de page d'administration uniquement pour élargir le chantier.

Ne réécris aucune migration existante. N'applique aucune migration sur l'installation réelle. Ne modifie pas le venv V5, ses paquets, la configuration du projet installé, les services, Redis ni les workers. N'effectue aucun appel ESI, OAuth ou Discord réel. Ne crée ni commit, ni push, ni déploiement.

## Travail demandé

1. Inventorie tous les lecteurs et auteurs de `MaterialExchangeSettings`, y compris `get_solo()`, `.first()`, `.create()`, `.save()`, `.update()` et les tâches périodiques.
1. Examine l'implémentation native de `SingletonModel`, sa clé d'instance, `get_solo()` et l'invalidation de cache. Vérifie que la dépendance est déclarée ou garantie par les dépendances effectivement prises en charge ; ne t'appuie pas uniquement sur sa présence accidentelle dans un venv.
1. Identifie les cas à supporter : table vide, ligne unique avec clé attendue, ligne unique avec autre clé, lignes multiples équivalentes et lignes multiples contradictoires.
1. Définis une transition déterministe qui préserve les valeurs utiles, les références et le comportement effectif. Ne supprime pas silencieusement les lignes supplémentaires.
1. Si des données contradictoires ne peuvent pas être réconciliées sans une règle métier nouvelle, prévois un diagnostic précis et une interruption avant toute mutation de ces données. Documente la règle manquante. Ne transforme pas une ambiguïté en activation ou désactivation automatique de Material Exchange.
1. Intègre django-solo avec le minimum de changements. Conserve l'API publique `MaterialExchangeSettings.get_solo()` et `default_permissions = ()`.
1. Vérifie les effets du cache et des chemins d'écriture : un changement de `is_enabled` doit être immédiatement visible selon le contrat existant.
1. Si une migration est nécessaire, crée un nouveau fichier dépendant de la vraie dernière migration du dépôt. Utilise les modèles historiques et le bon alias de base. Analyse les opérations inverses et les effets `post_migrate` ; ne simule pas une restauration de données impossible.
1. Mets à jour les notes concernées, notamment les limites du singleton et la procédure de qualification avant installation. Prépare la procédure sans l'exécuter sur l'environnement réel.

## Vérifications attendues

Utilise exclusivement une base temporaire, un cache de test dédié et un broker isolé. Les signaux et hooks de migration ne doivent pas publier de travail vers les workers actifs. Lis les tests existants avant d'ajouter des cas ; vérifie le comportement et la conservation des données, pas seulement le nom de la classe mère.

- Table vide : un appel crée la configuration attendue et les appels suivants la réutilisent.
- Configuration désactivée existante : elle reste désactivée après la transition.
- Ligne à clé différente : sa configuration reste accessible, sans création concurrente d'une configuration par défaut qui la masque.
- Lignes multiples : la politique documentée est appliquée, ou le diagnostic bloque sans perte de données lorsqu'aucune règle sûre n'existe.
- Appels répétés et accès concurrents : démontre la garantie réellement obtenue ; n'annonce pas de preuve de concurrence distribuée à partir d'un simple test séquentiel.
- Cache chaud, cache froid et modification du réglage : aucun état périmé introduit.
- Appels des vues et tâches : même résultat métier et mêmes permissions.
- Si une migration est ajoutée : scénario depuis l'état précédent sur base jetable, conservation des valeurs et vérification du retour arrière réellement supporté.
- Cohérence modèles/migrations avec `makemigrations --check --dry-run`, puis suite pertinente et contrôles plus larges prévus dans `TESTING.md`, dans l'environnement isolé.

Les commandes de documentation qui peuvent reformater les fichiers ne doivent pas modifier mes autres changements. Si un contrôle ne peut pas être exécuté sans toucher un service actif, indique cette limite et poursuis les contrôles isolés possibles.

## Critères d'acceptation

- Une seule API de singleton maintenue, avec des garanties décrites exactement.
- Aucune configuration existante remplacée silencieusement par une valeur par défaut.
- Aucun changement fonctionnel hors du périmètre et aucune migration publiée modifiée.
- Les tests isolés pertinents passent ; les limites de portabilité et de concurrence sont explicites.
- Si l'intégration native n'est pas sûre dans l'état rencontré, le diagnostic et la transition préparée expliquent précisément le blocage sans détruire les données.

## Compte rendu final attendu

Donne les fichiers modifiés, la stratégie retenue pour les données historiques, les avantages réellement obtenus, les inconvénients restants, les contrôles exécutés et leurs résultats. Distingue clairement une migration préparée, une migration testée sur copie jetable et une migration appliquée : cette dernière n'est pas autorisée sur l'installation réelle. Liste les opérations de qualification restantes sans les présenter comme exécutées.

## Validation des usages dans Chrome

Avant de clore ce chantier, lis et applique `indy_hub/temporary_prompt/VALIDATION-CODE-USAGE-CHROME.md`. En complément des tests du code, exécute dans une vraie session Chrome les parcours consommateurs concernés, avec des comptes synthétiques sur une instance de test isolée. Ne remplace pas les vues locales à qualifier par des réponses simulées ; seules les frontières externes restent simulées. Ne réutilise ni le port ni la session de l'installation active. Si seul Chromium est disponible, indique-le explicitement.

Consigne le navigateur et sa version, les scénarios et rôles exécutés, les résultats, les erreurs console/réseau/serveur et les captures utiles dans un dossier temporaire sans secrets. Un navigateur non fonctionnel, un cas non exécuté ou une assertion en échec doivent être rapportés comme tels : les tests unitaires seuls ne permettent pas de déclarer les usages concernés validés. Aucun appel ESI/OAuth réel ou modification de l'installation active n'est autorisé par cette validation.
