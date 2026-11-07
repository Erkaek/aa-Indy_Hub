# Prompts Codex — Indy Hub

Les dix prompts révisés sont disponibles dans [indy_hub/temporary_prompt](indy_hub/temporary_prompt/README.md).

La [seconde analyse des conventions Alliance Auth](indy_hub/temporary_prompt/ANALYSE-CONVENTIONS-AA.md) explique les références comparées, les corrections et les priorités.

Chaque prompt contient ses avantages, ses inconvénients, les étapes à suivre, les tests et les critères d'acceptation. Lance-les un à la fois et préserve les changements déjà présents dans le dépôt.

Chaque réalisation doit suivre la [validation du code et des usages dans Chrome](indy_hub/temporary_prompt/VALIDATION-CODE-USAGE-CHROME.md), avec preuves et limites explicites. Une session Chromium doit être annoncée comme telle ; un navigateur indisponible laisse les parcours concernés non qualifiés.

Le [blocage Chrome est résolu](indy_hub/temporary_prompt/CHROME-PRET.md) : modes visible et headless testés, sandbox activé. La commande de contrôle est `node scripts/check_indyhub_chrome.cjs --all-modes`.

1. [Tokens partagés](indy_hub/temporary_prompt/01-tokens-partages.md)
1. [Client ESI](indy_hub/temporary_prompt/02-client-esi.md)
1. [Limites ESI](indy_hub/temporary_prompt/03-limites-esi.md)
1. [Pagination et snapshots](indy_hub/temporary_prompt/04-pagination-snapshots.md)
1. [Profils d'autorisation](indy_hub/temporary_prompt/05-profils-autorisation.md)
1. [Routes legacy](indy_hub/temporary_prompt/06-routes-legacy.md)
1. [Singleton Material Exchange](indy_hub/temporary_prompt/07-singleton-material-exchange.md)
1. [Accès SDE par l'ORM](indy_hub/temporary_prompt/08-acces-sde-orm.md)
1. [Administration partagée Alliance Auth](indy_hub/temporary_prompt/09-admin-partage-allianceauth.md)
1. [Prix publics par le client ESI](indy_hub/temporary_prompt/10-prix-publics-client-esi.md)

Ordre conseillé : **09 → 01 → 02 → 05 → 10 → 03 → 08**. Le **04** reste conditionnel à une optimisation sûre ; les **06 et 07** sont facultatifs. Les numéros sont conservés pour ne pas casser les liens existants.

Pour lancer un chantier dans Codex :

```text
Lis indy_hub/temporary_prompt/01-tokens-partages.md
et exécute le chantier décrit. Préserve les modifications déjà présentes.
```
