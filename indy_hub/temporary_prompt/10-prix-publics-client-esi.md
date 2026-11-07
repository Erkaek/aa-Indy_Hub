# Prompt 10 — Faire passer les prix publics ESI par le fournisseur partagé

Travaille dans `/home/aadev/indyhub/aa-Indy_Hub`. Je veux aligner la récupération des prix de référence CCP sur le client django-esi partagé d'Indy Hub, en préservant les montants, les contrats de cache et les erreurs attendues par les calculs industriels.

## Problème à revérifier

L'audit a trouvé `requests.get()` vers `/latest/markets/prices/` dans `indy_hub/services/market_prices.py`, alors que les autres appels ESI passent par `services/providers.py` et `services/esi_client.py`. Le service expose `fetch_market_price_references()` et `fetch_adjusted_prices()` et conserve un cache d'une heure.

Revérifie cet état avant d'agir. Ce chemin peut déjà être corrigé. Le but est d'éviter un second transport ESI et de bénéficier des conventions du fournisseur, pas de réécrire les calculs de coûts ni la récupération des prix Jita de Fuzzwork.

## Avantages attendus

- Utiliser le même fournisseur filtré, la même identification d'application et la même date de compatibilité pour les appels ESI.
- Bénéficier de la gestion native des erreurs, limites, cache HTTP et ETags lorsque le contrat retenu l'autorise.
- Réduire les chemins réseau à maintenir et à simuler dans les tests.
- Rendre les contrôles d'indisponibilité cohérents avec les autres données publiques ESI.

## Inconvénients et risques

- Une nouvelle opération doit être présente dans la spécification filtrée ; une initialisation de client faite au mauvais endroit peut charger une spécification distante dans une requête ou un test.
- Une réponse native Pydantic et une réponse JSON brute ne se manipulent pas nécessairement de la même manière.
- Un cache HTTP, un ETag et le cache métier d'une heure n'ont pas le même contrat. Leur combinaison naïve peut produire un 304 sans données réutilisables.
- Modifier les replis vers zéro, les conversions Decimal ou la durée de validité peut changer les coûts affichés et les décisions du craft planner.
- Le paramètre `timeout` public du service n'est pas nécessairement transposable tel quel au fournisseur partagé. Le transmettre comme paramètre OpenAPI invalide ou modifier le timeout global pour un seul appel serait incorrect.
- Aligner ce transport ne prouve pas une réduction systématique des appels réseau.

## Préparation et références

1. Lis `AGENTS.md`, puis `docs/codex/INDEX.md` et `ESI.md`, `CACHE.md`, `RISK_AREAS.md`, `TESTING.md`, `CONFIGURATION.md`.
1. Examine `git status --short` et les diffs des fichiers concernés ; préserve mes modifications et celles du chantier 02.
1. Vérifie le Python V5 `/home/aadev/venv-v5/bin/python`, les modules réellement importables et la plage django-esi déclarée. Ne modifie pas les paquets de cet environnement.
1. Inspecte `market_prices.py`, ses consommateurs craft et les commandes de maintenance qui lisent ou nettoient sa clé de cache.
1. Lis le fournisseur Indy Hub et les appels publics existants de sa façade, puis les API publiques natives `ESIClientProvider`, `.result()`, le cache et les exceptions.
1. Vérifie l'identifiant et le tag exacts de l'opération de prix dans une spécification locale ou la documentation officielle. N'invente ni signature ni nom d'opération à partir d'un ancien client Swagger. La piste `GetMarketsPrices` doit être confirmée avant utilisation.

## Périmètre autorisé

- `indy_hub/services/market_prices.py`, `providers.py` et une méthode publique ciblée de `esi_client.py` si l'architecture la nécessite.
- Les tests et notes directement concernés ; les consommateurs seulement si un adaptateur est nécessaire pour préserver l'API.

Ne change pas Fuzzwork, le choix Jita, les markups, arrondis, calculs d'EIV ou d'installation, les modèles et migrations, les profils OAuth, ni le dispatch des tâches. Cet endpoint public ne demande aucun token et ne doit pas provoquer une nouvelle demande de scopes.

## Travail demandé

1. Écris le contrat actuel : structure du dictionnaire, types des clés, valeurs Decimal, cache, entrées invalides, champs absents, erreurs et paramètre `timeout`.
1. Déclare seulement l'opération nécessaire dans le fournisseur existant. Ne crée pas un deuxième fournisseur et ne charge pas tout le tag Market par commodité.
1. Ajoute un chemin d'appel public explicite dans la façade, en réutilisant ses contrôles utiles et les fonctions natives de transport. N'ajoute pas de retry applicatif caché en plus des reprises natives.
1. Transforme les données natives en la même structure métier avec les conversions numériques adaptées. N'introduis pas d'intermédiaire `float` pour les montants déjà fournis exactement sous une autre forme ; documente les limites de représentation de la réponse native si elles existent.
1. Garde `MarketPriceError` et les contrats de ses consommateurs, ou un adaptateur explicite qui conserve leur comportement en erreur. Les limites et cooldowns doivent rester identifiables, sans convertir un échec réseau en tableau de prix nul.
1. Définis le traitement d'un 304 : réutiliser des données encore disponibles ou demander explicitement un corps selon le contrat public natif. Ne transforme pas une exception 304 en cache métier vide ou en prix zéro.
1. Préserve la clé et la durée du cache métier si aucun besoin de changement n'est établi. Le nettoyage de `esi_status` ne doit pas effacer l'ensemble des caches natifs partagés.
1. Examine `timeout` : conserve son sens via une API publique compatible si possible, sans mutation globale du client partagé. S'il n'existe pas d'équivalence sûre, documente le choix de compatibilité et son effet sur les appelants ; ne l'ignore pas silencieusement.
1. Mets à jour la documentation ESI et les tests du fournisseur pour que l'opération reste incluse après filtrage. Ne relève pas automatiquement la date de compatibilité ESI ou la version minimale pour cacher une signature non vérifiée.

## Vérifications attendues

Utilise une spécification locale minimale ou des fixtures natives adaptées, une base jetable si nécessaire, un cache dédié et un broker isolé. Bloque les connexions ESI/OAuth/Discord réelles. L'initialisation des tests ne doit pas télécharger de spécification distante.

- Filtrage : l'opération et ses schémas survivent, les opérations Market inutiles restent exclues.
- Résultat normal : clés int et champs `adjusted_price`/`average_price` Decimal conformes au contrat.
- Champs absents, identifiant invalide, valeur nulle et réponse malformée : même politique explicite qu'avant, sans fabriquer une réussite après erreur de transport.
- Réponse 304 avec ou sans valeur de cache métier disponible : données correctes ou erreur contrôlée, jamais une baisse artificielle vers zéro.
- Cache froid, chaud et expiré : nombre d'appels logiques attendu, aucune requête par type demandé.
- Réponses 420, 429, 5xx et panne réseau : erreurs et délais cohérents, sans nouvelle couche de retry non bornée.
- Le paramètre `timeout` est traité selon le contrat documenté et ne devient pas un paramètre de l'opération ESI.
- Les consommateurs de `fetch_adjusted_prices()` gardent leurs types et résultats ; les tests existants de craft, coûts d'installation et EIV passent.
- Les outils de maintenance conservent leur nettoyage limité au bon cache.
- Aucun token sélectionné, aucun appel à `require_valid()` et aucun scope demandé pour ce prix public.

Lance les tests ciblés avant les suites plus larges prescrites par `TESTING.md`. Préserve les cas métier utiles même si d'anciens mocks visant `requests.get` doivent être remplacés ; ne transforme pas ces tests en simple miroir du nouveau wrapper.

## Limites et critères d'acceptation

Aucun changement de l'installation active, des venv, des fichiers `site-packages`, des services, de Redis actif ou de la base active. Aucun commit, push, déploiement, redémarrage ou migration réelle. Aucun reformatage global des fichiers en cours de modification.

Le service utilise le fournisseur partagé avec un contrat prix/cache explicite, les sorties métier sont préservées et les erreurs ne produisent pas de réussite fictive. Si une limite publique du fournisseur empêche de préserver un contrat essentiel, explique-la et prépare l'adaptation bornée au lieu d'introduire un hook privé.

## Compte rendu final

Donne l'opération et le chemin retenus, les fichiers modifiés, les contrats avant/après de cache et de timeout, les avantages obtenus, les inconvénients restants et les résultats des vérifications. Distingue tests natifs hors réseau, résultats de calcul et qualification ESI réelle non exécutée.

## Validation des usages dans Chrome

Avant de clore ce chantier, lis et applique `indy_hub/temporary_prompt/VALIDATION-CODE-USAGE-CHROME.md`. En complément des tests du code, exécute dans une vraie session Chrome les parcours consommateurs concernés, avec des comptes synthétiques sur une instance de test isolée. Ne remplace pas les vues locales à qualifier par des réponses simulées ; seules les frontières externes restent simulées. Ne réutilise ni le port ni la session de l'installation active. Si seul Chromium est disponible, indique-le explicitement.

Consigne le navigateur et sa version, les scénarios et rôles exécutés, les résultats, les erreurs console/réseau/serveur et les captures utiles dans un dossier temporaire sans secrets. Un navigateur non fonctionnel, un cas non exécuté ou une assertion en échec doivent être rapportés comme tels : les tests unitaires seuls ne permettent pas de déclarer les usages concernés validés. Aucun appel ESI/OAuth réel ou modification de l'installation active n'est autorisé par cette validation.
