# Audit différentiel — itération P0.x (gardes)

Livrable préalable demandé par le client. Données exclusivement fictives.
Aucune identité réelle. Le prototype n'est PAS déclaré gelé.

- Commit local actuel : `dce73c7` (arbre propre).
- Déployé : archive `v4` (Scalingo, planning-gardes-psy-demo, osc-fr1), succès 02/09 11:51.
  Déploiement par archive : Scalingo n'expose pas de SHA git, le libellé est `v4`.
- Migrations : `Procfile` -> `release: alembic upgrade head` ; une version présente
  (`241581395051_socle_initial`).
- Environnement live : `GARDES_ENVIRONMENT=production` positionné sur une instance
  de DEMONSTRATION (comptes @demo.invalid, mot de passe `demo`, seed drop_all()).
  Aucun garde-fou n'est actif aujourd'hui : anomalie critique (P0).

Colonnes : Exigence | État actuel (ancré code) | Modification prévue | Test associé | Résultat.
Résultat : `à faire` tant que non implémenté et prouvé.

Marqueur institutionnel : toute valeur non validée est administrable et étiquetée
« hypothèse de démonstration », consignée dans OPEN_QUESTIONS.md.

---

## Priorité 0 — transport et séparation des environnements

| # | Exigence | État actuel | Modification prévue | Test | Résultat |
|---|---|---|---|---|---|
| P0.1 | HTTP redirige vers HTTPS (301/308, via X-Forwarded-Proto) | Aucune redirection ; HTTP sert la page de connexion | Middleware de redirection basé sur `x-forwarded-proto`, actif hors demo locale | test : HTTP -> 308 vers HTTPS | à faire |
| P0.2 | Cookie de session Secure + HttpOnly + SameSite | `SessionMiddleware(https_only=False)` (main.py:37) | `https_only=True` en environnement déployé, SameSite=Lax, HttpOnly (Starlette pose HttpOnly) | test : Set-Cookie porte Secure | à faire |
| P0.3 | HSTS après validation redirection | Absent | En-tête `Strict-Transport-Security` en prod/staging | test : en-tête présent en HTTPS | à faire |
| P0.4 | HTTP ne sert aucun formulaire/contenu authentifié | HTTP sert tout | Redirection avant tout rendu | test : HTTP jamais 200 sur pages authentifiées | à faire |
| P0.5 | Séparer demo/staging/production ; production active de vrais garde-fous | `environment` défaut « demonstration » ; « production » n'active rien | `GARDES_ENVIRONMENT` normalisé (demonstration/staging/production) pilotant transport, docs, garde-fous | test : matrice de comportement par env | à faire |
| P0.6 | Échec démarrage en production si .invalid / mot de passe demo / secret faible / seed demo | Aucun contrôle | Contrôle de démarrage `assert_production_safe()` : refuse comptes .invalid, secret par défaut, marqueur demo | test : startup prod échoue sur base demo | à faire |
| P0.7 | Ne jamais transformer la base actuelle en production ; app+base distinctes par migrations | Base demo unique | Documenter et imposer : instance demo reste `GARDES_ENVIRONMENT=demonstration` ; prod = nouvelle app+base | doc + garde-fou P0.6 | à faire |
| P0.8 | Neutraliser le seed drop_all() hors demo | `seed_demo.py:814 drop_all()` inconditionnel | Verrou : refuse si env != demonstration OU base non marquée `is_demo` ; marqueur explicite requis | test : seed refuse sur staging/prod | à faire |
| P0.9 | Migrations Alembic versionnées en release + retour arrière testé | Release fait déjà `alembic upgrade head` ; startup create_all seulement en SQLite | Conserver ; ajouter downgrade testé sur base fictive (up/down/up) | test : downgrade/upgrade rejouable | partiel (release OK ; downgrade à tester) |

### État P0 — PROUVÉ EN DIRECT (déploiement `p5-transport`, 02/09/2026)

Instance déployée repassée en `GARDES_ENVIRONMENT=staging` (fictif, durci, seedable),
secret fort régénéré. `production` reste réservé à une future app+base distinctes.

- HTTP `/connexion` -> **308** vers `https://` (via X-Forwarded-Proto). Prouvé.
- HTTPS : en-têtes **HSTS**, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`. Prouvé.
- Cookie de session : `secure; httponly; samesite=lax`. Prouvé.
- `Cache-Control: no-store, private` sur `/api`. Prouvé.
- Swagger `/api/docs` -> **404** hors démonstration. Prouvé.
- Garde-fous démarrage (refus secret faible en déployé, refus comptes `.invalid` en production) + verrous seed/reset (interdits en production) : 12 tests verts, suite complète verte.
- Reste P0.9 : tester explicitement le downgrade Alembic (up/down/up) sur base fictive.

---

## Priorité 1 — matrice métier définitive

| # | Exigence | État actuel | Modification prévue | Test | Résultat |
|---|---|---|---|---|---|
| P1.1 | 15 seniors, pondérations 7,6,7,8,8,0,7,8,6,0,3,6,6,7,5 = 84/10 au 01/10/2026 | 14 seniors, quotités 10..5 ; pas de « pondération de garde » distincte | Modèle `garde_weight_history` (dixièmes, date d'effet) ; seed 15 seniors fictifs avec ces pondérations datées 01/10/2026 | test : somme = 84/10 au 01/10/2026 | à faire |
| P1.2 | Historiser quotité, pondération, exemptions/réductions + dates d'effet | Quotité historisée (`QuotiteHistory`) ; exemptions datées ; PAS de pondération de garde | Ajouter historique de pondération de garde ; conserver le reste | test : lecture datée correcte | à faire |
| P1.3 | Tous seniors L1 ou L2 sauf restriction/exemption datée ; L1 seul en mode A ; mode B assistant L1 + senior L2 | Éligibilités L1/L2 présentes ; modes A/B matérialisés | Aligner seed ; garder règles | test : éligibilités + modes | à faire |
| P1.4 | 3 assistants, 1re garde 19/10/2026, dernière 03/10/2027 incl., L1 uniquement, vert/rouge | 6 assistants, périodes 2026-09-01->2027-12-31, L1 only OK, mais orange autorisé | Seed 3 assistants, période 19/10/2026->03/10/2027 ; interdire orange pour assistants (saisie + contrainte) | test : bornes + refus orange assistant | à faire |
| P1.5 | Scénarios globaux 57 ou 68 gardes/assistant sur 50 semaines | Absent (quotas seedés différemment) | Deux scénarios de projection paramétrés (57 / 68) | test : projection 57 et 68 | à faire |
| P1.6 | Quota global période + plafond mensuel administrable ; plafond non fixé -> ne pas inventer | Pas de plafond mensuel | Ajouter `monthly_cap` administrable, NULL par défaut, alerte si absent | test : absence de plafond signalée, pas inventée | à faire |
| P1.7 | Objectif souple « 1 vendredi + 2 jours WE/mois » ; non contrainte dure | Absent | Critère souple, jamais dur | test : objectif ne crée aucune impossibilité dure | à faire |
| P1.8 | Week-end complet assistant (sam 9h-dim 9h + dim 9h-lun 9h) seulement sur demande explicite | Pas de notion de demande explicite week-end complet | Contrainte dure : bloc week-end complet assistant seulement si `explicit_request` | test : week-end complet refusé sans demande | à faire |
| P1.9 | Horaires : lun-jeu hors férié 17h-8h ; sam/dim/férié 9h-9h ; corriger le décalage live | `NUIT_SEMAINE` 20h-8h ; `SAMEDI/DIMANCHE/JOUR_FERIE` 8h-8h | Corriger horaires par défaut du seed : 17-8 (lun-jeu), 9-9 (sam/dim/férié) ; vendredi/veille administrables (demo hypothèse, pas 20h) | test : 17-8 et 9-9 ; absence de 20h et 8h-8h | à faire |
| P1.10 | Générer occurrences depuis dates+horaires réels ; jamais coder 365/372 | Génère 1/jour depuis dates (déjà correct) | Conserver ; vérifier volume issu des dates | test : volume = dates réelles, année bissextile, pas de doublon | à faire |
| P1.11 | 3 catégories comptables ; férié prime ; veille en WE reste WE | 3 catégories présentes ; férié via type ; include_eve | Vérifier priorité férié et rattachement veille | test : vendredi férié classé férié ; veille en WE = WE | à faire |
| P1.12 | 6 compteurs seniors (3 catégories x L1/L2) pondérés dixièmes+dates | Quotas par (catégorie, ligne) ; pas de 6 compteurs pré-agrégés | Vues/compteurs annuels 3x2 pondérés | test : 6 compteurs seniors | à faire |
| P1.13 | Sous-compteurs assistants (vendredi/veille/samedi/dimanche/férié) | Comptage par catégorie seulement | Sous-compteurs statistiques distincts | test : 5 sous-compteurs assistants | à faire |
| P1.14 | Publication d'un planning incomplet techniquement impossible | Génération bloquée si non-répondants ; publication ? | Refus serveur de publier une couverture incomplète | test : publish planning incomplet -> refus | à faire |
| P1.15 | Senior vert L1/L2 ; orange L2 seul ; rouge absolu ; non-réponse -> « dispo par défaut » distinct | H02 rouge, H02b non renseigné, S01 orange ; DISPO_DEFAUT distinct | Conserver ; vérifier interdiction L1 sur orange (dur) | test : orange -> L1 impossible, L2 possible | à faire |
| P1.16 | Aucune sanction auto si manque de verts : alerte + arbitrage humain | Non-réponse -> conversion ; pas de sanction | Alerte d'insuffisance de verts, sans effet automatique | test : alerte produite, pas de sanction | à faire |
| P1.17 | 5 paires de fêtes précises ; obligation minimale = férié choisi encodé vert ; veille = paramètre de simulation | 5 paires seedées ; include_eve existe | Aligner libellés ; obligation par défaut = jour férié vert ; veille = param simulation | test : jour férié vert suffit par défaut | à faire |
| P1.18 | Campagne T1 : ouverture 01/11, rappel 15/11, clôture 01/12, publication 07/12 ; modèle administrable propre au T1 | Dates campagne paramétrables mais pas via UI ; offsets « 30,14,7,2 » | Seed campagne T1 avec ces dates ; rendre administrable (UI P3) | test : dates 01/11, 15/11, 01/12, 07/12 | à faire |
| P1.19 | Permissions distinctes traçables : resp L1, resp L2, chef de service, gestion comptes, publication, consultation audit | Seulement is_admin / is_medecin | Modèle de permissions granulaire (rôles/permissions datées) + gardes serveur | test : matrice 401/403 par rôle | à faire |
| P1.20 | Ordre moteur : contraintes fermes listées ; critères souples 1..7 dans l'ordre | 13 contraintes fermes ; 6 souples S01..S07 ordre proche mais à aligner | Réordonner/nommer souples : (1) L2 vert>orange (2) écarts 6 quotas (3) concentration (4) équilibrer contraignantes (5) écarts historiques (6) limiter dispo défaut (7) préférence jour ultime | test : L2 préfère vert ; préférence jour en dernier | à faire |
| P1.21 | Explication précise règle + niveau de priorité départageant | Explication snapshot (quota, espacement, candidats écartés) | Ajouter la règle/niveau ayant départagé | test : explication cite règle+priorité | à faire |
| P1.22 | Reprises : titulaire publié responsable jusqu'au changement officiel | Handover : phrase « reste à charge tant que non confirmé » | Conserver principe ; corriger l'affichage après officialisation (P2.15) | test : titulaire actuel correct | à faire |
| P1.23 | Reprise L1 : uniquement verts explicites éligibles ; aucune vague orange ni dispo défaut | Vague verte inclut VERT + DISPO_DEFAUT ; puis vague orange | L1 : solliciter seulement VERT explicite ; supprimer vague orange et DISPO_DEFAUT en L1 | test : L1 limité aux verts explicites | à faire |
| P1.24 | Reprise L2 : une seule collecte verts+orange ; dispo défaut non incluse silencieusement | 2 vagues séquentielles | L2 : collecte unique VERT+ORANGE ; DISPO_DEFAUT laissé ouvert (non inclus par défaut) | test : L2 collecte unique verts+orange | à faire |
| P1.25 | Reprise simple seulement si volontaire a encore une garde à faire dans la catégorie et sous plafonds ; sinon échange équivalent | Attribution simple + ajustement quota | Ajouter la bascule vers échange bilatéral si cible/plafond atteint | test : cible atteinte -> échange, pas surcharge | à faire |
| P1.26 | Repos/récupération : pas de récup pour simple appel ; >12h sur place -> 12h récup ; jamais >24h continu ; garde ordinaire vendredi/samedi ne crée pas de crédit | H09 repos (min_hours_between, max_consecutive_weekends) ; pas de notion « sur place » ni 12/24h | Ajouter règles : interdiction >24h continu (dur) ; proposition 12h récup après 12h sur place (validation humaine) ; pas de crédit auto | test : >24h impossible ; 12h récup proposée | à faire |

---

## Priorité 2 — intégrité, concurrence, confidentialité

| # | Exigence | État actuel | Modification prévue | Test | Résultat |
|---|---|---|---|---|---|
| P2.1 | Version publiée totalement immuable ; retirer toutes commandes correction/verrou/validation/régénération/retour d'état ; refuser mêmes mutations services+API | Publié rejette corrections (planning_service:281) ; mais `set_lock()` et écrans de verrou existent sur versions | Retirer toute commande de mutation sur version PUBLIE (UI+services+API) ; toute modif = nouvelle version | test : version publiée refuse toute mutation | à faire |
| P2.2 | Contrainte + transaction : au max une version publiée par trimestre | Enforcé par logique (publish met les autres en REMPLACE) ; pas de contrainte | Index unique partiel `(quarter_id) WHERE state='PUBLIE'` + transaction | test : 2 publications même trimestre -> 1 seule | à faire |
| P2.3 | Atomicité « vague ouverte + enregistrer candidature » | `_guard()` UPDATE...WHERE state ; candidature séparée | Rendre atomique (transaction unique gardée) | test concurrent : candidature vs gel | à faire |
| P2.4 | Remplacer verrou générique `busy_operation='ECHANGE'` par identifiant unique d'opération | Verrou générique ECHANGE (swap_service) | `busy_operation_id` unique par opération | test : deux échanges chevauchants | à faire |
| P2.5 | Un rollback/nettoyage ne libère que les verrous de l'opération courante | `_release()` global | Libération ciblée par operation_id | test : nettoyage n'affecte pas une autre op | à faire |
| P2.6 | Acquérir/modifier les deux gardes d'un échange dans une transaction unique | execute_swap revérifie ; à confirmer transaction unique | Transaction unique verrouillant les deux assignments | test : échec -> aucun changement partiel | à faire |
| P2.7 | Sérialiser la tête de chaîne d'audit (verrou/séquence monotone + contrainte) | `last_hash()` par tri id DESC ; pas de séquence ni verrou | Séquence monotone + verrou transactionnel sur la tête | test concurrent : 2 écritures audit | à faire |
| P2.8 | Événement métier + audit dans la même transaction | Déjà dans la même transaction (record avant flush) | Conserver ; test | test : rollback annule les deux | à faire |
| P2.9 | Concurrence prouvée avec 2 connexions/processus (le seed séquentiel ne prouve rien) | Tests séquentiels une session | Harnais concurrence réelle (2 connexions distinctes) | voir suite « concurrence réelle » | à faire |
| P2.10 | Interdire une personne tirable après « refus » ; retrait officiel journalisé avant gel | Candidature DEPOSEE->VALIDE/EXCLUE ; refus ? | Un refus explicite retire de la liste, tracé ; jamais retirable ensuite | test : refus -> non tirable | à faire |
| P2.11 | Interdire modification directe d'un désidérata validé sans réouverture tracée | À vérifier | Refus serveur ; réouverture autorisée et journalisée | test : modif désidérata validé -> refus | à faire |
| P2.12 | Version EN_REVISION invisible aux médecins ordinaires (UI+API) | À vérifier (planning montre PUBLIE) | Filtrer EN_REVISION hors rôle habilité | test : médecin ne voit pas EN_REVISION | à faire |
| P2.13 | Corriger 4 explications obsolètes (01/01, 02/01, 17/01, 23/01 2027 L1) | Explications snapshot non recalculées | Recalcul/repli des explications après reprise/échange | test auto : 4 dates cohérentes | à faire |
| P2.14 | Afficher séparément : affectation initiale + explication ; titulaire actuel ; chronologie reprises/échanges | Affichage mêlé | Trois blocs distincts | test : 3 blocs présents | à faire |
| P2.15 | Reprises 1 et 4 attribuées : retirer la phrase « ancien titulaire responsable » après officialisation | Phrase persiste après attribution | Retirer la phrase une fois ATTRIBUEE | test : phrase absente après officialisation | à faire |
| P2.16 | Motif neutre aux collègues ; détail (rouge, repos exact) réservé à la personne + admins | Refus d'échange expose des valeurs de champs ; refus moteur peut exposer motifs fermes | Motif neutre côté collègue ; détail réservé | test : collègue voit motif neutre | à faire |
| P2.17 | Deux niveaux de preuve du tirage : anonymisée (médecins) / nominative (admins) | Preuve unique (proof_json) | Vue anonymisée + vue nominative habilitée | test : preuve anonymisée sans identités | à faire |
| P2.18 | Présenter l'audit comme détection de modifications, pas immutabilité absolue ; prévoir ancrage externe append-only ultérieur | Vérif de chaîne présente ; langage à ajuster | Reformuler UI/doc ; consigner ancrage externe en OPEN_QUESTIONS | test : libellé « détection » | à faire |

---

## Priorité 3 — Quotas et Projections utilisables

| # | Exigence | État actuel | Modification prévue | Test | Résultat |
|---|---|---|---|---|---|
| P3.1 | Quotas : saisir/historiser cibles/min/max/plafonds/exemptions/TIMA/dates/justif/auteur/aperçu | `set_target()` existe (service) + historique ; PAS d'UI de saisie ; exemptions non éditables UI | Écrans+routes admin de saisie et historisation (cibles, min/max, plafonds, exemptions, pondération, dates, justif, auteur, aperçu avant validation) | test : saisie -> historique+audit | à faire |
| P3.2 | Projections : créer/nommer/modifier/dupliquer/archiver/recalculer/comparer | save/duplicate/compute existent ; pas d'UI edit/archive/compare | UI complète scénarios + comparaison côte à côte ; JSON en volet secondaire | test : cycle scénario complet | à faire |
| P3.3 | Paramètres projection complets (assistants+périodes, quota global+plafond mensuel, pool senior daté, pondérations/exemptions, volumes/catégories réels, repos, modes A/B, conversion B->A, seuils souples) | ScenarioParams partiel | Étendre paramètres | test : projection 57/68 + params | à faire |
| P3.4 | Simulation sans effet opérationnel sans action admin explicite | promote exige confirmation | Conserver ; test | test : promote exige confirmation | à faire |

---

## Priorité 4 — durcissement application Internet

| # | Exigence | État actuel | Modification prévue | Test | Résultat |
|---|---|---|---|---|---|
| P4.1 | CSRF sur tous POST par cookie ; logout = POST protégé | Pas de CSRF ; `GET /deconnexion` | Jeton CSRF sur formulaires ; logout POST protégé | test : POST sans jeton -> refus | à faire |
| P4.2 | CSP (frame-ancestors), HSTS, X-Content-Type-Options, Referrer-Policy, Permissions-Policy, Cache-Control no-store authentifié | Aucun en-tête | Middleware d'en-têtes | test : en-têtes présents | à faire |
| P4.3 | Session admin plus courte (inactivité+absolue), renouvellement connexion/élévation, invalidation à rotation | Session sans expiration fine | Durées par rôle + rotation | test : expiration admin | à faire |
| P4.4 | Session Starlette signée non chiffrée : n'y placer aucun motif/commentaire/contrainte RH ; préférer id serveur opaque révocable | Session porte user_id (pas de RH) ; signée non chiffrée | Ne stocker qu'un id opaque révocable ; rien de sensible | test : contenu session minimal | à faire |
| P4.5 | Comptes nominatifs, invitation, activation/désactivation, récupération sûre, Argon2id, limitation échecs, MFA admin, SSO/OIDC idéal | PBKDF2 ; comptes seed ; pas de MFA/limitation | Argon2id (avec repli), limitation d'échecs, base invitation/activation ; MFA/SSO en OPEN_QUESTIONS | test : Argon2id + limitation | à faire |
| P4.6 | Swagger public seulement en demo | `/api/docs` toujours actif | Désactiver docs hors demonstration | test : docs 404 hors demo | à faire |
| P4.7 | Documenter OpenAPI (auth, permissions, schémas, 401/403/409) | Minimal | Enrichir OpenAPI | revue | à faire |
| P4.8 | Borner variants 1..3 et min_diversity | Non validé (variants Form(3)) | Bornes serveur | test : variants hors bornes -> 422 | à faire |
| P4.9 | Avancement reprise réservé à ordonnanceur/rôle habilité | Tout médecin peut avancer une reprise | Restreindre à rôle habilité (ou ordonnanceur) | test : médecin -> 403 sur avancer | à faire |
| P4.10 | Autorisation au niveau objet sur toutes routes {id} (anti IDOR) | Partiel (quotas OK) | Contrôle objet systématique | test : accès horizontal -> 403/404 | à faire |
| P4.11 | Journaux expurgés (motifs, dispo, commentaires, identités inutiles, secrets) | Payloads d'audit riches | Expurger les payloads sensibles | test : audit sans motif personnel | à faire |
| P4.12 | Neutraliser injection CSV | Export CSV non échappé | Préfixe protecteur sur cellules à risque | test : cellule `=cmd` neutralisée | à faire |
| P4.13 | Health liveness/readiness sans détail sensible | Absent | `/health/live` + `/health/ready` | test : 200/503 sans détail | à faire |
| P4.14 | Avant pilote : dépendances verrouillées/analysées, sauvegardes/restaurations testées, RPO/RTO, observabilité, charge, audit externe | Non traité | Documenter portes (OPEN_QUESTIONS + rapport) | doc | à faire |
| P4.HDS | Documenter périmètre HDS exact (app/base/sauvegardes), DPA + annexe HDS ; osc-fr1 vs osc-secnum-fr1 distincts | Marquage HDS:true seul | Documenter ; ne pas confondre HDS et SecNumCloud | doc | à faire |

---

## Tests d'acceptation (par suite)

- Suite MÉTIER (24) : voir P1/P2 (15 seniors 84/10, 3 assistants L1 vert/rouge 19/10/2026-03/10/2027,
  scénarios 57/68, plafond mensuel absent signalé, horaires 17-8 et 9-9, absence 20h/8h,
  objectif souple, dates réelles/bissextile/vendredi férié/veille, jour férié vert suffit,
  campagne T1, refus publication incomplète, L2 vert>orange, reprise L1 verts, reprise L2 collecte
  unique, vitesse sans effet, gel/revalidation/tirage, reprise sous quota/plafond, cible atteinte
  -> échange, échange même nature atomique, absence de solution -> titulaire inchangé, préférence
  jour en dernier, pas de repos auto après appel, 12h récup après 12h sur place, jamais >24h).
- Suite CONCURRENCE RÉELLE : deux connexions/transactions indépendantes (candidature vs gel,
  deux reprises même garde, reprise vs échange, deux échanges chevauchants, deux publications
  même trimestre, deux écritures audit, second accord échange vs modification simultanée).
- Suite SÉCURITÉ/DROITS : redirection HTTPS, cookie Secure, CSRF, limitation connexion, brouillon
  invisible, version publiée non modifiable (UI/services/API), preuve tirage anonymisée, motifs
  sensibles invisibles collègues, matrice 401/403/404 par rôle, IDOR, bornes génération,
  injection CSV et XSS champs libres/exports.

---

## Contradictions/points à clarifier (non bloquants, traités par défaut administrable)

- Horaires vendredi et veille de férié : non confirmés par le client. Défaut de démonstration
  aligné (17h-8h), administrable, consigné en OPEN_QUESTIONS (ne pas figer 20h).
- Plafond mensuel assistants : non fixé -> NULL + alerte, jamais inventé (Q nouvelle).
- Sémantique « pondération de garde » (84/10) vs quotité : traitée comme dixièmes datés, distincte
  de la quotité de temps de travail.
- Traitement des DISPO_DEFAUT en reprise L2 : laissé ouvert (non inclus par défaut), Q nouvelle.

---

## Avancement (02/09/2026) — tranches livrées après P0

Toutes prouvées par tests (suite complète verte, 85 tests) ; déploiement à suivre.

- P1.9 horaires : **corrigé** (17-8 lun-jeu, 9-9 sam/dim/férié ; vendredi/veille 17-8 hypothèse démo). Tests : `test_metier_p1.py` (horaires confirmés + absence des anciens 20h/8-8).
- P1.15 orange en L1 : **corrigé** (contrainte ferme H02c : orange possible en L2 uniquement). Tests : orange interdit L1 / possible L2.
- P4.8 bornes de génération : **corrigé** (variantes 1..3 et diversité 0..1 bornées côté service ; 422 en API, clamp en UI). Tests : service + API.
- P4.9 avancement de reprise : **corrigé** (réservé admin en UI et API ; un médecin reçoit 403). Tests : médecin 403 / admin franchit l'autorisation.
- P4.12 injection CSV : **corrigé** (neutralisation des formules + module csv pour l'échappement, appliqué CSV et XLSX). Test : `_csv_safe`.
- P4.13 health : **ajouté** (`/health/live` 200, `/health/ready` 200/503, sans détail sensible). Tests inclus.
- P2.2 une version publiée par trimestre : **garanti en base** (index unique partiel `uq_one_published_per_quarter` + migration Alembic `b2f1a7c9d3e0`) en plus de la démotion atomique existante. Tests : doublon rejeté (IntegrityError) + republication bascule en REMPLACE.

Restent à traiter (tranches suivantes) : P2 (immuabilité publication UI/services/API,
concurrence réelle 2 connexions, motifs neutres collègues, preuve tirage anonymisée,
refus non-tirable, EN_REVISION invisible), P3 (UI Quotas + Projections),
P4 (CSRF, Argon2id, session opaque révocable, IDOR systématique, logs expurgés,
OpenAPI enrichi).

---

## Avancement (03/09/2026) — arbitrages métier du client intégrés

Le client a tranché les quatre questions ouvertes le 03/09/2026. Les décisions
ci-dessous sont désormais **fermes** et implémentées. Suite complète : **160 tests
verts**. Chaîne Alembic vérifiée dans les deux sens (`upgrade head`,
`downgrade base`, `upgrade head`).

### Tranche 5 — couverture horaire continue (P1.9bis, P1.11)

Décision du client : supprimer le trou de 8 h à 9 h avant la relève du matin.

- Vendredi non férié : **vendredi 17 h → samedi 9 h**.
- Veille **ouvrable** d'un jour férié : **17 h → jour férié 9 h**.
- Samedi, dimanche, jour férié : 9 h → 9 h (inchangé).
- Un vendredi férié reste classé **férié**.
- Si la veille tombe déjà un samedi, un dimanche ou un jour férié, **aucune
  occurrence supplémentaire** n'est créée : la date garde son propre type.
- Q-03 est **close** : les six horaires sont confirmés, plus aucun type n'est
  marqué « horaires à valider ».

Migration `d4e3c2b1a098`. Tests : `tests/test_metier_p1c.py`, dont un invariant de
continuité qui échoue si une garde suivie d'une relève à 9 h ne se termine pas
exactement à 9 h.

### Tranche 6 — plafond mensuel administrable (P1.5, P1.6)

Décision du client : ne pas transformer automatiquement 5 ou 6 en plafond ferme.

- Nouveau modèle `MonthlyCap`, valeur **nullable**, avec **trois verrous cumulés**
  avant qu'un plafond ne devienne opposable : valeur chiffrée strictement
  positive, validation institutionnelle explicite, caractère déclaré ferme.
- Tant qu'un verrou manque, le plafond est **informatif** : il alimente
  projections et alertes, jamais le moteur.
- Nouvelle contrainte ferme `H12_PLAFOND_MENSUEL_FERME`, active uniquement quand
  les trois verrous sont franchis.
- Le jeu de démonstration enregistre une ligne **vide** par statut, ce qui produit
  une alerte explicite plutôt qu'une valeur devinée.
- Quota global et plafond mensuel restent **deux paramètres distincts**.
- Trois comparaisons calculées : quota 57 avec plafond 6 (saturation 82,6 %),
  quota 68 avec plafond 7 (84,5 %), et le scénario de contrainte quota 68 avec
  plafond 6 (**98,6 %**, marge mensuelle quasi nulle, alerte émise).
- La période assistante est calculée depuis les dates réelles : du 19/10/2026 au
  03/10/2027 inclus, soit 350 jours, exactement 50 semaines.

Migration `e5f4d3c2b109`. Tests : `tests/test_plafond_mensuel.py`.

### Tranche 7 — repos et récupération (P1.8, P1.26)

Décision du client : pas d'interdiction universelle de 24 h entre toutes les gardes.

- La règle ferme `REPOS_MIN_24H` est **retirée** (désactivée, pas supprimée, pour
  conserver la trace). Il ne reste **aucune** règle de repos ferme générique.
- L'espacement ordinaire reste un objectif **souple**, configurable et non validé.
- Nouvelle contrainte ferme `H13_DUREE_CONTINUE_MAXIMALE` : jamais plus de 24 h de
  service continu, **dérogeable uniquement** par une demande explicite et datée de
  la personne (`WeekendBlockRequest`). C'est le mécanisme du week-end complet.
- `OnSiteReport` : déclaration de travail réellement effectué sur place. **Aucune
  présomption** : sans déclaration, une garde ne vaut aucune heure sur place.
- `RecoveryProposal` : 12 h de récupération **proposées** après 12 h continues
  réellement travaillées sur place avec déplacement. État initial `PROPOSEE`,
  décision humaine obligatoire, aucune application automatique.
- Un simple appel sans déplacement n'ouvre aucun droit, quelle que soit la durée.
- Des heures fractionnées n'ouvrent aucun droit.
- La concentration produit une **alerte** paramétrable, jamais une règle ferme.

Migration `f6a5b4c3d210`. Tests : `tests/test_repos_recuperation.py`.

### Tranche 8 — reprises L1 et L2 (P1.23, P1.24, P2.10)

Décision du client : collecte unique en L2, sans effacer la priorité du vert.

- `DISPO_DEFAUT` est **exclu de toutes les reprises**. Une non-réponse peut servir
  à la génération initiale, jamais à désigner un volontaire.
- Reprise **L1** : uniquement les personnes explicitement vertes et éligibles.
- Reprise **L2** : une **seule** collecte, verts et orange sollicités en même
  temps, même fenêtre, aucun avantage à la rapidité.
- À la clôture, les candidatures sont revalidées, puis le tirage porte
  **uniquement sur les verts valides** ; les orange ne sont tirés qu'en l'absence
  totale de vert valide. La preuve du tirage documente le palier retenu, la liste
  des verts, celle des orange et la règle de priorité appliquée.
- La couleur retenue est celle constatée **à la clôture**, pas au dépôt.
- Plus aucune vague orange successive : sans volontaire valide, le titulaire
  publié reste responsable et les responsables sont alertés.
- Nouveaux états `COLLECTE_UNIQUE` / `LISTE_FIGEE_UNIQUE` ; les états orange sont
  conservés pour les données antérieures mais ne sont plus jamais atteints.

Tests : `tests/test_reprises_v2.py`.

### Tranche 9 — jeu de données métier et compteurs (P1.1 à P1.4, P1.12, P1.13, P1.18)

- **15 seniors** portant les pondérations de garde 7, 6, 7, 8, 8, 0, 7, 8, 6, 0, 3,
  6, 6, 7, 5, soit **84/10 au 01/10/2026**, historisées avec date d'effet et
  distinctes de la quotité de temps de travail.
- **3 assistants**, période d'activité du 19/10/2026 au 03/10/2027 incluse,
  première ligne uniquement, déclarations limitées au vert et au rouge.
- **Campagne T1** aux dates réelles : ouverture 01/11, rappel 15/11, clôture
  01/12, publication 07/12.
- L'obligation liée aux **paires de jours fériés n'est pas étendue aux assistants**.
- **Six compteurs seniors** : trois catégories comptables croisées avec les deux
  lignes, toujours présents même à zéro, pondérés au poids **en vigueur à la date
  de la garde**. Sans pondération enregistrée, le compteur pondéré reste vide.
- **Cinq sous-compteurs assistants** : vendredi, veille de férié, samedi,
  dimanche, jour férié. Indicateurs statistiques, jamais contraignants.

Tests : `tests/test_compteurs.py`.

### Tranche 10 — six permissions distinctes (P1.19)

- Nouveau modèle `PermissionGrant` : `RESP_L1`, `RESP_L2`, `CHEF_SERVICE`,
  `GESTION_COMPTES`, `PUBLICATION`, `CONSULTATION_AUDIT`.
- Chaque permission est attribuée séparément, **datée** et **journalisée**.
- Une révocation pose une date de fin, elle n'efface rien.
- Une permission accordée n'en donne aucune autre.
- Un administrateur les détient toutes, ce qui préserve le fonctionnement actuel
  pendant la mise en place des délégations fines.
- Branchées sur le journal d'audit (interface et API) et sur la validation puis la
  publication d'un planning.
- Le jeu de démonstration délègue les six permissions à six médecins **non
  administrateurs**.

Migration `a7b6c5d4e321`. Tests : `tests/test_permissions.py`.

### Points restant ouverts après ces arbitrages

- Le **plafond mensuel institutionnel** reste à chiffrer. L'application alerte,
  elle n'invente pas.
- Les **situations intermédiaires** de récupération restent volontairement sans
  automatisme, conformément à la demande d'appréciation humaine.

---

## Avancement (04/09/2026) — corrections demandées par le client

Suite complète : **171 tests verts**.

### Correction 1 — portée du bloc continu restreinte aux assistants

Le client a explicitement demandé de **ne pas généraliser aux seniors**
l'obligation de demander un week-end complet.

- `ContinuousDutyRuleIn` porte désormais `applies_to_statuses`, valorisé à
  `{ASSISTANT}` uniquement.
- La contrainte ferme `H13` n'est évaluée que pour un assistant. Pour un senior,
  **aucun blocage supplémentaire** n'est créé : les autres contraintes connues et
  la validation humaine habituelle restent seules applicables.
- La portée fait partie de l'empreinte d'exécution du moteur, donc un changement
  de portée serait visible dans l'instantané reproductible.
- Tests : `test_un_senior_n_est_pas_bloque_par_la_duree_continue` prouve qu'un
  enchaînement refusé pour un assistant passe sans obstacle pour un senior ;
  `test_la_regle_ne_vise_que_les_assistants` contrôle la portée déclarée ;
  `test_17` de `test_engine_hard.py` ne vérifie plus le maximum que sur les
  assistants.

### Correction 2 — droits administratifs attachés aux trois fonctions

La formulation antérieure, « six permissions distinctes déléguées à six médecins
non administrateurs », était effectivement ambiguë : elle laissait entendre que
les responsables de ligne et le chef de service n'avaient aucun droit
administratif. Corrigé.

- Trois **fonctions** ouvrent l'accès administratif :
  `RESP_L1` (responsable des gardes de première ligne), `RESP_L2` (responsable
  des gardes de deuxième ligne), `CHEF_SERVICE` (chef de service).
- Trois **permissions complémentaires** restent indépendantes et n'ouvrent à
  elles seules aucun accès administratif : `GESTION_COMPTES`, `PUBLICATION`,
  `CONSULTATION_AUDIT`.
- Les autres médecins restent **non administrateurs**.
- Les fonctions ont des **périmètres distincts**, matérialisés par la ligne
  supervisée : `RESP_L1` couvre la première ligne, `RESP_L2` la deuxième,
  `CHEF_SERVICE` les deux. L'avancement d'une reprise est refusé au responsable
  qui n'a pas la ligne concernée.
- Chaque attribution reste **séparée, datée et journalisée** : détenir une
  fonction n'en confère aucune autre, et la révocation coupe immédiatement
  l'accès administratif.
- Les contrôles `user.is_admin` de l'espace d'administration sont remplacés par
  `permission_service.has_administrative_access`, en interface comme en API.
- Le message de refus nomme les trois fonctions au lieu de parler
  d'« administrateurs ».
- Tests : `tests/test_permissions.py`, dont l'accès effectif à `/admin` et
  `/admin/quotas` pour un chef de service et pour un responsable de ligne, et le
  refus pour un médecin ordinaire.

### Question nouvelle ouverte par cette correction

Le périmètre de ligne (`RESP_L1` sur la première ligne, `RESP_L2` sur la
deuxième) est **déduit du nom des fonctions**, il n'a pas été validé
institutionnellement. Il n'est aujourd'hui appliqué qu'à l'avancement d'une
reprise. Le reste de l'espace d'administration est ouvert aux trois fonctions
sans distinction. À confirmer par le client : faut-il restreindre davantage,
par exemple la génération ou les quotas, selon la ligne ?


---

# Lot de clôture du 04/09/2026 — sous-lots A à E

Base : HEAD `43b4ff6` puis les commits de ce lot. Données exclusivement fictives.

## A — contrôles d'accès

| Exigence | État | Preuve |
|---|---|---|
| Statut médical contrôlé à **tous** les points d'entrée médicaux, API et UI | Fait | `deps.profile_medecin` / `deps.profil_medecin_de`, point unique ; `tests/test_lotA_acces.py::test_A1_*` |
| Contre-épreuve : POST de reprise après révocation | Fait | `test_A1_contre_epreuve_le_post_de_reprise_ne_repond_plus_200` |
| `GET /api/v1/planning/versions/{id}` fermé aux versions internes | Fait | `visibility_service.version_lisible` ; `test_A2_*` |
| Listes et détails limités aux acteurs légitimes, 404 uniforme | Fait | `visibility_service` ; `test_A3_*` compare les corps de réponse |
| Contrat d'anonymat honnête | Fait | `visibility_service.CONTRAT_ANONYMAT` ; `test_A4_*` |

## B — parcours d'échange

| Exigence | État | Preuve |
|---|---|---|
| Départ depuis sa propre garde, sans collègue ni contrepartie | Fait | `swap_flow_service.ouvrir` ; `test_B1_*` |
| Recherche dans le trimestre, verts explicites, éligibilité croisée | Fait | `swap_search_service.rechercher` |
| Sollicitation simultanée, sans avantage à la rapidité, sans motif | Fait | `test_B3_*` |
| Classement maximin puis tirage seulement en égalité parfaite | Fait | `swap_flow_service.cloturer` ; `test_lot3_echange.py` sans saut |
| Double consentement, revalidation atomique, officialisation unique | Fait | `test_B5_*` |
| Refus, retrait, expiration, annulation, conflit, absence de solution | Fait | `test_B6_*` |
| Ancien parcours à deux listes retiré de l'interface | Fait | `app/web/templates/echanges.html` ; `test_B1_l_interface_ne_propose_plus_de_choisir_la_garde_souhaitee` |
| Zéro test sauté | Fait | suite complète |

## C — règles métier

| Exigence | État | Preuve |
|---|---|---|
| Bascule reprise → échange réellement lancée | Fait | `handover_service.basculer_vers_un_echange` ; `test_C1_*` |
| Plafond mensuel comparé à la charge réelle | Fait | `handover_service.charge_du_mois` ; `test_C2_*` |
| Borne assistant par le vrai moteur | Fait | `test_C3_la_borne_assistant_est_appliquee_par_le_moteur` |
| Cycle annuel raccordé | Fait | `engine_bridge.cycle_bounds`, `prior_load`, `year_fraction`, `counters_service` ; `test_C4_*` |
| Récupération validée bloquante et routes protégées | Fait | `engine_bridge.recovery_intervals`, routes `/repos` ; `test_C5_*` |
| Week-end assistant strictement borné | Fait | `rest_service._assert_week_end_de_neuf_a_neuf`, `ContinuousDutyRuleIn.has_request` ; `test_C6_*` |
| Objectif mensuel assistant inactif | Fait | `quota_service.OBJECTIF_MENSUEL_ASSISTANT` ; `test_C7_*` |

## D — concurrence applicative

| Exigence | État | Preuve |
|---|---|---|
| Schéma migré, modèles et services réels | Prouvé sous PostgreSQL | `test_D0_le_schema_est_bien_celui_des_migrations` |
| Deux sessions distinctes synchronisées par barrières | Prouvé sous PostgreSQL | `_en_parallele`, `test_D0_les_deux_sessions_sont_reellement_distinctes` |
| Candidature contre gel | Prouvé sous PostgreSQL | `test_D1` |
| Double consentement d'échange | Prouvé sous PostgreSQL | `test_D2` |
| Double tirage réel | Prouvé sous PostgreSQL | `test_D3` |
| Collision d'outbox sans rollback métier | Prouvé sous PostgreSQL | `test_D4` |
| Publication concurrente réelle | Prouvé sous PostgreSQL | `test_D5` |
| Tête d'audit concurrente sans fourche | Prouvé sous PostgreSQL | `test_D6` — **a révélé un défaut réel**, corrigé |
| Engagement et révélation en deux transactions observables | Prouvé sous PostgreSQL | `handover_service.sceller_engagement` ; `test_D7` |

## E — gouvernance et documents

| Exigence | État | Preuve |
|---|---|---|
| Vraies routes d'écriture de quotas, périmètre objet × ligne | Fait | `POST /admin/quotas/cible`, `POST /api/v1/quotas/targets` ; `test_E1_*` |
| Validation institutionnelle distincte | Fait | `POST /api/v1/quotas/targets/validate` ; `test_E1_la_validation_institutionnelle_est_reservee_au_chef` |
| Publication, dérogation, journal : permissions séparées | Fait | `test_E2_*` |
| Documents réconciliés avec le registre canonique | Fait | `OPEN_QUESTIONS.md` (registre canonique), `README.md`, `test_E3_*` |
| Décompte des tests honnête | Fait | `docs/TESTS.md` |

## Ce qui reste explicitement ouvert

- Les quatre décisions humaines : quota 57/68, plafond mensuel, statut de
  l'objectif mensuel des assistants, règles des permanences de jour.
- Les portes de production non validées : MFA/SSO, Argon2id, limitation de débit
  partagée, contraintes de base supplémentaires, TLS `verify-full`, sauvegarde et
  restauration, supervision, tenue en charge, audit externe et conformité.
- La chaîne d'audit **n'est pas** qualifiée d'inviolable : elle détecte une
  réécriture et refuse une fourche, mais l'ancrage externe reste à faire.
- **NO_GO maintenu** pour les identités réelles et tout planning officiel.
