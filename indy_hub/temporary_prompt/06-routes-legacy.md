# Prompt 06 — Mutualiser les redirections des anciennes simulations

Commence par lire `AGENTS.md` et `docs/codex/INDEX.md`, puis inspecte `git status --short` et les diffs des fichiers concernés. Je peux avoir modifié les routes entre-temps : vérifie le code actuel, préserve mes changements et ne réintroduis pas un comportement déjà retiré volontairement.

Je veux que tu simplifies les vues de compatibilité des anciennes simulations dans Indy Hub. Réalise une modification locale petite et reviewable, puis vérifie les contrats de navigation. Il s'agit de mutualiser les redirections existantes, sans supprimer les anciennes URL ni restaurer l'ancien modèle de simulations.

## Statut facultatif et référence Django

Il s'agit d'une amélioration de maintenance à faible gain, pas d'une non-conformité Alliance Auth. Compare les primitives `django.shortcuts.redirect`, `reverse` et éventuellement `RedirectView`, mais n'adopte pas une vue de classe si elle masque les décorateurs, messages ou événements. Si les wrappers actuels sont plus lisibles qu'une extraction, justifie leur conservation. Ne crée pas un framework de redirections pour quatre fonctions.

## Résultat attendu

- Les anciennes routes continuent de mener à la liste actuelle des projets/craft tables.
- Leur texte d'information et la construction de la redirection utilisent un mécanisme commun quand leur contrat est identique.
- Les contrôles d'accès, méthodes acceptées, codes HTTP et éventuels événements analytiques sont préservés.
- Les projets actuels et leurs routes de modification/suppression gardent leur propre comportement.

## Avantages recherchés

- Éviter plusieurs copies du même message et du même choix de destination.
- Rendre le rôle des anciennes routes immédiatement lisible : compatibilité de navigation uniquement.
- Réduire le risque de corriger une redirection dans une vue en oubliant les autres.
- Conserver les favoris et liens historiques pendant la simplification du code interne.

## Inconvénients et risques à traiter

- Le gain de volume est modeste : une infrastructure générique de routage coûterait plus cher que la duplication actuelle.
- Les vues peuvent différer sur leurs décorateurs, leur signature ou l'émission d'un événement ; une fonction unique trop large peut effacer ces différences.
- Un changement de code de redirection peut être mis en cache par le navigateur et avoir un effet durable.
- Une ancienne URL contenant `simulation_id` ne doit pas être réinterprétée comme une demande d'action sur un projet actuel.
- Modifier uniquement les URL sans conserver leurs noms casserait des `reverse()`, templates ou liens externes.
- Une modification des méthodes HTTP acceptées serait un changement de comportement distinct, même si la vue ne fait plus que rediriger.

## Sources à lire

Lis `docs/codex/ARCHITECTURE.md`, `FRONTEND.md`, `PERMISSIONS.md` et `TESTING.md`.

Inspecte :

- `indy_hub/urls.py` et les imports des vues legacy ;
- `indy_hub/views/user.py` : `production_simulations` et `rename_production_simulation` ;
- `indy_hub/views/industry.py` : `delete_production_simulation` et `edit_simulation_name` ;
- la route actuelle nommée `production_simulations_list` et les routes `ProductionProject` encore actives ;
- les références de ces noms de vues et routes dans les templates, scripts, services, tests et documentation.

Les noms ci-dessus viennent d'un audit antérieur. S'ils ont changé, retrouve leurs équivalents et explique le périmètre finalement traité.

## Contraintes de travail

- Modifie seulement les helpers/vues de redirection, leurs imports, les vérifications pertinentes et les notes indispensables.
- Ne change aucun modèle, aucune migration, aucun objet existant et aucun parcours d'import des projets.
- Ne supprime aucune URL publique ni aucun nom de route existant dans ce chantier.
- Ne change pas le préfixe de montage d'Indy Hub dans Alliance Auth et n'introduis pas de nouvelle dépendance.
- Ne transforme pas une redirection temporaire en redirection permanente, ni l'inverse, sans nécessité démontrée dans le contrat à préserver.
- Aucun appel ESI/OAuth, accès à la base active, publication vers Celery actif ou modification du cache actif.
- Vérifie les versions et chemins avec `/home/aadev/venv-v5/bin/python` si tu l'utilises pour les tests ; le `.venv` du dépôt n'est pas la référence V5.
- Utilise des paramètres de test isolés, une base temporaire et un cache dédié. Inspecte les effets de démarrage Django avant de lancer le runner.
- Ne modifie pas le venv, les services ou les installations. Aucun commit, push, déploiement, redémarrage, `git reset` ou nettoyage global.
- Ne réécris jamais une migration publiée, même si elle contient l'historique de l'ancien modèle.

## Diagnostic puis implémentation

1. Dresse un inventaire des routes legacy réellement présentes : chemin, nom, arguments, vue, méthode(s), décorateurs, statut HTTP, destination, message et événement analytique.
1. Recherche leurs appels internes et relève les contrats déjà couverts par des tests. Un nom importé par un test ou une intégration peut nécessiter un wrapper de compatibilité.
1. Compare les corps des vues et identifie la plus petite partie véritablement commune.
1. Extrais un helper de redirection ou une vue commune uniquement si cela améliore la lisibilité ; conserve des wrappers explicites lorsque leurs signatures ou événements diffèrent.
1. Centralise le message avec la traduction Django existante. Préserve son contenu utilisateur et son niveau d'information.
1. Préserve les décorateurs publics et leur ordre quand il influe sur la réponse ; ne supprime pas une restriction en déplaçant le helper.
1. Préserve la route cible par son nom et `reverse`/`redirect`, sans chemin absolu codé en dur.
1. Continue d'ignorer l'ancien identifiant pour toute mutation : il ne doit servir ni à charger ni à supprimer un projet actuel.
1. Garde l'événement analytique existant seulement là où il était émis, sauf duplication déjà démontrée comme erreur ; ne crée pas de nouveau comptage par double décoration.
1. Évite une refonte du fichier de routes entier, un renommage général « simulations → projets » ou une suppression de composants historiques hors de ce périmètre.

## Vérifications proportionnées

Ce chantier est une simplification de faible impact : réutilise d'abord les tests existants. N'ajoute pas une suite qui ne ferait que vérifier l'implémentation du helper. Complète une régression de contrat seulement si une différence utile n'est pas couverte.

Vérifie au minimum, par les moyens adaptés au dépôt :

- Chaque ancien nom est encore réversible avec ses arguments et résout vers une vue protégée.
- Chaque ancienne URL publique conserve son statut HTTP et pointe vers la liste actuelle des projets.
- Un identifiant legacy inexistant ou égal à celui d'un projet actuel ne provoque aucune mutation.
- Utilisateur anonyme : même parcours de connexion qu'avant.
- Utilisateur connecté sans accès Indy Hub : même refus qu'avant.
- Utilisateur autorisé : même redirection et même message informatif.
- GET et POST : conserve le contrat constaté, sans faire d'une ancienne action destructive une suppression effective.
- L'événement analytique historique reste émis le même nombre de fois pour le parcours concerné.
- Les opérations actuelles de renommage/suppression de `ProductionProject` ne sont pas redirigées vers le helper legacy par erreur.
- Les templates et imports existants continuent de résoudre les noms conservés.

Commence par les vérifications de résolution et les tests de projets/navigation pertinents, puis les contrôles plus larges requis par `TESTING.md`. Un test de redirection ne valide pas une session navigateur réelle : précise cette limite.

## Critères d'acceptation

- Le diff réduit la duplication sans modifier le contrat des anciennes URL.
- Les quatre contrôleurs visés, s'ils existent encore, partagent uniquement la logique commune appropriée.
- Aucun ancien objet n'est recréé, transformé ou supprimé et aucune migration n'a changé.
- Les vues modernes de projets gardent leurs règles de permission et leur comportement.
- Tous les fichiers modifiés servent directement ce petit chantier.

## Bilan final demandé

Donne une liste des routes vérifiées et de leurs destinations, les fichiers modifiés, le mécanisme commun retenu et la raison de sa taille. Indique les vérifications exécutées, leurs résultats, les limites et ce qui n'a pas été exécuté. Réévalue les avantages et inconvénients après implémentation, en signalant honnêtement si le gain est seulement une petite amélioration de maintenance.

## Validation des usages dans Chrome

Avant de clore ce chantier, lis et applique `indy_hub/temporary_prompt/VALIDATION-CODE-USAGE-CHROME.md`. En complément des tests du code, exécute dans une vraie session Chrome les parcours consommateurs concernés, avec des comptes synthétiques sur une instance de test isolée. Ne remplace pas les vues locales à qualifier par des réponses simulées ; seules les frontières externes restent simulées. Ne réutilise ni le port ni la session de l'installation active. Si seul Chromium est disponible, indique-le explicitement.

Consigne le navigateur et sa version, les scénarios et rôles exécutés, les résultats, les erreurs console/réseau/serveur et les captures utiles dans un dossier temporaire sans secrets. Un navigateur non fonctionnel, un cas non exécuté ou une assertion en échec doivent être rapportés comme tels : les tests unitaires seuls ne permettent pas de déclarer les usages concernés validés. Aucun appel ESI/OAuth réel ou modification de l'installation active n'est autorisé par cette validation.
