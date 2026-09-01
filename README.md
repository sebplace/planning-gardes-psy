# Planification des gardes psychiatriques — PROTOTYPE

> ## ⚠️ Statut du logiciel
>
> **Ceci est un prototype de démonstration.**
> Toutes les données sont **entièrement fictives** (domaine réservé `.invalid`).
> Aucun message n'est réellement envoyé : les notifications sont écrites dans une
> boîte locale consultable dans l'application.
> **Ce n'est ni un outil institutionnel, ni un logiciel de production.**
> Aucune donnée patient n'est stockée, et ne doit jamais l'être.

---

## Les trois lectures possibles de ce dépôt

| Lecture | Ce que c'est | Ce que ce n'est pas |
|---|---|---|
| **Démonstration locale** | Un parcours complet jouable en une commande : projections, campagne, génération, publication, reprise avec tirage, échanges. | Une preuve que le logiciel fonctionnerait avec des données réelles. |
| **Prototype auditable** | Un code où chaque règle métier est explicite, traçable et testée : 60 tests couvrent les 52 exigences du cahier des charges. | Un logiciel validé institutionnellement. Les valeurs marquées « hypothèse de démonstration » ne sont **pas** des règles décidées. |
| **Base de discussion** | Un support pour trancher les questions ouvertes (`OPEN_QUESTIONS.md`) sur du concret plutôt que dans l'abstrait. | Une application prête à être déployée. La section « Avant une production » liste ce qui manque. |

---

## Démarrage en trois commandes

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows :  .venv\Scripts\Activate.ps1
pip install -r requirements.txt

python scripts/seed_demo.py       # crée la base et joue tout le parcours
uvicorn app.main:app --reload     # http://127.0.0.1:8000
```

Comptes de démonstration, mot de passe **`demo`** :

| Adresse | Rôle |
|---|---|
| `admin@demo.invalid` | administrateur seul |
| `sen01@demo.invalid` | médecin senior **et** administrateur (cumul, permissions séparées) |
| `sen02@demo.invalid` … `sen14@demo.invalid` | médecins seniors |
| `ass01@demo.invalid` … `ass06@demo.invalid` | assistants |
| `sen07@demo.invalid` | le **non-répondant** de la démonstration |

Documentation de l'API : <http://127.0.0.1:8000/api/docs>

### Variante PostgreSQL

```bash
docker compose up --build
docker compose exec app python scripts/seed_demo.py
```

---

## Ce que la démonstration montre réellement

`python scripts/seed_demo.py` déroule et commente sept étapes :

1. **Socle** — 14 seniors, 6 assistants, 2 administrateurs (dont un cumul),
   quotités variées, 90 occurrences sur un trimestre (modes A et B),
   5 paires de jours fériés fictives dont une à cheval sur deux années,
   quotas saisis manuellement, une exemption totale et une exemption partielle.
2. **Projections** — projection structurelle, matrice de sensibilité, simulation de
   faisabilité exécutée par le **même moteur** que le planning réel, et un scénario
   volontairement impossible qui produit un **déficit explicite** au lieu de gardes
   fictivement couvertes. Une tentative de promotion sans confirmation est refusée.
3. **Campagne de désidératas** — ouverture, rappels J-14 / J-7 / J-2 (qui cessent
   après validation), un non-répondant.
4. **Non-réponse** — la génération est **d'abord bloquée**, les administrateurs sont
   alertés, puis après relances et délai de grâce les dates non renseignées passent en
   *« disponible par défaut — non confirmé par la personne »*, statut distinct d'un
   vert déclaré partout.
5. **Génération et publication** — trois variantes comparées avec leur score détaillé,
   une tentative de correction manuelle sur une date rouge **refusée**, puis
   validation humaine et publication.
6. **Reprises** — une reprise verte avec plusieurs volontaires et tirage auditable,
   une reprise orange après échec de la vague verte, une reprise échouée avec escalade.
7. **Échanges** — un échange bilatéral officialisé entre deux gardes équivalentes,
   et un échange refusé entre gardes de nature différente.

En fin de parcours, l'intégrité de la chaîne d'audit est vérifiée.

---

## Architecture en une page

```
Interface web (Jinja2, française, responsive, accessible)
        │
API JSON (FastAPI)  /api/v1/...
        │
Couche services  ── règles métier, transactions atomiques, audit
        │                                   │
Persistance SQLAlchemy              MOTEUR (paquet `engine/`)
SQLite (démo) / PostgreSQL          pur, sans base ni HTTP
Alembic (migrations)                contraintes fermes · critères souples
                                    solveur déterministe · projections
```

Détails complets : **`ARCHITECTURE.md`**.

### Trois principes structurants

1. **Le moteur est isolé et pur.** `app/engine/` n'importe ni SQLAlchemy ni FastAPI.
   Il est donc testable seul, rejouable à l'identique, et le même code sert au planning
   réel et aux simulations capacitaires.
2. **Les contraintes fermes ont un point de définition unique.**
   `app/engine/hard.py` est appelé par le moteur, les corrections manuelles, les
   candidatures de reprise, le tirage, les échanges **et** l'API. Il n'existe aucun
   chemin où une règle serait contournable.
3. **Un rouge est absolu.** Aucun paramètre, aucune commande, aucun point d'entrée ne
   permet de le forcer. Seule la personne concernée peut le modifier, via une
   réouverture tracée de sa réponse.

---

## Le tirage au sort d'une reprise

C'est la seule exception à la validation humaine finale, et elle est explicitement
bornée au choix entre plusieurs volontaires **déjà éligibles**.

1. Sollicitation **simultanée** et **anonyme** de toutes les personnes éligibles.
2. Collecte pendant une fenêtre **adaptée à la proximité de la garde**.
   *Répondre plus vite ne procure aucun avantage.*
3. **Gel** de la liste. Le serveur tire alors une graine et n'enregistre que son
   empreinte : la graine est prouvablement antérieure au calcul du résultat.
4. **Revérification** de chaque candidature (un rouge exclut immédiatement).
5. **Tirage** : `index = HMAC-SHA256(graine, empreinte_liste) mod n`.
   La graine est révélée : n'importe qui peut recalculer.
6. **Une seule tentative officielle** (contrainte d'unicité + transition gardée).
7. Résultat **immédiatement officiel**. Planning, quotas, historique et clôture sont
   mis à jour **dans une seule transaction**.

L'écran `/reprises/{id}` affiche l'intégralité de cette preuve.

---

## Tests

```bash
python -m pytest tests -q
```

**60 tests, couvrant les 52 exigences de la section 22 du cahier des charges.**
La correspondance exigence → test est dans **`docs/TESTS.md`**.

---

## Ce qui n'est volontairement pas décidé

`OPEN_QUESTIONS.md` liste douze points laissés ouverts. Pour chacun :

- la valeur est **administrable** (paramètre en base, modifiable sans redéploiement) ;
- la valeur de démonstration est **étiquetée** dans l'interface et les exports ;
- aucun écran ne la présente comme une règle validée.

Points principaux : quotas exacts et formule TIMA, règles liées à l'âge (**aucun seuil
n'est codé**), horaires exacts, rattachement des veilles de fériés, exigence vert ou
vert + orange pour les paires, repos minimal, pondération des critères souples,
délai de grâce, seuils d'urgence des vagues, préférences par ligne, module de jour,
catalogue des classes d'échange.

Les arbitrages **déjà tranchés** sont dans `DECISIONS.md` (M-001 à M-008).

---

## Limites honnêtes de ce prototype

- **Le moteur ne prouve pas l'optimalité** et ne prouve pas l'infaisabilité globale.
  Il rapporte une *impossibilité constatée*, poste par poste, avec les motifs
  d'exclusion. `ortools` / CP-SAT n'étant pas installable sur la plateforme de
  développement (Windows ARM64), un moteur déterministe maison a été retenu derrière
  l'interface `SolverBackend` ; un `CpSatBackend` peut être branché sans toucher au
  reste (voir `DECISIONS.md` D-003).
- **L'interface est rendue côté serveur** plutôt qu'en SPA TypeScript, pour rester
  auditable et démarrable sans chaîne de build. L'API JSON est complète, donc un SPA
  reste possible (D-002).
- **La progression temporelle est manuelle.** Rappels, clôtures de fenêtres et tirages
  sont déclenchés par un appel explicite (bouton « Faire progresser », ou appel de
  service). Une exploitation réelle exigerait un ordonnanceur.
- **Le module « Permanences de jour » n'est pas développé.** Seuls les objets et la
  navigation sont préparés.
- **Aucun PDF imprimable** pour l'instant : CSV, Excel et ICS sont fournis.

---

## Avant une production : ce qui manque

Cette liste n'est pas décorative. Aucun des points suivants n'est traité.

1. **Authentification institutionnelle** (SSO/Entra ID) au lieu des comptes locaux ;
   hachage à facteur de travail mémoire (Argon2id) plutôt que PBKDF2.
2. **Ordonnanceur** fiable pour les rappels, clôtures de fenêtres, tirages et
   conversions de non-réponse, avec reprise après incident.
3. **Messagerie réelle** et gestion des échecs d'envoi, en remplacement de la boîte
   simulée.
4. **HTTPS**, en-têtes de sécurité, protection CSRF sur les formulaires, limitation de
   débit, journal des connexions.
5. **Sauvegardes et restaurations testées**, politique de conservation et de suppression.
6. **Revue de sécurité et analyse RGPD** complètes, registre des traitements, base
   légale, information des personnes, durées de conservation.
7. **Validation institutionnelle** de toutes les valeurs listées dans
   `OPEN_QUESTIONS.md`, en particulier les quotas, le repos minimal et les seuils
   d'urgence.
8. **Reprise des données existantes** et procédure de bascule.
9. **Tests de charge** et supervision.
10. **Accessibilité** : audit RGAA/WCAG par un tiers, et test réel avec lecteur d'écran.

---

## Organisation du dépôt

```
app/
  engine/          moteur pur : types, contraintes fermes, critères souples,
                   solveur déterministe, projections
  models/          persistance SQLAlchemy (comptes, catalogue, quotas, campagne,
                   planning, reprises, audit)
  services/        règles métier, transactions atomiques, journalisation
  web/             API JSON, interface Jinja2, gabarits, feuille de style
scripts/
  seed_demo.py     jeu de démonstration + parcours complet commenté
  smoke_engine.py  vérification rapide du moteur seul
tests/             60 tests couvrant les 52 exigences
migrations/        Alembic
ARCHITECTURE.md    architecture, modèle de données, matrice fermes/souples
DECISIONS.md       arbitrages métier (M-001…) et décisions techniques (D-001…)
OPEN_QUESTIONS.md  ce qui n'est pas décidé, et comment c'est rendu administrable
docs/TESTS.md      correspondance exigence → test
```
