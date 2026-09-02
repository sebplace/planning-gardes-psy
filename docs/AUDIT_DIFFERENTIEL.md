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
| P0.9 | Migrations Alembic versionnées en release + retour arrière testé | Release fait déjà `alembic upgrade head` ; startup create_all seulement en SQLite | Conserver ; ajouter downgrade testé sur base fictive (up/down/up) | test : downgrade/upgrade rejouable | à faire |

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
