# Architecture — Planification des gardes psychiatriques (PROTOTYPE)

> **Prototype de démonstration, données entièrement fictives.** Ni outil institutionnel,
> ni logiciel de production. Aucun message réel n'est envoyé.

---

## 1. Vue d'ensemble

Monolithe modulaire Python, API JSON d'abord, interface web rendue côté serveur,
base relationnelle, **moteur de planification isolé et sans dépendance à la base**.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Interface web (Jinja2 + JS minimal, française, responsive, a11y)     │
│  Écrans : connexion · module · tableau de bord · campagne · calendrier│
│  V/O/R · quotas · génération · comparaison · publication · reprises   │
│  · tirage · échanges · projections · notifications · audit            │
└───────────────┬──────────────────────────────────────────────────────┘
                │  (mêmes services que l'API)
┌───────────────▼──────────────────────────────────────────────────────┐
│  API HTTP JSON (FastAPI)   /api/v1/...                               │
└───────────────┬──────────────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────────────┐
│  Couche services (règles métier, transactions atomiques, audit)      │
│  accounts · catalog · quotas · campaign · planning · handover ·      │
│  swap · projection · notification · audit · rules (profil versionné) │
└───────┬───────────────────────────────────────────┬──────────────────┘
        │                                           │
┌───────▼────────────────────┐          ┌───────────▼──────────────────┐
│  Persistance SQLAlchemy    │          │  MOTEUR (paquet `engine/`)   │
│  SQLite (démo) / Postgres  │          │  PUR : dataclasses in/out    │
│  Alembic (migrations)      │          │  0 import SQLAlchemy         │
└────────────────────────────┘          │  hard_constraints            │
                                        │  soft_criteria               │
                                        │  solver (déterministe)       │
                                        │  projection (arithmétique)   │
                                        │  explain / impossibility     │
                                        └──────────────────────────────┘
```

### Pourquoi cette pile (et écarts assumés au brief §19)

| Préférence du brief | Choix retenu | Justification |
|---|---|---|
| TypeScript côté interface | **Jinja2 + JS vanilla** | Le prototype doit être *auditable* et démarrable sans chaîne de build ni `node_modules`. L'API reste JSON d'abord : un SPA TypeScript peut être ajouté ultérieurement sans toucher aux services. Écart consigné dans `DECISIONS.md` (D-002). |
| Python ou TypeScript côté serveur | **Python 3.11 / FastAPI** | Conforme. |
| PostgreSQL | **PostgreSQL supporté**, SQLite par défaut | `DATABASE_URL` pilote le dialecte. `docker-compose.yml` fourni pour Postgres. La démo locale tourne sans serveur. |
| Solveur CP-SAT | **Moteur déterministe maison**, interface `SolverBackend` | `ortools` n'a **aucune roue installable** sur la plateforme cible (Windows ARM64). Le brief autorise « une solution plus simple mieux justifiée ». Bénéfice supplémentaire : chaque décision est explicable pas à pas, ce qu'un modèle CP-SAT ne fournit pas nativement. Un `CpSatBackend` peut être ajouté derrière la même interface. Écart consigné (D-003). |
| Docker Compose | **Fourni** | Non exécuté dans l'environnement de développement (pas de Docker). |
| Éviter les microservices | **Monolithe modulaire** | Conforme. |

---

## 2. Modèle de données

Quatre séparations structurantes, jamais fusionnées dans un champ unique :

1. **statut professionnel** (`SENIOR` / `ASSISTANT`) ;
2. **droits applicatifs** (`is_medecin`, `is_admin`, cumulables et indépendants) ;
3. **éligibilité métier** (par ligne et par type de garde, datée) ;
4. **période d'activité et historique de quotité** (dates, TIMA en dixièmes).

### 2.1 Comptes et profils

| Table | Champs clés |
|---|---|
| `users` | `email`, `display_name`, `password_hash`, `is_medecin`, `is_admin`, `is_active` |
| `professional_profiles` | `user_id`, `status` (SENIOR/ASSISTANT), `code` |
| `activity_periods` | `profile_id`, `start_date`, `end_date?`, `reason` — l'expiration d'un assistant est *dérivée*, pas un booléen figé |
| `quotite_history` | `profile_id`, `start_date`, `end_date?`, `tenths` (0..10), `tima_label` — **donnée**, aucune formule appliquée par le moteur |
| `eligibilities` | `profile_id`, `line` (L1/L2), `garde_type_id?`, `eligible`, dates |

### 2.2 Catalogue des gardes

Quatre notions distinctes, comme exigé au §6 :

| Table | Rôle |
|---|---|
| `quota_categories` | **catégorie comptable** (nuits L-J · week-ends et veilles · jours fériés) |
| `garde_types` | **type concret** (nuit de semaine, nuit du vendredi, samedi, dimanche, veille de férié, jour férié) : horaires, `crosses_midnight`, `duration_class`, `count_weight`, `default_coverage_mode`, `exchange_class_id` |
| `garde_occurrences` | **instance datée** : `start_at`/`end_at` en UTC, `local_date`, `coverage_mode?` (surcharge de la date) |
| `coverage_posts` | **poste de couverture** requis : `line` (L1/L2), `required_status` |
| `exchange_classes` | classe d'équivalence administrable pour l'échange bilatéral |
| `holiday_pairs` / `holiday_pair_members` | paires fériées, un membre = intervalle de dates (permet de rattacher la veille nocturne) |

**Mode A** (senior en L1) → l'occurrence a **un seul** `coverage_post` de ligne L1 exigeant `SENIOR`.
**Mode B** → **deux** postes : L1 `ASSISTANT` + L2 `SENIOR`.
La matérialisation des postes est donc la seule source de vérité du mode ; il est impossible
de créer une L2 « derrière un senior de L1 » sans changer explicitement le mode.

### 2.3 Quotas

`quota_targets` (par profil × année × catégorie × ligne) distingue **cible souple**,
**minimum ferme optionnel** et **maximum ferme optionnel**.
`exemptions` (totale/partielle, datée, commentée, historisée).
`quota_adjustments` conserve l'écart reporté après une reprise, sans remanier le planning publié.

### 2.4 Campagne et désidératas

`campaigns` (états), `submissions` (états par personne), `availabilities`
(`occurrence`, `line?`, `color`, `is_declared`, `source`).

La couleur `DISPO_DEFAUT` est une **valeur d'énumération à part entière**, distincte de `VERT` :
le moteur la traite comme un vert, l'interface, les exports, les explications et les journaux
la présentent toujours comme « disponible par défaut — non confirmé par la personne ».

### 2.5 Planning

`engine_runs` (instantané reproductible : graine, version de règles, version moteur,
hash des entrées) → `proposals` (variantes scorées) → `schedule_versions` (états)
→ `assignments` (+ `is_locked`, `origin`) → `manual_corrections` (auteur, date, motif).

### 2.6 Reprises et échanges

`handover_requests` → `handover_waves` (VERTE puis ORANGE) → `candidacies` → `draws`
(unicité stricte sur la vague).
`swap_proposals` pour l'échange bilatéral.

### 2.7 Transverse

`notifications` (clé d'idempotence unique), `audit_events` (**chaînés par hash** :
`hash = sha256(prev_hash + payload)`, ce qui rend toute réécriture détectable),
`scenarios` / `scenario_results` (projections, sans effet opérationnel).

---

## 3. Machines à états

```
Campagne   PREPARATION → OUVERTE → CLOTUREE → RESOLUTION_NON_REPONDANTS → PRETE → ARCHIVEE
Réponse    NON_COMMENCEE → BROUILLON → VALIDEE → VERROUILLEE   (réouverture tracée)
Planning   GENERE → EN_REVISION → VALIDE → PUBLIE → REMPLACE
Reprise    BROUILLON → COLLECTE_VERTE → LISTE_FIGEE_VERTE → TIRAGE_VERT
                     ↘ (aucune candidature valide) → COLLECTE_ORANGE → LISTE_FIGEE_ORANGE
                       → TIRAGE_ORANGE → ATTRIBUEE
                     ↘ ESCALADE | ANNULEE | EXPIREE
Échange    PROPOSE → ACCEPTE_A / ACCEPTE_B → (revérification atomique) → OFFICIEL
                   ↘ REFUSE | ANNULE | EXPIRE
```

Un planning publié n'est jamais réécrit : toute modification crée une nouvelle version
et bascule la précédente en `REMPLACE`.

---

## 4. Matrice contraintes fermes / critères souples

### 4.1 Contraintes fermes (vérifiées **avant** toute optimisation, jamais violables)

| Code | Contrainte | Portée |
|---|---|---|
| **H01** | Tous les postes de couverture requis sont pourvus | moteur |
| **H02** | Aucune attribution sur `ROUGE`, ni par le moteur, ni par un administrateur, ni par l'API | moteur + services + API |
| **H03** | Un assistant n'est **jamais** en ligne 2 | moteur + services |
| **H04** | Toute ligne 2 est assurée par un senior | moteur + services |
| **H05** | Tout senior est compatible avec tout assistant (aucun binôme figé) | moteur (absence de contrainte) |
| **H06** | Aucun chevauchement temporel ni incompatibilité déclarée | moteur + services |
| **H07** | Comptes expirés, inactifs ou hors période d'activité exclus | moteur + services |
| **H08** | Exemptions, quotas nuls, maximums fermes et affectations verrouillées respectés | moteur + services |
| **H09** | Règles de repos déclarées **fermes** respectées | moteur + services |
| **H10** | Mode A : un seul poste L1 senior, **aucune L2** ; mode B : L1 assistant + L2 senior | modèle + moteur |
| **H11** | Une personne ne peut occuper deux postes de la même occurrence | moteur |
| **H12** | Le demandeur d'une reprise est exclu de sa propre vague | services |
| **H13** | Un échange n'est possible qu'entre gardes structurellement équivalentes | services |

**H02 est absolue.** Il n'existe aucune commande, aucun paramètre et aucun point d'entrée
d'API permettant de forcer un rouge. Seule la personne concernée peut remplacer son rouge
par orange ou vert, via une réouverture tracée de sa réponse.

### 4.2 Critères souples (départagent uniquement des solutions déjà réalisables)

| Code | Critère | Poids démo | Statut |
|---|---|---|---|
| **S01** | Privilégier le vert ; pénaliser l'orange | 100 | profil de démonstration |
| **S02** | Progresser vers les quotas annuels (écart au prorata) | 60 | profil de démonstration |
| **S03** | Prévenir les rattrapages massifs de fin d'année | 25 | profil de démonstration |
| **S04** | **Préférences souples des seniors prioritaires sur celles des assistants** | multiplicateur ×3 sur S01 pour les seniors | **arbitrage métier validé — verrouillé en profil opérationnel** |
| **S05** | Maximiser l'espacement entre gardes | 40 | profil de démonstration |
| **S06** | Limiter gardes rapprochées, nuits concentrées, week-ends successifs | 30 | profil de démonstration |
| **S07** | Équilibrer les catégories pénibles | 20 | profil de démonstration |

`DISPO_DEFAUT` est scorée **exactement comme un vert** : la non-réponse ne pénalise ni
n'avantage la personne.

**S04** est actif et non modifiable dans le profil `OPERATIONNEL` : une génération réelle ne
peut pas le désactiver silencieusement. Sa paramétrabilité n'existe que dans les profils
`SIMULATION`. Il ne s'applique jamais aux rouges, à la sécurité, au repos, aux éligibilités
ni aux plafonds fermes.

### 4.3 Départage reproductible

`(retard relatif au quota) → (meilleur espacement) → (pseudo-aléatoire reproductible)`,
la dernière étape étant un `random.Random(seed_run + clé stable du poste)`.
Graine, version des règles, version du moteur et hash des entrées sont persistés.

---

## 5. Le moteur

`engine/` ne connaît **ni la base, ni HTTP, ni FastAPI**. Entrées et sorties sont des
dataclasses. Il est donc testable seul et rejouable à l'identique.

**Algorithme** (`GreedyLocalSearchBackend`) :

1. matérialisation des postes et calcul, pour chacun, de l'ensemble des candidats
   **admissibles au regard des seules contraintes fermes** (avec, pour chaque personne
   écartée, un motif exploitable dans le rapport d'impossibilité) ;
2. ordonnancement déterministe des postes par contrainte croissante (moins de candidats
   d'abord, puis clé stable) ;
3. affectation gloutonne au meilleur coût marginal, départage §4.3 ;
4. retour arrière borné si un poste devient sans candidat ;
5. recherche locale déterministe (déplacements et échanges deux à deux) bornée en
   itérations, acceptation uniquement si le score s'améliore ;
6. production de 1 à 3 variantes, avec **distance de diversité minimale imposée** pour
   éviter trois résultats quasi identiques.

Si aucune solution complète n'existe : **brouillon partiel + rapport d'impossibilité**
listant chaque poste non pourvu et, personne par personne, la contrainte ferme qui l'exclut.
Aucune contrainte ferme n'est jamais relâchée automatiquement.

**Explication d'une affectation** : rôle, ligne, couleur (et son caractère déclaré ou non),
retard au quota au moment du choix, espacement obtenu, contribution de chaque critère souple,
et candidats écartés avec leur motif.

---

## 6. Le tirage au sort d'une reprise

Exception métier explicitement bornée (§4.4 du brief) : elle ne concerne **que** le choix
entre plusieurs volontaires **déjà éligibles**, jamais la génération du planning.

Déroulement auditable :

1. **Gel** de la liste à l'échéance de la fenêtre (ou dès que toutes les personnes
   sollicitées ont répondu). `list_hash = sha256(ids de candidatures triés)`.
2. Au gel, le serveur tire `server_seed` (`secrets.token_hex(32)`) et **n'enregistre que
   son empreinte** `seed_commitment = sha256(server_seed)` dans l'événement de gel :
   la graine est donc prouvablement antérieure au calcul du résultat.
3. **Revérification** de chaque candidature : couleur actuelle (un rouge exclut
   immédiatement), éligibilité, conflits, repos, activité du compte, expiration.
4. **Tirage** : `index = HMAC-SHA256(server_seed, list_hash) mod n` sur la liste **figée et
   revalidée**. La graine est alors révélée et stockée : n'importe qui peut recalculer.
5. **Une seule tentative** : contrainte d'unicité sur la vague + transition d'état gardée.
   Ni utilisateur ni administrateur ne peut choisir une graine, relancer ou demander un autre
   résultat.
6. Le résultat est **immédiatement officiel**. Planning, quotas, historique et clôture de la
   demande sont mis à jour **dans une seule transaction**.

Si une seule candidature reste valide, le **même événement d'attribution auditable** est
exécuté sur cet ensemble réduit : il n'y a pas d'aléa utile, mais l'officialisation résulte
toujours de cet événement journalisé.

**Atomicité** : toutes les transitions concurrentes (dépôt de candidature, annulation,
tirage, acceptation d'échange) passent par des transitions d'état gardées côté serveur
(`UPDATE ... WHERE state = :attendu` + contrôle du nombre de lignes affectées, `BEGIN
IMMEDIATE` sous SQLite, `SELECT ... FOR UPDATE` sous PostgreSQL). Deux opérations
concurrentes aboutissent donc toujours à un seul état final.

---

## 7. Fenêtres et rappels adaptatifs

Profil `UrgencyProfile` versionné et administrable, valeurs de démonstration :

| Délai avant la garde | Fenêtre de collecte | Rappels |
|---|---|---|
| < 12 h | 90 min | +30 min, +60 min |
| 12 h – 48 h | 6 h | +2 h, +4 h |
| 48 h – 7 j | 24 h | +8 h, +16 h |
| > 7 j | 72 h | +24 h, +48 h |

Ces seuils sont des **hypothèses de démonstration** (`OPEN_QUESTIONS.md`, Q-08).

---

## 8. Sécurité, confidentialité, absence de données patient

- Comptes et données **entièrement fictifs**.
- Mots de passe : PBKDF2-HMAC-SHA256, sel par compte (suffisant pour un prototype ; une
  production exigerait un facteur de travail mémoire type Argon2id).
- Un médecin ne voit que ses propres quotas, désidératas, affectations et reprises.
  Aucune comparaison nominative entre collègues n'est exposée.
- L'identité du demandeur d'une reprise est masquée jusqu'à l'attribution officialisée.
- Tout champ libre porte l'avertissement :
  *« Ne pas encoder d'information concernant un patient dans cette application. »*
  Une validation serveur refuse par ailleurs les champs libres dépassant la longueur prévue.

---

## 9. Extensibilité vers le module « Permanences de jour »

Le catalogue (`garde_types`, `quota_categories`, `coverage_posts`, `campaigns`) est
générique et porte un discriminant `module` (`GARDES` / `PERMANENCES_JOUR`).
La navigation expose déjà l'entrée désactivée « Permanences de jour — à venir ».
Aucune logique de ce module n'est implémentée dans ce prototype.
