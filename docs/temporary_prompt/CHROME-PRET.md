# Chrome prêt pour les validations locales

Le blocage de lancement a été résolu le 7 novembre 2026. Une session réelle **Google Chrome for Testing 149.0.7827.55** a été pilotée avec **Playwright Core 1.60.0**, en modes visible et headless, avec le sandbox Chrome activé.

## Réparation effectuée

- Les bibliothèques Ubuntu `libnspr4`, `libnss3` et `libasound2` ont été téléchargées depuis les dépôts configurés, puis extraites dans `/tmp/indyhub-browser-tools/runtime`. Aucun paquet système n'a été installé.
- Playwright Core a été installé exclusivement dans `/tmp/indyhub-browser-tools/node_modules`, avec sa version verrouillée dans les fichiers npm de ce répertoire. Le `package.json` et le lockfile du dépôt n'ont pas été modifiés.
- Le navigateur déjà disponible dans le cache utilisateur a été conservé. Son nom de dossier contient `chromium`, mais son exécutable s'identifie comme **Google Chrome for Testing** ; la qualification repose sur l'exécutable lancé et sa version.
- Le Node existant est utilisé, sans mise à jour globale. Aucun paquet Python, fichier du V5, réglage AA, worker, broker ou cache actif n'a été modifié.

## Relancer le contrôle de disponibilité

Depuis la racine du dépôt :

```bash
node scripts/check_indyhub_chrome.cjs --all-modes
```

Le script charge `/tmp/indyhub-browser-tools/config.json`, lance son propre serveur HTTP sur un port libre en loopback et crée des profils navigateur temporaires. Il n'accepte pas une URL du site actif et ne contacte pas Indy Hub.

- `--all-modes` vérifie le mode headless puis une fenêtre visible sur le display disponible.
- `--headed` vérifie seulement la fenêtre visible.
- Sans option, le contrôle s'exécute uniquement en headless.
- `INDY_HUB_BROWSER_CONFIG` permet de désigner une autre configuration préparée explicitement.

Le confinement Codex peut bloquer les primitives système de Chrome avant l'ouverture du navigateur. Lors de cette qualification, il a fallu exécuter la commande hors du confinement Codex, dans le cadre de l'autorisation de préparation du navigateur. **Le sandbox de Chrome est resté activé.** Ne remplace pas cette exécution par l'ajout de `--no-sandbox`.

## Preuves du contrôle réussi

Le [rapport JSON du contrôle](/tmp/indyhub-chrome-check-kKgYOT/report.json) contient les versions, les modes, les scénarios et les erreurs attendues. Les éléments suivants ont été vérifiés :

- Navigation HTTP locale, saisie et envoi d'un formulaire par POST.
- Rechargement avec conservation de la valeur dans le serveur synthétique temporaire.
- Erreur serveur 500 volontaire, conservation des données affichées puis reprise réussie.
- Contextes de session distincts et absence de partage du cookie synthétique.
- Blocage avant émission d'une requête de test vers un domaine extérieur.
- Rendu desktop et mobile, sans débordement horizontal ; captures ouvertes et inspectées.
- Aucune exception JavaScript de page ni réponse HTTP inattendue. Les erreurs console conservées dans le rapport correspondent au 500 volontaire et à la requête extérieure bloquée.
- Statut interne Chrome : namespaces PID/réseau et sandbox Seccomp-BPF actifs.

Captures de la fenêtre visible : [desktop](/tmp/indyhub-chrome-check-kKgYOT/headed-desktop.png) et [mobile](/tmp/indyhub-chrome-check-kKgYOT/headed-mobile.png). Le [statut du sandbox](/tmp/indyhub-chrome-check-kKgYOT/headed-sandbox.txt) est également conservé. Le serveur et les sessions de ce contrôle ont été arrêtés à sa fin.

Ces preuves qualifient **l'outillage navigateur**, pas les workflows d'Indy Hub. Les tests d'usage de chaque modification restent à exécuter sur une instance AA de test isolée, avec son backend réel et les frontières externes simulées selon le protocole.

## Réutiliser Chrome dans les scénarios des prompts

Le chemin de lancement qui a été vérifié est :

```javascript
const fs = require('node:fs');
const config = JSON.parse(fs.readFileSync('/tmp/indyhub-browser-tools/config.json', 'utf8'));
const {chromium} = require(config.playwrightModule);
const browser = await chromium.launch({
    executablePath: config.browserExecutable,
    headless: false,
    chromiumSandbox: true,
    env: {
        ...process.env,
        LD_LIBRARY_PATH: config.libraryPath
            + (process.env.LD_LIBRARY_PATH ? ':' + process.env.LD_LIBRARY_PATH : '')
    }
});
// Créer ensuite un contexte de test ; toujours fermer browser dans un finally.
```

Applique le [protocole code et usages](VALIDATION-CODE-USAGE-CHROME.md) pour l'instance Django, les comptes, la CSRF, les assertions et les preuves. Le [guide Playwright des navigateurs](https://playwright.dev/docs/browsers) distingue les moteurs, les canaux et leurs dépendances ; les versions effectivement utilisées doivent être rapportées, sans supposer qu'un autre binaire donne le même résultat.

## Durée de disponibilité et limites

Les dépendances isolées, leur configuration et les preuves sont dans `/tmp`. Elles sont disponibles pour les prochains prompts sur cet environnement, mais peuvent disparaître après nettoyage ou redémarrage. Vérifie les chemins et relance le contrôle avant de qualifier une modification.

Si ce répertoire a disparu, prépare de nouveau l'outillage isolé et ses trois bibliothèques ; ne lance pas une installation globale ni une mise à jour du V5 par défaut. Les empreintes des archives utilisées sont conservées dans `/tmp/indyhub-browser-tools/dependencies-sha256.json`, et les versions npm dans son lockfile tant que ces fichiers existent.

Un résultat antérieur ne remplace pas un contrôle après changement de navigateur, de pilote ou d'environnement. La fenêtre visible nécessite également un display fonctionnel ; un contrôle headless seul doit être présenté comme tel.
