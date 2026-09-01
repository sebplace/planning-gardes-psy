# Correspondance exigence → test

Les 52 exigences de la **section 22** du cahier des charges sont couvertes par
60 tests automatisés. Exécution :

```bash
python -m pytest tests -q
```

| # | Exigence | Test |
|---|---|---|
| 1 | Aucune affectation automatique sur rouge | `test_engine_hard.py::test_01_aucune_affectation_automatique_sur_rouge` |
| 2 | Assistant jamais en ligne 2 | `test_engine_hard.py::test_02_assistant_jamais_en_ligne_2` |
| 3 | Toute ligne 2 assurée par un senior | `test_engine_hard.py::test_03_toute_ligne_2_est_assuree_par_un_senior` |
| 4 | Senior compatible avec tout assistant | `test_engine_hard.py::test_04_senior_compatible_avec_tout_assistant` |
| 5 | Mode A sans ligne 2, mode B complet | `test_engine_hard.py::test_05_et_29_mode_a_sans_ligne_2_mode_b_complet` |
| 6 | Rappels cessant après validation | `test_campaign.py::test_06_rappels_cessent_apres_validation` |
| 7 | Non-réponse bloquant d'abord la génération | `test_campaign.py::test_07_non_reponse_bloque_d_abord_la_generation` |
| 8 | Disponibilité par défaut après relances **et** délai de grâce, traitée comme verte mais distinguée | `test_campaign.py::test_08_disponibilite_par_defaut_apres_relances_et_delai_de_grace` |
| 9 | Paires fériées contrôlées | `test_campaign.py::test_09_paires_feriees_controlees` |
| 10 | Quotas séparés par catégorie et ligne | `test_quotas.py::test_10_quotas_separes_par_categorie_et_ligne`, `test_10b_realise_et_programme_separes_par_categorie_et_ligne` |
| 11 | Confidentialité des quotas | `test_quotas.py::test_11_confidentialite_des_quotas` |
| 12 | Reprise initialement anonyme | `test_handover.py::test_12_49_52_anonymat_et_exclusion_du_demandeur` |
| 13 | Vague verte avant orange | `test_handover.py::test_13_42_vague_verte_avant_orange` |
| 14 | Toutes les candidatures collectées et figées avant l'attribution, y compris candidature unique | `test_handover.py::test_14_40_44_45_collecte_gel_tirage_et_effets`, `test_14b_candidature_unique_passe_par_le_meme_evenement` |
| 15 | Clic plus rapide sans avantage ; résultat immédiatement officiel | `test_handover.py::test_15_43_vitesse_de_reponse_sans_effet_et_resultat_immediatement_officiel` |
| 16 | Quotas corrects après reprise, écart de la personne remplacée suivi | `test_quotas.py::test_16_quotas_apres_reprise` |
| 17 | Règle de repos ferme jamais violée | `test_engine_hard.py::test_17_regle_de_repos_ferme_jamais_violee` |
| 18 | Rapport d'impossibilité explicite | `test_engine_hard.py::test_18_rapport_d_impossibilite_explicite` |
| 19 | Correction manuelle auditée | `test_planning.py::test_19_correction_manuelle_auditee`, `test_19b_un_planning_publie_n_est_jamais_reecrit` |
| 20 | Résultat reproductible avec même graine | `test_engine_hard.py::test_20_resultat_reproductible_avec_meme_graine` |
| 21 | Compte assistant expiré exclu | `test_engine_hard.py::test_21_compte_assistant_expire_exclu` |
| 22 | Minuit, changement d'heure, année bissextile | `test_engine_hard.py::test_22a_garde_traversant_minuit`, `test_22b_changement_d_heure_de_printemps_et_d_automne`, `test_22c_annee_bissextile` |
| 23 | Droits séparés médecin / administrateur | `test_planning.py::test_23_droits_separes_medecin_et_administrateur` |
| 24 | Architecture extensible au module de jour | `test_planning.py::test_24_architecture_extensible_au_module_de_jour` |
| 25 | Variation du nombre d'assistants recalculant L1/L2 résiduelles | `test_projections.py::test_25_variation_du_nombre_d_assistants_recalcule_les_charges` |
| 26 | Variation du quota par assistant conservant l'égalité arithmétique | `test_projections.py::test_26_egalite_arithmetique_conservee` (6 paramétrisations × 2 modes) |
| 27 | Scénario impossible produisant un déficit explicite | `test_projections.py::test_27_scenario_impossible_produit_un_deficit_explicite` |
| 28 | Deux scénarios comparés sans modification opérationnelle | `test_projections.py::test_28_comparaison_de_scenarios_sans_effet_operationnel` |
| 29 | Mode A ne créant jamais de L2 derrière un senior de L1 | `test_engine_hard.py::test_29_impossible_de_creer_une_l2_derriere_un_senior_de_l1` |
| 30 | Promotion d'un scénario impossible sans confirmation explicite | `test_projections.py::test_30_promotion_exige_une_confirmation_administrative_explicite`, `test_30b_promotion_via_api_refusee_sans_confirmation` |
| 31 | Impossibilité de forcer une date rouge (administrateur ou moteur) | `test_planning.py::test_31_administrateur_ne_peut_pas_forcer_un_rouge` |
| 32 | Fenêtres et rappels adaptatifs, sans doublon | `test_handover.py::test_32_fenetres_adaptatives_selon_la_proximite`, `test_32b_rappels_sans_doublon` |
| 33 | Tirage côté serveur après gel, preuve, horodatage, pas de relance | `test_handover.py::test_14_40_44_45_collecte_gel_tirage_et_effets`, `test_33_un_seul_tirage_officiel_possible` |
| 34 | Échange officiel après les deux accords, compteurs inchangés, permutation journalisée | `test_swap.py::test_34_echange_officiel_apres_les_deux_accords_compteurs_inchanges` |
| 35 | Échange refusé si un critère d'équivalence diffère | `test_swap.py::test_35_echange_refuse_si_la_nature_differe`, `test_35b_chaque_critere_d_equivalence_est_verifie` |
| 36 | Deux opérations concurrentes → un seul état final | `test_handover.py::test_36_deux_reprises_concurrentes_sur_la_meme_garde` |
| 37 | Seuls les champs non renseignés convertis | `test_campaign.py::test_37_seuls_les_champs_non_renseignes_sont_convertis` |
| 38 | Validation tardive annulant la conversion ; prolongation sans double événement | `test_campaign.py::test_38_validation_tardive_et_prolongation` |
| 39 | Disponibilité par défaut comptant pour une paire fériée et la vague verte, jamais affichée comme volontaire | `test_campaign.py::test_39_dispo_par_defaut_compte_pour_paire_et_vague_verte` |
| 40 | Titulaire initial responsable jusqu'à l'attribution atomique | `test_handover.py::test_14_40_44_45_collecte_gel_tirage_et_effets` |
| 41 | Couleur, éligibilité, conflits, repos, activité revérifiés après gel | `test_handover.py::test_41_reverification_apres_gel_un_rouge_exclut` |
| 42 | Candidature tardive rejetée ; vague orange seulement si aucune verte valide | `test_handover.py::test_13_42_vague_verte_avant_orange`, `test_42b_pas_de_vague_orange_si_une_candidature_verte_est_valide` |
| 43 | Ordre et vitesse sans influence ; un seul tirage officiel | `test_handover.py::test_15_43_vitesse_de_reponse_sans_effet_et_resultat_immediatement_officiel` |
| 44 | Planning, quota, historique et clôture dans une seule transaction | `test_handover.py::test_14_40_44_45_collecte_gel_tirage_et_effets` |
| 45 | Une seule notification de clôture par candidat non retenu | `test_handover.py::test_14_40_44_45_collecte_gel_tirage_et_effets` |
| 46 | Échange refusé si la garde n'est plus future, publiée, détenue par la personne annoncée, ou déjà engagée | `test_swap.py::test_46_refus_si_la_garde_n_est_plus_future`, `test_46b_refus_si_la_garde_change_de_titulaire` |
| 47 | Le rouge bloque conversion, candidature, reprise, échange, affectation manuelle et appel direct à l'API | `test_campaign.py::test_37_...` (conversion), `test_planning.py::test_47_le_rouge_bloque_tous_les_chemins`, `test_swap.py::test_50_...` |
| 48 | Concurrence reprise / échange sur une même garde | `test_handover.py::test_48_concurrence_entre_reprise_et_echange` |
| 49 | Demandeur exclu de sa propre vague | `test_handover.py::test_12_49_52_anonymat_et_exclusion_du_demandeur` |
| 50 | Revérification séparée pour chacun des deux médecins | `test_swap.py::test_50_reverification_separee_des_deux_medecins` |
| 51 | Annulation et tirage concurrents → un seul état final | `test_handover.py::test_51_annulation_et_tirage_concurrents_un_seul_etat_final` |
| 52 | Identité du demandeur masquée jusqu'à l'attribution | `test_handover.py::test_12_49_52_anonymat_et_exclusion_du_demandeur` |

---

## Notes de méthode

- **Exigence 15** — l'équité du tirage est vérifiée de deux manières complémentaires :
  structurellement (la liste utilisée par le tirage est triée, donc l'ordre de dépôt
  n'y figure pas) et statistiquement (sur au moins 8 tirages à deux candidats, le
  premier à répondre ne gagne ni jamais ni systématiquement).
  Ce test neutralise la règle de repos afin de disposer d'assez de tirages
  exploitables dans le petit univers de test ; la propriété testée n'en dépend pas.

- **Exigence 26** — l'égalité vérifiée est stricte :
  `postes requis == postes répartis + postes non couverts`, à la fois globalement et
  catégorie par catégorie, avec et sans conversion du mode B en mode A.

- **Exigence 47** — la vérification repose sur le fait qu'il n'existe **qu'un seul**
  point de définition des contraintes fermes (`app/engine/hard.py`), appelé par tous
  les chemins via `services/engine_bridge.check_assignment`. Le test vérifie en outre
  que la signature de `manual_correction` ne comporte aucun paramètre de dérogation.
