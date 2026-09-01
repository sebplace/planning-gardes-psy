# DECISIONS.md — Décisions d'architecture et arbitrages métier

Chaque décision porte un identifiant stable, une date, un statut et une justification.
Les décisions **métier** proviennent des arbitrages humains du 1er septembre 2026 et sont
considérées comme fermes pour le prototype. Les décisions **techniques** peuvent être
révisées sans nouvel arbitrage métier.

---

## Arbitrages métier (fermes pour le prototype)

### M-001 — Mode A : un senior en première ligne travaille seul
**Date** 2026-09-01 · **Statut** Fermé
Lorsqu'un senior assure directement la première ligne, aucune deuxième ligne supplémentaire
n'est requise. **Mise en œuvre** : le mode de couverture est matérialisé par les postes
créés (`coverage_posts`). Mode A ⇒ un unique poste L1 `SENIOR`. Il est structurellement
impossible de rattacher une L2 à une occurrence en mode A. Test 5, test 29.

### M-002 — Reprise : sollicitation simultanée, collecte, puis tirage au sort auditable
**Date** 2026-09-01 · **Statut** Fermé
Après publication, la reprise est proposée **simultanément** à toutes les personnes
éligibles d'une même vague. Toutes les réponses favorables sont collectées pendant une
fenêtre **adaptée à la proximité de la garde**, puis un tirage au sort auditable désigne la
personne retenue. **Conséquence explicite** : répondre plus vite ne procure aucun avantage.
Tests 14, 15, 32, 33, 43.

### M-003 — Le résultat du tirage est immédiatement officiel
**Date** 2026-09-01 · **Statut** Fermé
Aucune validation administrative supplémentaire. C'est l'unique exception à la règle de
validation humaine finale, et elle est bornée au choix entre volontaires déjà éligibles.
Tests 15, 44.

### M-004 — Quotas saisis manuellement
**Date** 2026-09-01 · **Statut** Fermé (jusqu'à stabilisation de la formule institutionnelle)
Les cibles de période et annuelles sont saisies par un administrateur. La quotité/TIMA est
**enregistrée comme donnée** ; le moteur n'en dérive aucune valeur. Le module de projections
peut tester des formules hypothétiques, toujours étiquetées « hypothèse de démonstration »,
sans jamais toucher aux quotas opérationnels. Voir Q-01.

### M-005 — Une date rouge est une interdiction absolue
**Date** 2026-09-01 · **Statut** Fermé
Tant que la personne concernée n'a pas elle-même modifié son rouge, aucune affectation n'est
possible : ni par le moteur, ni par un administrateur, ni par un appel direct à l'API, ni via
une reprise, un échange ou la conversion de non-réponse. **Aucune commande de dérogation
n'existe dans le code.** La modification passe par une réouverture tracée de la réponse de la
personne. Tests 1, 31, 47.

### M-006 — Priorité des préférences souples des seniors
**Date** 2026-09-01 · **Statut** Fermé
Les préférences **souples** (vert/orange) des seniors priment sur celles des assistants,
conformément au fonctionnement institutionnel décrit. Cette priorité :
- est **active et non désactivable** dans le profil de règles `OPERATIONNEL` ;
- n'est paramétrable que dans les profils `SIMULATION` ;
- ne s'applique **jamais** aux rouges, à la sécurité, au repos, aux éligibilités ni aux
  plafonds fermes ;
- une fois appliquée, les contraintes restantes sont réparties aussi équitablement que
  possible entre les assistants.
Les rouges des assistants sont **exactement aussi fermes** que ceux des seniors.

### M-007 — Comptabilisation d'une reprise et d'un échange
**Date** 2026-09-01 · **Statut** Fermé
Une reprise est comptabilisée à la personne qui **assure réellement** la garde et retirée du
compteur de la personne remplacée. L'écart de quota est **reporté dans le suivi** et pris en
compte lors de la campagne suivante, sans remanier automatiquement le planning publié.
Un échange bilatéral n'est possible **qu'entre deux gardes équivalentes** et laisse donc les
compteurs inchangés. Tests 16, 34, 35.

### M-008 — Non-réponse : disponibilité par défaut, distincte d'un vert déclaré
**Date** 2026-09-01 · **Statut** Fermé
`DISPO_DEFAUT` est une valeur d'énumération **à part entière**. Le moteur la traite comme un
vert (la non-réponse ne bloque pas la répartition et n'avantage pas la personne), mais
l'interface, les exports, les explications et les journaux la présentent toujours comme
« disponible par défaut — non confirmé par la personne ». Elle n'est appliquée qu'après les
relances **et** le délai de grâce, uniquement sur les champs réellement non renseignés.
Tests 8, 37, 38, 39.

---

## Décisions techniques

### D-001 — Monolithe modulaire, API JSON d'abord
**Statut** Accepté
Un seul processus, modules internes explicites, aucune communication réseau interne.
L'interface web et l'API partagent la même couche de services : il n'existe pas de règle
métier accessible par un chemin et contournable par l'autre.

### D-002 — Interface rendue côté serveur (Jinja2 + JS vanilla) plutôt que SPA TypeScript
**Statut** Accepté · **Écart assumé au brief §19**
Le brief indique « préférences à justifier : TypeScript côté interface ». Retenu autrement
parce qu'un prototype doit être auditable et démarrable sans chaîne de build ni
`node_modules`, et parce que l'exigence dominante est l'accessibilité et l'usage smartphone
sans formation, pas la richesse d'interaction. L'API JSON reste complète et documentée
(`/docs`), donc un SPA TypeScript peut être ajouté ultérieurement sans réécrire les services.

### D-003 — Moteur déterministe maison plutôt que CP-SAT
**Statut** Accepté · **Écart assumé au brief §11**
`ortools` n'a **aucune roue installable** sur la plateforme cible (Windows ARM64, Python
3.11), et sa compilation est hors de portée d'un prototype. Le brief autorise explicitement
« une solution plus simple si elle est mieux justifiée ».
Le moteur est donc un **glouton contraint + recherche locale**, entièrement déterministe à
graine fixée, et — avantage propre — capable d'expliquer chaque affectation candidat par
candidat, ce qu'un modèle CP-SAT ne fournit pas nativement.
L'interface `SolverBackend` isole cet algorithme : un `CpSatBackend` peut être branché sans
toucher au reste. Conséquence honnête à consigner : le moteur ne prouve pas l'optimalité et
ne prouve pas l'infaisabilité globale ; il rapporte une **impossibilité constatée** poste par
poste, avec les motifs d'exclusion.

### D-004 — SQLite par défaut, PostgreSQL supporté
**Statut** Accepté
`DATABASE_URL` pilote le dialecte. La démo locale tourne sans serveur de base.
`docker-compose.yml` fournit la variante PostgreSQL. Les migrations Alembic sont écrites de
manière portable. Les verrous d'atomicité sont abstraits (`BEGIN IMMEDIATE` sous SQLite,
`SELECT ... FOR UPDATE` sous PostgreSQL) derrière un helper unique.

### D-005 — Le moteur est un paquet pur, sans dépendance à la base
**Statut** Accepté
`engine/` n'importe ni SQLAlchemy, ni FastAPI. Entrées/sorties : dataclasses.
Conséquence : le moteur est testable seul, rejouable hors application, et le même code sert
au planning réel et aux simulations capacitaires — ce qui garantit qu'un équilibre déclaré
« théoriquement couvrable » a bien été vérifié par le moteur réel.

### D-006 — Journal d'audit chaîné par empreinte
**Statut** Accepté
`audit_events.hash = sha256(prev_hash || payload canonique)`. Une réécriture a posteriori
casse la chaîne et devient détectable. Le journal du tirage est donc immuable au sens
vérifiable, pas seulement au sens « on n'a pas prévu de bouton pour l'effacer ».

### D-007 — Preuve de hasard du tirage par engagement puis révélation
**Statut** Accepté
Au **gel de la liste**, le serveur tire `server_seed` et n'enregistre que
`sha256(server_seed)`. Au tirage, la graine est révélée et le résultat vaut
`HMAC-SHA256(server_seed, list_hash) mod n`. Tout tiers peut recalculer et vérifier que
la graine correspond bien à l'engagement pris avant le calcul. Une contrainte d'unicité sur
la vague interdit tout second tirage officiel.

### D-008 — Transitions d'état gardées plutôt que verrous applicatifs
**Statut** Accepté
Toute opération concurrente sensible (dépôt de candidature, annulation, tirage, acceptation
d'échange) s'exécute via `UPDATE ... WHERE state = :attendu` avec contrôle du nombre de
lignes affectées, dans une transaction. La protection est donc **côté serveur**, pas dans le
navigateur. Tests 36, 48, 51.

Deux garde-fous complètent ce mécanisme :
- `assignments.busy_operation` : une garde ne peut participer qu'à **une seule** opération
  à la fois. La prise de ce marqueur est elle-même une transition gardée, ce qui empêche
  qu'une reprise et un échange soient ouverts simultanément sur la même garde.
- `swap_proposals.announced_profile_a_id` / `announced_profile_b_id` : les titulaires
  **annoncés au moment de la proposition** sont mémorisés et revérifiés à l'officialisation.
  Un échange accepté est refusé si la garde a changé de main entre-temps — cas qu'une simple
  relecture du titulaire courant ne permettrait pas de détecter (test 46).

### D-009 — Idempotence des notifications par clé métier
**Statut** Accepté
`notifications.idempotency_key` est unique et dérivée du fait métier
(`type + entité + occurrence + rang de rappel`). Un redémarrage ou une nouvelle tentative
technique ne peut donc produire ni double rappel, ni double sollicitation, ni double
changement d'état. Tests 32, 38, 45.

### D-010 — Profils de règles versionnés
**Statut** Accepté
`OPERATIONNEL` et `SIMULATION` sont des profils nommés et versionnés. Les poids des critères
souples y sont des données, pas des constantes enfouies. Chaque exécution du moteur persiste
le profil utilisé, sa version, la graine et l'empreinte des entrées.

### D-011 — Discriminant de module dès le socle
**Statut** Accepté
`module` (`GARDES` / `PERMANENCES_JOUR`) est porté par le catalogue et les campagnes dès la
première version, afin que le futur module de permanences de jour réutilise comptes, rôles,
notifications et calendrier sans migration structurante. Aucune logique de ce module n'est
implémentée. Test 24.
