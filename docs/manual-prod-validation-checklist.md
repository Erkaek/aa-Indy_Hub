# Checklist de validation manuelle en production

Cette checklist sert à vérifier la version `unreleased` après déploiement en production.

Pour chaque test, cocher la case seulement si le comportement observé correspond exactement au résultat attendu.

## Avant de commencer
- [ ] Utiliser un compte de test ou un compte non critique si possible.
- [ ] Garder un accès aux logs applicatifs et, si disponible, aux logs Celery / scheduler.
- [ ] Ouvrir un projet Craft de test avec au moins une recette à plusieurs outputs finaux.
- [ ] Prévoir au moins un cas connu de notification qui peut être déclenché sans risque.

## 1. Santé générale
- [ ] Ouvrir l’application et vérifier qu’aucune erreur 500 n’apparaît sur les pages principales.
	- Contexte : confirmer que l’application démarre bien avec la version déployée et que les pages principales restent accessibles.
	- Attendu : les pages s’affichent normalement, sans page d’erreur, sans écran blanc et sans exception visible.
- [ ] Contrôler rapidement les logs applicatifs juste après le déploiement.
	- Contexte : repérer tout souci de boot, de migration ou de dépendance immédiatement après la mise en ligne.
	- Attendu : pas d’erreur critique, pas de traceback, pas de message répétitif anormal.
- [ ] Vérifier qu’aucune erreur liée à une migration ou à une tâche de démarrage n’est remontée.
	- Contexte : certains correctifs touchent l’initialisation et les tâches planifiées.
	- Attendu : aucune erreur de migration, aucun échec de tâche de démarrage, aucun warning bloquant.

## 2. Notifications
- [ ] Déclencher un événement de notification connu et vérifier qu’une seule notification est produite par canal attendu.
	- Contexte : prendre un scénario qui génère volontairement une notification, par exemple une action de test ou un événement métier simple.
	- Attendu : une seule notification visible par canal concerné, sans double envoi ni absence d’envoi.
- [ ] Si le mode de dispatch configuré en production est `both`, vérifier qu’une notification AA et une notification Discord sont bien émises, sans doublon dans un même canal.
	- Contexte : ce mode sert à envoyer vers deux destinations distinctes quand c’est souhaité.
	- Attendu : une notification AA et une notification Discord, chacune une seule fois.
- [ ] Relancer le même événement dans la fenêtre d’idempotence et confirmer qu’il n’est pas renvoyé.
	- Contexte : ce point vérifie la protection contre les doublons automatiques.
	- Attendu : le second déclenchement n’envoie rien de nouveau pendant la durée d’idempotence.
- [ ] Valider que le comportement réel correspond au mode de dispatch configuré en production.
	- Contexte : s’assurer que la config production utilisée correspond au mode attendu par l’exploitation.
	- Attendu : le comportement observé correspond exactement à la valeur configurée.

## 3. Structures synchronisées
- [ ] Ouvrir une structure synchronisée existante.
	- Contexte : utiliser une structure déjà remontée depuis ESI afin de tester le comportement en conditions réelles.
	- Attendu : la fiche de structure s’ouvre sans erreur et les données sont visibles.
- [ ] Vérifier que les champs de taxe et de rig restent éditables si c’est le comportement attendu.
	- Contexte : certains champs sont volontairement modifiables côté local, même si la structure est synchronisée.
	- Attendu : les champs éditables peuvent être modifiés dans l’interface.
- [ ] Modifier une valeur simple, enregistrer, puis recharger la page pour confirmer la persistance.
	- Contexte : faire un changement minime pour valider la chaîne lecture / écriture.
	- Attendu : la valeur sauvegardée est conservée après rechargement.
- [ ] Lancer une synchronisation de structure et vérifier que les champs locaux modifiables ne sont pas écrasés.
	- Contexte : tester le point critique où une synchro distante pourrait remplacer une donnée locale.
	- Attendu : les champs locaux autorisés restent inchangés après la synchronisation.
- [ ] Confirmer qu’un enregistrement partiel ne casse ni les autres champs ni l’affichage du formulaire.
	- Contexte : vérifier qu’une sauvegarde partielle n’introduit pas de régression dans le formulaire.
	- Attendu : aucun champ n’est perdu, aucun bloc du formulaire ne disparaît, aucune erreur n’apparaît.

## 4. Tâches périodiques
- [ ] Vérifier que les schedules attendues existent bien après déploiement.
	- Contexte : contrôler que les tâches récurrentes sont toujours enregistrées après l’upgrade.
	- Attendu : les schedules configurées sont présentes avec des intervalles cohérents.
- [ ] Observer au moins une exécution réelle d’une tâche planifiée ou un passage de beat.
	- Contexte : confirmer que le scheduler tourne bien en prod.
	- Attendu : au moins une tâche se déclenche normalement ou le beat montre un cycle d’exécution correct.
- [ ] Confirmer qu’aucune erreur liée à la réapplication des tâches périodiques n’apparaît dans les logs.
	- Contexte : la migration de remise en place des tâches doit rester silencieuse en fonctionnement normal.
	- Attendu : aucun traceback, aucun échec de réinitialisation, aucun état incohérent.

## 5. Craft workspace
- [ ] Ouvrir un projet Craft existant avec plusieurs outputs finaux.
	- Contexte : le test doit utiliser un projet qui a vraiment plusieurs produits finaux, pas un cas trivial.
	- Attendu : le workspace se charge et affiche plusieurs outputs finaux.
- [ ] Modifier la quantité demandée sur un output final dans le tree.
	- Contexte : vérifier que le champ de quantité finale est bien éditable item par item.
	- Attendu : la quantité saisie est prise en compte dans l’interface.
- [ ] Vérifier que le bouton de mise à jour des quantités finales apparaît quand il doit apparaître.
	- Contexte : le bouton ne doit s’afficher que quand les quantités ont été modifiées.
	- Attendu : le bouton apparaît uniquement après modification, puis disparaît une fois l’état resynchronisé.
- [ ] Cliquer sur la mise à jour, recharger le workspace, puis confirmer que les quantités sont conservées.
	- Contexte : valider la persistance côté workspace et côté payload sauvegardé.
	- Attendu : après rechargement, les quantités sont toujours présentes et identiques.
- [ ] Vérifier que le calcul des runs reste cohérent avec les quantités finales renseignées.
	- Contexte : s’assurer que la quantité finale modifie bien le calcul attendu et pas seulement l’affichage.
	- Attendu : les runs calculés correspondent aux quantités demandées.
- [ ] Faire un cas multi-output pour confirmer que chaque output final garde sa propre quantité.
	- Contexte : c’est le cas le plus important pour valider la logique par item final.
	- Attendu : chaque output final garde sa propre valeur, sans écrasement entre lignes.

## 6. Parcours métier de base
- [ ] Refaire un chargement complet de page après modification pour s’assurer qu’aucun état UI n’est perdu.
	- Contexte : tester la résilience de l’état applicatif après navigation ou refresh.
	- Attendu : les choix effectués restent cohérents après rechargement.
- [ ] Vérifier qu’un parcours normal de consultation, modification puis sauvegarde fonctionne toujours.
	- Contexte : faire un aller-retour complet sur un flux de base pour s’assurer qu’aucun correctif n’a cassé le quotidien.
	- Attendu : le parcours se termine sans erreur et les données sont bien enregistrées.
- [ ] Si possible, rejouer les scénarios les plus sensibles avec un compte de test plutôt qu’avec un compte principal.
	- Contexte : limiter le risque opérationnel pendant la vérification.
	- Attendu : la validation est possible sur un compte non critique.

## Critère de sortie
- [ ] Aucun comportement bloquant, aucune erreur visible et aucun écart entre ce qui est saisi, enregistré et rechargé.
	- Contexte : c’est le seuil minimum pour considérer la version validée.
	- Attendu : aucune régression bloquante et une cohérence complète entre saisie, sauvegarde et rechargement.
