# Validation du code et des usages dans Chrome

Ce protocole complète chacun des dix prompts. L'utilisateur demande des tests du code et des parcours réels dans un navigateur avant de considérer une modification comme qualifiée. Ce document ne lance aucun chantier applicatif à lui seul.

Le blocage de lancement initial est résolu : voir [Chrome prêt et commande de contrôle](CHROME-PRET.md). Commence par relancer ce contrôle pour confirmer l'état de l'outillage. Il ne remplace pas les parcours Indy Hub décrits ci-dessous.

## Niveaux de preuve

1. **Code** : tests unitaires et d'intégration ciblés, puis contrôles plus larges nécessaires selon `docs/codex/TESTING.md`.
1. **Usage navigateur** : interaction avec les vrais écrans et vues Django d'une instance de test, sous les rôles pertinents, avec des données synthétiques.
1. **Services externes** : ESI, OAuth, Discord et broker réel restent simulés ou isolés. Un parcours navigateur réussi avec ces frontières simulées ne qualifie pas leur fonctionnement réel.

Ne présente ni le client de test Django, ni un test de chaîne HTML, ni une capture seule comme une session Chrome réelle. Les assertions serveur et le navigateur se complètent. Les scénarios internes non observables dans l'interface restent vérifiés au niveau approprié, sans ajouter un écran artificiel uniquement pour les tester.

## Préparer l'instance et le navigateur

- Confirme le code effectivement servi et les dépendances utilisées. Une instance qui charge une ancienne copie de `site-packages` ne valide pas le diff du dépôt.
- Lance une instance de test distincte avec base temporaire, cache dédié, broker isolé et port disponible explicitement choisi. Ne réutilise pas implicitement `localhost:8001`, qui peut être l'installation active.
- Examine les effets de `AppConfig.ready()`, des migrations, signaux, GET de pages et tâches avant de démarrer. Ne publie rien sur les services actifs.
- Crée des comptes et des sessions de test locaux. Utilise des contextes ou profils temporaires séparés pour anonyme, utilisateur sans permission, utilisateur autorisé, administrateur limité et superutilisateur selon le chantier. N'emprunte pas la session personnelle existante de l'utilisateur ni ses cookies réels.
- Utilise Chrome avec Playwright ou un moyen de pilotage équivalent déjà disponible. Relève le nom, la version et le mode du navigateur réellement lancé. Si seul Chromium fonctionne, indique explicitement « Chromium », sans annoncer un test Chrome. Si seul le mode headless est exécuté, indique-le aussi.
- Vérifie le pilote et les bibliothèques nécessaires avant de déclarer le navigateur disponible. Ne modifie pas l'installation V5 ou le système pour contourner une erreur. Un outillage supplémentaire se prépare isolément quand c'est possible ; toute intervention système distincte doit être explicitement autorisée.
- Ne désactive pas les protections du navigateur pour obtenir un résultat. Privilégie un profil temporaire et un environnement où son sandbox fonctionne ; ne copie pas automatiquement un argument `--no-sandbox` d'un ancien script.
- Bloque les sorties réseau vers ESI/OAuth/Discord et les autres services externes durant ces tests. Utilise des réponses contrôlées aux frontières externes, en conservant le vrai rendu Django, les requêtes navigateur vers le serveur local, la CSRF et les écritures sur la base jetable.
- Ne remplace pas l'API locale concernée par une réponse simulée lorsqu'elle fait partie du comportement à valider. Cela masquerait le défaut du backend derrière une interface apparemment correcte.

Le script `scripts/test_admin_users_console_browser.cjs` est une référence de scénarios existants, pas une commande sûre à lancer sans lecture. Il utilise par défaut un port local et un cookie fourni et contient un argument de lancement à examiner. Adapte son environnement pour l'instance jetable ; n'utilise pas ses valeurs par défaut contre l'installation active.

## Scénarios de code et d'usage par chantier

| Prompt        | Vérifications de code indispensables                                             | Parcours réels à effectuer dans le navigateur                                                                                                                                                                         |
| ------------- | -------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 01 Tokens     | Cycle natif simulé, scopes M2M, autocommit/rollback, éligibilité et idempotence. | Ouvrir gestion des tokens et statuts ; vérifier un personnage couvert, incomplet et refusé pour le métier. Le refus ne fait pas disparaître l'autorisation partagée. Un simple affichage ne déclenche pas d'OAuth.    |
| 02 Client     | Contrats natifs hors réseau, cache chaud/froid, formes de réponse et erreurs.    | Ouvrir les listes ou pages qui consomment les méthodes changées ; déclencher une actualisation vers le backend local avec ESI simulé et vérifier données, attente, réussite ou erreur.                                |
| 03 Limites    | Délais, une intention de republication, budgets et invariants de snapshots.      | Simuler une limitation ou une indisponibilité côté frontière ESI et vérifier que la page n'annonce pas de faux succès, reste utilisable et expose le report prévu. Les détails de retry sont prouvés côté serveur.    |
| 04 Pagination | Plusieurs pages, 304, incohérence et absence de suppression indue en base.       | Afficher les snapshots synthétiques, faire échouer une synchronisation paginée et vérifier que les données précédentes restent visibles après retour/rechargement. La complétude reste établie par les tests serveur. |
| 05 Profils    | Matrice exacte scopes/permissions, couverture et retour sûr.                     | Parcourir les boutons d'autorisation, rôles autorisés/refusés et retours internes. Intercepter la sortie OAuth avant tout contact externe ; cela vérifie la demande préparée, pas un consentement CCP réel.           |
| 06 Legacy     | Résolution des URL, contrats des méthodes et aucune mutation.                    | Ouvrir les anciennes URL, vérifier destination, message et accès ; confirmer que l'identifiant historique ne modifie pas un projet synthétique actuel.                                                                |
| 07 Singleton  | Conservation du réglage et transition testée sur base jetable.                   | Via l'interface existante, vérifier la lecture et, si disponible, la modification du réglage, puis recharger. Ne crée pas un nouveau formulaire uniquement pour cette validation.                                     |
| 08 SDE        | Noms anglais, langues, replis, types publiés et requêtes bornées.                | Afficher les mêmes objets dans les pages affectées avec différentes langues et vérifier les noms et identifiants inconnus. Aucune résolution ESI supplémentaire ne doit être masquée par le navigateur.               |
| 09 Admin AA   | Registre concret/proxy, permissions et POST sur objets jetables.                 | Ouvrir les vrais écrans AA avec staff limité et superutilisateur ; contrôler routes, champs sensibles, formulaires et refus. Vérifier aussi qu'un POST local fabriqué ne contourne pas la restriction.                |
| 10 Prix ESI   | Opération publique, cache, Decimal, 304 et erreurs sans prix fictifs.            | Ouvrir les calculs craft concernés avec prix synthétiques connus ; vérifier les résultats affichés, le rechargement et le comportement après erreur simulée, sans modifier une commande réelle.                       |

Choisis les variantes pertinentes au diff ; ne rejoue pas toute l'application pour chaque petite extraction. Si aucune interface observable n'est affectée, explique-le et vérifie au moins le parcours consommateur approprié, sans qualifier une propriété serveur uniquement par l'absence d'erreur visuelle.

## Observations et preuves à conserver

- Scénarios attendus, actions effectuées et résultats observés, avec rôles et données synthétiques identifiés sans secret.
- Contrôles de navigation, chargement, formulaires, messages, actualisation et conservation des données selon le périmètre.
- Erreurs JavaScript, requêtes locales en erreur et exceptions serveur pertinentes ; distingue les refus attendus des erreurs imprévues.
- Captures utiles des étapes significatives et de l'état final. Contrôle le rendu ; une capture enregistrée sans lecture ni assertion ne prouve pas son exactitude.
- Vérification desktop et largeur mobile si le diff touche l'affichage ou les interactions adaptatives. Une modification strictement backend ne demande pas une campagne visuelle complète.
- Résultats avant/après nécessaires aux régressions, horodatage et provenance du code servi.

Stocke les preuves dans un répertoire temporaire clairement identifié. Ne commite pas les cookies, les fichiers de session, les tokens, les HAR non nettoyés ou des données personnelles. Ferme le navigateur et arrête uniquement les processus de test que tu as lancés ; préserve les ressources actives de l'utilisateur.

## Critère de clôture

Pour chaque scénario, indique **réussi**, **échoué** ou **non exécuté**, avec sa raison. Si un sélecteur du script est faux ou une assertion révèle un défaut, corrige la cause et rejoue le scénario ; ne supprime pas le contrôle pour obtenir un résultat vert.

Si le navigateur ne démarre pas, rapporte le blocage exact et les tests du code qui ont pu être terminés. La modification reste non qualifiée pour les parcours navigateur concernés. Ne remplace pas cette preuve par un « tout est OK » obtenu uniquement avec les tests unitaires.

Même après réussite, le compte rendu décrit le périmètre vérifié et les limites : ces tests réduisent le risque, sans garantir l'absence de tout défaut ni valider des services externes laissés simulés.
