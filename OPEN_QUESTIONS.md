# OPEN_QUESTIONS.md — Questions ouvertes : rien n'a été inventé

Chaque question ci-dessous correspond à une valeur **non validée institutionnellement**.
Règle appliquée sans exception :

1. la valeur est **administrable** (paramètre en base, modifiable sans redéploiement) ;
2. la valeur de démonstration est **clairement étiquetée** dans l'interface et les exports ;
3. aucun écran, aucun export et aucune explication ne la présente comme une règle validée.

Marqueur utilisé dans l'interface : **« hypothèse de démonstration »**.

---

## Registre canonique — ce qui est **acquis** et ne se rouvre pas

Ces points sont tranchés. Ils sont rappelés ici pour que la documentation ne
puisse plus les contredire (lot E du contre-audit du 04/09/2026).

| Point | Règle acquise |
|---|---|
| Collecte de reprise en deuxième ligne | **Collecte unique** : verts et orange sont sollicités ensemble. La **priorité au vert** s'applique **au tirage** : s'il existe au moins un volontaire vert valide, le tirage ne porte que sur les **verts valides** ; les orange ne sont tirés qu'en l'absence totale de vert valide. Aucune vague orange successive n'existe plus. |
| Disponibilité par défaut | Exclue de **toutes** les reprises et de tous les échanges. Elle ne sert qu'à la génération initiale. |
| Contrat d'anonymat | La sollicitation ne mentionne **ni le nom du demandeur ni son motif**. Le planning publié étant nominatif, l'application ne prétend pas rendre le titulaire indevinable. |
| Reprise et échange | Deux opérations **distinctes**. « Échange » lance une **recherche** dans le trimestre, ce n'est pas un formulaire d'enregistrement d'un accord trouvé hors application. |
| Bloc de service continu | Règle ferme **pour les assistants seulement**. Aucun blocage supplémentaire non validé n'est créé pour les seniors. |
| Cycle de quota | Du **premier lundi d'octobre inclus** au premier lundi d'octobre suivant **exclu**. Rattachement par la **date de service**. |
| Accès administratif | Ouvert aux trois fonctions (responsable L1, responsable L2, chef de service), avec des permissions **distinctes et traçables**. Publication, dérogation et consultation du journal restent des permissions **explicites**, jamais implicites. |

---

## Q-01 — Quotas exacts et future formule TIMA
**Statut** Ouverte · **Impact** fort
Aucune formule n'est appliquée. Les cibles sont saisies manuellement (M-004). La quotité est
stockée en dixièmes avec historique daté, ce qui permettra plus tard une formule fondée sur
les dixièmes, les périodes d'activité et des arrondis globaux.
*Valeur de démonstration* : cibles fictives variées par personne, catégorie et ligne.
*Où* : `quota_targets.source = 'MANUEL_ADMIN'`, écran Quotas, module Projections
(formules hypothétiques étiquetées).

## Q-02 — Règles liées à l'âge
**Statut** Ouverte · **Impact** moyen
**Aucun seuil d'âge n'est codé.** Le modèle n'expose que des exemptions et réductions
datées et commentées, sans motif normatif implicite. Si une règle d'âge est un jour validée,
elle se traduira par des exemptions générées, pas par une condition en dur.

## Q-03 — Horaires exacts de certains types de garde
**Statut CLOSE (03/09/2026)** · **Impact** moyen
*Confirmés par le client le 02/09/2026* : lundi à jeudi hors férié **17:00 → 08:00** ;
samedi, dimanche et jour férié **09:00 → 09:00 (lendemain)**.
*Confirmés par le client le 03/09/2026*, pour assurer une couverture continue :
nuit du vendredi non férié **17:00 → samedi 09:00** ; veille **ouvrable** d'un jour
férié **17:00 → jour férié 09:00**. Un vendredi férié reste classé férié, et si la
veille tombe déjà un samedi, un dimanche ou un jour férié, aucune occurrence
supplémentaire n'est créée.
Plus aucun type n'est marqué « horaires à valider ».
*Où* : `garde_types.start_time` / `end_time` / `crosses_midnight`, administrables.

## Q-04 — Rattachement des veilles de jours fériés
**Statut** Ouverte · **Impact** moyen
*Hypothèse de démonstration* : la veille nocturne d'un jour férié est comptée dans la
catégorie « week-ends et veilles de jours fériés », et **peut** être rattachée à la période
fériée d'une paire via `holiday_pair_members.include_eve`.
*Où* : mapping type → catégorie entièrement administrable.

## Q-05 — Paires fériées : vert seul, ou vert + orange ?
**Statut** Ouverte · **Impact** moyen
*Valeur de démonstration* : `VERT_ORANGE`.
*Où* : `campaigns.holiday_pair_requirement`, choisi par l'administrateur à l'ouverture.
*Tranché le 03/09/2026* : l'obligation liée aux paires de jours fériés **n'est pas
étendue aux assistants**. Elle ne concerne que les seniors.
*Tranché le 03/09/2026* : `DISPO_DEFAUT` peut compter pour cette règle après conversion
régulière, mais reste **exclu de toutes les reprises**.

## Q-13 — Plafond mensuel de gardes
**Statut Ouverte, valeur attendue** · **Impact** fort
Le client n'a **pas** chiffré le plafond institutionnel (03/09/2026). Consigne
explicite : ne pas transformer automatiquement 5 ou 6 en plafond ferme.
*Ce qui est implémenté* : un plafond administrable, nullable, qui n'est opposable
qu'après trois verrous cumulés — valeur chiffrée, validation institutionnelle
explicite, caractère déclaré ferme. Tant qu'un verrou manque, il est informatif.
Une alerte est produite pour chaque statut sans plafond enregistré.
*Valeurs de simulation utilisées en projection, jamais des règles* : quota 57 avec
plafond 6, quota 68 avec plafond 7, et le scénario de contrainte quota 68 avec
plafond 6 (saturation 98,6 % sur la période de 50 semaines).
*Où* : `monthly_caps`, `app/engine/hard.py` (H12), `projection_service`.

## Q-06 — Repos minimal et gardes rapprochées
**Statut Partiellement tranchée (03/09/2026)** · **Impact** fort
*Tranché par le client* : **aucune interdiction universelle de 24 h** entre toutes les
gardes. La règle ferme correspondante a été retirée. Ce qui est ferme désormais, et
**uniquement pour les assistants** (portée restreinte par le client le 04/09/2026) :
ne jamais dépasser **24 h de service continu**, sauf demande explicite et datée de
l'intéressé (cas du week-end complet). Pour les seniors, **aucun blocage
supplémentaire** n'est créé. S'y ajoutent, hors moteur : au moins **12 h de
récupération** après **12 h continues réellement travaillées sur place**, proposées et
soumises à validation humaine ; aucun droit ouvert par un simple appel sans
déplacement ; **aucune présomption** de nuit travaillée du seul fait d'avoir été de
garde.
*Reste ouvert* : l'espacement prévisionnel ordinaire (valeur de démonstration : 7 jours,
**souple**) et le maximum de week-ends consécutifs (2, **souple**) ne sont pas validés
institutionnellement. La concentration produit une alerte paramétrable, jamais une règle.
*Nouvelle question* : le périmètre de ligne des fonctions administratives
(responsable des gardes 1 sur la première ligne, responsable des gardes 2 sur la
deuxième) est **déduit du nom des fonctions** et n'a pas été validé. Il n'est
appliqué qu'à l'avancement d'une reprise. Voir Q-14.
*Où* : `rest_rules`, `weekend_block_requests`, `on_site_reports`, `recovery_proposals`.

## Q-14 — Périmètre exact des trois fonctions administratives
**Statut Ouverte** · **Impact** moyen
*Tranché par le client le 04/09/2026* : responsable des gardes 1, responsable des
gardes 2 et chef de service disposent des droits administratifs nécessaires à leur
fonction ; leurs permissions peuvent différer mais restent distinctes et traçables ;
les autres médecins restent non administrateurs.
*Hypothèse de démonstration, non validée* : les trois fonctions ouvrent le même
espace d'administration, et se distinguent par la **ligne supervisée** (L1, L2, ou
les deux pour le chef de service). Cette distinction n'est aujourd'hui appliquée
qu'à l'avancement d'une reprise.
*Question ouverte* : faut-il restreindre davantage selon la ligne, par exemple la
génération d'un planning ou la saisie des quotas ?
*Où* : `app/models/permissions.py` (`ROLES_ADMINISTRATIFS`, `LIGNES_SUPERVISEES`),
`app/services/permission_service.py`.

## Q-07 — Ordre et pondération exacts des critères souples
**Statut** Ouverte sauf pour la priorité seniors (M-006, tranchée) · **Impact** fort
*Valeurs de démonstration* : S01 vert/orange 100 · S02 quotas 60 · S05 espacement 40 ·
S06 concentration 30 · S03 rattrapage 25 · S07 pénibilité 20 · S04 multiplicateur seniors ×3.
*Où* : profil de règles `OPERATIONNEL` versionné, table `rule_profiles`.

## Q-08 — Délai de grâce avant disponibilité par défaut
**Statut** Ouverte · **Impact** fort
*Valeur de démonstration* : **48 h** après l'échéance et après l'envoi des relances prévues.
*Où* : `campaigns.grace_period_hours`.
Une validation tardive pendant le délai de grâce **annule** la conversion ; une prolongation
ou une réouverture **reprogramme** la tâche sans double événement ni double notification.

## Q-09 — Seuils de proximité, fenêtres et rappels de la collecte de reprise
**Statut** Ouverte · **Impact** fort
*Note* : la collecte est **unique**. Le tableau ci-dessous décrit ses paliers,
pas une succession de vagues.
*Valeurs de démonstration* (profil `urgence_demo_v1`) :

| Délai avant la garde | Fenêtre | Rappels |
|---|---|---|
| < 12 h | 90 min | +30 min, +60 min |
| 12 h – 48 h | 6 h | +2 h, +4 h |
| 48 h – 7 j | 24 h | +8 h, +16 h |
| > 7 j | 72 h | +24 h, +48 h |

*Où* : `urgency_profiles`, versionné et administrable.

## Q-10 — Préférences communes ou distinctes selon la ligne
**Statut** Ouverte · **Impact** moyen
Le modèle **supporte déjà** une couleur par ligne : `availabilities.line` est nullable.
*Hypothèse de démonstration* : la saisie produit une couleur unique (`line = NULL`)
applicable à toutes les lignes éligibles de la date. Aucune migration ne sera nécessaire pour
passer à des couleurs distinctes par ligne.

## Q-11 — Règles du futur module de permanences de jour
**Statut** Ouverte · **Impact** faible pour ce prototype
Seuls les objets et la navigation sont préparés (`module = PERMANENCES_JOUR`).
Créneaux de démonstration relevés du brief : matin 08:00–12:30, après-midi 12:30–17:00.
Aucune logique de campagne mensuelle, de récurrence ou de volontariat n'est implémentée.

## Q-12 — Catalogue exact des classes de gardes équivalentes pour un échange
**Statut** Ouverte · **Impact** fort
*Hypothèse de démonstration* : une classe d'échange par type, sauf « samedi » et « dimanche »
regroupés dans la classe `WEEKEND_24H`.
L'équivalence exigée reste cumulative et **ne peut pas** être contournée par la classe seule :
même ligne, même catégorie comptable, même poids de décompte, même classe d'échange,
même classe de durée, mêmes exigences de couverture.
*Où* : `exchange_classes` + contrôle explicite dans `swap_service.check_equivalence()`.

---

## Q-15 — Statut de l'objectif mensuel des assistants
**Statut** Ouverte · **Impact** moyen
« Un vendredi et deux jours de week-end par mois » n'a fait l'objet d'**aucune
décision institutionnelle explicite**. Le paramètre existe donc, il est lisible
et modifiable, mais il est **inactif** : ni le moteur, ni les reprises, ni les
échanges ne le consultent. Un test le prouve en vérifiant qu'aucun fichier du
paquet `app/engine/` ne le référence.
*Où* : `quota_service.OBJECTIF_MENSUEL_ASSISTANT`, `actif = False`.
**Décision humaine attendue** avant tout caractère opposable.

---

## Points volontairement laissés sans valeur

- Aucun barème, aucune sanction, aucun classement entre médecins.
- Aucune règle d'âge.
- Aucune formule TIMA opérationnelle.
- Aucun seuil « acceptable » de charge décidé par le logiciel : le module de projections
  compare des hypothèses, il ne recommande pas.

---

## Les quatre vraies décisions humaines restantes

Après les lots A à E, la liste des décisions strictement humaines se réduit à :

1. **quota assistant 57 ou 68** sur la période du 19/10/2026 au 03/10/2027 (Q-01) ;
2. **plafond mensuel** institutionnel, non chiffré (Q-13) ;
3. **statut de l'objectif mensuel des assistants** (Q-15) ;
4. **règles des permanences psychiatriques de jour** (Q-11), module distinct dont
   les horaires d'une ancienne démonstration ne sont **pas** repris comme règles.
