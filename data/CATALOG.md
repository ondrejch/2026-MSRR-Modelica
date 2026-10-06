# Plant-data catalog

Generated from plant `msrr`. Every quantity path, unit, and `doc` field.
Regenerate with `python3.12 -m helpers.emit_modelica_plant --catalog`.

| Path | Unit | Value (abbrev.) | Doc |
|---|---|---|---|
| `nominal_power` | W | `1000000` | Nominal fission-plus-decay power P used by PowerBlock (1 MW). |
| `total_fuel_vol` | m3 | `0.5` | Total primary salt inventory (core cells + loop). Cross-check, not a derived sum in the lumped models. |
| `kinetics.num_groups` | 1 | `6` | Number of delayed-neutron precursor groups. |
| `kinetics.lambda` | 1/s | `[0.0124, 0.0305, 0.111, … (6)]` | Delayed-neutron decay constants, group order 1..6. |
| `kinetics.beta` | 1 | `[0.000226677, 0.001481023, 0.001328549, … (6)]` | Delayed-neutron fractions. sumBeta = 6.720e-3 (672 pcm). |
| `kinetics.generation_time` | s | `0.0002593` | Prompt neutron generation time LAMBDA. |
| `kinetics.n_floor` | 1 | `1e-09` | Numerical neutron-population floor (solver aid, not source strength). |
| `kinetics.source_scale` | 1/s | `1.58e+20` | LEGACY external-source normalizer, formerly inlined as S.nDot/1.58E20 in the lumped mPKE/PKE and still kept verbatim in SegmentedMSR.Verify.RefMPKE and the SegmentedMSR.Constants.nomSourceScale copy. Not a physical population: the full-power neutron population is N0 = LAMBDA*nu*P/E_f = 1.82e13, so this legacy value makes an absolute source ~8.7e6 times too weak (physics review 2026-09-27). Neither the SegmentedMSR product kinetics (PKE_T) nor the legacy SMD_MSR_Modelica PKE/mPKE reads it any more - both derive N0 from generation_time, nu, nominal_power and poisons.energy_per_fission.  |
| `kinetics.nu` | 1 | `2.43` | Mean number of neutrons emitted per fission (prompt + delayed). Converts the absolute external source S [n/s] into the normalized population rate S/N0 with the full-power population N0 = LAMBDA*nu*P/E_f (SegmentedMSR PKE_T; legacy SMD_MSR_Modelica PKE/mPKE via MSRR_PlantData.Kinetics.nu), and sets the homogeneous poison-worth denominator Sigma_a = nu*Sigma_f (critical-core one-group absorption) in SegmentedMSR.Nuclear.HomogeneousPoisons.  |
| `kinetics.source_effectiveness` | 1 | `1` | External-source effectiveness eta_S (importance of a source neutron relative to the fundamental mode): the normalized source rate is eta_S*S/N0 with N0 = LAMBDA*nu*P/E_f (SegmentedMSR PKE_T; legacy SMD_MSR_Modelica PKE/mPKE via MSRR_PlantData.Kinetics.sourceEffectiveness). The default 1.0 assumes unit importance. This is an ASSUMPTION, not a sourced value: absolute source-range power (startup phases 1-3) scales linearly with it and is a sensitivity result, not a qualified prediction, until eta_S is sourced (physics review 2026-09-27; external review of rev031). Numbers at power (sourceRate = 0 after the startup source is withdrawn) do not depend on it.  |
| `kinetics.lumped_tau_core` | s | `43.5146` | Nominal in-core fuel transit used by the lumped 1R mPKE, 0.4 m3 / vdot_fuel (the legacy 9R core derives its own value from its regional volumes). |
| `kinetics.lumped_tau_loop` | s | `10.8787` | Nominal ex-core loop transit used by the lumped 1R mPKE, primary-loop volume 0.1000004 m3 / vdot_fuel. |
| `kinetics.feedback.a_F` | 1/K | `-6.26e-05` | Fuel-temperature reactivity coefficient (same numeric in degC or K because it is per kelvin). |
| `kinetics.feedback.a_G` | 1/K | `-5.16e-05` | Graphite-temperature reactivity coefficient. |
| `decay_heat.num_groups` | 1 | `3` | Number of decay-heat groups. |
| `decay_heat.DHYG` | 1/s | `[0.0023751, 8.7763e-05, 1.9596e-06]` | Decay-heat group yield coefficients. |
| `decay_heat.DHlamG` | 1/s | `[0.09453, 0.00442, 8.6098e-05]` | Decay-heat group decay constants. |
| `decay_heat.nom_frac` | 1 | `0.068` | Equilibrium decay-heat fraction of nominal power. Prompt fission carries 1 - nom_frac. |
| `materials.fuel.rho` | kg/m3 | `2600` | Fuel-salt density of 67LiF-28BeF2-5UF4 (7Li, 19.75 wt% U-235) at the 568 degC core average (2583 kg/m3 at 600 degC; +-3 %). |
| `materials.fuel.cp` | J/(kg.K) | `1776` | Fuel-salt specific heat of 67LiF-28BeF2-5UF4 (+-4 %). |
| `materials.fuel.k` | W/(m.K) | `0` | Fuel-salt conductivity. Zero in production (axial salt conduction off). |
| `materials.graphite.rho` | kg/m3 | `1800` | Graphite density. |
| `materials.graphite.cp` | J/(kg.K) | `1773` | Graphite specific heat. |
| `materials.coolant.rho` | kg/m3 | `2038` | Secondary-coolant density, FLiBe 67LiF-33BeF2 (PSAR Sec. 4.1) at 495 degC (+-1 %). |
| `materials.coolant.cp` | J/(kg.K) | `2390` | Secondary-coolant specific heat. |
| `materials.coolant.k` | W/(m.K) | `0` | Secondary-coolant conductivity (off). |
| `materials.hx_tube.rho` | kg/m3 | `8774.5` | Intermediate-HX tube-wall density. |
| `materials.hx_tube.cp` | J/(kg.K) | `450` | Intermediate-HX tube-wall specific heat. |
| `primary_loop.vdot_fuel` | m3/s | `0.0091923` | Nominal primary volumetric flow at pump FF = 1 (= mdot/rho_fuel; keep consistent with materials.fuel.rho). |
| `primary_loop.volumes.pipeCoreToDHRS` | m3 | `0.0024369` | Pipe from core outlet to DHRS. |
| `primary_loop.volumes.DHRS` | m3 | `0.04` | Drain-heat-removal-system salt volume. |
| `primary_loop.volumes.pipeDHRStoHX` | m3 | `0.0048738` | Pipe from DHRS to intermediate HX primary inlet. |
| `primary_loop.volumes.HXprimary` | m3 | `0.045379` | Intermediate HX primary-side salt volume (also heatExchanger.vol_P). |
| `primary_loop.volumes.pipeHXtoCore` | m3 | `0.0073107` | Pipe from HX primary outlet back to core inlet. |
| `primary_loop.pipes.pipeCoreToDHRS.volFracNode` | 1 | `0.1` | Node volume fraction for the pipe thermal nodes. |
| `primary_loop.pipes.pipeCoreToDHRS.Ac` | m2 | `0.0027321` |  |
| `primary_loop.pipes.pipeCoreToDHRS.L` | m | `0.8919` |  |
| `primary_loop.pipes.pipeCoreToDHRS.Ar` | m2 | `0.1653` |  |
| `primary_loop.pipes.pipeCoreToDHRS.e` | 1 | `0.08` |  |
| `primary_loop.pipes.pipeCoreToDHRS.Tinf` | degC | `644.4` |  |
| `primary_loop.pipes.pipeCoreToDHRS.T_0` | degC | `580.41` |  |
| `primary_loop.pipes.pipeDHRStoHX.volFracNode` | 1 | `0.01` |  |
| `primary_loop.pipes.pipeDHRStoHX.Ac` | m2 | `0.0027321` |  |
| `primary_loop.pipes.pipeDHRStoHX.L` | m | `1.7839` |  |
| `primary_loop.pipes.pipeDHRStoHX.Ar` | m2 | `0.3305` |  |
| `primary_loop.pipes.pipeDHRStoHX.e` | 1 | `0.08` |  |
| `primary_loop.pipes.pipeDHRStoHX.Tinf` | degC | `644.4` |  |
| `primary_loop.pipes.pipeDHRStoHX.T_0` | degC | `580.41` |  |
| `primary_loop.pipes.pipeHXtoCore.volFracNode` | 1 | `0.1` |  |
| `primary_loop.pipes.pipeHXtoCore.Ac` | m2 | `0.0027321` |  |
| `primary_loop.pipes.pipeHXtoCore.L` | m | `2.6758` |  |
| `primary_loop.pipes.pipeHXtoCore.Ar` | m2 | `0.4958` |  |
| `primary_loop.pipes.pipeHXtoCore.e` | 1 | `0.08` |  |
| `primary_loop.pipes.pipeHXtoCore.Tinf` | degC | `644.4` |  |
| `primary_loop.pipes.pipeHXtoCore.T_0` | degC | `560` |  |
| `primary_loop.dhrs.tK` | s | `10` | DHRS actuation time constant. |
| `primary_loop.dhrs.max_remove` | W | `320000` | DHRS maximum heat-removal rate. |
| `primary_loop.dhrs.bleed` | W | `0` | DHRS continuous bleed load. |
| `primary_loop.dhrs.engage_time` | s | `1000000` | Default DHRS engagement time on the production loop (trip vehicles override). |
| `primary_loop.dhrs.Ac` | m2 | `0.282743339` |  |
| `primary_loop.dhrs.L` | m | `0.141471061` |  |
| `primary_loop.dhrs.Ar` | m2 | `0.266666667` |  |
| `primary_loop.dhrs.e` | 1 | `0.08` |  |
| `primary_loop.dhrs.Tinf` | degC | `644.5` | DHRS ambient temperature. 0.1 degC above the other Tinf nodes (644.4): the historically observed production value (every legacy R1MSRRuhx dhrs instantiation reads Tinf = 644.5; core/MSRR.mo binds Tinf from this generated table, so "verbatim" here records the long-standing value preserved unchanged, not a copy from Modelica source); not normalized (rev021 finding #23).  |
| `primary_loop.dhrs.T_0` | degC | `580.41` |  |
| `secondary_loop.vdot_coolant` | m3/s | `0.026202` | Nominal secondary volumetric flow at pump FF = 1 (= mdot/rho_coolant; keep consistent with materials.coolant.rho). |
| `secondary_loop.hx.vol_P` | m3 | `0.045379` | Intermediate HX primary volume (same as primary_loop.volumes.HXprimary). |
| `secondary_loop.hx.vol_T` | m3 | `0.023343` | Intermediate HX tube-wall volume. |
| `secondary_loop.hx.vol_S` | m3 | `0.055947` | Intermediate HX secondary volume. |
| `secondary_loop.hx.hApNom` | W/K | `77966` | Primary-side UA at FF = 1. |
| `secondary_loop.hx.hAsNom` | W/K | `18150` | Secondary-side UA at FF = 1. |
| `secondary_loop.hx.hAExp` | 1 | `0.33` | HX primary film exponent (hA ~ FF^hAExp). Bound by the lumped HX and, through SegmentedMSR_PlantData.SecondaryLoop.hAExp, by the plant-bound SegmentedMSR HX; the SegmentedMSR library default of 1/3 applies only where a rig leaves it unbound.  |
| `secondary_loop.hx.AcShell` | m2 | `0.1298` |  |
| `secondary_loop.hx.AcTube` | m2 | `0.020276` |  |
| `secondary_loop.hx.ArShell` | m2 | `3.1165` |  |
| `secondary_loop.hx.L_shell` | m | `6.2592` |  |
| `secondary_loop.hx.L_tube` | m | `12.5185` |  |
| `secondary_loop.hx.e` | 1 | `0.01` |  |
| `secondary_loop.hx.Tinf` | degC | `500` |  |
| `secondary_loop.hx.TpIn_0` | degC | `580.41` |  |
| `secondary_loop.hx.TpOut_0` | degC | `560` |  |
| `secondary_loop.hx.TsIn_0` | degC | `500` |  |
| `secondary_loop.hx.TsOut_0` | degC | `507.84` |  |
| `secondary_loop.uhx.vol` | m3 | `0.204183209633634` | UHX demand-node inventory. |
| `secondary_loop.uhx.Tp_0` | degC | `500` |  |
| `secondary_loop.pipes.HXtoUHX.vol` | m3 | `0.2526` |  |
| `secondary_loop.pipes.HXtoUHX.volFracNode` | 1 | `0.1` |  |
| `secondary_loop.pipes.HXtoUHX.Ac` | m2 | `0.012673` |  |
| `secondary_loop.pipes.HXtoUHX.L` | m | `19.932` |  |
| `secondary_loop.pipes.HXtoUHX.Ar` | m2 | `7.9559` |  |
| `secondary_loop.pipes.HXtoUHX.e` | 1 | `0.08` |  |
| `secondary_loop.pipes.HXtoUHX.Tinf` | degC | `644.4` |  |
| `secondary_loop.pipes.HXtoUHX.T_0` | degC | `507.84` |  |
| `secondary_loop.pipes.UHXtoHX.vol` | m3 | `0.4419` |  |
| `secondary_loop.pipes.UHXtoHX.volFracNode` | 1 | `0.1` |  |
| `secondary_loop.pipes.UHXtoHX.Ac` | m2 | `0.012673` |  |
| `secondary_loop.pipes.UHXtoHX.L` | m | `34.87` |  |
| `secondary_loop.pipes.UHXtoHX.Ar` | m2 | `13.918` |  |
| `secondary_loop.pipes.UHXtoHX.e` | 1 | `0.08` |  |
| `secondary_loop.pipes.UHXtoHX.Tinf` | degC | `644.4` |  |
| `secondary_loop.pipes.UHXtoHX.T_0` | degC | `500` |  |
| `pumps.tripK` | s | `50` | Pump-trip coast-down time constant. Every production assembly binds tripK = 50 s. |
| `pumps.freeConvFF` | 1 | `0.01` | Forced-flow-fraction floor. Production pumps never reach FF = 0. |
| `pumps.tripTime` | s | `10000000` | Default pump trip time on the production loop (pushed to 1e12 on no-trips variants). |
| `pumps.primary.numRampUp` | 1 | `1` |  |
| `pumps.primary.rampUpK` | s | `[1]` |  |
| `pumps.primary.rampUpTo` | 1 | `[1]` |  |
| `pumps.primary.rampUpTime` | s | `[0]` |  |
| `pumps.secondary.numRampUp` | 1 | `1` |  |
| `pumps.secondary.rampUpK` | s | `[100]` |  |
| `pumps.secondary.rampUpTo` | 1 | `[1]` |  |
| `pumps.secondary.rampUpTime` | s | `[0]` |  |
| `poisons.Te135_lambda` | 1/s | `0.0364814305557866` | Te-135 decay constant lambda = ln(2)/T_half with T_half = 19.0 s. |
| `poisons.I135_lambda` | 1/s | `2.93059083713558e-05` | I-135 decay constant lambda = ln(2)/T_half with T_half = 6.57 h. |
| `poisons.Xe135_lambda` | 1/s | `2.10649019809183e-05` | Xe-135 decay constant lambda = ln(2)/T_half with T_half = 9.14 h. |
| `poisons.Pm149_lambda` | 1/s | `3.62730793880912e-06` | Pm-149 decay constant lambda = ln(2)/T_half with T_half = 53.08 h. Sm-149 is treated as stable. |
| `poisons.Te135_yield` | 1 | `0.03112` | Independent fission yield of Te-135 (atoms per fission). Independent convention; not cumulative. |
| `poisons.I135_yield` | 1 | `0.0288` | Independent fission yield of I-135 (atoms per fission). Independent convention. |
| `poisons.Xe135_yield` | 1 | `0.00241` | Independent (direct) fission yield of Xe-135 (atoms per fission). Independent convention. |
| `poisons.Pm149_yield` | 1 | `0.01071` | Fission yield assigned to Pm-149 (atoms per fission). This is the CUMULATIVE A=149 chain yield (yield_basis.Pm149 = cumulative_chain_entry). Nd-149 and earlier precursors are not modeled, so their independent yields are folded into the first modeled member; Sm-149 then receives only the explicit Pm-149 decay. |
| `poisons.Sm149_yield` | 1 | `0` | Direct independent fission yield of Sm-149. Set to 0; Sm-149 is produced by Pm-149 decay in this inventory. |
| `poisons.sigma_a_Xe` | b | `2650000` | Xe-135 microscopic absorption cross-section (2200 m/s thermal equivalent) in barns. The segmented emitter converts to SI (1 b = 1e-28 m2) before binding PlantData. |
| `poisons.sigma_a_Sm` | b | `40140` | Sm-149 microscopic absorption cross-section (2200 m/s thermal equivalent) in barns. The segmented emitter converts to SI (1 b = 1e-28 m2) before binding PlantData. |
| `poisons.sigma_a_fuel` | 1/m | `1` | LEGACY - not read by the current SegmentedMSR library (physics review 2026-09-27). The homogeneous poison-worth denominator is now DERIVED in SegmentedMSR.Nuclear.HomogeneousPoisons and helpers.poison_oracle as the critical-core one-group absorption Sigma_a = kinetics.nu*F0/(phi0*V_avg) (1.8958 1/m for the 0.4 m3 core-volume averaging); this former 1.0/m placeholder made the Xe/Sm worth ~1.9x the self-consistent value. Kept only so the pre-2026-09-27 loader/library copies replayed by the history non-vacuity tests still bind.  |
| `poisons.phi0` | 1/(cm2.s) | `10000000000000` | Full-power core-average neutron flux in n/(cm2.s). The segmented emitter converts to SI (1/(m2.s) = 1e4 * 1/(cm2.s)) before binding PlantData. Averaging convention is flux_averaging=core_volume; absorption uses b_s = sigma_a * phi0 * (V_core/V_fuel) * (n/n0) in SI. |
| `poisons.energy_per_fission` | J | `3.204353268e-11` | Recoverable energy per fission used to derive the whole-reactor fission rate F0 = P / E from plant nominal_power. 200 MeV * 1.602176634e-19 J/eV. |
| `poisons.k_rem_Xe` | 1/s | `0` | Optional global first-order Xe-135 removal rate. Zero in the first dataset (no sparging/off-gas). |
| `poisons.k_rem_Sm` | 1/s | `0` | Optional global first-order Sm-149 removal rate. Zero; Sm is treated as stable and unremoved. |
| `outer_fuel_annulus.inventory.legacy_term` | m3 | `0.4` | Legacy term identity: in_vessel_fuel_inventory. This is the legacy term that implicitly contained any in-vessel outer-annulus fuel: the cited design-table row "Reactor fuel volume 400 L" and the residence-time sentence "in-vessel fuel inventory 400 L" (msrrPrelim via latex/MSRR_journal_article/msrr_system_dynamics.tex design-data table and 'Residence Times and Circulation Reactivity Loss' subsection). PSAR primary source (S6, section 4.2.1.1 folio 4-6): "The reactor vessel requires 400 L to fill." Bound in-repo as cores/r1.yaml cell_vol [0.2, 0.2] (MSRR.Components.MSRR1R fuelchannel vol_FN1/vol_FN2) and cores/r1_10seg.yaml cell_vol (ten x 0.04, sum 0.4); consumed by SegmentedMSR PowerBlock.TotalFuelVol = sum(cellVol)+sum(volLoop) (core/SegmentedMSR.mo:2432, 4565, 4994, 5348, 5806).  |
| `outer_fuel_annulus.inventory.total_retained` | m3 | `0.5` | plant.yaml total_fuel_vol is NOT changed under split_existing; it stays the cross-check total (in-repo closure: 0.4 + 0.1000004 = 0.5000004, inside the 1e-6 warning tolerance). PSAR primary source (S6, section 4.2.1.1 folio 4-6): the "reactor loop, during normal operation, will be filled with approximately 500 L"; the approximately 550 L reactor SYSTEM adds the roughly 50 L drain-tank inventory, so 0.55 m3 is NOT the loop total (decision record D7, discrepancy 1).  |
| `outer_fuel_annulus.axial_grid_1r10seg.lf_per_segment` | m | `0.1575` | Modeled per-segment active length; ten segments span the 1.575 m modeled active channel length (2 x the r1 value 0.7875 m). The outer-annulus assembly must use the same per-cell Lcell[nSeg] contract (TASK-20260916-01 P3; nonuniform-capable, never L/nSeg).  |
| `outer_fuel_annulus.axial_grid_1r10seg.dz_pitch` | m | `0.14` | Axial pitch per segment (10 x 0.14 m = 1.4 m = 2 x the r1 value 0.7 m); moderator center spacing, does not control heat capacity. |
| `outer_fuel_annulus.axial_grid_1r10seg.modeled_active_length` | m | `1.575` | Ten x 0.1575 m; the r1-inherited lumped active length (2 x 0.7875 m). |
| `outer_fuel_annulus.axial_grid_1r10seg.graphite_height_design_value` | m | `1.5168` | Cited "Maximum graphite height 151.68 cm" from the design-data table (msrrPrelim). PSAR primary source (S6, Table 4.2-1 folio 4-19): "Maximum height of graphite core 151.68 cm", tabulated at 600 degC and marked preliminary ("Final design parameters will be provided in the Operating License application", section 4.2.3 folio 4-12). Disclosed approximation: the modeled active length 1.575 m exceeds it by ~5.8 cm because the modeled length is the r1 inherited lump, not a surveyed graphite height. Recorded for comparison only; the annulus elevations follow the model grid, not this design value.  |
| `outer_fuel_annulus.fission.source_fraction` | 1 | `[0, 0, 0, … (10)]` | f^S_a,j [1]: fraction of whole-reactor fission EVENTS occurring in annulus segment j (ten entries for the r1_10seg target; segment j = 1..10 spans [(j-1) x 0.1575 m, j x 0.1575 m] measured from the BOTTOM of the modeled active region, matching the axial_grid_1r10seg span_definition). Validation (fail-closed, plan section 5.2): finite, nonnegative entries; length = n_seg (10); STRICT retained-share 1-fsum(f) >= 1e-9 (hence 0 <= sum_j f^S_a,j < 1; the active core keeps F_c^S = 1 - F_a^S > 0 with at least a 1e-9 share); nonzero entries illegal while the outer annulus or the fission feature is disabled; no silent renormalization. Provenance is required for every nonzero quantity (and on any fission-enabled deck, where the enabled-deck provenance walk demands doc+source on both vectors). The zeros here are the shipped disabled state, NOT sourced data. NON-SOURCE (TASK-20260919-01 P2): the MSRR PSAR (Rev. 0, August 2022, NRC ADAMS ML22227A203) predates the fission increment and provides NO annulus fission split -- section 4.6.1.2 folio 4-47 deposits "almost all of the reactor power" within the reactor vessel, and section 4.5.2.6 folio 4-35 locates criticality only in the vessel -- so these zeros are a documented non-source, never an inherited default; any future distribution needs its own reviewed source.  |
| `outer_fuel_annulus.fission.heat_deposition_fraction` | 1 | `[0, 0, 0, … (10)]` | f^Q_a,j [1]: fraction of whole-reactor prompt fission POWER deposited in annulus segment j (P2 delivers f^Q_a,j * P_total to annulus cell j and (1 - F_a^Q) * P_total to the embedded core). Same fail-closed rules as source_fraction (length 10, strict retained-share 1-fsum(f) >= 1e-9, hence 0 <= sum < 1, nonnegative, no renormalization). Under local_same_fraction this vector must agree with source_fraction ELEMENTWISE within OUTER_ANNULUS_FISSION_FRACTION_TOLERANCE (1e-9) - a validation rule that keeps the distinct names, not a merge. NON-SOURCE (TASK-20260919-01 P2): the PSAR (Rev. 0, August 2022) provides no annulus heat-deposition split either (section 4.6.1.2 folio 4-47; Figures 4.5-4/4.5-5 are image-only in the extracted text) -- the same documented non-source as source_fraction; the zeros are not an inherited default.  |
| `cores.r1.n_chan` | 1 | `1` | Channel count (lumped 1R and segmented product first cut). |
| `cores.r1.n_seg` | 1 | `2` | Axial salt segments (legacy fuelNode1 / fuelNode2). |
| `cores.r1.cell_vol` | m3 | `[0.2, 0.2]` | Salt volume per axial node (vol_FN1, vol_FN2). |
| `cores.r1.vol_graphite` | m3 | `1.758` | Graphite volume worth of heat capacity (vol_GN). Named capacity, not pitch*dz*annulus. |
| `cores.r1.hAnom` | W/K | `4565` | Channel-total fuel-salt-to-graphite UA at pump FF = 1. |
| `cores.r1.q_fiss` | 1 | `[0.465, 0.465]` | Direct fission heat split to the two salt nodes (kFN1, kFN2). |
| `cores.r1.q_mod` | 1 | `[0.035, 0.035]` | Direct-to-moderator heat split (product PerCell; 0.07/nSeg). Collapse harness uses [0.07, 0] instead. |
| `cores.r1.kG` | 1 | `0.07` | Lumped single-graphite-node fission fraction (kG). Segmented product splits this equally over nSeg. |
| `cores.r1.kHT` | 1 | `[0.5, 0.5]` | Convection split kHT_FN1 / kHT_FN2 (htFrac). |
| `cores.r1.flow_frac` | 1 | `[1]` | Design flow split over channels; 1R is a single channel. |
| `cores.r1.IF` | 1 | `[0.5, 0.5]` | Fuel-node importance weights IF1, IF2. |
| `cores.r1.IG` | 1 | `1` | Graphite importance weight. |
| `cores.r1.channel_geom.Ac` | m2 | `1.5882` |  |
| `cores.r1.channel_geom.LF` | m | `[0.7875, 0.7875]` |  |
| `cores.r1.channel_geom.ArF` | m2 | `[3.5172, 3.5172]` |  |
| `cores.r1.channel_geom.e` | 1 | `0.08` |  |
| `cores.r1.hAExp` | 1 | `0.33` | Lumped fuel-channel film exponent (hA = hAnom * FF^hAExp). Documentation copy of the MSRR.Components.FuelChannel literal default. |
| `cores.r1.channel_map.pitch` | m | `0.02` | Placeholder lattice pitch. Does not control heat capacity. |
| `cores.r1.channel_map.dz` | m | `0.7` | Placeholder axial pitch. Does not control heat capacity. |
| `cores.r1.channel_map.chanR` | m | `[0.008]` | Placeholder channel radius. Does not control heat capacity. |
| `cores.r1.channel_map.xy` | m | `[[0.0, 0.0]]` |  |
| `cores.r1.trim.TF1` | degC | `570` | Fuel-node-1 trim setpoint (lumped default). Segmented emit adds 273.15 K. |
| `cores.r1.trim.TF2` | degC | `580.41` | Fuel-node-2 trim setpoint. |
| `cores.r1.trim.TG` | degC | `570.0000028` | Graphite trim setpoint. |
| `cores.r1.heat_loss_tinf` | degC | `550` | Ambient trench temperature when heaters are off (startup heat-loss cases). |
| `cores.r1.segmented_steady_state.power` | 1 | `1` | Relative power level of the steady state (segmented runs require powerLevel = 1). |
| `cores.r1.segmented_steady_state.reference_temperature` | degC | `570` | Isothermal zero-power critical reference state; the feedback-weighted core temperature equals it at every power (lumped core/init convention). |
| `cores.r1.segmented_steady_state.T_fuel` | degC | `[557.8830962495, 569.1987898633]` | Qualified steady-state fuel-salt cell temperatures, per salt cell / moderator node of the 1-channel x 2-segment map (the collapse rig lumps the graphite into ONE node, so both T_mod entries are equal). |
| `cores.r1.segmented_steady_state.T_mod` | degC | `[577.8359876874, 577.8359876874]` | Qualified steady-state moderator (graphite) node temperatures, same layout as T_fuel. |
| `cores.r1_10seg.n_chan` | 1 | `1` | Channel count (single channel, as r1). |
| `cores.r1_10seg.n_seg` | 1 | `10` | Axial salt segments (10-segment axial resolution; uniform first cut). |
| `cores.r1_10seg.cell_vol` | m3 | `[0.04, 0.04, 0.04, … (10)]` | Salt volume per axial node; uniform partition of the r1 total (2 x 0.2 m3). Sum = 0.4 m3. |
| `cores.r1_10seg.vol_graphite` | m3 | `1.758` | Graphite volume worth of heat capacity (vol_GN); inherited unchanged from r1. Named capacity, not pitch*dz*annulus. |
| `cores.r1_10seg.hAnom` | W/K | `4565` | Channel-total UA at pump FF = 1; inherited unchanged from r1. |
| `cores.r1_10seg.q_fiss` | 1 | `[0.093, 0.093, 0.093, … (10)]` | Direct fission heat split to the ten salt nodes; uniform partition of the r1 total (2 x 0.465). Sum = 0.93. |
| `cores.r1_10seg.q_mod` | 1 | `[0.007, 0.007, 0.007, … (10)]` | Direct-to-moderator heat split (product PerCell); uniform partition of the r1 total (2 x 0.035). Sum = 0.07 = kG. |
| `cores.r1_10seg.kG` | 1 | `0.07` | Lumped single-graphite-node fission fraction (kG); inherited unchanged from r1. Segmented product splits this equally over nSeg. |
| `cores.r1_10seg.kHT` | 1 | `[0.1, 0.1, 0.1, … (10)]` | Convection split over the ten salt nodes (htFrac); uniform partition of the r1 total (2 x 0.5). Sum = 1.0. |
| `cores.r1_10seg.flow_frac` | 1 | `[1]` | Design flow split over channels; 1R is a single channel. |
| `cores.r1_10seg.IF` | 1 | `[0.5, 0.5]` | Fuel-node importance weights IF1, IF2. The product feedback API stays 2-node (segments 1-5 / 6-10 map to nodes 1 / 2). |
| `cores.r1_10seg.IG` | 1 | `1` | Graphite importance weight; inherited unchanged from r1. |
| `cores.r1_10seg.channel_geom.Ac` | m2 | `1.5882` |  |
| `cores.r1_10seg.channel_geom.LF` | m | `[0.1575, 0.1575, 0.1575, … (10)]` |  |
| `cores.r1_10seg.channel_geom.ArF` | m2 | `[3.5172, 3.5172, 3.5172, … (10)]` |  |
| `cores.r1_10seg.channel_geom.e` | 1 | `0.08` |  |
| `cores.r1_10seg.hAExp` | 1 | `0.33` | Lumped fuel-channel film exponent (hA = hAnom * FF^hAExp); inherited unchanged from r1. |
| `cores.r1_10seg.channel_map.pitch` | m | `0.02` | Lattice pitch; inherited unchanged from r1. Does not control heat capacity. |
| `cores.r1_10seg.channel_map.dz` | m | `0.14` | Axial pitch per segment (10 x 0.14 m = 1.4 m = 2 x the r1 value 0.7 m). Does not control heat capacity. |
| `cores.r1_10seg.channel_map.chanR` | m | `[0.008]` | Channel radius; inherited unchanged from r1. Does not control heat capacity. |
| `cores.r1_10seg.channel_map.xy` | m | `[[0.0, 0.0]]` |  |
| `cores.r1_10seg.trim.TF1` | degC | `570` | Fuel-node-1 trim setpoint; applies to segments 1-5 (5+5 mapping). Inherited from r1; segmented emit adds 273.15 K. |
| `cores.r1_10seg.trim.TF2` | degC | `580.41` | Fuel-node-2 trim setpoint; applies to segments 6-10 (5+5 mapping). Inherited from r1. |
| `cores.r1_10seg.trim.TG` | degC | `570.0000028` | Graphite trim setpoint; applies to all ten graphite segments. Inherited from r1. |
| `cores.r1_10seg.heat_loss_tinf` | degC | `550` | Ambient trench temperature when heaters are off (startup heat-loss cases); inherited unchanged from r1. |
| `cores.r1_10seg.segmented_steady_state.power` | 1 | `1` | Relative power level of the steady state (segmented runs require powerLevel = 1). |
| `cores.r1_10seg.segmented_steady_state.reference_temperature` | degC | `570` | Isothermal zero-power critical reference state; the feedback-weighted core temperature equals it at every power (lumped core/init convention). |
| `cores.r1_10seg.segmented_steady_state.T_fuel` | degC | `[553.0829995653, 555.4069870102, 557.7309744551, … (10)]` | Qualified steady-state fuel-salt cell temperatures, per axial segment 1..10 of the single channel. |
| `cores.r1_10seg.segmented_steady_state.T_mod` | degC | `[567.3780441721, 569.702031617, 572.0260190618, … (10)]` | Qualified steady-state moderator (graphite) node temperatures, same layout as T_fuel. |
| `cores.r5x5_z10.n_chan` | 1 | `25` | Channel count (5 x 5 staggered triangular lattice, 25 channels). |
| `cores.r5x5_z10.n_seg` | 1 | `10` | Axial salt segments (10-segment axial resolution, as r1_10seg). |
| `cores.r5x5_z10.cell_vol` | m3 | `[[0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016], [0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016, 0.0016]]` | Salt volume per (channel, segment) cell; uniform 25 x 10 partition of the r1 total, every entry 0.4/250 = 0.0016 m3. Sum = 0.4 m3. |
| `cores.r5x5_z10.vol_graphite` | m3 | `1.758` | Graphite volume worth of heat capacity (vol_GN); inherited unchanged from r1. The product assembly binds vol_GN/nChan = 1.758/25 on SegmentedCore so the 250 PerCell moderator nodes sum to rho_grap*1.758 exactly (9R zone-partition precedent). |
| `cores.r5x5_z10.hAnom` | W/K | `[182.6, 182.6, 182.6, … (25)]` | Per-channel UA at pump FF = 1; uniform 25-channel partition of the r1 channel total (4565/25 = 182.6 W/K each; 9R per-region-UA-sums-to-total precedent). Sum = 4565 W/K. |
| `cores.r5x5_z10.q_fiss` | 1 | `[[0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372], [0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372, 0.00372]]` | Direct fission heat split per salt cell; uniform 250-cell partition of the r1 total, every entry 0.93/250 = 0.00372. Sum = 0.93. |
| `cores.r5x5_z10.q_mod` | 1 | `[[0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028], [0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028, 0.00028]]` | Direct-to-moderator heat split per cell (product PerCell); uniform 250-cell partition of the r1 total, every entry 0.07/250 = 0.00028. Sum = 0.07 = kG. |
| `cores.r5x5_z10.kG` | 1 | `0.07` | Lumped single-graphite-node fission fraction (kG); inherited unchanged from r1. |
| `cores.r5x5_z10.kHT` | 1 | `[0.1, 0.1, 0.1, … (10)]` | Convection split over the ten salt nodes (htFrac); as r1_10seg (uniform 0.1 per segment). Sum = 1.0. |
| `cores.r5x5_z10.flow_frac` | 1 | `[0.04, 0.04, 0.04, … (25)]` | Design flow split over the 25 channels; explicit uniform 1.0/25.0 = 0.04 per channel (owner decision 3, 2026-09-06). Sum = 1.0. |
| `cores.r5x5_z10.IF` | 1 | `[0.5, 0.5]` | Fuel-node importance weights IF1, IF2. The product feedback API stays 2-node (segments 1-5 / 6-10 map to nodes 1 / 2, over all 25 channels). |
| `cores.r5x5_z10.IG` | 1 | `1` | Graphite importance weight; inherited unchanged from r1. |
| `cores.r5x5_z10.channel_geom.Ac` | m2 | `0` | Channel flow area; owner-approved 0.0 for this first cut. r1.yaml/r1_10seg.yaml record Ac = 1.5882 m2 as provenance-only channel_geom metadata - Ac is emitted into the record but no assembly binds it, so there is no behavioral difference. |
| `cores.r5x5_z10.channel_geom.LF` | m | `[0.1575, 0.1575, 0.1575, … (10)]` |  |
| `cores.r5x5_z10.channel_geom.ArF` | m2 | `[3.5172, 3.5172, 3.5172, … (10)]` | Fuel-salt radiating interface area of the lumped fuel node (radPowFN1/radPowFN2 = e*SigSBK*ArF*(Tinf^4 - T^4) in the r1 FuelChannel). SHARED FULL-CORE GEOMETRY, NOT A PER-CHANNEL PARTITION - the value repeats the r1.yaml full-core per-fuel-node 3.5172 m2 unchanged (identical to r1_10seg.yaml), unlike cell_vol/q_fiss/q_mod/hAnom/flow_frac, which are 1/25 radial partitions; LF follows the same full-core-per-segment repeat. Emitted as SegmentedMSR_PlantData.CoreR5x5_Z10.ArF[nSeg] provenance only; no segmented assembly binds per-segment LF/ArF (emitter convention note), so there is no behavioral difference - the same treatment as Ac above. hAnom/ArF is therefore NOT a per-channel film coefficient; hAnom is already the per-channel UA in W/K (documented 2026-09-20, TASK-20260920-01 P17). |
| `cores.r5x5_z10.channel_geom.e` | 1 | `0.08` |  |
| `cores.r5x5_z10.hAExp` | 1 | `0.33` | Lumped fuel-channel film exponent (hA = hAnom * FF^hAExp); inherited unchanged from r1. |
| `cores.r5x5_z10.k_mod` | W/(m.K) | `86` | Graphite thermal conductivity (K_mod); bound on SegmentedCore by the product assembly with enableRadialMod = true - the first product map to bind a non-zero value (TASK-20260906-02 decision 6, first cut 2026-09-06, owner-approved). The value is the structured provenance below evaluated at the recorded temperature and rounded as recorded; the machine-readable fields recompute it. |
| `cores.r5x5_z10.channel_map.pitch` | m | `0.266824916475` | EFFECTIVE lattice pitch (staggered triangular) derived from the core inventory (physics review 2026-09-27; replaces the 0.02 m placeholder inherited from r1). Each of the 25 model channels lumps several physical channels, so its lattice cell must hold that channel's graphite and salt over the modeled height H = nSeg*dz = 1.4 m - cellA = (vol_graphite/25 + sum_j cell_vol[c,j])/H = (0.07032 + 0.016)/1.4 m2 = (sqrt(3)/2)*p^2. With chanR below, the moderator axial-conduction section map.modAnnulusArea = cellA - pi*chanR^2 = vol_graphite/(25*H) = 0.0502286 m2, consistent with the moderator heat capacity (the 0.02 m placeholder gave 1.4535e-4 m2, ~346x too small, so K_mod = 86 carried almost no axial conduction). G_ck = K_mod*dz/sqrt(3) stays pitch-independent. |
| `cores.r5x5_z10.channel_map.dz` | m | `0.14` | Axial pitch per segment (10 x 0.14 m = 1.4 m, as r1_10seg). Does not control heat capacity. |
| `cores.r5x5_z10.channel_map.chanR` | m | `[0.060314403509, 0.060314403509, 0.060314403509, … (25)]` | EFFECTIVE channel radius (25 identical entries) derived from the salt inventory (physics review 2026-09-27; replaces the 0.008 m placeholder) - pi*chanR^2*H = sum_j cell_vol[c,j] = 0.016 m3 per channel over H = 1.4 m, so chanR = sqrt(0.016/(pi*1.4)). Packing holds (2*chanR <= pitch). Thermal inventories still come from cell_vol and vol_graphite; the pitch/chanR pair only makes the geometric conduction section consistent with them. |
| `cores.r5x5_z10.channel_map.xy` | m | `[[0.0, 0.0], [0.266824916475, 0.0], [0.533649832951, 0.0], [0.800474749426, 0.0], [1.067299665901, 0.0], [0.133412458238, 0.23107715603], [0.400237374713, 0.23107715603], [0.667062291188, 0.23107715603], [0.933887207664, 0.23107715603], [1.200712124139, 0.23107715603], [0.0, 0.462154312061], [0.266824916475, 0.462154312061], [0.533649832951, 0.462154312061], [0.800474749426, 0.462154312061], [1.067299665901, 0.462154312061], [0.133412458238, 0.693231468091], [0.400237374713, 0.693231468091], [0.667062291188, 0.693231468091], [0.933887207664, 0.693231468091], [1.200712124139, 0.693231468091], [0.0, 0.924308624121], [0.266824916475, 0.924308624121], [0.533649832951, 0.924308624121], [0.800474749426, 0.924308624121], [1.067299665901, 0.924308624121]]` | Channel centers [m] on the 5x5 staggered triangular lattice (decision 2). The centered construction (even rows (i=0,2,4) x in (-2p,-p,0,p,2p), odd rows (i=1,3) x in (-3p/2,-p/2,p/2,3p/2,5p/2)) is stored translated by +2p in x so all coordinates are non-negative (the shared data envelope rejects negative 'm' values); the translation is physics-inert because the Modelica neighborIndices match is distance-based. Row i (i=0..4) at y = i*p*sin(60 deg), row-major channel order; stored even rows x in (0, p, 2p, 3p, 4p), stored odd rows x in (p/2, 3p/2, 5p/2, 7p/2, 9p/2), p = the effective pitch above (0.266824916475 m; the 2026-09-27 rescale of the former 0.02 m placeholder lattice by p/0.02 preserves the distance-based neighbor table exactly). Neighbor table (independently re-derived 2026-09-06), 56 undirected edges = 112 of 150 directed slots, per-degree channel counts for degrees 2, 3, 4, 5, 6 are 2, 5, 6, 3, 9. |
| `cores.r5x5_z10.trim.TF1` | degC | `570` | Fuel-node-1 trim setpoint; applies to segments 1-5 (5+5 mapping over all 25 channels). Inherited from r1; segmented emit adds 273.15 K. |
| `cores.r5x5_z10.trim.TF2` | degC | `580.41` | Fuel-node-2 trim setpoint; applies to segments 6-10 (5+5 mapping over all 25 channels). Inherited from r1. |
| `cores.r5x5_z10.trim.TG` | degC | `570.0000028` | Graphite trim setpoint; applies to all 250 graphite cells. Inherited from r1. |
| `cores.r5x5_z10.heat_loss_tinf` | degC | `550` | Ambient trench temperature when heaters are off (startup heat-loss cases); inherited unchanged from r1. |
| `cores.r5x5_z10.segmented_steady_state.power` | 1 | `1` | Relative power level of the steady state (segmented runs require powerLevel = 1). |
| `cores.r5x5_z10.segmented_steady_state.reference_temperature` | degC | `570` | Isothermal zero-power critical reference state; the feedback-weighted core temperature equals it at every power (lumped core/init convention). |
| `cores.r5x5_z10.segmented_steady_state.T_fuel` | degC | `[553.0748863684, 555.4093305517, 557.7381390964, … (10)]` | Qualified steady-state fuel-salt cell temperatures, per axial segment 1..10, identical for all 25 channels (the uniform first-cut forcing keeps the channels exact radial replicas). |
| `cores.r5x5_z10.segmented_steady_state.T_mod` | degC | `[569.447278359, 570.676665482, 572.4814600774, … (10)]` | Qualified steady-state moderator (graphite) node temperatures, same layout as T_fuel. |
| `cores.r9.n_regions` | 1 | `9` | Number of lumped radial regions. |
| `cores.r9.n_zones` | 1 | `4` | Number of series flow zones. |
| `cores.r9.zone_start` | 1 | `[1, 2, 5, … (4)]` | First region index of each zone (1-based, Modelica). |
| `cores.r9.zone_end` | 1 | `[1, 4, 7, … (4)]` | Last region index of each zone (1-based, Modelica). |
| `cores.r9.n_seg_zone` | 1 | `[2, 6, 6, … (4)]` | Salt cells per zone chain (= 2 x regions in the zone). ChannelMap nSeg for the 9R adapter. |
| `cores.r9.vol_F1` | m3 | `[0.003795391373, 0.012869720861, 0.007038081469, … (9)]` | Per-region fuel-node-1 volumes. Verbatim from MSRR.mo msre9r. |
| `cores.r9.vol_F2` | m3 | `[0.003971456514, 0.008772341956, 0.007038081469, … (9)]` | Per-region fuel-node-2 volumes. |
| `cores.r9.vol_G` | m3 | `[0.034880478, 0.105337602, 0.08002416, … (9)]` | Per-region graphite volumes (one graphite node per region). Grand total ≈1.758 m3 = 1R vol_GN. |
| `cores.r9.hA` | W/K | `[94.04, 262.05, 170.44, … (9)]` | Per-region UA at pump FF = 1; sums to the 1R channel total (4565 W/K). |
| `cores.r9.hAExp` | 1 | `0.33` | Fuel-channel film exponent of every region (hA = hA_r * FF^hAExp). Read only by the SegmentedMSR emission (SegmentedMSR_PlantData.Core9R.hAExp, bound by the 9R zone cores); the lumped MSRR.Components.MSRR9R binds no exponent and keeps the FuelChannel default. |
| `cores.r9.kFN1` | 1 | `[0.0149203167145, 0.0273422548766, 0.0450107879986, … (9)]` | Per-region fuel-node-1 fission share (renormalized). Provenance (TASK-20260920-01 P8, 2026-09-20, rev021 finding B3) — all four deposition arrays (kFN1, kFN2, kHT1, kHT2) are the raw MSRR.R9MSRRuhx msre9r values multiplied by the scale factor 1/1.000649 = 0.999351420927818 so the regional total Σ(kFN1+kFN2+kHT1+kHT2) = 1 (original raw sum 1.000649; each region applies its shares to the same fissionPower signal, so the 0.0649 percent surplus was deposited power exceeding powerblock.fissionPower). The renormalized total is 1 within a 1e-12 float bar (measured deviation 4.8e-14). |
| `cores.r9.kFN2` | 1 | `[0.0171988379542, 0.0454704896522, 0.0465298021584, … (9)]` | Per-region fuel-node-2 fission share (renormalized with kFN1 — same scale factor 1/1.000649, 2026-09-20, TASK-20260920-01 P8; see the kFN1 provenance note). |
| `cores.r9.kHT1` | 1 | `[0.000945386444198, 0.00168390714426, 0.00302703545399, … (9)]` | Per-region convection split to fuel-node-1 (also dual-use graphite-direct with kHT2; renormalized with kFN1 — same scale factor 1/1.000649, 2026-09-20, TASK-20260920-01 P8). The graphite-direct share stays sum(kHT1+kHT2) ≈ 0.060730 (the raw ~6.08% family scaled by the same factor), not 1R's 7%. |
| `cores.r9.kHT2` | 1 | `[0.00108029888602, 0.00305801534804, 0.00312896929892, … (9)]` | Per-region convection split to fuel-node-2 (renormalized with kFN1 — same scale factor 1/1.000649, 2026-09-20, TASK-20260920-01 P8; see the kFN1 provenance note). |
| `cores.r9.flow_frac_zones` | 1 | `[0.06141, 0.13855, 0.234231, … (4)]` | Zone flow fractions. Sum = 1. |
| `cores.r9.vol_upper_plenum` | m3 | `0.022986` | Mixing-pot (upper-plenum) volume, fixed so the 9R in-core fuel inventory stays the 400 L vessel volume (0.377013 m3 of regions + 0.022986 m3). |
| `cores.r9.IF1` | 1 | `[0.02168, 0.02197, 0.07897, … (9)]` | Per-region fuel-node-1 importance. |
| `cores.r9.IF2` | 1 | `[0.02678, 0.06519, 0.08438, … (9)]` | Per-region fuel-node-2 importance. |
| `cores.r9.IG` | 1 | `[0.04443, 0.08835, 0.16671, … (9)]` | Per-region graphite importance. |
| `cores.r9.LF1` | m | `[0.7412, 0.3166, 0.1731, … (9)]` | Per-region fuel-node-1 length. |
| `cores.r9.LF2` | m | `[0.7756, 0.2158, 0.1731, … (9)]` | Per-region fuel-node-2 length. |
| `cores.r9.Ac_zones` | m2 | `[0.016189, 0.036524, 0.061748, … (4)]` | Per-zone flow area. |
| `cores.r9.ArF1` | m2 | `[0.7946, 0.3287, 0.1798, … (9)]` |  |
| `cores.r9.ArF2` | m2 | `[0.8314, 0.2241, 0.1798, … (9)]` |  |
| `cores.r9.e` | 1 | `0.08` | Region surface emissivity. |
| `cores.r9.mixing_pot.Ac` | m2 | `1.6118` |  |
| `cores.r9.mixing_pot.L` | m | `0.093874` |  |
| `cores.r9.mixing_pot.Ar` | m2 | `0.4225` |  |
| `cores.r9.mixing_pot.Tmix_0` | degC | `580.41` | Upper-plenum initial temperature. |
| `cores.r9.region_trip_time` | s | `[200000, 200000, 200000, … (4)]` | Per-zone trip times on the production 9R loop. |
| `cores.r9.region_coast_down_K` | 1/s | `0.02` | Per-region coast-down rate after a zone trip. |
| `cores.r9.trim.TF1` | degC | `566.94` | Region-1 fuel-node-1 scalar setpoint (not the volume-weighted table average). |
| `cores.r9.trim.TF2` | degC | `576.02` |  |
| `cores.r9.trim.TG` | degC | `570.89` |  |
| `cores.r9.trim.TF1_regions` | degC | `[566.94, 562.89, 584.55, … (9)]` | Per-region fuel-node-1 initial/trim temperatures. Region 1 aliases the scalar TF1. |
| `cores.r9.trim.TF2_regions` | degC | `[576.02, 572.69, 597.23, … (9)]` |  |
| `cores.r9.trim.TG_regions` | degC | `[570.89, 566.21, 591.18, … (9)]` |  |
| `cores.r9.segmented_steady_state.power` | 1 | `1` | Relative power level of the steady state (segmented runs require powerLevel = 1). |
| `cores.r9.segmented_steady_state.reference_temperature` | degC | `570` | Isothermal zero-power critical reference state; the feedback-weighted core temperature equals it at every power (lumped core/init convention). |
| `cores.r9.segmented_steady_state.T_fuel` | degC | `[547.0644910457, 553.8083551336, 546.0906876243, … (18)]` | Qualified steady-state fuel-salt cell temperatures, per global salt cell / PerCell moderator node in zone-chain order {R1.FN1, R1.FN2, R2.FN1, ..., R9.FN2}. |
| `cores.r9.segmented_steady_state.T_mod` | degC | `[567.1455966388, 573.8894607268, 562.9600452131, … (18)]` | Qualified steady-state moderator (graphite) node temperatures, same layout as T_fuel. |
| `cores.r9.segmented_steady_state.T_plenum` | degC | `564.4327997401` | Qualified steady-state shared upper-plenum (MixingPot) temperature of the zone chain. |
| `harness.perturbation_amplitude_pcm` | pcm | `1` | HARNESS parity sine amplitude. |
| `harness.perturbation_omega` | rad/s | `0.01` |  |
| `harness.perturbation_start` | s | `2000` |  |
| `harness.pump_trip_time` | s | `10000000` | Harness pump tripTime (matches production loop default). |
| `harness.dhrs_time` | s | `1000000` |  |
| `harness.collapse_q_mod` | 1 | `[0.07, 0]` | Collapse ChannelMap qModDirect — all 7% graphite heat on the Single graphite node. |

## Core maturity

| Core | Maturity | Physical data |
|---|---|---|
| `r1` | reference_regression | legacy_inherited |
| `r1_10seg` | discretization_study | legacy_inherited |
| `r5x5_z10` | exploratory_geometry | synthetic |
| `r9` | reference_regression | legacy_inherited |
