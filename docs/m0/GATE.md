# Gate humain M0

**Décision actuelle : VALIDÉ POUR DRY_RUN/PAPER et construction TESTNET le 10
juillet 2026.** L'ajout explicite des identifiants Hyperliquid et la demande de
poursuivre autorisent l'adaptateur TESTNET. Son activation reste conditionnée par le
diagnostic automatique (wallet API dédié, autorisation, solde de test et Postgres).
SUPERVISED et LIVE restent bloqués.

## Décisions proposées

- Valider la séparation stricte entre checkpoint LangGraph et état métier Postgres.
- Valider `ExecutionService` comme unique frontière d'effets de bord.
- Valider le protocole at-most-once : intent unique, `cloid` déterministe, nonce et
  requête signée persistés, réconciliation avant toute nouvelle décision.
- Valider que la sûreté prime sur la disponibilité lorsqu'un ACK reste ambigu.
- Valider les DTO de prompt sans equity, taille, notionnel, levier ni PnL.
- Valider le schéma durable minimal et les cinq modes d'exécution.
- Valider le graphe incluant le stratège conditionnel avant le trader.

## Blocages d'entrée à fournir

1. Le repo privé réel à auditer/migrer (stratégies, orchestration et intégration
   Hyperliquid existantes).
2. La suite du plan source, absente après le titre `2.3 AgentState`, afin de conserver
   les milestones et critères d'acceptation voulus.
3. Décision prise en v0.3 : SL complet + échelle de TP persistés, simulation PAPER,
   `normalTpsl`/`positionTpsl` TESTNET et réconciliation par CLOID.
4. Une décision sur la venue du mode SUPERVISED (testnet ou mainnet) ; aucune valeur
   implicite ne doit être utilisée.

## Durcissements requis avant intégration de la couche LLM

Les 47 contrôles fournis passent, mais ne démontrent pas encore toute la frontière
I2. Avant de brancher cette couche au graphe, un milestone doit ajouter :

- la cohérence obligatoire `decision.symbol == plan.symbol == snapshot.symbol` et
  l'identité du plan avec la version persistée du playbook ;
- la validation des datetimes UTC (`created_at`, `expires_at`, `as_of`) et de leur
  ordre, pour éviter une exception naïf/aware au milieu d'un cycle ;
- des contraintes strictement positives/cohérentes sur tous les seuils de
  `LLMLayerConfig`, ainsi qu'une map de caps non vide couvrant la whitelist ;
- le remplacement des `assert` de précondition du sizing par des erreurs métier
  explicites (les asserts disparaissent avec `python -O`) ;
- un DTO et un test d'intégration prouvant que le payload réellement envoyé aux LLM
  ne contient equity, quantité, taille, notionnel, levier ou PnL.

Les clés, webhooks et identifiants Langfuse ne sont pas nécessaires pour valider M0
et ne doivent pas être ajoutés au dépôt.

## Formule de validation attendue

`M0 VALIDÉ` autorise DRY_RUN/PAPER et la campagne TESTNET soumise à ses interlocks.
Toute réserve doit identifier la décision à modifier. SUPERVISED et LIVE resteront
bloqués par leurs gates dédiés.
