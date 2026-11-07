# Prompts Codex — Alignement d'Indy Hub avec Alliance Auth

Ces dix prompts reprennent les chantiers de l'audit du 7 novembre 2026, après une seconde comparaison avec Alliance Auth 5, django-esi et des modules communautaires disponibles localement. Chaque fichier constitue un prompt autonome, à copier dans Codex ou à lui demander de lire et d'exécuter. Chacun contient ses avantages, ses inconvénients, son périmètre, ses risques, son plan de travail, ses tests et ses critères d'acceptation.

Les prompts préparent une modification du dépôt ; leur création n'exécute aucune des modifications proposées. Les constats de l'audit doivent être revérifiés à chaque lancement, car le module et ses dépendances évoluent en parallèle.

La [revue des conventions et des prompts](ANALYSE-CONVENTIONS-AA.md) explique les références comparées, les corrections apportées, les éléments déjà conformes et les limites de l'analyse. Ce rapport est un document de lecture, pas un onzième chantier à exécuter.

La [validation du code et des usages dans Chrome](VALIDATION-CODE-USAGE-CHROME.md) est exigée pour les changements applicatifs : tests automatisés et parcours réels des écrans concernés sur instance isolée. ESI/OAuth restent simulés tant qu'un essai réel n'est pas explicitement autorisé. Le navigateur exact et les scénarios non exécutés doivent être indiqués ; aucune réussite navigateur n'est déduite des seuls tests Django.

**Outillage prêt :** le [contrôle Chrome](CHROME-PRET.md) a réussi en modes visible et headless, avec sandbox activé, sans intervention sur le V5 ou les services actifs. Sa configuration isolée est réutilisable tant que son répertoire temporaire existe.

## Choisir un chantier

| N°  | Prompt                                                                        | Avantage principal                                                                              | Inconvénient ou risque principal                                                                                       |
| --- | ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| 01  | [Tokens partagés](01-tokens-partages.md)                                      | Confier leur cycle de vie à django-esi et éviter les suppressions métier dans la table commune. | Déplacer correctement les déclencheurs après la disponibilité des scopes, sans perdre ni doubler les synchronisations. |
| 02  | [Client ESI](02-client-esi.md)                                                | Retirer les anciens fallbacks et utiliser les API natives documentées.                          | Préserver les contrats de la façade et les versions supportées.                                                        |
| 03  | [Limites ESI](03-limites-esi.md)                                              | Éviter deux gestions du même mécanisme de limitation ESI.                                       | Distinguer les limites natives des budgets métier et des protections propres à Indy Hub.                               |
| 04  | [Pagination et snapshots](04-pagination-snapshots.md) — conditionnel          | Réduire les lectures redondantes quand une solution sûre existe.                                | Un snapshot incomplet peut provoquer des suppressions de données ; conserver le contrôle est une conclusion valide.    |
| 05  | [Profils d'autorisation](05-profils-autorisation.md)                          | Centraliser les scopes et règles réutilisés par les vues et CharLink.                           | Ne pas élargir les autorisations ou confondre couverture d'un personnage et de tous les personnages.                   |
| 06  | [Routes legacy](06-routes-legacy.md) — facultatif                             | Mutualiser les redirections conservées pour les anciens liens.                                  | Gain faible ; préserver les URL, permissions et événements sans nouvelle infrastructure.                               |
| 07  | [Singleton Material Exchange](07-singleton-material-exchange.md) — facultatif | Utiliser django-solo pour une configuration singleton explicite.                                | Le gain doit justifier le coût de préservation des configurations historiques.                                         |
| 08  | [Accès SDE par l'ORM](08-acces-sde-orm.md)                                    | Réduire le SQL spécifique et l'introspection des noms d'objets.                                 | Préserver l'anglais, les replis et les anciennes versions SDE supportées.                                              |
| 09  | [Administration partagée AA](09-admin-partage-allianceauth.md) — prioritaire  | Éviter les écrans concrets User/Group supplémentaires et laisser AA gérer ses restrictions.     | Vérifier les permissions des modèles/proxys et accepter les libellés natifs si nécessaire.                             |
| 10  | [Prix publics par le client ESI](10-prix-publics-client-esi.md)               | Unifier le transport ESI public avec le fournisseur partagé.                                    | Préserver les montants Decimal, le cache, les 304 et le contrat de timeout.                                            |

## Lancement

Dans une session Codex ouverte sur ce dépôt, utilise par exemple :

```text
Lis indy_hub/temporary_prompt/01-tokens-partages.md
et exécute le chantier décrit. Préserve les modifications déjà présentes.
```

Lance un seul prompt à la fois et examine son résultat avant le suivant. Cette progression réduit les conflits dans `signals.py`, `views/user.py`, `services/esi_client.py`, les tests et les documents techniques.

Les numéros sont des identifiants stables, pas l'ordre de priorité. Progression conseillée après réinspection : **09 → 01 → 02 → 05 → 10 → 03 → 08**, puis **04** seulement si l'optimisation est démontrée sûre. Les prompts **06 et 07 sont facultatifs** et peuvent attendre si leur bénéfice ne justifie pas l'intervention.

Le prompt 05 bénéficie du travail sur les tokens. Les prompts 03, 04 et 10 doivent relire la façade après le prompt 02. Ne lance pas une seconde refonte du même fichier si un chantier précédent en a déjà résolu le problème. Une correction peut être livrée en plusieurs petits diffs identifiables sans mélanger les responsabilités.

## Limites communes

- Chaque prompt autorise l'implémentation dans son périmètre du dépôt et des vérifications isolées. Il ne demande pas de modifier directement l'installation active.
- Les migrations publiées, les données existantes et les modifications utilisateur doivent être préservées.
- Le V5 connu se trouve dans `/home/aadev/venv-v5`, à revérifier. Sa présence n'autorise pas une installation de paquets, une migration réelle ou un redémarrage.
- Aucun appel OAuth/ESI/Discord réel ni usage des workers, du cache ou du broker actifs n'est nécessaire pour ces travaux.
- Un chantier déjà corrigé doit être vérifié, pas réimplémenté. Une optimisation dont la sûreté n'est pas établie doit conserver la protection existante et expliquer la limite.
- Les snapshots industriels, les permissions métier et les budgets propres à Indy Hub restent nécessaires ; ces prompts ne demandent pas de les supprimer pour réduire artificiellement le nombre de lignes.
- Les exemples de modules AA servent de comparaison, pas de dépendances à ajouter ou de modèles à recopier intégralement. Les helpers Bravado ou les templates d'application AA 4 ne sont pas une référence de transport AA 5.
- Ne crée pas de moteur de profils, de scheduler, de table ou de migration simplement pour rendre la structure plus « classique ». Le contrat réel et le bénéfice démontré priment sur l'uniformité de forme.

Les anciennes alertes déjà traitées pendant la session — opération ESI des divisions, commandes de maintenance, qualification des dépendances ou sauvegarde Material Exchange — ne sont pas des chantiers à répéter automatiquement.
