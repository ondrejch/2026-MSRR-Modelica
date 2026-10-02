/* MSRR (Molten Salt Reactor Reimagined) simulation package.
   Components contains the core building blocks: FuelChannel (lumped T-H),
   HeatExchanger (MSRR-specific HX variant), MSRR1R (single-region core),
   and MSRR9R (nine-region core).  Top-level models (R1MSRRuhx, R9MSRRuhx,
   and their extends variants) assemble the complete primary + secondary loop
   for steady-state trimming, transient, and startup simulations.
   Plant numbers bind MSRR_PlantData (generated from data/plants/msrr YAML).
   Load order: MSRR_PlantData.mo, then SMD_MSR_Modelica.mo, then this file. */
package MSRR
  package Functions
    function circulationLossPcm
      "Steady-state circulating-fuel reactivity loss beta - sum(CG_0) [pcm] at flow fraction ff (the analytic delay-loop precursor solution used by SMD_MSR_Modelica.Nuclear.mPKE)"
      input Real beta[:] "Delayed-neutron fractions";
      input Real lambda[size(beta, 1)] "Precursor decay constants [1/s]";
      input Real tauCore "Nominal in-core transit time [s]";
      input Real tauLoop "Nominal ex-core transit time [s]";
      input Real ff "Flow fraction (> 0)";
      output Real lossPcm;
    algorithm
      lossPcm := 1e5*(sum(beta) - sum(beta[i]/(1 + (1/(lambda[i]*tauCore/ff))*(1 - exp(-lambda[i]*tauLoop/ff))) for i in 1:size(beta, 1)));
    end circulationLossPcm;
  end Functions;

  /* Core sub-models: lumped T-H fuel/graphite channel, shell-and-tube HX
     (MSRR-specific), and assembled single-region / nine-region reactor blocks. */
  package Components
    /* Lumped two-node fuel-salt + one-node graphite channel for one reactor region.
       hA uses a FF^hAExp power-law approximation (default exponent 0.33, a fit
       to laminar modified Seider-Tate recalculations; Cooke & Cox ORNL-TM-4079,
       Pathirana 2023 dissertation) rather than the 6th-order polynomial in
       SMD_MSR_Modelica.Nuclear.FuelChannel.
       Each fuel node's film convection is driven by that node's own temperature.
       Radiation is only active when OuterRegion == true (outermost annular region).
       Fission-power split: kFN1/kFN2/kG divide the total fission source among fuel
       node 1, fuel node 2, and the graphite node (fissPowFN1/fissPowFN2/fissPowGN).
       The flat kG = 0.07 is the lumped dissertation assumption (93%/7% fuel/graphite
        split). The nine-region models pass region-resolved shares instead,
        kG[i] = kHT1[i] + kHT2[i], whose total is 0.060729586498348 after the
        2026-09-20 B3 Sigma=1 renormalization (the raw MSRR.R9MSRRuhx family was
        ~0.060769) and is intentionally never forced to the thesis 7% (see the
        qModChain9R provenance note and the data-derived normalizer
        fSaltNormalizer9R in SegmentedMSR.Reactors). */
    model FuelChannel
      parameter SMD_MSR_Modelica.Units.Volume vol_FN1;
      parameter SMD_MSR_Modelica.Units.Volume vol_FN2;
      parameter SMD_MSR_Modelica.Units.Volume vol_GN;
      parameter SMD_MSR_Modelica.Units.Density rho_fuel;
      parameter SMD_MSR_Modelica.Units.Density rho_grap;
      parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP_fuel;
      parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP_grap;
      parameter SMD_MSR_Modelica.Units.VolumetricFlowRate Vdot_fuelNom;
      parameter SMD_MSR_Modelica.Units.VolumeImportance kFN1;
      parameter SMD_MSR_Modelica.Units.VolumeImportance kFN2;
      parameter SMD_MSR_Modelica.Units.VolumeImportance kG;
      parameter SMD_MSR_Modelica.Units.Convection hAnom;
      parameter SMD_MSR_Modelica.Units.HeatTransferFraction kHT_FN1;
      parameter SMD_MSR_Modelica.Units.HeatTransferFraction kHT_FN2;
      parameter SMD_MSR_Modelica.Units.Temperature TF1_0;
      parameter SMD_MSR_Modelica.Units.Temperature TF2_0;
      parameter SMD_MSR_Modelica.Units.Temperature TG_0;
      parameter SMD_MSR_Modelica.Units.FlowFraction regionFlowFrac;
      parameter SMD_MSR_Modelica.Units.Conductivity KF;
      parameter SMD_MSR_Modelica.Units.Area Ac;
      parameter SMD_MSR_Modelica.Units.Length LF1;
      parameter SMD_MSR_Modelica.Units.Length LF2;
      parameter Boolean OuterRegion;
      parameter SMD_MSR_Modelica.Units.Area ArF1;
      parameter SMD_MSR_Modelica.Units.Area ArF2;
      parameter SMD_MSR_Modelica.Units.Emissivity e;
      parameter Real hAExp = 0.33
        "Exponent of the FF power-law convection scaling hA = hAnom*FF^hAExp (default 0.33, fit to laminar modified Seider-Tate recalculations)";
      parameter SMD_MSR_Modelica.Units.InitMode initMode = SMD_MSR_Modelica.Units.InitMode.FixedStart;
      SMD_MSR_Modelica.Units.MassFlowRate mdot_fuel;
      SMD_MSR_Modelica.Units.Convection hA;
      SMD_MSR_Modelica.Units.Mass m_FN1;
      SMD_MSR_Modelica.Units.Mass m_FN2;
      SMD_MSR_Modelica.Units.Mass m_GN;
      SMD_MSR_Modelica.Units.Power powFN1;
      SMD_MSR_Modelica.Units.Power powFN2;
      SMD_MSR_Modelica.Units.Power powGN;
      SMD_MSR_Modelica.Units.Power flowPowFN1;
      SMD_MSR_Modelica.Units.Power flowPowFN2;
      SMD_MSR_Modelica.Units.Power condPowFN1;
      SMD_MSR_Modelica.Units.Power condPowFN2;
      SMD_MSR_Modelica.Units.Power convPowFN1;
      SMD_MSR_Modelica.Units.Power convPowFN2;
      SMD_MSR_Modelica.Units.Power radPowFN1;
      SMD_MSR_Modelica.Units.Power radPowFN2;
      SMD_MSR_Modelica.Units.Power fissPowFN1;
      SMD_MSR_Modelica.Units.Power fissPowFN2;
      SMD_MSR_Modelica.Units.Power fissPowGN;
      SMD_MSR_Modelica.Units.Power decayPowFN1;
      SMD_MSR_Modelica.Units.Power decayPowFN2;
      input SMD_MSR_Modelica.PortsConnectors.VolumetricPowerIn decayHeat annotation(
        Placement(transformation(origin = {-80, 32}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-80, 30}, extent = {{-10, -10}, {10, 10}})));
      output SMD_MSR_Modelica.PortsConnectors.TempOut grapNode annotation(
        Placement(transformation(origin = {40, 0}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {40, 0}, extent = {{-10, -10}, {10, 10}})));
      output SMD_MSR_Modelica.PortsConnectors.TempOut fuelNode1 annotation(
        Placement(transformation(origin = {40, -40}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {40, -40}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.TempIn temp_In annotation(
        Placement(transformation(origin = {-80, -60}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-80, -60}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.PowerIn fissionPower annotation(
        Placement(transformation(origin = {-80, 60}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-80, 60}, extent = {{-10, -10}, {10, 10}})));
      output SMD_MSR_Modelica.PortsConnectors.TempOut fuelNode2 annotation(
        Placement(transformation(origin = {40, 40}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {40, 40}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.FlowFractionIn fuelFlowFraction annotation(
        Placement(transformation(origin = {-80, 2}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-80, 0}, extent = {{-10, -10}, {10, 10}})));
      parameter SMD_MSR_Modelica.Units.Temperature Tinf;
      
    initial equation
      /* Parameter validity: convPowFN1/convPowFN2 split the film convection
         through kHT_FN1/(kHT_FN1 + kHT_FN2) and kHT_FN2/(kHT_FN1 + kHT_FN2).
         A zero share on one node is a valid limiting case; the denominator
         only requires a strictly positive sum of nonnegative shares. */
       assert(kHT_FN1 >= 0 and kHT_FN2 >= 0 and kHT_FN1 + kHT_FN2 > 0,
         "FuelChannel: heat-transfer shares must be nonnegative and not both zero");
       // Remaining parameter validity: LF1/LF2 divide condPowFN1/condPowFN2; vol_* are
       // the fuel/graphite inventories; rho_*/cP_* are density and heat capacity;
       // Vdot_fuelNom is the nominal flow.
       assert(LF1 > 0, "FuelChannel: LF1 must be > 0 (divides condPowFN1)");
       assert(LF2 > 0, "FuelChannel: LF2 must be > 0 (divides condPowFN2)");
       assert(vol_FN1 > 0, "FuelChannel: vol_FN1 must be > 0 (node-1 fuel inventory)");
       assert(vol_FN2 > 0, "FuelChannel: vol_FN2 must be > 0 (node-2 fuel inventory)");
       assert(vol_GN > 0, "FuelChannel: vol_GN must be > 0 (graphite inventory)");
       assert(rho_fuel > 0, "FuelChannel: rho_fuel must be > 0 (fuel density)");
       assert(rho_grap > 0, "FuelChannel: rho_grap must be > 0 (graphite density)");
       assert(cP_fuel > 0, "FuelChannel: cP_fuel must be > 0 (fuel heat capacity)");
       assert(cP_grap > 0, "FuelChannel: cP_grap must be > 0 (graphite heat capacity)");
       assert(Vdot_fuelNom > 0, "FuelChannel: Vdot_fuelNom must be > 0 (nominal fuel flow)");
       assert(hAExp >= 0 and hAExp <= 1,
         "FuelChannel: hAExp must be in [0,1] (FF power-law convection exponent)");
       assert(not OuterRegion or (e >= 0 and e <= 1),
         "FuelChannel: e must be in [0,1] when OuterRegion is true");
       // FixedStart: set node temperatures from TF1_0, TF2_0, TG_0 parameters.
       // SteadyState: solver finds equilibrium (all derivatives = 0).
       if initMode == SMD_MSR_Modelica.Units.InitMode.FixedStart then
         fuelNode1.T = TF1_0;
         fuelNode2.T = TF2_0;
         grapNode.T = TG_0;
       else
         der(fuelNode1.T) = 0;
         der(fuelNode2.T) = 0;
         der(grapNode.T) = 0;
       end if;
    equation
      // Flow-domain guard (runtime, not init-only): hA below takes the
      // fractional power FF^hAExp of this externally supplied signal;
      // reverse flow is unsupported.
      assert(fuelFlowFraction.FF >= 0,
        "FuelChannel: fuelFlowFraction.FF must be >= 0; reverse flow unsupported");
//hA = hAnom*(0.8215*fuelFlowFraction.FF^6 - 4.108*fuelFlowFraction.FF^5 + 7.848*fuelFlowFraction.FF^4 - 7.165*fuelFlowFraction.FF^3 + 3.004*fuelFlowFraction.FF^2 + 0.5903*fuelFlowFraction.FF + 0.008537);
//hA = (hAnom);
      // FF^hAExp (default 0.33): power-law fit to laminar modified Seider-Tate
      // recalculations (core channel Re ~ 776; Dittus-Boelter does not apply).
      // Evaluate on a nonnegative proxy so the guard above,
      // not an invalid-root race, aborts reverse-flow runs.
      hA = hAnom*noEvent(max(fuelFlowFraction.FF, 0.0))^(hAExp);
      mdot_fuel = Vdot_fuelNom*rho_fuel*fuelFlowFraction.FF*regionFlowFrac;
      m_FN1 = vol_FN1*rho_fuel;
      m_FN2 = vol_FN2*rho_fuel;
      m_GN = vol_GN*rho_grap;
      powFN1 = m_FN1*cP_fuel*der(fuelNode1.T);
      powFN2 = m_FN2*cP_fuel*der(fuelNode2.T);
      powGN = m_GN*cP_grap*der(grapNode.T);
      flowPowFN1 = mdot_fuel*cP_fuel*(temp_In.T - fuelNode1.T);
      flowPowFN2 = mdot_fuel*cP_fuel*(fuelNode1.T - fuelNode2.T);
      condPowFN1 = ((KF*Ac)/LF1)*(temp_In.T - fuelNode1.T);
      condPowFN2 = ((KF*Ac)/LF2)*(fuelNode1.T - fuelNode2.T);
      convPowFN1 = hA*(kHT_FN1/(kHT_FN1 + kHT_FN2))*(fuelNode1.T - grapNode.T);
      convPowFN2 = hA*(kHT_FN2/(kHT_FN1 + kHT_FN2))*(fuelNode2.T - grapNode.T);
      if OuterRegion == true then
        radPowFN1 = e*SMD_MSR_Modelica.Constants.SigSBK*ArF1*((Tinf + 273.15)^4 - (fuelNode1.T + 273.15)^4);
        radPowFN2 = e*SMD_MSR_Modelica.Constants.SigSBK*ArF2*((Tinf + 273.15)^4 - (fuelNode2.T + 273.15)^4);
      else
        radPowFN1 = 0;
        radPowFN2 = 0;
      end if;
      // kFN1, kFN2, kG: fission power importance fractions for each node.
      fissPowFN1 = kFN1*fissionPower.P;
      fissPowFN2 = kFN2*fissionPower.P;
      fissPowGN = kG*fissionPower.P;
      // Decay heat is a volumetric source (W/m³); multiply by node volume to obtain node power.
      decayPowFN1 = decayHeat.Q*vol_FN1;
      decayPowFN2 = decayHeat.Q*vol_FN2;
      powFN1 = flowPowFN1 + condPowFN1 - condPowFN2 - convPowFN1 + radPowFN1 + fissPowFN1 + decayPowFN1;
      powFN2 = flowPowFN2 + condPowFN2 - convPowFN2 + radPowFN2 + fissPowFN2 + decayPowFN2;
      powGN = convPowFN1 + convPowFN2 + fissPowGN;
      annotation(
        Diagram(coordinateSystem(extent = {{-100, 80}, {60, -80}}), graphics = {Rectangle(origin = {-19, 0}, lineThickness = 1, extent = {{-80, 80}, {80, -80}}), Rectangle(origin = {-40, 33}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 27}, {20, -27}}), Rectangle(origin = {5, 0}, lineColor = {136, 138, 133}, fillColor = {136, 138, 133}, fillPattern = FillPattern.Solid, extent = {{-15, 60}, {15, -60}}), Rectangle(origin = {-40, -33}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 27}, {20, -27}})}),
        Icon(coordinateSystem(extent = {{-100, 80}, {60, -80}}), graphics = {Rectangle(origin = {-40, 34}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 26}, {20, -26}}), Rectangle(origin = {5, 0}, lineColor = {136, 138, 133}, fillColor = {136, 138, 133}, fillPattern = FillPattern.Solid, extent = {{-15, 60}, {15, -60}}), Rectangle(origin = {-20, 0}, lineThickness = 1, extent = {{-79, 80}, {79, -80}}), Rectangle(origin = {-40, -34}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 26}, {20, -26}})}));
    end FuelChannel;

    /* Counter-flow shell-and-tube HX used in MSRR assembly models.
       Primary hA: hApn = hApNom*FF^hAExp (power-law, default exponent 0.33,
       vs the 6th-order polynomial in SMD_MSR_Modelica.HeatTransport.HeatExchanger).
       Secondary hA: hAsn = (0.99*FF + 0.01)*hAsNom; 1% floor avoids zero hA at
       zero flow.
       Supports detailedStateInitWeight to blend reconstructed vs. explicit initial states. */
    model HeatExchanger
      parameter SMD_MSR_Modelica.Units.Volume vol_P;
      parameter SMD_MSR_Modelica.Units.Volume vol_T;
      parameter SMD_MSR_Modelica.Units.Volume vol_S;
      parameter SMD_MSR_Modelica.Units.Density rhoP;
      parameter SMD_MSR_Modelica.Units.Density rhoT;
      parameter SMD_MSR_Modelica.Units.Density rhoS;
      parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP_P;
      parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP_T;
      parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP_S;
      parameter SMD_MSR_Modelica.Units.VolumetricFlowRate VdotPnom;
      parameter SMD_MSR_Modelica.Units.VolumetricFlowRate VdotSnom;
      parameter SMD_MSR_Modelica.Units.Convection hApNom;
      parameter SMD_MSR_Modelica.Units.Convection hAsNom;
      parameter SMD_MSR_Modelica.Units.Conductivity Kp;
      parameter SMD_MSR_Modelica.Units.Conductivity Ks;
      parameter SMD_MSR_Modelica.Units.Area AcShell;
      parameter SMD_MSR_Modelica.Units.Area AcTube;
      parameter SMD_MSR_Modelica.Units.Length L_shell;
      parameter SMD_MSR_Modelica.Units.Length L_tube;
      parameter Boolean EnableRad;
      parameter SMD_MSR_Modelica.Units.Area ArShell;
      parameter SMD_MSR_Modelica.Units.Emissivity e;
      parameter Real hAExp = 0.33
        "Exponent of the primary-side FF power-law convection scaling hApn = hApNom*FF^hAExp (default 0.33)";
      parameter SMD_MSR_Modelica.Units.Temperature Tinf;
      parameter SMD_MSR_Modelica.Units.Temperature TpIn_0;
      parameter SMD_MSR_Modelica.Units.Temperature TpOut_0;
      parameter SMD_MSR_Modelica.Units.Temperature TsIn_0;
      parameter SMD_MSR_Modelica.Units.Temperature TsOut_0;
      parameter Real detailedStateInitWeight(min = 0, max = 1) = 0
        "Blend between legacy reconstructed fixed-start values (0) and explicit state parameters (*_0, value 1).";
      parameter SMD_MSR_Modelica.Units.Temperature T_PN1_0 = 0;
      parameter SMD_MSR_Modelica.Units.Temperature T_PN2_0 = 0;
      parameter SMD_MSR_Modelica.Units.Temperature T_PN3_0 = 0;
      parameter SMD_MSR_Modelica.Units.Temperature T_PN4_0 = 0;
      parameter SMD_MSR_Modelica.Units.Temperature T_SN1_0 = 0;
      parameter SMD_MSR_Modelica.Units.Temperature T_SN2_0 = 0;
      parameter SMD_MSR_Modelica.Units.Temperature T_SN3_0 = 0;
      parameter SMD_MSR_Modelica.Units.Temperature T_SN4_0 = 0;
      parameter SMD_MSR_Modelica.Units.Temperature T_TN1_0 = 0;
      parameter SMD_MSR_Modelica.Units.Temperature T_TN2_0 = 0;
      parameter SMD_MSR_Modelica.Units.InitMode initMode = SMD_MSR_Modelica.Units.InitMode.FixedStart;
      SMD_MSR_Modelica.Units.MassFlowRate mDotP;
      SMD_MSR_Modelica.Units.MassFlowRate mDotS;
      SMD_MSR_Modelica.Units.Mass m_PN;
      SMD_MSR_Modelica.Units.Mass m_TN;
      SMD_MSR_Modelica.Units.Mass m_SN;
      SMD_MSR_Modelica.Units.Volume volPN;
      SMD_MSR_Modelica.Units.Volume volTN;
      SMD_MSR_Modelica.Units.Volume volSN;
      SMD_MSR_Modelica.Units.Area Ar_PN;
      SMD_MSR_Modelica.Units.Length LpN;
      SMD_MSR_Modelica.Units.Length LsN;
      SMD_MSR_Modelica.Units.Convection hApn;
      SMD_MSR_Modelica.Units.Convection hAsn;
      SMD_MSR_Modelica.Units.Temperature T_PN1;
      SMD_MSR_Modelica.Units.Temperature T_PN2;
      SMD_MSR_Modelica.Units.Temperature T_PN3;
      SMD_MSR_Modelica.Units.Temperature T_TN1;
      SMD_MSR_Modelica.Units.Temperature T_TN2;
      SMD_MSR_Modelica.Units.Temperature T_SN1;
      SMD_MSR_Modelica.Units.Temperature T_SN2;
      SMD_MSR_Modelica.Units.Temperature T_SN3;
      SMD_MSR_Modelica.Units.Power powPN1;
      SMD_MSR_Modelica.Units.Power powPN2;
      SMD_MSR_Modelica.Units.Power powPN3;
      SMD_MSR_Modelica.Units.Power powPN4;
      SMD_MSR_Modelica.Units.Power powTN1;
      SMD_MSR_Modelica.Units.Power powTN2;
      SMD_MSR_Modelica.Units.Power powSN1;
      SMD_MSR_Modelica.Units.Power powSN2;
      SMD_MSR_Modelica.Units.Power powSN3;
      SMD_MSR_Modelica.Units.Power powSN4;
      SMD_MSR_Modelica.Units.Power flowPowPN1;
      SMD_MSR_Modelica.Units.Power flowPowPN2;
      SMD_MSR_Modelica.Units.Power flowPowPN3;
      SMD_MSR_Modelica.Units.Power flowPowPN4;
      SMD_MSR_Modelica.Units.Power flowPowSN1;
      SMD_MSR_Modelica.Units.Power flowPowSN2;
      SMD_MSR_Modelica.Units.Power flowPowSN3;
      SMD_MSR_Modelica.Units.Power flowPowSN4;
      SMD_MSR_Modelica.Units.Power condPowPN1;
      SMD_MSR_Modelica.Units.Power condPowPN2;
      SMD_MSR_Modelica.Units.Power condPowPN3;
      SMD_MSR_Modelica.Units.Power condPowPN4;
      SMD_MSR_Modelica.Units.Power condPowSN1;
      SMD_MSR_Modelica.Units.Power condPowSN2;
      SMD_MSR_Modelica.Units.Power condPowSN3;
      SMD_MSR_Modelica.Units.Power condPowSN4;
      SMD_MSR_Modelica.Units.Power convPowPN1;
      SMD_MSR_Modelica.Units.Power convPowPN2;
      SMD_MSR_Modelica.Units.Power convPowPN3;
      SMD_MSR_Modelica.Units.Power convPowPN4;
      SMD_MSR_Modelica.Units.Power convPowSN1;
      SMD_MSR_Modelica.Units.Power convPowSN2;
      SMD_MSR_Modelica.Units.Power convPowSN3;
      SMD_MSR_Modelica.Units.Power convPowSN4;
      SMD_MSR_Modelica.Units.Power radPowPN1;
      SMD_MSR_Modelica.Units.Power radPowPN2;
      SMD_MSR_Modelica.Units.Power radPowPN3;
      SMD_MSR_Modelica.Units.Power radPowPN4;
      SMD_MSR_Modelica.Units.Power decayPowPN1;
      SMD_MSR_Modelica.Units.Power decayPowPN2;
      SMD_MSR_Modelica.Units.Power decayPowPN3;
      SMD_MSR_Modelica.Units.Power decayPowPN4;
      input SMD_MSR_Modelica.PortsConnectors.TempIn T_in_pFluid annotation(
        Placement(visible = true, transformation(origin = {-100, 32}, extent = {{-16, -16}, {16, 16}}, rotation = 0), iconTransformation(origin = {-100, 30}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
      input SMD_MSR_Modelica.PortsConnectors.TempIn T_in_sFluid annotation(
        Placement(visible = true, transformation(origin = {163, -31}, extent = {{-17, -17}, {17, 17}}, rotation = 0), iconTransformation(origin = {160, -30}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
      output SMD_MSR_Modelica.PortsConnectors.TempOut T_out_sFluid annotation(
        Placement(visible = true, transformation(origin = {-100, -38}, extent = {{-14, -14}, {14, 14}}, rotation = 0), iconTransformation(origin = {-100, -30}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
      output SMD_MSR_Modelica.PortsConnectors.TempOut T_out_pFluid annotation(
        Placement(visible = true, transformation(origin = {158, 28}, extent = {{-18, -18}, {18, 18}}, rotation = 0), iconTransformation(origin = {160, 30}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
      input SMD_MSR_Modelica.PortsConnectors.FlowFractionIn primaryFF annotation(
        Placement(visible = true, transformation(origin = {-69, 49}, extent = {{-9, -9}, {9, 9}}, rotation = 0), iconTransformation(origin = {-70, 50}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
      input SMD_MSR_Modelica.PortsConnectors.FlowFractionIn secondaryFF annotation(
        Placement(visible = true, transformation(origin = {123, -47}, extent = {{-11, -11}, {11, 11}}, rotation = 0), iconTransformation(origin = {130, -50}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
      input SMD_MSR_Modelica.PortsConnectors.VolumetricPowerIn P_decay annotation(
        Placement(visible = true, transformation(origin = {30, 50}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {30, 50}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
    initial equation
      // Parameter validity: L_shell/L_tube (via LpN/LsN) and numNodes divide the
      // conduction terms; vol_*/rho_*/cP_* are fluid/wall mass and thermal capacity;
      // VdotPnom/VdotSnom are nominal flows; hApNom/hAsNom are convection coefficients.
      assert(vol_P > 0, "HeatExchanger: vol_P must be > 0 (primary fluid inventory)");
      assert(vol_T > 0, "HeatExchanger: vol_T must be > 0 (tube-wall inventory)");
      assert(vol_S > 0, "HeatExchanger: vol_S must be > 0 (secondary fluid inventory)");
      assert(rhoP > 0, "HeatExchanger: rhoP must be > 0 (primary density)");
      assert(rhoT > 0, "HeatExchanger: rhoT must be > 0 (tube-wall density)");
      assert(rhoS > 0, "HeatExchanger: rhoS must be > 0 (secondary density)");
      assert(cP_P > 0, "HeatExchanger: cP_P must be > 0 (primary heat capacity)");
      assert(cP_T > 0, "HeatExchanger: cP_T must be > 0 (tube-wall heat capacity)");
      assert(cP_S > 0, "HeatExchanger: cP_S must be > 0 (secondary heat capacity)");
      assert(L_shell > 0, "HeatExchanger: L_shell must be > 0 (divides condPow primary)");
      assert(L_tube > 0, "HeatExchanger: L_tube must be > 0 (divides condPow secondary)");
      assert(VdotPnom > 0, "HeatExchanger: VdotPnom must be > 0 (primary nominal flow)");
      assert(VdotSnom > 0, "HeatExchanger: VdotSnom must be > 0 (secondary nominal flow)");
      assert(hApNom > 0, "HeatExchanger: hApNom must be > 0 (primary convection coefficient)");
      assert(hAsNom > 0, "HeatExchanger: hAsNom must be > 0 (secondary convection coefficient)");
      assert(hAExp >= 0 and hAExp <= 1,
        "HeatExchanger: hAExp must be in [0,1] (primary FF power-law convection exponent)");
      assert(not EnableRad or (e >= 0 and e <= 1),
        "HeatExchanger: e must be in [0,1] when EnableRad is true");
      if initMode == SMD_MSR_Modelica.Units.InitMode.FixedStart then
        T_PN1 = detailedStateInitWeight*T_PN1_0 + (1 - detailedStateInitWeight)*(TpIn_0 - (TpIn_0 - TpOut_0)/4);
        T_PN2 = detailedStateInitWeight*T_PN2_0 + (1 - detailedStateInitWeight)*(TpIn_0 - 2*(TpIn_0 - TpOut_0)/4);
        T_PN3 = detailedStateInitWeight*T_PN3_0 + (1 - detailedStateInitWeight)*(TpIn_0 - 3*(TpIn_0 - TpOut_0)/4);
        T_out_pFluid.T = detailedStateInitWeight*T_PN4_0 + (1 - detailedStateInitWeight)*TpOut_0;
        T_SN1 = detailedStateInitWeight*T_SN1_0 + (1 - detailedStateInitWeight)*(TsIn_0 + (TsOut_0 - TsIn_0)/4);
        T_SN2 = detailedStateInitWeight*T_SN2_0 + (1 - detailedStateInitWeight)*(TsIn_0 + 2*(TsOut_0 - TsIn_0)/4);
        T_SN3 = detailedStateInitWeight*T_SN3_0 + (1 - detailedStateInitWeight)*(TsIn_0 + 3*(TsOut_0 - TsIn_0)/4);
        T_out_sFluid.T = detailedStateInitWeight*T_SN4_0 + (1 - detailedStateInitWeight)*TsOut_0;
        T_TN1 = detailedStateInitWeight*T_TN1_0 + (1 - detailedStateInitWeight)*((T_PN1*hApn + T_SN3*hAsn)/(hApn + hAsn));
        T_TN2 = detailedStateInitWeight*T_TN2_0 + (1 - detailedStateInitWeight)*((T_PN3*hApn + T_SN1*hAsn)/(hApn + hAsn));
      else
        der(T_PN1) = 0;
        der(T_PN2) = 0;
        der(T_PN3) = 0;
        der(T_out_pFluid.T) = 0;
        der(T_TN1) = 0;
        der(T_TN2) = 0;
        der(T_SN1) = 0;
        der(T_SN2) = 0;
        der(T_SN3) = 0;
        der(T_out_sFluid.T) = 0;
      end if;
    equation
      // Flow-domain guards (runtime, not init-only): reverse flow is unsupported
      // on both sides. The secondary law is linear in FF.
      assert(primaryFF.FF >= 0,
        "HeatExchanger: primaryFF.FF must be >= 0; reverse flow unsupported");
      assert(secondaryFF.FF >= 0,
        "HeatExchanger: secondaryFF.FF must be >= 0; reverse flow unsupported");
      mDotP = VdotPnom*rhoP*primaryFF.FF;
      mDotS = VdotSnom*rhoS*secondaryFF.FF;
      // Per-node film coefficients (physics review 2026-09-27): hApNom and
      // hAsNom are SIDE TOTALS (their series combination 1/(1/hApNom +
      // 1/hAsNom) = 1.47e4 W/K; the deck/article quote UA_hx = 1.52e4 W/K),
      // each spread over the four nodes of its side - the
      // node equations previously applied the full side total at every node,
      // 4x the documented UA (secondary at 537/545 degC instead of the
      // 500/508 degC design). SMD_MSR_Modelica.HeatTransport.HeatExchanger
      // already divides by 4.
      // Primary hA: FF^hAExp (default 0.33) power-law scaled by the nominal
      // primary coefficient. Evaluate on a nonnegative proxy so the guard above,
      // not an invalid-root race, aborts reverse-flow runs.
      hApn = (hApNom/4)*noEvent(max(primaryFF.FF, 0.0))^(hAExp);
      // Secondary hA: 99% scales linearly with FF; 1% floor prevents singularity
      // at zero flow.
      hAsn = (0.99*secondaryFF.FF + 0.01)*(hAsNom/4);
      volPN = vol_P/4;
      volTN = vol_T/2;
      volSN = vol_S/4;
      Ar_PN = ArShell/4;
      LpN = L_shell/4;
      LsN = L_tube/4;
      m_PN = volPN*rhoP;
      m_TN = volTN*rhoT;
      m_SN = volSN*rhoS;
      powPN1 = m_PN*cP_P*der(T_PN1);
      powPN2 = m_PN*cP_P*der(T_PN2);
      powPN3 = m_PN*cP_P*der(T_PN3);
      powPN4 = m_PN*cP_P*der(T_out_pFluid.T);
      powTN1 = m_TN*cP_T*der(T_TN1);
      powTN2 = m_TN*cP_T*der(T_TN2);
      powSN1 = m_SN*cP_S*der(T_SN1);
      powSN2 = m_SN*cP_S*der(T_SN2);
      powSN3 = m_SN*cP_S*der(T_SN3);
      powSN4 = m_SN*cP_S*der(T_out_sFluid.T);
      flowPowPN1 = mDotP*cP_P*(T_in_pFluid.T - T_PN1);
      flowPowPN2 = mDotP*cP_P*(T_PN1 - T_PN2);
      flowPowPN3 = mDotP*cP_P*(T_PN2 - T_PN3);
      flowPowPN4 = mDotP*cP_P*(T_PN3 - T_out_pFluid.T);
      flowPowSN1 = mDotS*cP_S*(T_in_sFluid.T - T_SN1);
      flowPowSN2 = mDotS*cP_S*(T_SN1 - T_SN2);
      flowPowSN3 = mDotS*cP_S*(T_SN2 - T_SN3);
      flowPowSN4 = mDotS*cP_S*(T_SN3 - T_out_sFluid.T);
      // Inlet faces are adiabatic for axial conduction: the TempIn signal
      // connectors carry no heat flow, so conduction from the upstream
      // component's outlet temperature into node 1 would be credited here
      // without being debited upstream (energy created or destroyed whenever
      // Kp/Ks > 0; physics review 2026-09-27). Interior faces stay paired.
      condPowPN1 = 0;
      condPowPN2 = ((Kp*AcShell)/LpN)*(T_PN1 - T_PN2);
      condPowPN3 = ((Kp*AcShell)/LpN)*(T_PN2 - T_PN3);
      condPowPN4 = ((Kp*AcShell)/LpN)*(T_PN3 - T_out_pFluid.T);
      condPowSN1 = 0;
      condPowSN2 = ((Ks*AcTube)/LsN)*(T_SN1 - T_SN2);
      condPowSN3 = ((Ks*AcTube)/LsN)*(T_SN2 - T_SN3);
      condPowSN4 = ((Ks*AcTube)/LsN)*(T_SN3 - T_out_sFluid.T);
      convPowPN1 = hApn*(T_PN1 - T_TN1);
      convPowPN2 = hApn*(T_PN2 - T_TN1);
      convPowPN3 = hApn*(T_PN3 - T_TN2);
      convPowPN4 = hApn*(T_out_pFluid.T - T_TN2);
      convPowSN1 = hAsn*(T_TN2 - T_SN1);
      convPowSN2 = hAsn*(T_TN2 - T_SN2);
      convPowSN3 = hAsn*(T_TN1 - T_SN3);
      convPowSN4 = hAsn*(T_TN1 - T_out_sFluid.T);
      if EnableRad == true then
        radPowPN1 = e*SMD_MSR_Modelica.Constants.SigSBK*Ar_PN*((Tinf + 273.15)^4 - (T_PN1 + 273.15)^4);
        radPowPN2 = e*SMD_MSR_Modelica.Constants.SigSBK*Ar_PN*((Tinf + 273.15)^4 - (T_PN2 + 273.15)^4);
        radPowPN3 = e*SMD_MSR_Modelica.Constants.SigSBK*Ar_PN*((Tinf + 273.15)^4 - (T_PN3 + 273.15)^4);
        radPowPN4 = e*SMD_MSR_Modelica.Constants.SigSBK*Ar_PN*((Tinf + 273.15)^4 - (T_out_pFluid.T + 273.15)^4);
      else
        radPowPN1 = 0;
        radPowPN2 = 0;
        radPowPN3 = 0;
        radPowPN4 = 0;
      end if;
      decayPowPN1 = P_decay.Q*volPN;
      decayPowPN2 = P_decay.Q*volPN;
      decayPowPN3 = P_decay.Q*volPN;
      decayPowPN4 = P_decay.Q*volPN;
      // Axial conduction is inflow-minus-outflow (matching FuelChannel/Pipe):
      // each node also subtracts the heat it conducts to the next node
      // downstream, so interior conduction faces create no net energy.
      powPN1 = flowPowPN1 + condPowPN1 - condPowPN2 - convPowPN1 + radPowPN1 + decayPowPN1;
      powPN2 = flowPowPN2 + condPowPN2 - condPowPN3 - convPowPN2 + radPowPN2 + decayPowPN2;
      powPN3 = flowPowPN3 + condPowPN3 - condPowPN4 - convPowPN3 + radPowPN3 + decayPowPN3;
      powPN4 = flowPowPN4 + condPowPN4 - convPowPN4 + radPowPN4 + decayPowPN4;
      powTN1 = convPowPN1 + convPowPN2 - convPowSN3 - convPowSN4;
      powTN2 = convPowPN3 + convPowPN4 - convPowSN1 - convPowSN2;
      powSN1 = flowPowSN1 + condPowSN1 - condPowSN2 + convPowSN1;
      powSN2 = flowPowSN2 + condPowSN2 - condPowSN3 + convPowSN2;
      powSN3 = flowPowSN3 + condPowSN3 - condPowSN4 + convPowSN3;
      powSN4 = flowPowSN4 + condPowSN4 + convPowSN4;
      annotation(
        Diagram(graphics = {Rectangle(origin = {30.2864, 0.0340212}, extent = {{-149.242, 59.993}, {149.242, -59.993}}), Rectangle(origin = {-60, 30}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 10}, {20, -10}}), Rectangle(origin = {120, 30}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 10}, {20, -10}}), Rectangle(origin = {0, 30}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 10}, {20, -10}}), Rectangle(origin = {-59.95, -29.61}, lineColor = {239, 41, 41}, fillColor = {239, 41, 41}, fillPattern = FillPattern.Solid, extent = {{-19.63, -9.61}, {19.63, 9.61}}), Rectangle(origin = {-0.12, -29.82}, lineColor = {239, 41, 41}, fillColor = {239, 41, 41}, fillPattern = FillPattern.Solid, extent = {{-19.88, 9.82}, {19.88, -9.82}}), Rectangle(origin = {59.8, -29.85}, lineColor = {239, 41, 41}, fillColor = {239, 41, 41}, fillPattern = FillPattern.Solid, extent = {{-19.8, 9.85}, {19.8, -9.85}}), Rectangle(origin = {119.85, -29.89}, lineColor = {239, 41, 41}, fillColor = {239, 41, 41}, fillPattern = FillPattern.Solid, extent = {{-19.85, 9.89}, {19.85, -9.89}}), Rectangle(origin = {89.92, -1.05}, lineColor = {136, 138, 133}, fillColor = {136, 138, 133}, fillPattern = FillPattern.Solid, extent = {{-49.92, 8.83}, {49.92, -8.83}}), Rectangle(origin = {60, 30}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 10}, {20, -10}}), Rectangle(origin = {-30.08, -1.05}, lineColor = {136, 138, 133}, fillColor = {136, 138, 133}, fillPattern = FillPattern.Solid, extent = {{-49.92, 8.83}, {49.92, -8.83}}), Text(origin = {150, 49}, extent = {{-28, 9}, {28, -9}}, textString = "HX")}, coordinateSystem(extent = {{-120, 60}, {180, -60}})),
        Icon(graphics = {Rectangle(origin = {30.29, 0.03}, lineThickness = 2, extent = {{-149.24, 59.99}, {149.24, -59.99}}), Rectangle(origin = {-30.08, -1.05}, lineColor = {136, 138, 133}, fillColor = {136, 138, 133}, fillPattern = FillPattern.Solid, extent = {{-49.92, 8.83}, {49.92, -8.83}}), Rectangle(origin = {60, 30}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 10}, {20, -10}}), Rectangle(origin = {89.92, -1.05}, lineColor = {136, 138, 133}, fillColor = {136, 138, 133}, fillPattern = FillPattern.Solid, extent = {{-49.92, 8.83}, {49.92, -8.83}}), Rectangle(origin = {-0.12, -29.82}, lineColor = {239, 41, 41}, fillColor = {239, 41, 41}, fillPattern = FillPattern.Solid, extent = {{-19.88, 9.82}, {19.88, -9.82}}), Rectangle(origin = {0, 30}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 10}, {20, -10}}), Rectangle(origin = {-60, 30}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 10}, {20, -10}}), Rectangle(origin = {120, 30}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 10}, {20, -10}}), Rectangle(origin = {59.8, -29.85}, lineColor = {239, 41, 41}, fillColor = {239, 41, 41}, fillPattern = FillPattern.Solid, extent = {{-19.8, 9.85}, {19.8, -9.85}}), Rectangle(origin = {119.85, -29.89}, lineColor = {239, 41, 41}, fillColor = {239, 41, 41}, fillPattern = FillPattern.Solid, extent = {{-19.85, 9.89}, {19.85, -9.89}}), Rectangle(origin = {-59.95, -29.61}, lineColor = {239, 41, 41}, fillColor = {239, 41, 41}, fillPattern = FillPattern.Solid, extent = {{-19.63, -9.61}, {19.63, 9.61}}), Text(origin = {150, 49}, extent = {{-28, 9}, {28, -9}}, textString = "HX")}, coordinateSystem(extent = {{-120, 60}, {180, -60}})));
    end HeatExchanger;

    /* Nine-region reactor core model.
       Nine FuelChannel instances (R1..R9) are grouped into 4 radial flow zones;
       each region has an independent ReactivityFeedback (RF1..RF9) module.
       All feedbacks are summed by sumFB and fed to mPKE. Fuel exits each
       zone via the outermost channel (R1, R4, R7, R9) and is mixed in the
       upper plenum (MixingPot) before leaving the core. The FlowDistributor
       maps the pump FF to per-zone flow fractions with optional zone trips.
       Per-region graphite fission fractions reuse kG = kHT1[i] + kHT2[i]
       (sums to ~6.1% of core power vs the thesis's nominal 7% fuel/graphite
       split, Methodology Sec. Heat Generation), i.e. region-wise graphite
       heating is taken as flux-weighted via the convection split arrays. */
    model MSRR9R
      parameter Integer numGroups = 6;
      parameter SMD_MSR_Modelica.Units.DecayConstant lambda[numGroups];
      parameter SMD_MSR_Modelica.Units.DelayedNeutronFrac beta[numGroups];
      parameter SMD_MSR_Modelica.Units.NeutronGenerationTime LAMBDA;
      parameter SMD_MSR_Modelica.Units.NominalNeutronPopulation n_0;
      parameter SMD_MSR_Modelica.Units.NominalNeutronPopulation nFloor = MSRR_PlantData.Kinetics.nFloor
        "Numerical neutron floor passed to mPKE";
      parameter SMD_MSR_Modelica.Units.NominalNeutronPopulation nFloorDuringForcing = MSRR_PlantData.Kinetics.nFloor
        "Optional forcing-window neutron floor passed to mPKE";
      parameter SMD_MSR_Modelica.Units.InitiationTime nFloorSwitchTime = 1e100
        "Time to switch from nFloor to nFloorDuringForcing in mPKE";
      parameter SMD_MSR_Modelica.Units.ResidenceTime nomTauCore = (sum(volF1) + sum(volF2))/volDotFuel
        "Nominal core fuel transit time passed to mPKE [s]: the 9R mesh's own in-core fuel volume over the nominal flow (41.01 s at the PSAR-basis fuel density, TASK-20261001-01; physics review 2026-09-27 B2 - formerly the 1R 0.4 m3 convention MSRR_PlantData.Kinetics.nomTauCore)";
      parameter SMD_MSR_Modelica.Units.ResidenceTime nomTauLoop = (MSRR_PlantData.totalFuelVol - (sum(volF1) + sum(volF2)))/volDotFuel
        "Nominal loop fuel transit time passed to mPKE [s]: the ex-core remainder of the total fuel inventory, upper plenum included (13.38 s at the PSAR-basis fuel density, TASK-20261001-01; 10.70 s before; formerly the 1R 0.1 m3 convention)";
      parameter SMD_MSR_Modelica.Units.TemperatureReactivityCoef aF;
      parameter SMD_MSR_Modelica.Units.TemperatureReactivityCoef aG;
      parameter SMD_MSR_Modelica.Units.Density rho_fuel;
      parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP_fuel;
      parameter SMD_MSR_Modelica.Units.Conductivity kFuel;
      parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotFuel;
      parameter SMD_MSR_Modelica.Units.Density rho_grap;
      parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP_grap;
      parameter SMD_MSR_Modelica.Units.Volume volF1[9];
      parameter SMD_MSR_Modelica.Units.Volume volF2[9];
      parameter SMD_MSR_Modelica.Units.Volume volG[9];
      parameter SMD_MSR_Modelica.Units.Volume volUP = MSRR_PlantData.Core9R.volUpperPlenum
        "Upper-plenum mixing volume, a fixed geometric volume from the plant
         data (TASK-20261001-01): the 9R in-core fuel inventory is the regions
         plus this plenum, the 400 L vessel. It was formerly volDotFuel x a
         2 s residence time (rev021-B5), which would have shrunk the plenum
         when the fuel density, and with it the volumetric flow, changed";
      parameter SMD_MSR_Modelica.Units.Convection hA[9];
      parameter SMD_MSR_Modelica.Units.VolumeImportance kFN1[9];
      parameter SMD_MSR_Modelica.Units.VolumeImportance kFN2[9];
      parameter SMD_MSR_Modelica.Units.HeatTransferFraction kHT1[9];
      parameter SMD_MSR_Modelica.Units.HeatTransferFraction kHT2[9];
      parameter SMD_MSR_Modelica.Units.Area Ac[4];
      parameter SMD_MSR_Modelica.Units.Length LF1[9];
      parameter SMD_MSR_Modelica.Units.Length LF2[9];
      parameter SMD_MSR_Modelica.Units.Area ArF1[9];
      parameter SMD_MSR_Modelica.Units.Area ArF2[9];
      parameter SMD_MSR_Modelica.Units.Temperature TF1_0[9];
      parameter SMD_MSR_Modelica.Units.Temperature TF2_0[9];
      parameter SMD_MSR_Modelica.Units.Temperature TG_0[9];
      parameter SMD_MSR_Modelica.Units.Temperature Tmix_0;
      parameter Real IF1[9];
      parameter Real IF2[9];
      parameter Real IG[9];
      parameter SMD_MSR_Modelica.Units.FlowFraction flowFracRegions[4];
      parameter SMD_MSR_Modelica.Units.InitiationTime regionTripTime[4];
      parameter SMD_MSR_Modelica.Units.PumpConstant regionCoastDownK;
      parameter SMD_MSR_Modelica.Units.FlowFraction freeConvFF;
      parameter Boolean EnableRad;
      parameter SMD_MSR_Modelica.Units.Emissivity e;
      parameter SMD_MSR_Modelica.Units.Temperature T_inf;
      parameter SMD_MSR_Modelica.Units.Area mixingPotAc = MSRR_PlantData.Core9R.mixingPotAc;
      parameter SMD_MSR_Modelica.Units.Length mixingPotL = MSRR_PlantData.Core9R.mixingPotL;
      parameter SMD_MSR_Modelica.Units.Area mixingPotAr = MSRR_PlantData.Core9R.mixingPotAr;
      parameter Integer numSourceSteps(min = 1) = 1;
      parameter SMD_MSR_Modelica.Units.InitiationTime sourceStepTime[numSourceSteps] = {0};
      parameter SMD_MSR_Modelica.Units.NeutronEmissionRate sourceAmplitude[numSourceSteps] = {0};
      SMD_MSR_Modelica.Nuclear.mPKE mpke(numGroups = numGroups, lambda = lambda, beta = beta, LAMBDA = LAMBDA, n_0 = n_0, nFloor = nFloor, nFloorDuringForcing = nFloorDuringForcing, nFloorSwitchTime = nFloorSwitchTime, nomTauLoop = nomTauLoop, nomTauCore = nomTauCore, nu = MSRR_PlantData.Kinetics.nu, nominalPower = MSRR_PlantData.nominalPower, energyPerFission = MSRR_PlantData.Kinetics.energyPerFission, sourceEffectiveness = MSRR_PlantData.Kinetics.sourceEffectiveness) annotation(
        Placement(transformation(origin = {174.6, 322.2}, extent = {{-63.6, -63.6}, {42.4, 42.4}}, rotation = -90)));
      SMD_MSR_Modelica.Nuclear.PowerBlock powerblock(P(displayUnit = "W") = MSRR_PlantData.nominalPower, TotalFuelVol = MSRR_PlantData.totalFuelVol) annotation(
        Placement(transformation(origin = {-92.4, 330.2}, extent = {{29.6, -59.2}, {-44.4, 14.8}}, rotation = -90)));
      SMD_MSR_Modelica.Nuclear.DecayHeat decayHeat(numGroups = MSRR_PlantData.DecayHeat.numGroups, DHYG = MSRR_PlantData.DecayHeat.DHYG, DHlamG = MSRR_PlantData.DecayHeat.DHlamG) annotation(
        Placement(transformation(origin = {-18.4, 480.4}, extent = {{-29.6, 29.6}, {44.4, -44.4}}, rotation = 90)));
      SMD_MSR_Modelica.Signals.TimeDependent.Stepper sourceStepper(numSteps = numSourceSteps, stepTime = sourceStepTime, amplitude = sourceAmplitude) annotation(
        Placement(transformation(origin = {390, 430}, extent = {{-27, -27}, {27, 27}}, rotation = 90)));
      MSRR.Components.FuelChannel R1(vol_FN1 = volF1[1], vol_FN2 = volF2[1], vol_GN = volG[1], rho_fuel = rho_fuel, rho_grap = rho_grap, cP_fuel = cP_fuel, cP_grap = cP_grap, Vdot_fuelNom = volDotFuel, kFN1 = kFN1[1], kFN2 = kFN2[1], kG = kHT1[1] + kHT2[1], hAnom = hA[1], kHT_FN1 = kHT1[1], kHT_FN2 = kHT2[1], TF1_0 = TF1_0[1], TF2_0 = TF2_0[1], TG_0 = TG_0[1], regionFlowFrac = flowFracRegions[1], KF = kFuel, Ac = Ac[1], LF1 = LF1[1], LF2 = LF2[1], OuterRegion = false, ArF1 = ArF1[1], ArF2 = ArF2[1], e = e, Tinf = T_inf) annotation(
        Placement(transformation(origin = {-450.972, -402.111}, extent = {{-75.1389, -75.1389}, {45.0833, 60.1111}})));
      MSRR.Components.FuelChannel R2(vol_FN1 = volF1[2], vol_FN2 = volF2[2], vol_GN = volG[2], rho_fuel = rho_fuel, rho_grap = rho_grap, cP_fuel = cP_fuel, cP_grap = cP_grap, Vdot_fuelNom = volDotFuel, kFN1 = kFN1[2], kFN2 = kFN2[2], kG = kHT1[2] + kHT2[2], hAnom = hA[2], kHT_FN1 = kHT1[2], kHT_FN2 = kHT2[2], TF1_0 = TF1_0[2], TF2_0 = TF2_0[2], TG_0 = TG_0[2], regionFlowFrac = flowFracRegions[2], KF = kFuel, Ac = Ac[2], LF1 = LF1[2], LF2 = LF2[2], OuterRegion = false, ArF1 = ArF1[2], ArF2 = ArF2[2], e = e, Tinf = T_inf) annotation(
        Placement(transformation(origin = {-40.25, -600.333}, extent = {{-75.4167, -75.4167}, {45.25, 60.3333}})));
      MSRR.Components.FuelChannel R3(vol_FN1 = volF1[3], vol_FN2 = volF2[3], vol_GN = volG[3], rho_fuel = rho_fuel, rho_grap = rho_grap, cP_fuel = cP_fuel, cP_grap = cP_grap, Vdot_fuelNom = volDotFuel, kFN1 = kFN1[3], kFN2 = kFN2[3], kG = kHT1[3] + kHT2[3], hAnom = hA[3], kHT_FN1 = kHT1[3], kHT_FN2 = kHT2[3], TF1_0 = TF1_0[3], TF2_0 = TF2_0[3], TG_0 = TG_0[3], regionFlowFrac = flowFracRegions[2], KF = kFuel, Ac = Ac[2], LF1 = LF1[3], LF2 = LF2[3], OuterRegion = false, ArF1 = ArF1[3], ArF2 = ArF2[3], e = e, Tinf = T_inf) annotation(
        Placement(transformation(origin = {-40.3333, -400.944}, extent = {{-75.5556, -75.5556}, {45.3333, 60.4444}})));
      MSRR.Components.FuelChannel R4(vol_FN1 = volF1[4], vol_FN2 = volF2[4], vol_GN = volG[4], rho_fuel = rho_fuel, rho_grap = rho_grap, cP_fuel = cP_fuel, cP_grap = cP_grap, Vdot_fuelNom = volDotFuel, kFN1 = kFN1[4], kFN2 = kFN2[4], kG = kHT1[4] + kHT2[4], hAnom = hA[4], kHT_FN1 = kHT1[4], kHT_FN2 = kHT2[4], TF1_0 = TF1_0[4], TF2_0 = TF2_0[4], TG_0 = TG_0[4], regionFlowFrac = flowFracRegions[2], KF = kFuel, Ac = Ac[2], LF1 = LF1[4], LF2 = LF2[4], OuterRegion = false, ArF1 = ArF1[4], ArF2 = ArF2[4], e = e, Tinf = T_inf) annotation(
        Placement(transformation(origin = {-42.2778, -200.278}, extent = {{-74.7222, -74.7222}, {44.8333, 59.7778}})));
      MSRR.Components.FuelChannel R5(vol_FN1 = volF1[5], vol_FN2 = volF2[5], vol_GN = volG[5], rho_fuel = rho_fuel, rho_grap = rho_grap, cP_fuel = cP_fuel, cP_grap = cP_grap, Vdot_fuelNom = volDotFuel, kFN1 = kFN1[5], kFN2 = kFN2[5], kG = kHT1[5] + kHT2[5], hAnom = hA[5], kHT_FN1 = kHT1[5], kHT_FN2 = kHT2[5], TF1_0 = TF1_0[5], TF2_0 = TF2_0[5], TG_0 = TG_0[5], regionFlowFrac = flowFracRegions[3], KF = kFuel, Ac = Ac[3], LF1 = LF1[5], LF2 = LF2[5], OuterRegion = false, ArF1 = ArF1[5], ArF2 = ArF2[5], e = e, Tinf = T_inf) annotation(
        Placement(transformation(origin = {330.167, -600.833}, extent = {{-74.1667, -74.1667}, {44.5, 59.3333}})));
      MSRR.Components.FuelChannel R6(vol_FN1 = volF1[6], vol_FN2 = volF2[6], vol_GN = volG[6], rho_fuel = rho_fuel, rho_grap = rho_grap, cP_fuel = cP_fuel, cP_grap = cP_grap, Vdot_fuelNom = volDotFuel, kFN1 = kFN1[6], kFN2 = kFN2[6], kG = kHT1[6] + kHT2[6], hAnom = hA[6], kHT_FN1 = kHT1[6], kHT_FN2 = kHT2[6], TF1_0 = TF1_0[6], TF2_0 = TF2_0[6], TG_0 = TG_0[6], regionFlowFrac = flowFracRegions[3], KF = kFuel, Ac = Ac[3], LF1 = LF1[6], LF2 = LF2[6], OuterRegion = false, ArF1 = ArF1[6], ArF2 = ArF2[6], e = e, Tinf = T_inf) annotation(
        Placement(transformation(origin = {330.139, -400.861}, extent = {{-75.1389, -75.1389}, {45.0833, 60.1111}})));
      MSRR.Components.FuelChannel R7(vol_FN1 = volF1[7], vol_FN2 = volF2[7], vol_GN = volG[7], rho_fuel = rho_fuel, rho_grap = rho_grap, cP_fuel = cP_fuel, cP_grap = cP_grap, Vdot_fuelNom = volDotFuel, kFN1 = kFN1[7], kFN2 = kFN2[7], kG = kHT1[7] + kHT2[7], hAnom = hA[7], kHT_FN1 = kHT1[7], kHT_FN2 = kHT2[7], TF1_0 = TF1_0[7], TF2_0 = TF2_0[7], TG_0 = TG_0[7], regionFlowFrac = flowFracRegions[3], KF = kFuel, Ac = Ac[3], LF1 = LF1[7], LF2 = LF2[7], OuterRegion = false, ArF1 = ArF1[7], ArF2 = ArF2[7], e = e, Tinf = T_inf) annotation(
        Placement(transformation(origin = {330.611, -200.389}, extent = {{-73.6111, -73.6111}, {44.1667, 58.8889}})));
      MSRR.Components.FuelChannel R8(vol_FN1 = volF1[8], vol_FN2 = volF2[8], vol_GN = volG[8], rho_fuel = rho_fuel, rho_grap = rho_grap, cP_fuel = cP_fuel, cP_grap = cP_grap, Vdot_fuelNom = volDotFuel, kFN1 = kFN1[8], kFN2 = kFN2[8], kG = kHT1[8] + kHT2[8], hAnom = hA[8], kHT_FN1 = kHT1[8], kHT_FN2 = kHT2[8], TF1_0 = TF1_0[8], TF2_0 = TF2_0[8], TG_0 = TG_0[8], regionFlowFrac = flowFracRegions[4], KF = kFuel, Ac = Ac[4], LF1 = LF1[8], LF2 = LF2[8], OuterRegion = EnableRad, ArF1 = ArF1[8], ArF2 = ArF2[8], e = e, Tinf = T_inf) annotation(
        Placement(transformation(origin = {732.861, -514.889}, extent = {{-74.8611, -74.8611}, {44.9167, 59.8889}})));
      MSRR.Components.FuelChannel R9(vol_FN1 = volF1[9], vol_FN2 = volF2[9], vol_GN = volG[9], rho_fuel = rho_fuel, rho_grap = rho_grap, cP_fuel = cP_fuel, cP_grap = cP_grap, Vdot_fuelNom = volDotFuel, kFN1 = kFN1[9], kFN2 = kFN2[9], kG = kHT1[9] + kHT2[9], hAnom = hA[9], kHT_FN1 = kHT1[9], kHT_FN2 = kHT2[9], TF1_0 = TF1_0[9], TF2_0 = TF2_0[9], TG_0 = TG_0[9], regionFlowFrac = flowFracRegions[4], KF = kFuel, Ac = Ac[4], LF1 = LF1[9], LF2 = LF2[9], OuterRegion = EnableRad, ArF1 = ArF1[9], ArF2 = ArF2[9], e = e, Tinf = T_inf) annotation(
        Placement(transformation(origin = {710.833, -320.889}, extent = {{-73.6111, -73.6111}, {44.1667, 58.8889}})));
      SMD_MSR_Modelica.Nuclear.ReactivityFeedback RF1(a_F = aF, a_G = aG, IF1 = IF1[1], IF2 = IF2[1], IG = IG[1], FuelTempSetPointNode1 = TF1_0[1], FuelTempSetPointNode2 = TF2_0[1], GrapTempSetPoint = TG_0[1]) annotation(
        Placement(transformation(origin = {-280.4, -401.6}, extent = {{-69.6, -46.4}, {46.4, 69.6}}, rotation = 90)));
      SMD_MSR_Modelica.Nuclear.ReactivityFeedback RF2(a_F = aF, a_G = aG, IF1 = IF1[2], IF2 = IF2[2], IG = IG[2], FuelTempSetPointNode1 = TF1_0[2], FuelTempSetPointNode2 = TF2_0[2], GrapTempSetPoint = TG_0[2]) annotation(
        Placement(transformation(origin = {137.6, -596.4}, extent = {{-69.6, -46.4}, {46.4, 69.6}}, rotation = 90)));
      SMD_MSR_Modelica.Nuclear.ReactivityFeedback RF3(a_F = aF, a_G = aG, IF1 = IF1[3], IF2 = IF2[3], IG = IG[3], FuelTempSetPointNode1 = TF1_0[3], FuelTempSetPointNode2 = TF2_0[3], GrapTempSetPoint = TG_0[3]) annotation(
        Placement(transformation(origin = {137.6, -400}, extent = {{-69.6, -46.4}, {46.4, 69.6}}, rotation = 90)));
      SMD_MSR_Modelica.Nuclear.ReactivityFeedback RF4(a_F = aF, a_G = aG, IF1 = IF1[4], IF2 = IF2[4], IG = IG[4], FuelTempSetPointNode1 = TF1_0[4], FuelTempSetPointNode2 = TF2_0[4], GrapTempSetPoint = TG_0[4]) annotation(
        Placement(transformation(origin = {135.6, -200}, extent = {{-69.6, -46.4}, {46.4, 69.6}}, rotation = 90)));
      SMD_MSR_Modelica.Nuclear.ReactivityFeedback RF5(a_F = aF, a_G = aG, IF1 = IF1[5], IF2 = IF2[5], IG = IG[5], FuelTempSetPointNode1 = TF1_0[5], FuelTempSetPointNode2 = TF2_0[5], GrapTempSetPoint = TG_0[5]) annotation(
        Placement(transformation(origin = {510.6, -606}, extent = {{-69.6, -46.4}, {46.4, 69.6}}, rotation = 90)));
      SMD_MSR_Modelica.Nuclear.ReactivityFeedback RF6(a_F = aF, a_G = aG, IF1 = IF1[6], IF2 = IF2[6], IG = IG[6], FuelTempSetPointNode1 = TF1_0[6], FuelTempSetPointNode2 = TF2_0[6], GrapTempSetPoint = TG_0[6]) annotation(
        Placement(transformation(origin = {510.6, -400}, extent = {{-69.6, -46.4}, {46.4, 69.6}}, rotation = 90)));
      SMD_MSR_Modelica.Nuclear.ReactivityFeedback RF7(a_F = aF, a_G = aG, IF1 = IF1[7], IF2 = IF2[7], IG = IG[7], FuelTempSetPointNode1 = TF1_0[7], FuelTempSetPointNode2 = TF2_0[7], GrapTempSetPoint = TG_0[7]) annotation(
        Placement(transformation(origin = {510.6, -200}, extent = {{-69.6, -46.4}, {46.4, 69.6}}, rotation = 90)));
      SMD_MSR_Modelica.Nuclear.ReactivityFeedback RF8(a_F = aF, a_G = aG, IF1 = IF1[8], IF2 = IF2[8], IG = IG[8], FuelTempSetPointNode1 = TF1_0[8], FuelTempSetPointNode2 = TF2_0[8], GrapTempSetPoint = TG_0[8]) annotation(
        Placement(transformation(origin = {900.6, -510}, extent = {{-69.6, -46.4}, {46.4, 69.6}}, rotation = 90)));
      SMD_MSR_Modelica.Nuclear.ReactivityFeedback RF9(a_F = aF, a_G = aG, IF1 = IF1[9], IF2 = IF2[9], IG = IG[9], FuelTempSetPointNode1 = TF1_0[9], FuelTempSetPointNode2 = TF2_0[9], GrapTempSetPoint = TG_0[9]) annotation(
        Placement(transformation(origin = {904.6, -320}, extent = {{-69.6, -46.4}, {46.4, 69.6}}, rotation = 90)));
      SMD_MSR_Modelica.Nuclear.SumReactivity sumFB(numInput = 9) annotation(
        Placement(transformation(origin = {516.544, -5.0561}, extent = {{-37.5439, 56.3158}, {56.3158, -37.5439}})));
      SMD_MSR_Modelica.HeatTransport.FlowDistributor flowDistributor(numOutput = 4, freeConvectionFF = freeConvFF, coastDownK = regionCoastDownK, regionTripTime = regionTripTime) annotation(
        Placement(transformation(origin = {395.602, -954.4}, extent = {{30.3983, 91.195}, {-91.195, -30.3983}})));
      SMD_MSR_Modelica.HeatTransport.MixingPot upperPlenum(numStreams = 4, vol = volUP, VdotNom = volDotFuel, flowFractionsNom = flowFracRegions, rho = rho_fuel, Cp = cP_fuel, K = kFuel, Ac = mixingPotAc, L = mixingPotL, Ar = mixingPotAr, e = e, T_0 = Tmix_0, Tinf = T_inf, EnableRad = EnableRad) annotation(
        Placement(transformation(origin = {450.182, 131.988}, extent = {{-51.443, 38.5821}, {25.7215, -38.5821}}, rotation = 90)));
      input SMD_MSR_Modelica.PortsConnectors.FlowFractionIn flowFracIn annotation(
        Placement(transformation(origin = {329, -1071}, extent = {{-37, -37}, {37, 37}}), iconTransformation(origin = {-40, 40}, extent = {{-18, -18}, {18, 18}})));
      input SMD_MSR_Modelica.PortsConnectors.TempIn tempIn annotation(
        Placement(transformation(origin = {217, -827}, extent = {{-21, -21}, {21, 21}}), iconTransformation(origin = {-40, -38}, extent = {{-18, -18}, {18, 18}})));
      output SMD_MSR_Modelica.PortsConnectors.TempOut tempOut annotation(
        Placement(transformation(origin = {1117, 235}, extent = {{-28, -28}, {28, 28}}), iconTransformation(origin = {40, 40}, extent = {{-18, -18}, {18, 18}})));
      output SMD_MSR_Modelica.PortsConnectors.VolumetricPowerOut volumetricPowerOut annotation(
        Placement(transformation(origin = {-144, 522}, extent = {{13, 13}, {-13, -13}}), iconTransformation(origin = {41, -39}, extent = {{-17, -17}, {17, 17}})));
      input SMD_MSR_Modelica.PortsConnectors.RealIn realIn annotation(
        Placement(transformation(origin = {290, 450}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {0, 40}, extent = {{-18, -18}, {18, 18}})));
      output SMD_MSR_Modelica.Units.NeutronEmissionRate sourceRate;
    equation
      sourceRate = sourceStepper.step.R;
      mpke.S.nDot = sourceRate;
      connect(R1.fuelNode1, RF1.fuelNode1) annotation(
        Line(points = {{-420.916, -447.194}, {-371.416, -447.194}, {-371.416, -448.194}, {-321.916, -448.194}}, color = {204, 0, 0}, thickness = 1));
      connect(R1.fuelNode2, RF1.fuelNode2) annotation(
        Line(points = {{-420.916, -370.553}, {-371.416, -370.553}, {-371.416, -377.553}, {-321.916, -377.553}}, color = {204, 0, 0}, thickness = 1));
      connect(R1.grapNode, RF1.grapNode) annotation(
        Line(points = {{-420.916, -409.625}, {-392.916, -409.625}, {-392.916, -412.625}, {-321.916, -412.625}}, color = {204, 0, 0}, thickness = 1));
      connect(R2.fuelNode1, RF2.fuelNode1) annotation(
        Line(points = {{-10, -646}, {36, -646}, {36, -643}, {91, -643}}, color = {204, 0, 0}, thickness = 1));
      connect(R2.grapNode, RF2.grapNode) annotation(
        Line(points = {{-10, -608}, {91, -608}}, color = {204, 0, 0}, thickness = 1));
      connect(R2.fuelNode2, RF2.fuelNode2) annotation(
        Line(points = {{-10, -568}, {40, -568}, {40, -573}, {91, -573}}, color = {204, 0, 0}, thickness = 1));
      connect(R3.fuelNode1, RF3.fuelNode1) annotation(
        Line(points = {{-10, -446}, {91, -446}}, color = {204, 0, 0}, thickness = 1));
      connect(R3.grapNode, RF3.grapNode) annotation(
        Line(points = {{-10, -408}, {40.5, -408}, {40.5, -412}, {91, -412}}, color = {204, 0, 0}, thickness = 1));
      connect(R3.fuelNode2, RF3.fuelNode2) annotation(
        Line(points = {{-10, -370}, {44, -370}, {44, -377}, {91, -377}}, color = {204, 0, 0}, thickness = 1));
      connect(R4.fuelNode1, RF4.fuelNode1) annotation(
        Line(points = {{-27, -253}, {-27, -258}, {78, -258}}, color = {204, 0, 0}, thickness = 1));
      connect(R4.grapNode, RF4.grapNode) annotation(
        Line(points = {{-27, -215}, {88, -215}, {88, -223}, {78, -223}}, color = {204, 0, 0}, thickness = 1));
      connect(R4.fuelNode2, RF4.fuelNode2) annotation(
        Line(points = {{-27, -176}, {-27, -188}, {78, -188}}, color = {204, 0, 0}, thickness = 1));
      connect(R5.fuelNode1, RF5.fuelNode1) annotation(
        Line(points = {{359.834, -645.333}, {399.334, -645.333}, {399.334, -651.333}, {466.834, -651.333}}, color = {204, 0, 0}, thickness = 1));
      connect(R5.grapNode, RF5.grapNode) annotation(
        Line(points = {{359.834, -608.25}, {466.834, -608.25}, {466.834, -618.25}}, color = {204, 0, 0}, thickness = 1));
      connect(R5.fuelNode2, RF5.fuelNode2) annotation(
        Line(points = {{359.834, -569.683}, {466.834, -569.683}, {466.834, -582.683}}, color = {204, 0, 0}, thickness = 1));
      connect(R6.fuelNode1, RF6.fuelNode1) annotation(
        Line(points = {{360.195, -445.944}, {471.195, -445.944}}, color = {204, 0, 0}, thickness = 1));
      connect(R6.grapNode, RF6.grapNode) annotation(
        Line(points = {{360.195, -408.375}, {471.195, -408.375}, {471.195, -412.375}}, color = {204, 0, 0}, thickness = 1));
      connect(R6.fuelNode2, RF6.fuelNode2) annotation(
        Line(points = {{360.195, -369.303}, {471.195, -369.303}, {471.195, -376.303}}, color = {204, 0, 0}, thickness = 1));
      connect(R7.fuelNode1, RF7.fuelNode1) annotation(
        Line(points = {{345, -252}, {345, -246.556}, {472.055, -246.556}}, color = {204, 0, 0}, thickness = 1));
      connect(R7.grapNode, RF7.grapNode) annotation(
        Line(points = {{345, -215}, {345, -211.75}, {472.055, -211.75}}, color = {204, 0, 0}, thickness = 1));
      connect(R7.fuelNode2, RF7.fuelNode2) annotation(
        Line(points = {{345, -177}, {345, -175.472}, {472.055, -175.472}}, color = {204, 0, 0}, thickness = 1));
      connect(R8.fuelNode1, RF8.fuelNode1) annotation(
        Line(points = {{748, -567}, {803, -567}, {803, -568}, {843, -568}}, color = {204, 0, 0}, thickness = 1));
      connect(R8.grapNode, RF8.grapNode) annotation(
        Line(points = {{748, -530}, {802, -530}, {802, -533}, {843, -533}}, color = {204, 0, 0}, thickness = 1));
      connect(R8.fuelNode2, RF8.fuelNode2) annotation(
        Line(points = {{748, -491}, {805, -491}, {805, -498}, {843, -498}}, color = {204, 0, 0}, thickness = 1));
      connect(R9.fuelNode1, RF9.fuelNode1) annotation(
        Line(points = {{740.277, -365.056}, {798.277, -365.056}, {798.277, -378}, {847, -378}}, color = {204, 0, 0}, thickness = 1));
      connect(R9.grapNode, RF9.grapNode) annotation(
        Line(points = {{740.277, -328.25}, {740.277, -343}, {847, -343}}, color = {204, 0, 0}, thickness = 1));
      connect(R9.fuelNode2, RF9.fuelNode2) annotation(
        Line(points = {{740.277, -289.972}, {740.277, -308}, {847, -308}}, color = {204, 0, 0}, thickness = 1));
      connect(R2.fuelNode2, R3.temp_In) annotation(
        Line(points = {{-10, -568}, {-12, -568}, {-12, -508}, {-94, -508}, {-94, -456}}, color = {204, 0, 0}, thickness = 1));
      connect(R3.fuelNode2, R4.temp_In) annotation(
        Line(points = {{-10, -370}, {-10, -306}, {-111, -306}, {-111, -262}}, color = {204, 0, 0}, thickness = 1));
      connect(R6.fuelNode2, R7.temp_In) annotation(
        Line(points = {{360.195, -369.303}, {360.195, -305.303}, {263, -305.303}, {263, -261}}, color = {204, 0, 0}, thickness = 1));
      connect(R5.fuelNode2, R6.temp_In) annotation(
        Line(points = {{359.834, -569.683}, {357.834, -569.683}, {357.834, -509.683}, {275.834, -509.683}, {275.834, -453.683}}, color = {204, 0, 0}, thickness = 1));
      connect(R8.fuelNode2, R9.temp_In) annotation(
        Line(points = {{748, -491}, {748, -431.447}, {658.805, -431.447}, {658.805, -373.447}}, color = {204, 0, 0}, thickness = 1));
      connect(RF1.feedback, sumFB.reactivityIn[1]) annotation(
        Line(points = {{-257, -413}, {-174, -413}, {-174, -14}, {533, -14}}, color = {78, 154, 6}, thickness = 1));
      connect(RF4.feedback, sumFB.reactivityIn[4]) annotation(
        Line(points = {{147, -223}, {162, -223}, {162, -14}, {533, -14}}, color = {78, 154, 6}, thickness = 1));
      connect(RF3.feedback, sumFB.reactivityIn[3]) annotation(
        Line(points = {{160, -412}, {164, -412}, {164, -14}, {533, -14}}, color = {78, 154, 6}, thickness = 1));
      connect(RF2.feedback, sumFB.reactivityIn[2]) annotation(
        Line(points = {{160, -608}, {160, -14}, {533, -14}}, color = {78, 154, 6}, thickness = 1));
      connect(RF7.feedback, sumFB.reactivityIn[7]) annotation(
        Line(points = {{534, -212}, {534, -111}, {533, -111}, {533, -14}}, color = {78, 154, 6}, thickness = 1));
      connect(RF6.feedback, sumFB.reactivityIn[6]) annotation(
        Line(points = {{534, -412}, {534, -211}, {533, -211}, {533, -14}}, color = {78, 154, 6}, thickness = 1));
      connect(RF5.feedback, sumFB.reactivityIn[5]) annotation(
        Line(points = {{534, -618}, {534, -314}, {533, -314}, {533, -14}}, color = {78, 154, 6}, thickness = 1));
      connect(RF9.feedback, sumFB.reactivityIn[9]) annotation(
        Line(points = {{928, -338}, {916, -9}, {533, -14}}, color = {78, 154, 6}, thickness = 1));
      connect(RF8.feedback, sumFB.reactivityIn[8]) annotation(
        Line(points = {{924, -550}, {912, -31}, {533, -14}}, color = {78, 154, 6}, thickness = 1));
      connect(sumFB.reactivityOut, mpke.feedback) annotation(
        Line(points = {{535, 42}, {535, 333}, {185, 333}}, color = {78, 154, 6}, thickness = 1));
      connect(mpke.n_population, decayHeat.nPop) annotation(
        Line(points = {{122, 343}, {98.4, 343}, {98.4, 494}, {19, 494}}, color = {20, 36, 248}, thickness = 1));
      connect(decayHeat.decayHeat_Out, powerblock.decayNP) annotation(
        Line(points = {{-26, 495}, {-26, 367}, {-115, 367}}, color = {0, 225, 255}, thickness = 1));
      connect(mpke.n_population, powerblock.nPop) annotation(
        Line(points = {{122, 343}, {8.5, 343}, {8.5, 323}, {-115, 323}}, color = {20, 36, 248}, thickness = 1));
      connect(realIn, mpke.ReactivityIn) annotation(
        Line(points = {{290, 450}, {290, 442}, {185, 442}, {185, 375}}, thickness = 1));
      connect(tempIn, R2.temp_In) annotation(
        Line(points = {{217, -827}, {-94, -827}, {-94, -654}}, color = {204, 0, 0}, thickness = 1));
      connect(tempIn, R1.temp_In) annotation(
        Line(points = {{217, -827}, {-505, -827}, {-505, -456}}, color = {204, 0, 0}, thickness = 1));
      connect(tempIn, R5.temp_In) annotation(
        Line(points = {{217, -827}, {277, -827}, {277, -654}}, color = {204, 0, 0}, thickness = 1));
      connect(powerblock.fissionPower, R1.fissionPower) annotation(
        Line(points = {{-159, 323}, {-159, 181}, {-505, 181}, {-505, -360}}, color = {129, 61, 156}, thickness = 1));
      connect(powerblock.fissionPower, R4.fissionPower) annotation(
        Line(points = {{-159, 323}, {-142, 323}, {-142, -166}, {-111, -166}}, color = {129, 61, 156}, thickness = 1));
      connect(powerblock.fissionPower, R3.fissionPower) annotation(
        Line(points = {{-159, 323}, {-142, 323}, {-142, -358}, {-94, -358}}, color = {129, 61, 156}, thickness = 1));
      connect(powerblock.fissionPower, R2.fissionPower) annotation(
        Line(points = {{-159, 323}, {-159, -558}, {-94, -558}}, color = {129, 61, 156}, thickness = 1));
      connect(powerblock.fissionPower, R7.fissionPower) annotation(
        Line(points = {{-159, 323}, {-140, 323}, {-140, -96}, {208, -96}, {208, -167}, {263, -167}}, color = {129, 61, 156}, thickness = 1));
      connect(powerblock.fissionPower, R6.fissionPower) annotation(
        Line(points = {{-159, 323}, {-138, 323}, {-138, -98}, {206, -98}, {206, -359}, {276, -359}}, color = {129, 61, 156}, thickness = 1));
      connect(powerblock.fissionPower, R5.fissionPower) annotation(
        Line(points = {{-159, 323}, {-140, 323}, {-140, -100}, {204, -100}, {204, -559}, {277, -559}}, color = {129, 61, 156}, thickness = 1));
      connect(powerblock.fissionPower, R9.fissionPower) annotation(
        Line(points = {{-159, 323}, {-140, 323}, {-140, -96}, {610, -96}, {610, -280}, {658, -280}}, color = {129, 61, 156}, thickness = 1));
      connect(powerblock.fissionPower, R8.fissionPower) annotation(
        Line(points = {{-159, 323}, {-140, 323}, {-140, -98}, {610, -98}, {610, -477}, {609.5, -477}, {609.5, -480}, {664, -480}}, color = {129, 61, 156}, thickness = 1));
      connect(powerblock.decayPowerM, R1.decayHeat) annotation(
        Line(points = {{-159, 367}, {-546, 367}, {-546, -393}, {-505, -393}}, color = {220, 138, 221}, thickness = 1));
      connect(powerblock.decayPowerM, R4.decayHeat) annotation(
        Line(points = {{-159, 367}, {-198, 367}, {-198, -199}, {-111, -199}}, color = {220, 138, 221}, thickness = 1));
      connect(powerblock.decayPowerM, R3.decayHeat) annotation(
        Line(points = {{-159, 367}, {-198, 367}, {-198, -392}, {-94, -392}}, color = {220, 138, 221}, thickness = 1));
      connect(powerblock.decayPowerM, R2.decayHeat) annotation(
        Line(points = {{-159, 367}, {-198, 367}, {-198, -592}, {-94, -592}}, color = {220, 138, 221}, thickness = 1));
      connect(powerblock.decayPowerM, R7.decayHeat) annotation(
        Line(points = {{-159, 367}, {-198, 367}, {-198, -50}, {226, -50}, {226, -199}, {263, -199}}, color = {220, 138, 221}, thickness = 1));
      connect(powerblock.decayPowerM, R6.decayHeat) annotation(
        Line(points = {{-159, 367}, {-198, 367}, {-198, -50}, {224, -50}, {224, -392}, {276, -392}}, color = {220, 138, 221}, thickness = 1));
      connect(powerblock.decayPowerM, R5.decayHeat) annotation(
        Line(points = {{-159, 367}, {-196, 367}, {-196, -50}, {224, -50}, {224, -592}, {276, -592}}, color = {220, 138, 221}, thickness = 1));
      connect(powerblock.decayPowerM, R9.decayHeat) annotation(
        Line(points = {{-159, 367}, {-196, 367}, {-196, -52}, {590, -52}, {590, -312}, {658, -312}}, color = {220, 138, 221}, thickness = 1));
      connect(powerblock.decayPowerM, R8.decayHeat) annotation(
        Line(points = {{-159, 367}, {-198, 367}, {-198, -54}, {590, -54}, {590, -513}, {664, -513}}, color = {220, 138, 221}, thickness = 1));
      connect(powerblock.decayPowerM, volumetricPowerOut) annotation(
        Line(points = {{-159, 367}, {-159, 489.6}, {-144, 489.6}, {-144, 522}}, color = {220, 138, 221}, thickness = 1));
      connect(flowFracIn, flowDistributor.flowFracIn) annotation(
        Line(points = {{329, -1071}, {329, -929}, {336, -929}}, color = {255, 120, 0}, thickness = 1));
      connect(flowDistributor.flowFracOut[1], R1.fuelFlowFraction) annotation(
        Line(points = {{336, -865}, {-586, -865}, {-586, -424}, {-504, -424}}, color = {255, 120, 0}, thickness = 1));
      connect(flowDistributor.flowFracOut[2], R2.fuelFlowFraction) annotation(
        Line(points = {{336, -865}, {-154, -865}, {-154, -622}, {-94, -622}}, color = {255, 120, 0}, thickness = 1));
      connect(flowDistributor.flowFracOut[2], R3.fuelFlowFraction) annotation(
        Line(points = {{336, -865}, {-156, -865}, {-156, -424}, {-94, -424}}, color = {255, 120, 0}, thickness = 1));
      connect(flowDistributor.flowFracOut[2], R4.fuelFlowFraction) annotation(
        Line(points = {{336, -865}, {-156, -865}, {-156, -230}, {-110, -230}}, color = {255, 120, 0}, thickness = 1));
      connect(flowDistributor.flowFracOut[3], R5.fuelFlowFraction) annotation(
        Line(points = {{336, -865}, {242, -865}, {242, -624}, {278, -624}}, color = {255, 120, 0}, thickness = 1));
      connect(flowDistributor.flowFracOut[3], R6.fuelFlowFraction) annotation(
        Line(points = {{336, -865}, {242, -865}, {242, -424}, {278, -424}}, color = {255, 120, 0}, thickness = 1));
      connect(flowDistributor.flowFracOut[3], R7.fuelFlowFraction) annotation(
        Line(points = {{336, -865}, {242, -865}, {242, -230}, {264, -230}}, color = {255, 120, 0}, thickness = 1));
      connect(flowDistributor.flowFracOut[4], R8.fuelFlowFraction) annotation(
        Line(points = {{336, -865}, {336, -546}, {665, -546}, {665, -545}}, color = {255, 120, 0}, thickness = 1));
      connect(flowDistributor.flowFracOut[4], R9.fuelFlowFraction) annotation(
        Line(points = {{336, -865}, {626, -865}, {626, -342}, {660, -342}}, color = {255, 120, 0}, thickness = 1));
      connect(flowFracIn, mpke.fuelFlowFrac) annotation(
        Line(points = {{329, -1071}, {1050, -1071}, {1050, 312}, {185, 312}}, color = {255, 120, 0}, thickness = 1));
      connect(R1.fuelNode2, upperPlenum.temp_In[1]) annotation(
        Line(points = {{-420, -370}, {-414, -370}, {-414, 34}, {445, 34}, {445, 81}, {449, 81}}, color = {204, 0, 0}, thickness = 1));
      connect(R4.fuelNode2, upperPlenum.temp_In[2]) annotation(
        Line(points = {{-27, -176}, {-8, -176}, {-8, 81}, {449, 81}}, color = {204, 0, 0}, thickness = 1));
      connect(R7.fuelNode2, upperPlenum.temp_In[3]) annotation(
        Line(points = {{345, -177}, {345, 36}, {449, 36}, {449, 81}}, color = {204, 0, 0}, thickness = 1));
      connect(R9.fuelNode2, upperPlenum.temp_In[4]) annotation(
        Line(points = {{740, -290}, {740, -76}, {449, -76}, {449, 81}}, color = {204, 0, 0}, thickness = 1));
      connect(flowDistributor.flowFracOut, upperPlenum.flowFraction) annotation(
        Line(points = {{336, -865}, {-594, -865}, {-594, 81}, {423, 81}}, color = {245, 121, 0}, thickness = 1));
      connect(upperPlenum.temp_Out, tempOut) annotation(
        Line(points = {{448, 146}, {448, 235}, {1117, 235}}, color = {204, 0, 0}, thickness = 1));
      connect(powerblock.decayPowerM, upperPlenum.decayHeat) annotation(
        Line(points = {{-159, 367}, {-196, 367}, {-196, 196}, {606, 196}, {606, 81}, {476, 81}}, color = {220, 138, 221}, thickness = 1));
      connect(tempIn, R8.temp_In) annotation(
        Line(points = {{218, -826}, {218, -576}, {664, -576}}, color = {204, 0, 0}, thickness = 1));
      annotation(
        Diagram(coordinateSystem(extent = {{-2900, 2880}, {3760, -3560}})),
        Icon(coordinateSystem(extent = {{-60, 60}, {60, -60}}), graphics = {Rectangle(lineThickness = 1, extent = {{-60, 60}, {60, -60}}), Text(origin = {3, -1}, extent = {{-53, 31}, {53, -31}}, textString = "Reactor")}));
    end MSRR9R;

    /* rev021-B7 (TASK-20260920-01 P9): XENON/SAMARIUM POISONING IS NOT
       INTEGRATED in either production assembly - neither MSRR1R nor MSRR9R
       instantiates or connects SMD_MSR_Modelica.Nuclear.Poisons (the
       135Te-I-Xe / 149Pm-Sm chain model exists in the library with
       enableFeedback = false hard-wiring poisonReactivity.rho = 0, but it
       has NO instance here), so Xe/Sm feedback cannot be enabled in 1R/9R
       lumped runs at all. The SegmentedMSR package carries its own opt-in
       HomogeneousPoisons model (default off) for the segmented cores.
       Wiring the lumped Poisons model into these assemblies is an owner
       decision; do not enable Xe/Sm in production runs until then. */
    /* Single-region (lumped-core) reactor model.
       Combines one FuelChannel, mPKE, ReactivityFeedback, PowerBlock, and DecayHeat
       into a compact assembly. Used when spatial power distribution is not needed
       or for initial steady-state and fast transient studies. */
    model MSRR1R
      parameter Integer numGroups=6;
      parameter SMD_MSR_Modelica.Units.DecayConstant lambda[numGroups];
      parameter SMD_MSR_Modelica.Units.DelayedNeutronFrac beta[numGroups];
      parameter SMD_MSR_Modelica.Units.NeutronGenerationTime LAMBDA;
      parameter SMD_MSR_Modelica.Units.TemperatureReactivityCoef a_F;
      parameter SMD_MSR_Modelica.Units.TemperatureReactivityCoef a_G;
      parameter SMD_MSR_Modelica.Units.NominalNeutronPopulation n_0;
      parameter SMD_MSR_Modelica.Units.NominalNeutronPopulation nFloor = MSRR_PlantData.Kinetics.nFloor
        "Numerical neutron floor passed to mPKE";
      parameter SMD_MSR_Modelica.Units.NominalNeutronPopulation nFloorDuringForcing = MSRR_PlantData.Kinetics.nFloor
        "Optional forcing-window neutron floor passed to mPKE";
      parameter SMD_MSR_Modelica.Units.InitiationTime nFloorSwitchTime = 1e100
        "Time to switch from nFloor to nFloorDuringForcing in mPKE";
      parameter SMD_MSR_Modelica.Units.ResidenceTime nomTauCore = MSRR_PlantData.Kinetics.nomTauCore
        "Nominal core fuel transit time passed to mPKE [s]";
      parameter SMD_MSR_Modelica.Units.ResidenceTime nomTauLoop = MSRR_PlantData.Kinetics.nomTauLoop
        "Nominal loop fuel transit time passed to mPKE [s]";
      parameter SMD_MSR_Modelica.Units.Density rho_fuel;
      parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP_fuel;
      parameter SMD_MSR_Modelica.Units.Conductivity kFuel;
      parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotFuel;
      parameter SMD_MSR_Modelica.Units.Density rho_grap;
      parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP_grap;
      parameter SMD_MSR_Modelica.Units.Temperature TF1_0;
      parameter SMD_MSR_Modelica.Units.Temperature TF2_0;
      parameter SMD_MSR_Modelica.Units.Temperature TG_0;
      parameter Boolean EnableRad;
      parameter SMD_MSR_Modelica.Units.Temperature Tinf;
      parameter SMD_MSR_Modelica.Units.Volume vol_FN1 = MSRR_PlantData.Core1R.cellVol[1];
      parameter SMD_MSR_Modelica.Units.Volume vol_FN2 = MSRR_PlantData.Core1R.cellVol[2];
      parameter SMD_MSR_Modelica.Units.Volume vol_GN = MSRR_PlantData.Core1R.volGN;
      parameter SMD_MSR_Modelica.Units.VolumeImportance kFN1 = MSRR_PlantData.Core1R.qFiss[1];
      parameter SMD_MSR_Modelica.Units.VolumeImportance kFN2 = MSRR_PlantData.Core1R.qFiss[2];
      parameter SMD_MSR_Modelica.Units.VolumeImportance kG = MSRR_PlantData.Core1R.kG;
      parameter SMD_MSR_Modelica.Units.Convection hAnom = MSRR_PlantData.Core1R.hAnom;
      parameter SMD_MSR_Modelica.Units.HeatTransferFraction kHT_FN1 = MSRR_PlantData.Core1R.kHT[1];
      parameter SMD_MSR_Modelica.Units.HeatTransferFraction kHT_FN2 = MSRR_PlantData.Core1R.kHT[2];
      parameter SMD_MSR_Modelica.Units.FlowFraction regionFlowFrac = MSRR_PlantData.Core1R.flowFrac;
      parameter SMD_MSR_Modelica.Units.Area Ac = MSRR_PlantData.Core1R.Ac;
      parameter SMD_MSR_Modelica.Units.Length LF1 = MSRR_PlantData.Core1R.LF1;
      parameter SMD_MSR_Modelica.Units.Length LF2 = MSRR_PlantData.Core1R.LF2;
      parameter SMD_MSR_Modelica.Units.Area ArF1 = MSRR_PlantData.Core1R.ArF1;
      parameter SMD_MSR_Modelica.Units.Area ArF2 = MSRR_PlantData.Core1R.ArF2;
      parameter SMD_MSR_Modelica.Units.Emissivity e = MSRR_PlantData.Core1R.e;
      // rev021-B6 (TASK-20260920-01 P9): this wrapper previously declared a
      // dead `parameter Real hAExp = 0.33` copy - nothing read it (the fuel
      // channel below binds its own component-level literal default), so
      // setting it silently did nothing; the copy is DELETED rather than
      // passed through to the channel because omc 1.27 demotes parameters to
      // non-overridable calculatedParameters when their binding is not a
      // plain constant - a `hAExp = hAExp` modifier on the instantiation
      // would re-bind the channel parameter non-literally and silently
      // disable -override=core1R.fuelchannel.hAExp. The PlantData entry
      // (MSRR_PlantData.Core1R.hAExp, 0.33) stays a documentation copy of
      // the channel default; sensitivity overrides must target
      // core1R.fuelchannel.hAExp.
      parameter Real IF1 = MSRR_PlantData.Core1R.IF1;
      parameter Real IF2 = MSRR_PlantData.Core1R.IF2;
      parameter Real IG = MSRR_PlantData.Core1R.IG;
      parameter Integer numSourceSteps(min = 1) = 1;
      parameter SMD_MSR_Modelica.Units.InitiationTime sourceStepTime[numSourceSteps] = {0};
      parameter SMD_MSR_Modelica.Units.NeutronEmissionRate sourceAmplitude[numSourceSteps] = {0};
      
      SMD_MSR_Modelica.Nuclear.mPKE mpke(numGroups = numGroups, lambda = lambda, beta = beta, LAMBDA = LAMBDA, n_0 = n_0, nFloor = nFloor, nFloorDuringForcing = nFloorDuringForcing, nFloorSwitchTime = nFloorSwitchTime, nomTauLoop = nomTauLoop, nomTauCore = nomTauCore, nu = MSRR_PlantData.Kinetics.nu, nominalPower = MSRR_PlantData.nominalPower, energyPerFission = MSRR_PlantData.Kinetics.energyPerFission, sourceEffectiveness = MSRR_PlantData.Kinetics.sourceEffectiveness) annotation(
        Placement(transformation(origin = {-205.6, 84.4}, extent = {{-20.4, -20.4}, {13.6, 13.6}})));
      MSRR.Components.FuelChannel fuelchannel(rho_fuel = rho_fuel, rho_grap = rho_grap, cP_fuel = cP_fuel, cP_grap = cP_grap, Vdot_fuelNom = volDotFuel, kFN1 = kFN1, kFN2 = kFN2, kG = kG, kHT_FN1 = kHT_FN1, kHT_FN2 = kHT_FN2, TF1_0 = TF1_0, TF2_0 = TF2_0, TG_0 = TG_0, regionFlowFrac = regionFlowFrac, KF = kFuel, Ac = Ac, LF1 = LF1, LF2 = LF2, OuterRegion = EnableRad, ArF1 = ArF1, ArF2 = ArF2, e = e, Tinf = Tinf, vol_FN1 = vol_FN1, vol_FN2 = vol_FN2, vol_GN = vol_GN, hAnom = hAnom) annotation(
        Placement(transformation(origin = {-80.3333, -106.278}, extent = {{-67.2222, -67.2222}, {40.3333, 53.7778}})));
      SMD_MSR_Modelica.Nuclear.ReactivityFeedback react(FuelTempSetPointNode1 = TF1_0, FuelTempSetPointNode2 = TF2_0, GrapTempSetPoint = TG_0, IF1 = IF1, IF2 = IF2, IG = IG, a_F = a_F, a_G = a_G) annotation(
        Placement(transformation(origin = {1.69334, 61.8548}, extent = {{-22.2896, 14.8598}, {14.8598, -22.2896}})));
      SMD_MSR_Modelica.Nuclear.PowerBlock powerblock(P(displayUnit = "W") = MSRR_PlantData.nominalPower, TotalFuelVol = MSRR_PlantData.totalFuelVol) annotation(
        Placement(transformation(origin = {-201.2, 5.6}, extent = {{-12.8, -25.6}, {19.2, 6.4}})));
      SMD_MSR_Modelica.Nuclear.DecayHeat decayHeat(numGroups = MSRR_PlantData.DecayHeat.numGroups, DHYG = MSRR_PlantData.DecayHeat.DHYG, DHlamG = MSRR_PlantData.DecayHeat.DHlamG) annotation(
        Placement(transformation(origin = {-140, 18}, extent = {{-16, -16}, {24, 24}})));
      SMD_MSR_Modelica.Signals.TimeDependent.Stepper sourceStepper(numSteps = numSourceSteps, stepTime = sourceStepTime, amplitude = sourceAmplitude) annotation(
        Placement(transformation(origin = {-293, 79}, extent = {{-27, -27}, {27, 27}})));
      input SMD_MSR_Modelica.PortsConnectors.FlowFractionIn flowFracIn annotation(
        Placement(transformation(origin = {-370, -124}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-42, 40}, extent = {{-18, -18}, {18, 18}})));
      input SMD_MSR_Modelica.PortsConnectors.TempIn tempIn annotation(
        Placement(transformation(origin = {-368, -156}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {0, -38}, extent = {{-18, -18}, {18, 18}})));
      output SMD_MSR_Modelica.PortsConnectors.TempOut tempOut annotation(
        Placement(transformation(origin = {158, -76}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {38, 40}, extent = {{-18, -18}, {18, 18}})));
      output SMD_MSR_Modelica.PortsConnectors.VolumetricPowerOut volumetricPowerOut annotation(
        Placement(transformation(origin = {160, -14}, extent = {{-6, -6}, {6, 6}}), iconTransformation(origin = {39, -39}, extent = {{-17, -17}, {17, 17}})));
      input SMD_MSR_Modelica.PortsConnectors.RealIn realIn annotation(
        Placement(transformation(origin = {-372, 148}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-2, 40}, extent = {{-18, -18}, {18, 18}})));
      output SMD_MSR_Modelica.Units.NeutronEmissionRate sourceRate;
      
    
    equation
      sourceRate = sourceStepper.step.R;
      mpke.S.nDot = sourceRate;
      connect(mpke.n_population, powerblock.nPop) annotation(
        Line(points = {{-209, 71}, {-208, 71}, {-208, 6}}, color = {20, 36, 248}, thickness = 1));
      connect(powerblock.decayPowerM, fuelchannel.decayHeat) annotation(
        Line(points = {{-188, -14}, {-188, -90}, {-148, -90}}, color = {220, 138, 221}, thickness = 1));
      connect(powerblock.fissionPower, fuelchannel.fissionPower) annotation(
        Line(points = {{-208, -14}, {-208, -68}, {-148, -68}}, color = {129, 61, 156}, thickness = 1));
      connect(mpke.n_population, decayHeat.nPop) annotation(
        Line(points = {{-209, 71}, {-170.75, 71}, {-170.75, 34}, {-136, 34}}, color = {20, 36, 248}, thickness = 1));
      connect(decayHeat.decayHeat_Out, powerblock.decayNP) annotation(
        Line(points = {{-136, 10}, {-136, 6}, {-188, 6}}, color = {0, 225, 255}, thickness = 1));
      connect(react.feedback, mpke.feedback) annotation(
        Line(points = {{-6, 66}, {-6, 108}, {-206, 108}, {-206, 92}}, color = {78, 154, 6}, thickness = 1));
      connect(flowFracIn, fuelchannel.fuelFlowFraction) annotation(
        Line(points = {{-370, -124}, {-253.5, -124}, {-253.5, -113}, {-148, -113}}, color = {255, 120, 0}));
      connect(fuelchannel.fuelNode2, react.fuelNode2) annotation(
        Line(points = {{-67, -83}, {0, -83}, {0, 43}, {5, 43}}, color = {204, 0, 0}, thickness = 1));
      connect(fuelchannel.grapNode, react.grapNode) annotation(
        Line(points = {{-67, -113}, {-67, 43}, {-6, 43}}, color = {204, 0, 0}, thickness = 1));
      connect(fuelchannel.fuelNode1, react.fuelNode1) annotation(
        Line(points = {{-67, -143}, {-12, -143}, {-12, 43}, {-17, 43}}, color = {204, 0, 0}, thickness = 1));
      connect(flowFracIn, mpke.fuelFlowFrac) annotation(
        Line(points = {{-370, -124}, {-366, -124}, {-366, 122}, {-198, 122}, {-198, 92}}, color = {255, 120, 0}));
      connect(tempIn, fuelchannel.temp_In) annotation(
        Line(points = {{-368, -156}, {-257, -156}, {-257, -158}, {-148, -158}}, color = {204, 0, 0}));
      connect(fuelchannel.fuelNode2, tempOut) annotation(
        Line(points = {{-67, -83}, {47, -83}, {47, -76}, {158, -76}}, color = {204, 0, 0}));
      connect(powerblock.decayPowerM, volumetricPowerOut) annotation(
        Line(points = {{-188, -14}, {160, -14}}, color = {220, 138, 221}, thickness = 1));
      connect(realIn, mpke.ReactivityIn) annotation(
        Line(points = {{-372, 148}, {-220, 148}, {-220, 92}}, thickness = 1));
    
      annotation(
        Diagram(coordinateSystem(extent = {{-380, 160}, {180, -180}})),
        Icon(coordinateSystem(extent = {{-60, 60}, {60, -60}}), graphics = {Rectangle(lineThickness = 1, extent = {{-60, 60}, {60, -60}}), Text(origin = {3, -1}, extent = {{-53, 31}, {53, -31}}, textString = "Reactor")}));
    end MSRR1R;
  end Components;

  /* Full primary-and-secondary loop with single-region core (MSRR1R).
     Primary loop: core → pipeCoreToDHRS → DHRS → pipeDHRStoHX → HX → pipeHXtoCore.
     Secondary loop: HX → pipeHXtoUHX → UHX → pipeUHXtoHX → HX.
     External reactivity (step + sinusoidal) is summed and injected into core kinetics. */
  model R1MSRRuhx
    parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotFuel = MSRR_PlantData.PrimaryLoop.vdotFuel;
    parameter SMD_MSR_Modelica.Units.Density rhoFuel = MSRR_PlantData.Materials.rhoFuel;
    parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpFuel = MSRR_PlantData.Materials.cpFuel;
    parameter SMD_MSR_Modelica.Units.Conductivity kFuel = MSRR_PlantData.Materials.kFuel;
    parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotCoolant = MSRR_PlantData.SecondaryLoop.vdotCoolant;
    parameter SMD_MSR_Modelica.Units.Density rhoCoolant = MSRR_PlantData.Materials.rhoCoolant;
    parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpCoolant = MSRR_PlantData.Materials.cpCoolant;
    parameter SMD_MSR_Modelica.Units.Conductivity kCoolant = MSRR_PlantData.Materials.kCoolant;
    parameter SMD_MSR_Modelica.Units.Density rhoGrap = MSRR_PlantData.Materials.rhoGrap;
    parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpGrap = MSRR_PlantData.Materials.cpGrap;
    parameter SMD_MSR_Modelica.Units.Density rhoHXtube = MSRR_PlantData.Materials.rhoHXtube;
    parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpHXtube = MSRR_PlantData.Materials.cpHXtube;
    parameter SMD_MSR_Modelica.Units.NominalPower powerLevel = 1 "Relative reactor/UHX demand level";
    parameter Real perturbationAmplitudePcm = 0 "Sinusoidal reactivity perturbation amplitude [pcm]";
    parameter SMD_MSR_Modelica.Units.AngularFrequency perturbationOmega = 0.01 "Sinusoidal perturbation frequency [rad/s]";
    parameter SMD_MSR_Modelica.Units.InitiationTime perturbationStartTime = 2000 "Sinusoidal perturbation start time [s]";
    parameter SMD_MSR_Modelica.Units.InitiationTime forcingTimeStep = 0
      "If >0, inject periodic time events after perturbationStartTime to shorten forcing-window time steps";
    parameter SMD_MSR_Modelica.Units.Temperature fuelTempSetPointNode1 = MSRR_PlantData.Core1R.TF1;
    parameter SMD_MSR_Modelica.Units.Temperature fuelTempSetPointNode2 = MSRR_PlantData.Core1R.TF2;
    parameter SMD_MSR_Modelica.Units.Temperature graphiteTempSetPoint = MSRR_PlantData.Core1R.TG;
    parameter Integer numUhxSteps(min = 1) = 1;
    parameter SMD_MSR_Modelica.Units.InitiationTime uhxDemandStepTime[numUhxSteps] = {0};
    parameter SMD_MSR_Modelica.Units.Power uhxDemandAmplitude[numUhxSteps] = {powerLevel*MSRR_PlantData.nominalPower};
    parameter Integer numExternalReactivitySteps(min = 1) = 2;
    parameter SMD_MSR_Modelica.Units.InitiationTime externalReactivityStepTime[numExternalReactivitySteps] = {0, 4000};
    parameter Real externalReactivityAmplitude[numExternalReactivitySteps] = {0, 0};
    parameter Boolean heatLossEnabled = false
      "Enable radiative heat loss in core (startup only)";
    parameter SMD_MSR_Modelica.Units.Temperature heatLossTinf = MSRR_PlantData.Core1R.heatLossTinf
      "Ambient trench temperature when heaters are off (thesis assumption)";
    MSRR.Components.HeatExchanger heatExchanger(vol_P = MSRR_PlantData.SecondaryLoop.hxVolP, vol_T = MSRR_PlantData.SecondaryLoop.hxVolT, vol_S = MSRR_PlantData.SecondaryLoop.hxVolS, rhoP = rhoFuel, rhoT = rhoHXtube, rhoS = rhoCoolant, cP_P = scpFuel, cP_T = scpHXtube, cP_S = scpCoolant, VdotPnom = volDotFuel, VdotSnom = volDotCoolant, hApNom = MSRR_PlantData.SecondaryLoop.hApNom, hAsNom = MSRR_PlantData.SecondaryLoop.hAsNom, hAExp = 0.33, EnableRad = false, AcShell = MSRR_PlantData.SecondaryLoop.HX.AcShell, AcTube = MSRR_PlantData.SecondaryLoop.HX.AcTube, ArShell = MSRR_PlantData.SecondaryLoop.HX.ArShell, Kp = kFuel, Ks = kCoolant, L_shell = MSRR_PlantData.SecondaryLoop.HX.L_shell, L_tube = MSRR_PlantData.SecondaryLoop.HX.L_tube, e = MSRR_PlantData.SecondaryLoop.HX.e, Tinf = MSRR_PlantData.SecondaryLoop.HX.Tinf, TpIn_0 = MSRR_PlantData.SecondaryLoop.HX.TpIn_0, TpOut_0 = MSRR_PlantData.SecondaryLoop.HX.TpOut_0, TsIn_0 = MSRR_PlantData.SecondaryLoop.HX.TsIn_0, TsOut_0 = MSRR_PlantData.SecondaryLoop.HX.TsOut_0) annotation(
      Placement(transformation(origin = {-28.2, 32.6}, extent = {{-50.8, -25.4}, {76.2, 25.4}})));
    SMD_MSR_Modelica.HeatTransport.UHX uhx(Tp_0 = MSRR_PlantData.SecondaryLoop.UHX.Tp_0, vDot = volDotCoolant, cP = scpCoolant, rho = rhoCoolant, vol = MSRR_PlantData.SecondaryLoop.uhxVol) annotation(
      Placement(transformation(origin = {-2.538, -94.3463}, extent = {{-54.8618, -41.1463}, {27.4309, 41.1463}})));
    SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoUHX(vol = MSRR_PlantData.SecondaryLoop.pipeHXtoUHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.T_0) annotation(
      Placement(transformation(origin = {-132.8, -65.0667}, extent = {{-27.2, -9.06667}, {18.1333, -36.2667}}, rotation = -0)));
    SMD_MSR_Modelica.HeatTransport.Pipe pipeUHXtoHX(vol = MSRR_PlantData.SecondaryLoop.pipeUHXtoHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.T_0) annotation(
      Placement(transformation(origin = {67.2, -64.2667}, extent = {{-27.2, -9.06667}, {18.1333, -36.2667}})));
    SMD_MSR_Modelica.HeatTransport.DHRS dhrs(vol = MSRR_PlantData.PrimaryLoop.volLoop[2], rho = rhoFuel, cP = scpFuel, vDotNom = volDotFuel, DHRS_tK = MSRR_PlantData.PrimaryLoop.dhrsTK, DHRS_MaxP_Rm(displayUnit = "MW") = MSRR_PlantData.PrimaryLoop.dhrsMaxRemove, DHRS_P_Bleed = MSRR_PlantData.PrimaryLoop.dhrsBleed, DHRS_time = MSRR_PlantData.PrimaryLoop.dhrsEngageTime, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.Ac, L = MSRR_PlantData.PrimaryLoop.L, Ar = MSRR_PlantData.PrimaryLoop.Ar, EnableRad = false, e = MSRR_PlantData.PrimaryLoop.e, Tinf = MSRR_PlantData.PrimaryLoop.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.T_0) annotation(
      Placement(transformation(origin = {-267, 32.6667}, extent = {{-49.3333, -24.6667}, {37, 49.3333}})));
    SMD_MSR_Modelica.HeatTransport.Pipe pipeDHRStoHX(vol = MSRR_PlantData.PrimaryLoop.volLoop[3], volFracNode = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ac, L = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ar, e = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.T_0) annotation(
      Placement(transformation(origin = {-163.6, 15.2}, extent = {{-38.4, 12.8}, {25.6, 51.2}})));
    SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoCore(vol = MSRR_PlantData.PrimaryLoop.volLoop[5], volFracNode = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ac, L = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ar, e = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.T_0) annotation(
      Placement(transformation(origin = {-320.4, -151.6}, extent = {{-37.2, 12.4}, {24.8, 49.6}}, rotation = 180)));
    SMD_MSR_Modelica.HeatTransport.Pipe pipeCoreToDHRS(vol = MSRR_PlantData.PrimaryLoop.volLoop[1], volFracNode = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ac, L = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ar, e = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.T_0) annotation(
      Placement(transformation(origin = {-398.4, 13.2}, extent = {{-39.6, 13.2}, {26.4, 52.8}})));
    MSRR.Components.MSRR1R core1R(numGroups = MSRR_PlantData.Kinetics.numGroups, EnableRad = heatLossEnabled, lambda = MSRR_PlantData.Kinetics.lambda, beta = MSRR_PlantData.Kinetics.beta, LAMBDA = MSRR_PlantData.Kinetics.LAMBDA, a_F = MSRR_PlantData.Kinetics.a_F, a_G = MSRR_PlantData.Kinetics.a_G, n_0 = powerLevel, rho_fuel = rhoFuel, rho_grap = rhoGrap, cP_fuel = scpFuel, cP_grap = scpGrap, volDotFuel = volDotFuel, Tinf = heatLossTinf, kFuel = kFuel, TF1_0 = fuelTempSetPointNode1, TF2_0 = fuelTempSetPointNode2, TG_0 = graphiteTempSetPoint) annotation(
      Placement(transformation(origin = {-643.667, -81}, extent = {{-60, -60}, {60, 60}})));
    SMD_MSR_Modelica.Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
      Placement(transformation(origin = {-127, -209}, extent = {{-27, -27}, {27, 27}})));
    SMD_MSR_Modelica.HeatTransport.Pump primaryPump(numRampUp = MSRR_PlantData.Pumps.primaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.primaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.primaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.primaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
      Placement(transformation(origin = {-722, 138}, extent = {{-24, -32}, {24, 16}})));
    SMD_MSR_Modelica.HeatTransport.Pump secondaryPump(numRampUp = MSRR_PlantData.Pumps.secondaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.secondaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.secondaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.secondaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
      Placement(transformation(origin = {-111, -19.333}, extent = {{-23, -30.6667}, {23, 15.3333}})));
    SMD_MSR_Modelica.Signals.TimeDependent.Stepper uhxDemand(numSteps = numUhxSteps, stepTime = uhxDemandStepTime, amplitude = uhxDemandAmplitude) annotation(
      Placement(transformation(origin = {-53, -231}, extent = {{-27, -27}, {27, 27}})));
    SMD_MSR_Modelica.Signals.TimeDependent.Stepper externalReact(numSteps = numExternalReactivitySteps, stepTime = externalReactivityStepTime, amplitude = externalReactivityAmplitude) annotation(
      Placement(transformation(origin = {-650.198, 65.8017}, extent = {{-29.7983, 29.7983}, {29.7983, -29.7983}}, rotation = -0)));
    SMD_MSR_Modelica.Signals.TimeDependent.SineSource perturbationSine(amplitude = perturbationAmplitudePcm, omega = perturbationOmega, activationTime = perturbationStartTime) annotation(
      Placement(transformation(origin = {-748, 62}, extent = {{-24, -24}, {24, 24}})));
    SMD_MSR_Modelica.Signals.Operations.SumSignals sumExternalReactivity(numInput = 2) annotation(
      Placement(transformation(origin = {-700, 10}, extent = {{-20, -20}, {20, 20}})));
    output SMD_MSR_Modelica.Units.NeutronEmissionRate sourceRate = core1R.sourceRate
      "External source rate passed into core kinetics [n/s]";
    discrete Integer forcingTick(start = 0, fixed = true)
      "Dummy counter to enforce forcing-window event cadence";
  equation
    when sample(perturbationStartTime, if forcingTimeStep > 0 then forcingTimeStep else 1e100) then
      forcingTick = pre(forcingTick) + 1;
    end when;
    connect(pipeCoreToDHRS.PiTempOut, dhrs.tempIn) annotation(
      Line(points = {{-382, 46}, {-300, 46}}, color = {204, 0, 0}));
    connect(dhrs.tempOut, pipeDHRStoHX.PiTemp_IN) annotation(
      Line(points = {{-248, 46}, {-192, 46}, {-192, 48}}, color = {204, 0, 0}));
    connect(pipeDHRStoHX.PiTempOut, heatExchanger.T_in_pFluid) annotation(
      Line(points = {{-148, 48}, {-70, 48}, {-70, 46}}, color = {204, 0, 0}));
    connect(core1R.tempOut, pipeCoreToDHRS.PiTemp_IN) annotation(
      Line(points = {{-606, -41}, {-606, 46}, {-426, 46}}, color = {204, 0, 0}));
    connect(heatExchanger.T_out_pFluid, pipeHXtoCore.PiTemp_IN) annotation(
      Line(points = {{40, 46}, {76, 46}, {76, -183}, {-294, -183}}, color = {204, 0, 0}));
    connect(externalReact.step, sumExternalReactivity.realIn[1]) annotation(
      Line(points = {{-648, 54}, {-690, 54}, {-690, 22}}));
    connect(perturbationSine.signal, sumExternalReactivity.realIn[2]) annotation(
      Line(points = {{-747, 74}, {-710, 74}, {-710, 22}}));
    connect(sumExternalReactivity.realOut, core1R.realIn) annotation(
      Line(points = {{-718, 32}, {-718, 4}, {-646, 4}, {-646, -41}}));
    connect(primaryPump.flowFrac, core1R.flowFracIn) annotation(
      Line(points = {{-722, 106}, {-720, 106}, {-720, -41}, {-686, -41}}, color = {245, 121, 0}));
    connect(primaryPump.flowFrac, pipeCoreToDHRS.flowFrac) annotation(
      Line(points = {{-722, 106}, {-484, 106}, {-484, 36}, {-426, 36}}, color = {245, 121, 0}));
    connect(primaryPump.flowFrac, dhrs.flowFrac) annotation(
      Line(points = {{-722, 106}, {-340, 106}, {-340, 20}, {-300, 20}}, color = {245, 121, 0}));
    connect(primaryPump.flowFrac, pipeDHRStoHX.flowFrac) annotation(
      Line(points = {{-722, 106}, {-214, 106}, {-214, 38}, {-192, 38}}, color = {245, 121, 0}));
    connect(primaryPump.flowFrac, heatExchanger.primaryFF) annotation(
      Line(points = {{-722, 106}, {-58, 106}, {-58, 54}}, color = {245, 121, 0}));
    connect(pipeHXtoUHX.PiTempOut, uhx.tempIn) annotation(
      Line(points = {{-122, -88}, {-95.5, -88}, {-95.5, -94}, {-57, -94}}, color = {204, 0, 0}));
    connect(uhx.tempOut, pipeUHXtoHX.PiTemp_IN) annotation(
      Line(points = {{-3, -94}, {11, -94}, {11, -87}, {48, -87}}, color = {204, 0, 0}));
    connect(heatExchanger.T_out_sFluid, pipeHXtoUHX.PiTemp_IN) annotation(
      Line(points = {{-70, 20}, {-176, 20}, {-176, -88}, {-152, -88}}, color = {204, 0, 0}));
    connect(pipeUHXtoHX.PiTempOut, heatExchanger.T_in_sFluid) annotation(
      Line(points = {{78, -87}, {78, 20}, {40, 20}}, color = {204, 0, 0}));
    connect(secondaryPump.flowFrac, pipeHXtoUHX.flowFrac) annotation(
      Line(points = {{-110, -50}, {-200, -50}, {-200, -81}, {-152, -81}}, color = {245, 121, 0}));
    connect(secondaryPump.flowFrac, uhx.flowFrac) annotation(
      Line(points = {{-110, -50}, {-110, -67}, {-57, -67}}, color = {245, 121, 0}));
    connect(secondaryPump.flowFrac, pipeUHXtoHX.flowFrac) annotation(
      Line(points = {{-110, -50}, {26, -50}, {26, -80}, {48, -80}}, color = {245, 121, 0}));
    connect(constantVolumetricPower.volPow, pipeHXtoUHX.PiDecay_Heat) annotation(
      Line(points = {{-129, -193}, {-152, -193}, {-152, -94}}, color = {220, 138, 221}));
    connect(constantVolumetricPower.volPow, pipeUHXtoHX.PiDecay_Heat) annotation(
      Line(points = {{-129, -193}, {26, -193}, {26, -94}, {48, -94}}, color = {220, 138, 221}));
    connect(uhxDemand.step, uhx.powDemand) annotation(
      Line(points = {{-52, -218}, {-62, -218}, {-62, -122}, {-57, -122}}));
    connect(secondaryPump.flowFrac, heatExchanger.secondaryFF) annotation(
      Line(points = {{-110, -50}, {26, -50}, {26, 12}}, color = {245, 121, 0}));
    connect(primaryPump.flowFrac, pipeHXtoCore.flowFrac) annotation(
      Line(points = {{-722, 106}, {-346, 106}, {-346, -173}, {-294, -173}}, color = {245, 121, 0}));
    connect(core1R.volumetricPowerOut, pipeCoreToDHRS.PiDecay_Heat) annotation(
      Line(points = {{-605, -120}, {-460, -120}, {-460, 56}, {-426, 56}}, color = {220, 138, 221}));
    connect(core1R.volumetricPowerOut, dhrs.pDecay) annotation(
      Line(points = {{-605, -120}, {-328, -120}, {-328, 70}, {-300, 70}}, color = {220, 138, 221}));
    connect(core1R.volumetricPowerOut, pipeDHRStoHX.PiDecay_Heat) annotation(
      Line(points = {{-605, -120}, {-222, -120}, {-222, 56}, {-192, 56}}, color = {220, 138, 221}));
    connect(core1R.volumetricPowerOut, heatExchanger.P_decay) annotation(
      Line(points = {{-605, -120}, {-471, -120}, {-471, 132}, {-16, 132}, {-16, 54}}, color = {220, 138, 221}));
    connect(core1R.volumetricPowerOut, pipeHXtoCore.PiDecay_Heat) annotation(
      Line(points = {{-605, -120}, {-575, -120}, {-575, -230}, {-294, -230}, {-294, -192}}, color = {220, 138, 221}));
    connect(pipeHXtoCore.PiTempOut, core1R.tempIn) annotation(
      Line(points = {{-334, -182}, {-644, -182}, {-644, -118}}, color = {204, 0, 0}));
    annotation(
      Diagram(coordinateSystem(extent = {{-760, 180}, {220, -280}}), graphics = {Polygon(points = {{108, 32}, {108, 32}, {108, 32}})}));
  end R1MSRRuhx;

  /* Full primary-and-secondary loop with nine-region core (MSRR9R).
     Identical loop topology to R1MSRRuhx; only the core block differs. */
  model R9MSRRuhx
    parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotFuel = MSRR_PlantData.PrimaryLoop.vdotFuel;
    parameter SMD_MSR_Modelica.Units.Density rhoFuel = MSRR_PlantData.Materials.rhoFuel;
    parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpFuel = MSRR_PlantData.Materials.cpFuel;
    parameter SMD_MSR_Modelica.Units.Conductivity kFuel = MSRR_PlantData.Materials.kFuel;
    parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotCoolant = MSRR_PlantData.SecondaryLoop.vdotCoolant;
    parameter SMD_MSR_Modelica.Units.Density rhoCoolant = MSRR_PlantData.Materials.rhoCoolant;
    parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpCoolant = MSRR_PlantData.Materials.cpCoolant;
    parameter SMD_MSR_Modelica.Units.Conductivity kCoolant = MSRR_PlantData.Materials.kCoolant;
    parameter SMD_MSR_Modelica.Units.Density rhoGrap = MSRR_PlantData.Materials.rhoGrap;
    parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpGrap = MSRR_PlantData.Materials.cpGrap;
    parameter SMD_MSR_Modelica.Units.Density rhoHXtube = MSRR_PlantData.Materials.rhoHXtube;
    parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpHXtube = MSRR_PlantData.Materials.cpHXtube;
    parameter SMD_MSR_Modelica.Units.NominalPower powerLevel = 1 "Relative reactor/UHX demand level";
    parameter Real perturbationAmplitudePcm = 0 "Sinusoidal reactivity perturbation amplitude [pcm]";
    parameter SMD_MSR_Modelica.Units.AngularFrequency perturbationOmega = 0.01 "Sinusoidal perturbation frequency [rad/s]";
    parameter SMD_MSR_Modelica.Units.InitiationTime perturbationStartTime = 2000 "Sinusoidal perturbation start time [s]";
    parameter SMD_MSR_Modelica.Units.InitiationTime forcingTimeStep = 0
      "If >0, inject periodic time events after perturbationStartTime to shorten forcing-window time steps";
    parameter SMD_MSR_Modelica.Units.Temperature fuelTempSetPointNode1 = MSRR_PlantData.Core9R.TF1;
    parameter SMD_MSR_Modelica.Units.Temperature fuelTempSetPointNode2 = MSRR_PlantData.Core9R.TF2;
    parameter SMD_MSR_Modelica.Units.Temperature graphiteTempSetPoint = MSRR_PlantData.Core9R.TG;
    // Region trims (physics review 2026-09-27 B1.3): all nine elements bind
    // the PlantData region profile directly, so EVERY element - region 1
    // included - is overridable from a setpoint table. The former
    // cat(1, {fuelTempSetPointNode1}, ...[2:9]) binding made the arrays
    // non-overridable in OMC ("not possible to override"), so region 1
    // silently took the shell scalars (the table writes the core AVERAGE
    // there, +3.9/+5.8/+2.8 K off region 1's own trim). The shell scalars
    // fuelTempSetPointNode1/fuelTempSetPointNode2/graphiteTempSetPoint no
    // longer feed the 9R core; they remain as table-compatible inputs.
    parameter SMD_MSR_Modelica.Units.Temperature TF1_0_regions[9] = MSRR_PlantData.Core9R.TF1_0_regions;
    parameter SMD_MSR_Modelica.Units.Temperature TF2_0_regions[9] = MSRR_PlantData.Core9R.TF2_0_regions;
    parameter SMD_MSR_Modelica.Units.Temperature TG_0_regions[9] = MSRR_PlantData.Core9R.TG_0_regions;
    parameter SMD_MSR_Modelica.Units.Temperature Tmix_0 = MSRR_PlantData.Core9R.Tmix_0
      "Upper-plenum initial temperature (physics review 2026-09-27 B1.2: the setpoint tables' Tmix_0 column targets this top-level name; it was previously bound inside msre9r to the PlantData constant, so every table override was 'not found' and the plenum started at 580.41 degC)";
    parameter Integer numUhxSteps(min = 1) = 1;
    parameter SMD_MSR_Modelica.Units.InitiationTime uhxDemandStepTime[numUhxSteps] = {0};
    parameter SMD_MSR_Modelica.Units.Power uhxDemandAmplitude[numUhxSteps] = {powerLevel*MSRR_PlantData.nominalPower};
    parameter Integer numExternalReactivitySteps(min = 1) = 2;
    parameter SMD_MSR_Modelica.Units.InitiationTime externalReactivityStepTime[numExternalReactivitySteps] = {0, 4000};
    parameter Real externalReactivityAmplitude[numExternalReactivitySteps] = {0, 0};
    parameter Boolean heatLossEnabled = false
      "Enable radiative heat loss in core (startup only)";
    parameter SMD_MSR_Modelica.Units.Temperature heatLossTinf = MSRR_PlantData.Core1R.heatLossTinf
      "Ambient trench temperature when heaters are off (thesis assumption)";
    MSRR.Components.HeatExchanger heatExchanger(vol_P = MSRR_PlantData.SecondaryLoop.hxVolP, vol_T = MSRR_PlantData.SecondaryLoop.hxVolT, vol_S = MSRR_PlantData.SecondaryLoop.hxVolS, rhoP = rhoFuel, rhoT = rhoHXtube, rhoS = rhoCoolant, cP_P = scpFuel, cP_T = scpHXtube, cP_S = scpCoolant, VdotPnom = volDotFuel, VdotSnom = volDotCoolant, hApNom = MSRR_PlantData.SecondaryLoop.hApNom, hAsNom = MSRR_PlantData.SecondaryLoop.hAsNom, hAExp = 0.33, EnableRad = false, AcShell = MSRR_PlantData.SecondaryLoop.HX.AcShell, AcTube = MSRR_PlantData.SecondaryLoop.HX.AcTube, ArShell = MSRR_PlantData.SecondaryLoop.HX.ArShell, Kp = kFuel, Ks = kCoolant, L_shell = MSRR_PlantData.SecondaryLoop.HX.L_shell, L_tube = MSRR_PlantData.SecondaryLoop.HX.L_tube, e = MSRR_PlantData.SecondaryLoop.HX.e, Tinf = MSRR_PlantData.SecondaryLoop.HX.Tinf, TpIn_0 = MSRR_PlantData.SecondaryLoop.HX.TpIn_0, TpOut_0 = MSRR_PlantData.SecondaryLoop.HX.TpOut_0, TsIn_0 = MSRR_PlantData.SecondaryLoop.HX.TsIn_0, TsOut_0 = MSRR_PlantData.SecondaryLoop.HX.TsOut_0) annotation(
      Placement(transformation(origin = {6.7, 27.2}, extent = {{-76, -38}, {114, 38}})));
    SMD_MSR_Modelica.HeatTransport.UHX uhx(Tp_0 = MSRR_PlantData.SecondaryLoop.UHX.Tp_0, vDot = volDotCoolant, cP = scpCoolant, rho = rhoCoolant, vol = MSRR_PlantData.SecondaryLoop.uhxVol) annotation(
      Placement(transformation(origin = {64.462, -208.346}, extent = {{-54.8618, -41.1463}, {27.4309, 41.1463}})));
    SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoUHX(vol = MSRR_PlantData.SecondaryLoop.pipeHXtoUHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.T_0) annotation(
      Placement(transformation(origin = {-47.8, -170.067}, extent = {{-45.2, -15.0667}, {30.1333, -60.2667}}, rotation = -0)));
    SMD_MSR_Modelica.HeatTransport.Pipe pipeUHXtoHX(vol = MSRR_PlantData.SecondaryLoop.pipeUHXtoHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.T_0) annotation(
      Placement(transformation(origin = {171, -157.2}, extent = {{-60, -20}, {39.9999, -80}}, rotation = -0)));
    SMD_MSR_Modelica.HeatTransport.DHRS dhrs(vol = MSRR_PlantData.PrimaryLoop.volLoop[2], rho = rhoFuel, cP = scpFuel, vDotNom = volDotFuel, DHRS_tK = MSRR_PlantData.PrimaryLoop.dhrsTK, DHRS_MaxP_Rm(displayUnit = "MW") = MSRR_PlantData.PrimaryLoop.dhrsMaxRemove, DHRS_P_Bleed = MSRR_PlantData.PrimaryLoop.dhrsBleed, DHRS_time = MSRR_PlantData.PrimaryLoop.dhrsEngageTime, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.Ac, L = MSRR_PlantData.PrimaryLoop.L, Ar = MSRR_PlantData.PrimaryLoop.Ar, EnableRad = false, e = MSRR_PlantData.PrimaryLoop.e, Tinf = MSRR_PlantData.PrimaryLoop.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.T_0) annotation(
      Placement(transformation(origin = {-249, 30.6667}, extent = {{-49.3333, -24.6667}, {37, 49.3333}})));
    SMD_MSR_Modelica.HeatTransport.Pipe pipeDHRStoHX(vol = MSRR_PlantData.PrimaryLoop.volLoop[3], volFracNode = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ac, L = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ar, e = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.T_0) annotation(
      Placement(transformation(origin = {-141.6, 11.2}, extent = {{-38.4, 12.8}, {25.6, 51.2}})));
    SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoCore(vol = MSRR_PlantData.PrimaryLoop.volLoop[5], volFracNode = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ac, L = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ar, e = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.T_0) annotation(
      Placement(transformation(origin = {-260.4, -119.6}, extent = {{-37.2, 12.4}, {24.8, 49.6}}, rotation = 180)));
    SMD_MSR_Modelica.HeatTransport.Pipe pipeCoreToDHRS(vol = MSRR_PlantData.PrimaryLoop.volLoop[1], volFracNode = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ac, L = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ar, e = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.T_0) annotation(
      Placement(transformation(origin = {-374, 6}, extent = {{-39.6, 13.2}, {26.4, 52.8}})));
    SMD_MSR_Modelica.Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
      Placement(transformation(origin = {-66, -300}, extent = {{-27, -27}, {27, 27}})));
    SMD_MSR_Modelica.HeatTransport.Pump primaryPump(numRampUp = MSRR_PlantData.Pumps.primaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.primaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.primaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.primaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
      Placement(transformation(origin = {-636, 136}, extent = {{-24, -32}, {24, 16}})));
    SMD_MSR_Modelica.HeatTransport.Pump secondaryPump(numRampUp = MSRR_PlantData.Pumps.secondaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.secondaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.secondaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.secondaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
      Placement(transformation(origin = {9, -69.333}, extent = {{-23, -30.6667}, {23, 15.3333}})));
    SMD_MSR_Modelica.Signals.TimeDependent.Stepper uhxDemand(numSteps = numUhxSteps, stepTime = uhxDemandStepTime, amplitude = uhxDemandAmplitude) annotation(
      Placement(transformation(origin = {14, -324}, extent = {{-27, -27}, {27, 27}})));
    SMD_MSR_Modelica.Signals.TimeDependent.Stepper externalReact(numSteps = numExternalReactivitySteps, stepTime = externalReactivityStepTime, amplitude = externalReactivityAmplitude) annotation(
      Placement(transformation(origin = {-564.198, 53.8017}, extent = {{-23.7983, 23.7983}, {23.7983, -23.7983}})));
    SMD_MSR_Modelica.Signals.TimeDependent.SineSource perturbationSine(amplitude = perturbationAmplitudePcm, omega = perturbationOmega, activationTime = perturbationStartTime) annotation(
      Placement(transformation(origin = {-610, 54}, extent = {{-20, -20}, {20, 20}})));
    SMD_MSR_Modelica.Signals.Operations.SumSignals sumExternalReactivity(numInput = 2) annotation(
      Placement(transformation(origin = {-584, 10}, extent = {{-20, -20}, {20, 20}})));
    MSRR.Components.MSRR9R msre9r(numGroups = MSRR_PlantData.Kinetics.numGroups, lambda = MSRR_PlantData.Kinetics.lambda, beta = MSRR_PlantData.Kinetics.beta, LAMBDA = MSRR_PlantData.Kinetics.LAMBDA, n_0 = powerLevel, aF = MSRR_PlantData.Kinetics.a_F, aG = MSRR_PlantData.Kinetics.a_G, volF1 = MSRR_PlantData.Core9R.volF1, volF2 = MSRR_PlantData.Core9R.volF2, volG = MSRR_PlantData.Core9R.volG, hA = MSRR_PlantData.Core9R.hA, kFN1 = MSRR_PlantData.Core9R.kFN1, kFN2 = MSRR_PlantData.Core9R.kFN2, kHT1 = MSRR_PlantData.Core9R.kHT1, kHT2 = MSRR_PlantData.Core9R.kHT2, TF1_0 = TF1_0_regions, TF2_0 = TF2_0_regions, TG_0 = TG_0_regions, Tmix_0 = Tmix_0, IF1 = MSRR_PlantData.Core9R.IF1, IF2 = MSRR_PlantData.Core9R.IF2, IG = MSRR_PlantData.Core9R.IG, flowFracRegions = MSRR_PlantData.Core9R.flowFracRegions, rho_fuel = rhoFuel, cP_fuel = scpFuel, kFuel = kFuel, volDotFuel = volDotFuel, rho_grap = rhoGrap, cP_grap = scpGrap, regionTripTime = MSRR_PlantData.Core9R.regionTripTime, regionCoastDownK = MSRR_PlantData.Core9R.regionCoastDownK, freeConvFF = MSRR_PlantData.Pumps.freeConvFF, EnableRad = heatLossEnabled, T_inf = heatLossTinf, LF1 = MSRR_PlantData.Core9R.LF1, LF2 = MSRR_PlantData.Core9R.LF2, Ac = MSRR_PlantData.Core9R.Ac, ArF1 = MSRR_PlantData.Core9R.ArF1, ArF2 = MSRR_PlantData.Core9R.ArF2, e = MSRR_PlantData.Core9R.e) annotation(
      Placement(transformation(origin = {-566.333, -73}, extent = {{-60, -60}, {60, 60}})));
    output SMD_MSR_Modelica.Units.NeutronEmissionRate sourceRate = msre9r.sourceRate
      "External source rate passed into core kinetics [n/s]";
    discrete Integer forcingTick(start = 0, fixed = true)
      "Dummy counter to enforce forcing-window event cadence";
  equation
    when sample(perturbationStartTime, if forcingTimeStep > 0 then forcingTimeStep else 1e100) then
      forcingTick = pre(forcingTick) + 1;
    end when;
    connect(externalReact.step, sumExternalReactivity.realIn[1]) annotation(
      Line(points = {{-562, 46}, {-576, 46}, {-576, 22}}));
    connect(perturbationSine.signal, sumExternalReactivity.realIn[2]) annotation(
      Line(points = {{-609, 66}, {-592, 66}, {-592, 22}}));
    connect(sumExternalReactivity.realOut, msre9r.realIn) annotation(
      Line(points = {{-602, 32}, {-570, 32}, {-570, -33}, {-566, -33}}));
    connect(primaryPump.flowFrac, msre9r.flowFracIn) annotation(
      Line(points = {{-636, 104}, {-636, 105}, {-606, 105}, {-606, -33}}, color = {245, 121, 0}));
    connect(msre9r.tempOut, pipeCoreToDHRS.PiTemp_IN) annotation(
      Line(points = {{-526, -33}, {-466, -33}, {-466, 40}, {-402, 40}}, color = {204, 0, 0}));
    connect(pipeCoreToDHRS.PiTempOut, dhrs.tempIn) annotation(
      Line(points = {{-358, 40}, {-282, 40}, {-282, 44}}, color = {204, 0, 0}));
    connect(dhrs.tempOut, pipeDHRStoHX.PiTemp_IN) annotation(
      Line(points = {{-230, 44}, {-170, 44}}, color = {204, 0, 0}));
    connect(pipeHXtoCore.PiTempOut, msre9r.tempIn) annotation(
      Line(points = {{-275, -151}, {-275, -150.5}, {-363, -150.5}, {-363, -160}, {-606, -160}, {-606, -111}}, color = {204, 0, 0}));
    connect(pipeHXtoUHX.PiTempOut, uhx.tempIn) annotation(
      Line(points = {{-30, -208}, {10, -208}}, color = {204, 0, 0}));
    connect(uhx.tempOut, pipeUHXtoHX.PiTemp_IN) annotation(
      Line(points = {{64, -208}, {128, -208}, {128, -207}}, color = {204, 0, 0}));
    connect(pipeHXtoUHX.PiTemp_IN, heatExchanger.T_out_sFluid) annotation(
      Line(points = {{-80, -208}, {-118, -208}, {-118, -4}, {-52, -4}, {-52, 8}, {-38, 8}}, color = {204, 0, 0}));
    connect(heatExchanger.T_out_pFluid, pipeHXtoCore.PiTemp_IN) annotation(
      Line(points = {{127, 46}, {156, 46}, {156, -151}, {-234, -151}}, color = {204, 0, 0}));
    connect(pipeUHXtoHX.PiTempOut, heatExchanger.T_in_sFluid) annotation(
      Line(points = {{194, -207}, {226, -207}, {226, 8}, {127, 8}}, color = {204, 0, 0}));
    connect(secondaryPump.flowFrac, pipeHXtoUHX.flowFrac) annotation(
      Line(points = {{10, -100}, {-80, -100}, {-80, -196}}, color = {245, 121, 0}));
    connect(secondaryPump.flowFrac, uhx.flowFrac) annotation(
      Line(points = {{10, -100}, {10, -180}}, color = {245, 121, 0}));
    connect(secondaryPump.flowFrac, pipeUHXtoHX.flowFrac) annotation(
      Line(points = {{10, -100}, {128, -100}, {128, -192}}, color = {245, 121, 0}));
    connect(constantVolumetricPower.volPow, pipeHXtoUHX.PiDecay_Heat) annotation(
      Line(points = {{-68, -284}, {-80, -284}, {-80, -220}}, color = {220, 138, 221}));
    connect(constantVolumetricPower.volPow, pipeUHXtoHX.PiDecay_Heat) annotation(
      Line(points = {{-68, -284}, {128, -284}, {128, -222}}, color = {220, 138, 221}));
    connect(uhxDemand.step, uhx.powDemand) annotation(
      Line(points = {{15, -310.5}, {10, -310.5}, {10, -236}}));
    connect(primaryPump.flowFrac, pipeCoreToDHRS.flowFrac) annotation(
      Line(points = {{-636, 112}, {-448, 112}, {-448, 30}, {-402, 30}}, color = {245, 121, 0}));
    connect(primaryPump.flowFrac, dhrs.flowFrac) annotation(
      Line(points = {{-636, 112}, {-318, 112}, {-318, 18}, {-282, 18}}, color = {245, 121, 0}));
    connect(primaryPump.flowFrac, pipeDHRStoHX.flowFrac) annotation(
      Line(points = {{-636, 112}, {-198, 112}, {-198, 34}, {-170, 34}}, color = {245, 121, 0}));
    connect(primaryPump.flowFrac, heatExchanger.primaryFF) annotation(
      Line(points = {{-636, 112}, {-18, 112}, {-18, 58}}, color = {245, 121, 0}));
    connect(primaryPump.flowFrac, pipeHXtoCore.flowFrac) annotation(
      Line(points = {{-636, 112}, {-206, 112}, {-206, -141}, {-234, -141}}, color = {245, 121, 0}));
    connect(msre9r.volumetricPowerOut, pipeCoreToDHRS.PiDecay_Heat) annotation(
      Line(points = {{-526, -112}, {-428, -112}, {-428, 48}, {-402, 48}}, color = {220, 138, 221}));
    connect(msre9r.volumetricPowerOut, dhrs.pDecay) annotation(
      Line(points = {{-526, -112}, {-304, -112}, {-304, 68}, {-282, 68}}, color = {220, 138, 221}));
    connect(msre9r.volumetricPowerOut, pipeDHRStoHX.PiDecay_Heat) annotation(
      Line(points = {{-526, -112}, {-188, -112}, {-188, 52}, {-170, 52}}, color = {220, 138, 221}));
    connect(msre9r.volumetricPowerOut, pipeHXtoCore.PiDecay_Heat) annotation(
      Line(points = {{-526, -112}, {-524, -112}, {-524, -184}, {-234, -184}, {-234, -160}}, color = {220, 138, 221}));
    connect(msre9r.volumetricPowerOut, heatExchanger.P_decay) annotation(
      Line(points = {{-526, -112}, {-92, -112}, {-92, 92}, {44, 92}, {44, 58}}, color = {220, 138, 221}));
    connect(secondaryPump.flowFrac, heatExchanger.secondaryFF) annotation(
      Line(points = {{10, -100}, {108, -100}, {108, -4}}, color = {245, 121, 0}));
    connect(pipeDHRStoHX.PiTempOut, heatExchanger.T_in_pFluid) annotation(
      Line(points = {{-126, 44}, {-80, 44}, {-80, 46}, {-38, 46}}, color = {204, 0, 0}));
    annotation(
      Diagram(coordinateSystem(extent = {{-680, 180}, {260, -400}})));
  end R9MSRRuhx;

  // R1 loop trim: 1 pcm sinusoidal perturbation for frequency-response identification.
  model MSRRuhxNominalTrim
    extends R1MSRRuhx(
      perturbationAmplitudePcm = 1);
  end MSRRuhxNominalTrim;

  // R1 trim with all trips disabled (pump coast-down and DHRS activation suppressed).
  model MSRRuhxNominalTrimNoTrips
    extends R1MSRRuhx(
      perturbationAmplitudePcm = 1,
      primaryPump(tripTime = 1e12),
      secondaryPump(tripTime = 1e12),
      dhrs(DHRS_time = 1e12));
  end MSRRuhxNominalTrimNoTrips;

  // R1 trim with the LOOP thermal components (heatExchanger, uhx, dhrs, and
  // the four loop pipes) initialized at steady state (SteadyState initMode).
  // rev021-B4 (TASK-20260920-01 P9): the former "all thermal components"
  // claim is narrowed to the code's actual content - the core FuelChannel
  // keeps its DEFAULT FixedStart setpoint initialization (TF1_0/TF2_0/TG_0;
  // the trimmed point's thermal derivatives are ~0 there), and propagating
  // SteadyState into the channel would change these trim wrappers'
  // initialization numerics. The lumped loop has no MixingPot.
  model MSRRuhxNominalTrimThermalSS
    extends R1MSRRuhx(
      perturbationAmplitudePcm = 1,
      heatExchanger(initMode = SMD_MSR_Modelica.Units.InitMode.SteadyState),
      uhx(initMode = SMD_MSR_Modelica.Units.InitMode.SteadyState),
      pipeHXtoUHX(initMode = SMD_MSR_Modelica.Units.InitMode.SteadyState),
      pipeUHXtoHX(initMode = SMD_MSR_Modelica.Units.InitMode.SteadyState),
      dhrs(initMode = SMD_MSR_Modelica.Units.InitMode.SteadyState),
      pipeDHRStoHX(initMode = SMD_MSR_Modelica.Units.InitMode.SteadyState),
      pipeHXtoCore(initMode = SMD_MSR_Modelica.Units.InitMode.SteadyState),
      pipeCoreToDHRS(initMode = SMD_MSR_Modelica.Units.InitMode.SteadyState));
  end MSRRuhxNominalTrimThermalSS;

  model MSRRuhxNominalTrimThermalSSNoTrips
    extends MSRRuhxNominalTrimThermalSS(
      primaryPump(tripTime = 1e12),
      secondaryPump(tripTime = 1e12),
      dhrs(DHRS_time = 1e12));
  end MSRRuhxNominalTrimThermalSSNoTrips;

  // R9 loop trim: 1 pcm sinusoidal perturbation for frequency-response identification.
  model MSRRuhxNominalTrim9R
    extends R9MSRRuhx(
      perturbationAmplitudePcm = 1);
  end MSRRuhxNominalTrim9R;

  // R9 trim with all trips disabled.
  model MSRRuhxNominalTrim9RNoTrips
    extends R9MSRRuhx(
      perturbationAmplitudePcm = 1,
      primaryPump(tripTime = 1e12),
      secondaryPump(tripTime = 1e12),
      dhrs(DHRS_time = 1e12),
      msre9r(regionTripTime = {1e12, 1e12, 1e12, 1e12}));
  end MSRRuhxNominalTrim9RNoTrips;

  // R9 trim with the LOOP thermal components (heatExchanger, uhx, dhrs, and
  // the four loop pipes) initialized at steady state (SteadyState initMode).
  // rev021-B4 (TASK-20260920-01 P9): deliberately NOT a full steady-state
  // core init - the nine core FuelChannels R1..R9 keep their DEFAULT
  // FixedStart setpoint initialization (the trimmed point's thermal
  // derivatives are ~0 there) and the upper-plenum MixingPot has no
  // initMode at all (always T = T_0, see
  // SMD_MSR_Modelica.HeatTransport.MixingPot); propagating SteadyState
  // would require a MixingPot initMode and would change these trim
  // wrappers' initialization numerics, so the claim is scoped to the loop.
  model MSRRuhxNominalTrim9RThermalSS
    extends R9MSRRuhx(
      perturbationAmplitudePcm = 1,
      heatExchanger(initMode = SMD_MSR_Modelica.Units.InitMode.SteadyState),
      uhx(initMode = SMD_MSR_Modelica.Units.InitMode.SteadyState),
      pipeHXtoUHX(initMode = SMD_MSR_Modelica.Units.InitMode.SteadyState),
      pipeUHXtoHX(initMode = SMD_MSR_Modelica.Units.InitMode.SteadyState),
      dhrs(initMode = SMD_MSR_Modelica.Units.InitMode.SteadyState),
      pipeDHRStoHX(initMode = SMD_MSR_Modelica.Units.InitMode.SteadyState),
      pipeHXtoCore(initMode = SMD_MSR_Modelica.Units.InitMode.SteadyState),
      pipeCoreToDHRS(initMode = SMD_MSR_Modelica.Units.InitMode.SteadyState));
  end MSRRuhxNominalTrim9RThermalSS;

  model MSRRuhxNominalTrim9RThermalSSNoTrips
    extends MSRRuhxNominalTrim9RThermalSS(
      primaryPump(tripTime = 1e12),
      secondaryPump(tripTime = 1e12),
      dhrs(DHRS_time = 1e12),
      msre9r(regionTripTime = {1e12, 1e12, 1e12, 1e12}));
  end MSRRuhxNominalTrim9RThermalSSNoTrips;

  // R1 trip-and-DHRS transient: UHX demand set to zero at t=4000 s with DHRS engaging.
  model MSRRuhxTripThermalSS
    extends MSRRuhxNominalTrimThermalSS(
      numUhxSteps = 2,
      uhxDemandStepTime = {0, 4000},
      uhxDemandAmplitude = {powerLevel * MSRR_PlantData.nominalPower, 0},
      dhrs(
        DHRS_P_Bleed = 0.005 * powerLevel * MSRR_PlantData.nominalPower,
        DHRS_MaxP_Rm = 0.1 * powerLevel * MSRR_PlantData.nominalPower,
        DHRS_time = 4000));
  end MSRRuhxTripThermalSS;

  // R9 trip-and-DHRS transient: UHX demand set to zero at t=4000 s with DHRS engaging.
  model MSRRuhxTrip9RThermalSS
    extends MSRRuhxNominalTrim9RThermalSS(
      numUhxSteps = 2,
      uhxDemandStepTime = {0, 4000},
      uhxDemandAmplitude = {powerLevel * MSRR_PlantData.nominalPower, 0},
      dhrs(
        DHRS_P_Bleed = 0.005 * powerLevel * MSRR_PlantData.nominalPower,
        DHRS_MaxP_Rm = 0.1 * powerLevel * MSRR_PlantData.nominalPower,
        DHRS_time = 4000));
  end MSRRuhxTrip9RThermalSS;

  /* Startup-to-criticality scenario (R1 core).
     Drives the reactor from cold sub-critical to near-criticality
     through a sequence of 21 external reactivity insertions and 4 pulsed source
     bursts. Pump ramps up in three stages to full flow. UHX demand is zero
     (no heat removal) throughout the approach to criticality. */
  model MSRRstartUpCriticality
    parameter SMD_MSR_Modelica.Units.NeutronEmissionRate startupSourceStrength = 1E8
      "External neutron source used during startup [n/s]";
    parameter SMD_MSR_Modelica.Units.Temperature startupFuelNode1SetPoint = 570.0000000000202
      "0-power fuel-node-1 setpoint from core/init/setpoints_1r.csv";
    parameter SMD_MSR_Modelica.Units.Temperature startupFuelNode2SetPoint = 570.0000000000202
      "0-power fuel-node-2 setpoint from core/init/setpoints_1r.csv";
    parameter SMD_MSR_Modelica.Units.Temperature startupGraphiteSetPoint = 570.00000000002
      "0-power graphite setpoint from core/init/setpoints_1r.csv";
    parameter Integer startupSourceSteps(min = 1) = 8;
    parameter SMD_MSR_Modelica.Units.InitiationTime startupSourceStepTime[startupSourceSteps] = {
      0, 40200, 43200, 65400, 68400, 83400, 86400, 101400
    };
    parameter SMD_MSR_Modelica.Units.NeutronEmissionRate startupSourceAmplitude[startupSourceSteps] = {
      startupSourceStrength, 0, startupSourceStrength, 0, startupSourceStrength, 0, startupSourceStrength, 0
    };
    parameter SMD_MSR_Modelica.Units.InitiationTime startupReactivityStepTime[29] = {
      -1, 3600, 7200, 10800, 14400, 18000, 21600, 25200, 28800, 32400, 36000,
      39600, 43200, 46800, 50400, 54000, 57600, 61200, 64800, 68400, 72000,
      75600, 79200, 82800, 86400, 90000, 93600, 97200, 100800
    };
    parameter Real startupReactivityAmplitudePcm[29] = {
      -3500, -2000, -1000, -700, -500, -350, -200, -100, -50, -20, -5, 0,
      -350, -200, -100, -50, -20, -5, 0, -100, -50, -20, -5, 0,
      -100, -50, -20, -5, 0
    };
    // Primary-pump startup schedule (bound into primaryPump below; also the
    // flow stages that reference the staircase).
    parameter SMD_MSR_Modelica.Units.FlowFraction startupPumpFreeConvFF = 0.01
      "Free-convection flow fraction before the first pump stage";
    parameter SMD_MSR_Modelica.Units.FlowFraction startupPumpRampUpTo[3] = {0.25, 0.50, 1.0}
      "Pump-stage target flow fractions";
    parameter SMD_MSR_Modelica.Units.InitiationTime startupPumpRampUpTime[3] = {50400, 72000, 90000}
      "Pump-stage start times [s] (each staircase step at or after a stage time belongs to that stage)";
    // Physics review 2026-09-27: with the circulating-fuel compensation frozen
    // at nominal flow (mPKE rho_0nom), the core is more reactive at reduced
    // pump flow by loss(FF = 1) - loss(FF). The staircase amplitudes are
    // therefore measured from the critical position AT THE PUMP FLOW IN
    // EFFECT at each step - a control-rod program re-referenced at every
    // pump stage - so "0 pcm" is still "just critical" at every flow. The
    // offsets come from this core's own kinetics data and transit times.
    parameter Boolean flowReferencedStaircase = true
      "true: staircase amplitudes relative to the flow-dependent critical position (commanded external reactivity = amplitude - (loss(1) - loss(FF_stage))); false: amplitudes relative to the nominal-flow critical position";
    final parameter Real startupStageFlow[4] = cat(1, {startupPumpFreeConvFF}, startupPumpRampUpTo)
      "Flow fraction of each pump stage";
    final parameter Real startupStageLossPcm[4] = {MSRR.Functions.circulationLossPcm(MSRR_PlantData.Kinetics.beta, MSRR_PlantData.Kinetics.lambda, core1R.nomTauCore, core1R.nomTauLoop, startupStageFlow[k]) for k in 1:4}
      "Circulation loss at each pump stage [pcm]";
    final parameter Real startupStaircaseOffsetPcm[29] = {(if flowReferencedStaircase then -(startupStageLossPcm[4] - startupStageLossPcm[1 + sum({(if startupReactivityStepTime[i] >= startupPumpRampUpTime[k] then 1 else 0) for k in 1:3})]) else 0) for i in 1:29}
      "Flow-reference offset added to each staircase step [pcm] (0 in the final full-flow stage)";
    extends R1MSRRuhx(
      powerLevel = 0,
      perturbationAmplitudePcm = 0,
      fuelTempSetPointNode1 = startupFuelNode1SetPoint,
      fuelTempSetPointNode2 = startupFuelNode2SetPoint,
      graphiteTempSetPoint = startupGraphiteSetPoint,
      heatLossEnabled = true,
      heatLossTinf = startupGraphiteSetPoint,
      core1R(
        nFloor = 0,
        nFloorDuringForcing = 0,
        nFloorSwitchTime = 1e100,
        numSourceSteps = startupSourceSteps,
        sourceStepTime = startupSourceStepTime,
        sourceAmplitude = startupSourceAmplitude),
      heatExchanger(
        TpIn_0 = startupGraphiteSetPoint,
        TpOut_0 = startupGraphiteSetPoint,
        TsIn_0 = startupGraphiteSetPoint,
        TsOut_0 = startupGraphiteSetPoint,
        Tinf = startupGraphiteSetPoint),
      uhx(Tp_0 = startupGraphiteSetPoint),
      pipeHXtoUHX(T_0 = startupGraphiteSetPoint, Tinf = startupGraphiteSetPoint),
      pipeUHXtoHX(T_0 = startupGraphiteSetPoint, Tinf = startupGraphiteSetPoint),
      dhrs(T_0 = startupGraphiteSetPoint, Tinf = startupGraphiteSetPoint),
      pipeDHRStoHX(T_0 = startupGraphiteSetPoint, Tinf = startupGraphiteSetPoint),
      pipeHXtoCore(T_0 = startupGraphiteSetPoint, Tinf = startupGraphiteSetPoint),
      pipeCoreToDHRS(T_0 = startupGraphiteSetPoint, Tinf = startupGraphiteSetPoint),
      primaryPump(
        numRampUp = 3,
        rampUpK = {50, 50, 50},
        rampUpTo = startupPumpRampUpTo,
        rampUpTime = startupPumpRampUpTime,
        tripK = 50,
        tripTime = 10800000,
        freeConvFF = startupPumpFreeConvFF),
      numExternalReactivitySteps = 29,
      externalReactivityStepTime = startupReactivityStepTime,
      externalReactivityAmplitude = startupReactivityAmplitudePcm + startupStaircaseOffsetPcm,
      numUhxSteps = 1,
      uhxDemandStepTime = {0},
      uhxDemandAmplitude = {0});
  end MSRRstartUpCriticality;

  /* Startup-to-criticality scenario for the nine-region core.
     Same protocol as MSRRstartUpCriticality but using R9MSRRuhx / MSRR9R. */
  model MSRRstartUpCriticality9R
    parameter SMD_MSR_Modelica.Units.NeutronEmissionRate startupSourceStrength = 1E8
      "External neutron source used during startup [n/s]";
    // Physics review 2026-09-27 B1.4: the 9R startup references are the
    // isothermal zero-power critical reference, 570 degC, as for the 1R
    // startup (the former 552.835 degC values matched no setpoint table:
    // setpoints_9r.csv gives 570.04 degC at 1e-5 MW), so the 1R and 9R
    // startup figures share one reference temperature and trench Tinf.
    parameter SMD_MSR_Modelica.Units.Temperature startupFuelNode1SetPoint = 570.0
      "0-power fuel-node-1 setpoint: the 570 degC isothermal zero-power critical reference";
    parameter SMD_MSR_Modelica.Units.Temperature startupFuelNode2SetPoint = 570.0
      "0-power fuel-node-2 setpoint: the 570 degC isothermal zero-power critical reference";
    parameter SMD_MSR_Modelica.Units.Temperature startupGraphiteSetPoint = 570.0
      "0-power graphite setpoint: the 570 degC isothermal zero-power critical reference";
    parameter Integer startupSourceSteps(min = 1) = 8;
    parameter SMD_MSR_Modelica.Units.InitiationTime startupSourceStepTime[startupSourceSteps] = {
      0, 40200, 43200, 65400, 68400, 83400, 86400, 101400
    };
    parameter SMD_MSR_Modelica.Units.NeutronEmissionRate startupSourceAmplitude[startupSourceSteps] = {
      startupSourceStrength, 0, startupSourceStrength, 0, startupSourceStrength, 0, startupSourceStrength, 0
    };
    parameter SMD_MSR_Modelica.Units.InitiationTime startupReactivityStepTime[29] = {
      -1, 3600, 7200, 10800, 14400, 18000, 21600, 25200, 28800, 32400, 36000,
      39600, 43200, 46800, 50400, 54000, 57600, 61200, 64800, 68400, 72000,
      75600, 79200, 82800, 86400, 90000, 93600, 97200, 100800
    };
    parameter Real startupReactivityAmplitudePcm[29] = {
      -3500, -2000, -1000, -700, -500, -350, -200, -100, -50, -20, -5, 0,
      -350, -200, -100, -50, -20, -5, 0, -100, -50, -20, -5, 0,
      -100, -50, -20, -5, 0
    };
    // Primary-pump startup schedule (bound into primaryPump below; also the
    // flow stages that reference the staircase).
    parameter SMD_MSR_Modelica.Units.FlowFraction startupPumpFreeConvFF = 0.01
      "Free-convection flow fraction before the first pump stage";
    parameter SMD_MSR_Modelica.Units.FlowFraction startupPumpRampUpTo[3] = {0.25, 0.50, 1.0}
      "Pump-stage target flow fractions";
    parameter SMD_MSR_Modelica.Units.InitiationTime startupPumpRampUpTime[3] = {50400, 72000, 90000}
      "Pump-stage start times [s] (each staircase step at or after a stage time belongs to that stage)";
    // Physics review 2026-09-27: with the circulating-fuel compensation frozen
    // at nominal flow (mPKE rho_0nom), the core is more reactive at reduced
    // pump flow by loss(FF = 1) - loss(FF). The staircase amplitudes are
    // therefore measured from the critical position AT THE PUMP FLOW IN
    // EFFECT at each step - a control-rod program re-referenced at every
    // pump stage - so "0 pcm" is still "just critical" at every flow. The
    // offsets come from this core's own kinetics data and transit times.
    parameter Boolean flowReferencedStaircase = true
      "true: staircase amplitudes relative to the flow-dependent critical position (commanded external reactivity = amplitude - (loss(1) - loss(FF_stage))); false: amplitudes relative to the nominal-flow critical position";
    final parameter Real startupStageFlow[4] = cat(1, {startupPumpFreeConvFF}, startupPumpRampUpTo)
      "Flow fraction of each pump stage";
    final parameter Real startupStageLossPcm[4] = {MSRR.Functions.circulationLossPcm(MSRR_PlantData.Kinetics.beta, MSRR_PlantData.Kinetics.lambda, msre9r.nomTauCore, msre9r.nomTauLoop, startupStageFlow[k]) for k in 1:4}
      "Circulation loss at each pump stage [pcm]";
    final parameter Real startupStaircaseOffsetPcm[29] = {(if flowReferencedStaircase then -(startupStageLossPcm[4] - startupStageLossPcm[1 + sum({(if startupReactivityStepTime[i] >= startupPumpRampUpTime[k] then 1 else 0) for k in 1:3})]) else 0) for i in 1:29}
      "Flow-reference offset added to each staircase step [pcm] (0 in the final full-flow stage)";
    extends R9MSRRuhx(
      powerLevel = 0,
      perturbationAmplitudePcm = 0,
      fuelTempSetPointNode1 = startupFuelNode1SetPoint,
      fuelTempSetPointNode2 = startupFuelNode2SetPoint,
      graphiteTempSetPoint = startupGraphiteSetPoint,
      heatLossEnabled = true,
      heatLossTinf = startupGraphiteSetPoint,
      TF1_0_regions = fill(startupFuelNode1SetPoint, 9),
      TF2_0_regions = fill(startupFuelNode2SetPoint, 9),
      TG_0_regions = fill(startupGraphiteSetPoint, 9),
      Tmix_0 = startupFuelNode2SetPoint,
      msre9r(
        nFloor = 0,
        nFloorDuringForcing = 0,
        nFloorSwitchTime = 1e100,
        numSourceSteps = startupSourceSteps,
        sourceStepTime = startupSourceStepTime,
        sourceAmplitude = startupSourceAmplitude),
      heatExchanger(
        TpIn_0 = startupGraphiteSetPoint,
        TpOut_0 = startupGraphiteSetPoint,
        TsIn_0 = startupGraphiteSetPoint,
        TsOut_0 = startupGraphiteSetPoint,
        Tinf = startupGraphiteSetPoint),
      uhx(Tp_0 = startupGraphiteSetPoint),
      pipeHXtoUHX(T_0 = startupGraphiteSetPoint, Tinf = startupGraphiteSetPoint),
      pipeUHXtoHX(T_0 = startupGraphiteSetPoint, Tinf = startupGraphiteSetPoint),
      dhrs(T_0 = startupGraphiteSetPoint, Tinf = startupGraphiteSetPoint),
      pipeDHRStoHX(T_0 = startupGraphiteSetPoint, Tinf = startupGraphiteSetPoint),
      pipeHXtoCore(T_0 = startupGraphiteSetPoint, Tinf = startupGraphiteSetPoint),
      pipeCoreToDHRS(T_0 = startupGraphiteSetPoint, Tinf = startupGraphiteSetPoint),
      primaryPump(
        numRampUp = 3,
        rampUpK = {50, 50, 50},
        rampUpTo = startupPumpRampUpTo,
        rampUpTime = startupPumpRampUpTime,
        tripK = 50,
        tripTime = 10800000,
        freeConvFF = startupPumpFreeConvFF),
      numExternalReactivitySteps = 29,
      externalReactivityStepTime = startupReactivityStepTime,
      externalReactivityAmplitude = startupReactivityAmplitudePcm + startupStaircaseOffsetPcm,
      numUhxSteps = 1,
      uhxDemandStepTime = {0},
      uhxDemandAmplitude = {0});
  end MSRRstartUpCriticality9R;

  /* Extends MSRRstartUpCriticality to add a 9-step UHX demand ramp after
     criticality. The control input here is total UHX heat-removal demand,
     but the startup objective is approximately 100 kW of fission power.
     The startup-to-100kW plotting workflow interprets the demand as a
     fission-equivalent target using an assumed 95% fission fraction, so the
     final demand is set to 0.1 MW / 0.95 ~= 0.107 MW instead of exactly
     0.1 MW. */
  model MSRRstartUpTo100kW
    parameter SMD_MSR_Modelica.Units.Power uhxDemandFinal = 0.1*1E6/0.95
      "Final UHX demand [W] for ~100 kW fission target with 95% fission fraction";
    parameter SMD_MSR_Modelica.Units.InitiationTime demandRampStart = 102600
      "Start of final UHX-demand ramp [s]";
    parameter SMD_MSR_Modelica.Units.InitiationTime demandRampDeltaT = 3600
      "Time between UHX-demand steps [s]; 7*demandRampDeltaT = 7 h";
    parameter Real demandRampFrac[8] = {0.03, 0.08, 0.15, 0.26, 0.42, 0.62, 0.82, 1.0}
      "Fractions of uhxDemandFinal used during final ramp";
    extends MSRRstartUpCriticality(
      numUhxSteps = 9,
      uhxDemandStepTime = {
        0,
        demandRampStart, demandRampStart + demandRampDeltaT,
        demandRampStart + 2*demandRampDeltaT, demandRampStart + 3*demandRampDeltaT,
        demandRampStart + 4*demandRampDeltaT, demandRampStart + 5*demandRampDeltaT,
        demandRampStart + 6*demandRampDeltaT, demandRampStart + 7*demandRampDeltaT
      },
      uhxDemandAmplitude = {
        0,
        uhxDemandFinal*demandRampFrac[1], uhxDemandFinal*demandRampFrac[2],
        uhxDemandFinal*demandRampFrac[3], uhxDemandFinal*demandRampFrac[4],
        uhxDemandFinal*demandRampFrac[5], uhxDemandFinal*demandRampFrac[6],
        uhxDemandFinal*demandRampFrac[7], uhxDemandFinal*demandRampFrac[8]
      });
  end MSRRstartUpTo100kW;

  model MSRRstartUpTo100kW9R
    parameter SMD_MSR_Modelica.Units.Power uhxDemandFinal = 0.1*1E6/0.95
      "Final UHX demand [W] for ~100 kW fission target with 95% fission fraction";
    parameter SMD_MSR_Modelica.Units.InitiationTime demandRampStart = 102600
      "Start of final UHX-demand ramp [s]";
    parameter SMD_MSR_Modelica.Units.InitiationTime demandRampDeltaT = 3600
      "Time between UHX-demand steps [s]; 7*demandRampDeltaT = 7 h";
    parameter Real demandRampFrac[8] = {0.03, 0.08, 0.15, 0.26, 0.42, 0.62, 0.82, 1.0}
      "Fractions of uhxDemandFinal used during final ramp";
    extends MSRRstartUpCriticality9R(
      numUhxSteps = 9,
      uhxDemandStepTime = {
        0,
        demandRampStart, demandRampStart + demandRampDeltaT,
        demandRampStart + 2*demandRampDeltaT, demandRampStart + 3*demandRampDeltaT,
        demandRampStart + 4*demandRampDeltaT, demandRampStart + 5*demandRampDeltaT,
        demandRampStart + 6*demandRampDeltaT, demandRampStart + 7*demandRampDeltaT
      },
      uhxDemandAmplitude = {
        0,
        uhxDemandFinal*demandRampFrac[1], uhxDemandFinal*demandRampFrac[2],
        uhxDemandFinal*demandRampFrac[3], uhxDemandFinal*demandRampFrac[4],
        uhxDemandFinal*demandRampFrac[5], uhxDemandFinal*demandRampFrac[6],
        uhxDemandFinal*demandRampFrac[7], uhxDemandFinal*demandRampFrac[8]
      });
  end MSRRstartUpTo100kW9R;

  model MSRRstartUpTo1MW
    parameter SMD_MSR_Modelica.Units.Power uhxDemandFinal = 1E6
      "Final UHX demand [W] (~1 MW fission target)";
    parameter SMD_MSR_Modelica.Units.InitiationTime demandRampStart = 102600
      "Start of final UHX-demand ramp [s]";
    parameter SMD_MSR_Modelica.Units.InitiationTime demandRampDeltaT = 3600
      "Time between UHX-demand steps [s]; 7*demandRampDeltaT = 7 h";
    parameter Real demandRampFrac[8] = {0.03, 0.08, 0.15, 0.26, 0.42, 0.62, 0.82, 1.0}
      "Fractions of uhxDemandFinal used during final ramp";
    extends MSRRstartUpCriticality(
      numUhxSteps = 9,
      uhxDemandStepTime = {
        0,
        demandRampStart, demandRampStart + demandRampDeltaT,
        demandRampStart + 2*demandRampDeltaT, demandRampStart + 3*demandRampDeltaT,
        demandRampStart + 4*demandRampDeltaT, demandRampStart + 5*demandRampDeltaT,
        demandRampStart + 6*demandRampDeltaT, demandRampStart + 7*demandRampDeltaT
      },
      uhxDemandAmplitude = {
        0,
        uhxDemandFinal*demandRampFrac[1], uhxDemandFinal*demandRampFrac[2],
        uhxDemandFinal*demandRampFrac[3], uhxDemandFinal*demandRampFrac[4],
        uhxDemandFinal*demandRampFrac[5], uhxDemandFinal*demandRampFrac[6],
        uhxDemandFinal*demandRampFrac[7], uhxDemandFinal*demandRampFrac[8]
      });
  end MSRRstartUpTo1MW;

  model MSRRstartUpTo1MW9R
    parameter SMD_MSR_Modelica.Units.Power uhxDemandFinal = 1E6
      "Final UHX demand [W] (~1 MW fission target)";
    parameter SMD_MSR_Modelica.Units.InitiationTime demandRampStart = 102600
      "Start of final UHX-demand ramp [s]";
    parameter SMD_MSR_Modelica.Units.InitiationTime demandRampDeltaT = 3600
      "Time between UHX-demand steps [s]; 7*demandRampDeltaT = 7 h";
    parameter Real demandRampFrac[8] = {0.03, 0.08, 0.15, 0.26, 0.42, 0.62, 0.82, 1.0}
      "Fractions of uhxDemandFinal used during final ramp";
    extends MSRRstartUpCriticality9R(
      numUhxSteps = 9,
      uhxDemandStepTime = {
        0,
        demandRampStart, demandRampStart + demandRampDeltaT,
        demandRampStart + 2*demandRampDeltaT, demandRampStart + 3*demandRampDeltaT,
        demandRampStart + 4*demandRampDeltaT, demandRampStart + 5*demandRampDeltaT,
        demandRampStart + 6*demandRampDeltaT, demandRampStart + 7*demandRampDeltaT
      },
      uhxDemandAmplitude = {
        0,
        uhxDemandFinal*demandRampFrac[1], uhxDemandFinal*demandRampFrac[2],
        uhxDemandFinal*demandRampFrac[3], uhxDemandFinal*demandRampFrac[4],
        uhxDemandFinal*demandRampFrac[5], uhxDemandFinal*demandRampFrac[6],
        uhxDemandFinal*demandRampFrac[7], uhxDemandFinal*demandRampFrac[8]
      });
  end MSRRstartUpTo1MW9R;

  /* Quick diagnostic case: source-assisted near-critical hold.
     Source is on from t=0 to 1 h, then off; external reactivity is held
     at the startup criticality value (-72 pcm) throughout. */
  model MSRRstartUpSource72QuickTest
    extends MSRRstartUpCriticality(
      startupSourceSteps = 2,
      startupSourceStepTime = {-1, 3600},
      startupSourceAmplitude = {startupSourceStrength, 0},
      numExternalReactivitySteps = 1,
      externalReactivityStepTime = {-1},
      externalReactivityAmplitude = {-72},
      primaryPump(
        numRampUp = 1,
        rampUpK = {100},
        rampUpTo = {1},
        rampUpTime = {0},
        tripTime = 1e12),
      secondaryPump(
        numRampUp = 1,
        rampUpK = {100},
        rampUpTo = {1},
        rampUpTime = {0},
        tripTime = 1e12),
      dhrs(DHRS_time = 1e12));
  end MSRRstartUpSource72QuickTest;

  package Transients
    package R1fullSteps
      model R1MSRR2dol
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotFuel = MSRR_PlantData.PrimaryLoop.vdotFuel;
        parameter SMD_MSR_Modelica.Units.Density rhoFuel = MSRR_PlantData.Materials.rhoFuel;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpFuel = MSRR_PlantData.Materials.cpFuel;
        parameter SMD_MSR_Modelica.Units.Conductivity kFuel = MSRR_PlantData.Materials.kFuel;
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotCoolant = MSRR_PlantData.SecondaryLoop.vdotCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoCoolant = MSRR_PlantData.Materials.rhoCoolant;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpCoolant = MSRR_PlantData.Materials.cpCoolant;
        parameter SMD_MSR_Modelica.Units.Conductivity kCoolant = MSRR_PlantData.Materials.kCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoGrap = MSRR_PlantData.Materials.rhoGrap;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpGrap = MSRR_PlantData.Materials.cpGrap;
        parameter SMD_MSR_Modelica.Units.Density rhoHXtube = MSRR_PlantData.Materials.rhoHXtube;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpHXtube = MSRR_PlantData.Materials.cpHXtube;
        MSRR.Components.HeatExchanger heatExchanger(vol_P = MSRR_PlantData.SecondaryLoop.hxVolP, vol_T = MSRR_PlantData.SecondaryLoop.hxVolT, vol_S = MSRR_PlantData.SecondaryLoop.hxVolS, rhoP = rhoFuel, rhoT = rhoHXtube, rhoS = rhoCoolant, cP_P = scpFuel, cP_T = scpHXtube, cP_S = scpCoolant, VdotPnom = volDotFuel, VdotSnom = volDotCoolant, hApNom = MSRR_PlantData.SecondaryLoop.hApNom, hAsNom = MSRR_PlantData.SecondaryLoop.hAsNom, hAExp = 0.33, EnableRad = false, AcShell = MSRR_PlantData.SecondaryLoop.HX.AcShell, AcTube = MSRR_PlantData.SecondaryLoop.HX.AcTube, ArShell = MSRR_PlantData.SecondaryLoop.HX.ArShell, Kp = kFuel, Ks = kCoolant, L_shell = MSRR_PlantData.SecondaryLoop.HX.L_shell, L_tube = MSRR_PlantData.SecondaryLoop.HX.L_tube, e = MSRR_PlantData.SecondaryLoop.HX.e, Tinf = MSRR_PlantData.SecondaryLoop.HX.Tinf, TpIn_0 = MSRR_PlantData.SecondaryLoop.HX.TpIn_0, TpOut_0 = MSRR_PlantData.SecondaryLoop.HX.TpOut_0, TsIn_0 = MSRR_PlantData.SecondaryLoop.HX.TsIn_0, TsOut_0 = MSRR_PlantData.SecondaryLoop.HX.TsOut_0) annotation(
          Placement(transformation(origin = {-28.2, 32.6}, extent = {{-50.8, -25.4}, {76.2, 25.4}})));
        SMD_MSR_Modelica.HeatTransport.UHX uhx(Tp_0 = MSRR_PlantData.SecondaryLoop.UHX.Tp_0, vDot = volDotCoolant, cP = scpCoolant, rho = rhoCoolant, vol = MSRR_PlantData.SecondaryLoop.uhxVol) annotation(
          Placement(transformation(origin = {77.462, -70.3463}, extent = {{-54.8618, -41.1463}, {27.4309, 41.1463}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoUHX(vol = MSRR_PlantData.SecondaryLoop.pipeHXtoUHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.T_0) annotation(
          Placement(transformation(origin = {-28.8, -81.0667}, extent = {{-27.2, 9.06667}, {18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeUHXtoHX(vol = MSRR_PlantData.SecondaryLoop.pipeUHXtoHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.T_0) annotation(
          Placement(transformation(origin = {93.2, 7.73333}, extent = {{27.2, 9.06667}, {-18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.DHRS dhrs(vol = MSRR_PlantData.PrimaryLoop.volLoop[2], rho = rhoFuel, cP = scpFuel, vDotNom = volDotFuel, DHRS_tK = MSRR_PlantData.PrimaryLoop.dhrsTK, DHRS_MaxP_Rm(displayUnit = "MW") = MSRR_PlantData.PrimaryLoop.dhrsMaxRemove, DHRS_P_Bleed = MSRR_PlantData.PrimaryLoop.dhrsBleed, DHRS_time = MSRR_PlantData.PrimaryLoop.dhrsEngageTime, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.Ac, L = MSRR_PlantData.PrimaryLoop.L, Ar = MSRR_PlantData.PrimaryLoop.Ar, EnableRad = false, e = MSRR_PlantData.PrimaryLoop.e, Tinf = MSRR_PlantData.PrimaryLoop.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.T_0) annotation(
          Placement(transformation(origin = {-267, 32.6667}, extent = {{-49.3333, -24.6667}, {37, 49.3333}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeDHRStoHX(vol = MSRR_PlantData.PrimaryLoop.volLoop[3], volFracNode = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ac, L = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ar, e = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.T_0) annotation(
          Placement(transformation(origin = {-135.6, 23.2}, extent = {{-38.4, 12.8}, {25.6, 51.2}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoCore(vol = MSRR_PlantData.PrimaryLoop.volLoop[5], volFracNode = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ac, L = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ar, e = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.T_0) annotation(
          Placement(transformation(origin = {-176.4, -61.6}, extent = {{-37.2, 12.4}, {24.8, 49.6}}, rotation = 180)));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeCoreToDHRS(vol = MSRR_PlantData.PrimaryLoop.volLoop[1], volFracNode = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ac, L = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ar, e = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.T_0) annotation(
          Placement(transformation(origin = {-382.4, -32.8}, extent = {{-39.6, 13.2}, {26.4, 52.8}})));
        MSRR.Components.MSRR1R core1R(numGroups = MSRR_PlantData.Kinetics.numGroups, EnableRad = false, lambda = MSRR_PlantData.Kinetics.lambda, beta = MSRR_PlantData.Kinetics.beta, LAMBDA = MSRR_PlantData.Kinetics.LAMBDA, a_F = MSRR_PlantData.Kinetics.a_F, a_G = MSRR_PlantData.Kinetics.a_G, n_0 = 1, rho_fuel = rhoFuel, rho_grap = rhoGrap, cP_fuel = scpFuel, cP_grap = scpGrap, volDotFuel = volDotFuel, Tinf = MSRR_PlantData.Core1R.TF1, kFuel = kFuel, TF1_0 = MSRR_PlantData.Core1R.TF1, TF2_0 = MSRR_PlantData.Core1R.TF2, TG_0 = MSRR_PlantData.Core1R.TG) annotation(
          Placement(transformation(origin = {-519.334, -66.0002}, extent = {{-110, -89.9998}, {-49.9999, -29.9999}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
          Placement(transformation(origin = {-27, -161}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.HeatTransport.Pump primaryPump(numRampUp = MSRR_PlantData.Pumps.primaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.primaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.primaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.primaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {-554, 158}, extent = {{-24, -32}, {24, 16}})));
        SMD_MSR_Modelica.HeatTransport.Pump secondaryPump(numRampUp = MSRR_PlantData.Pumps.secondaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.secondaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.secondaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.secondaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {175, 126.667}, extent = {{-23, -30.6667}, {23, 15.3333}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantReal uhxDemand(magnitude = MSRR_PlantData.nominalPower) annotation(
          Placement(transformation(origin = {43, -209}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.Signals.TimeDependent.Stepper externalReact(numSteps = 2, stepTime = {0, 4000}, amplitude = {0, 1178.138}) annotation(
          Placement(transformation(origin = {-708.198, 63.8017}, extent = {{-29.7983, -29.7983}, {29.7983, 29.7983}})));
      equation
        connect(heatExchanger.T_out_sFluid, pipeHXtoUHX.PiTemp_IN) annotation(
          Line(points = {{-58, 20}, {-94, 20}, {-94, -58}, {-48, -58}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeHXtoUHX.PiTempOut, uhx.tempIn) annotation(
          Line(points = {{-18, -58}, {23, -58}, {23, -70}}, color = {204, 0, 0}, thickness = 1));
        connect(uhx.tempOut, pipeUHXtoHX.PiTemp_IN) annotation(
          Line(points = {{77, -70}, {140, -70}, {140, 30}, {113, 30}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeUHXtoHX.PiTempOut, heatExchanger.T_in_sFluid) annotation(
          Line(points = {{83, 30}, {60.5, 30}, {60.5, 20}, {52, 20}}, color = {204, 0, 0}, thickness = 1));
        connect(dhrs.tempOut, pipeDHRStoHX.PiTemp_IN) annotation(
          Line(points = {{-247, 45}, {-205.5, 45}, {-205.5, 55}, {-163, 55}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeDHRStoHX.PiTempOut, heatExchanger.T_in_pFluid) annotation(
          Line(points = {{-121, 55}, {-95, 55}, {-95, 45}, {-58, 45}}, color = {204, 0, 0}, thickness = 1));
        connect(heatExchanger.T_out_pFluid, pipeHXtoCore.PiTemp_IN) annotation(
          Line(points = {{52, 45}, {156, 45}, {156, -93}, {-150, -93}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeCoreToDHRS.PiTempOut, dhrs.tempIn) annotation(
          Line(points = {{-367, 0}, {-328.5, 0}, {-328.5, 45}, {-300, 45}}, color = {204, 0, 0}, thickness = 1));
        connect(core1R.tempOut, pipeCoreToDHRS.PiTemp_IN) annotation(
          Line(points = {{-659, -166}, {-409.5, -166}, {-409.5, 0}, {-411, 0}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeHXtoCore.PiTempOut, core1R.tempIn) annotation(
          Line(points = {{-191, -93}, {-390, -93}, {-390, -205}, {-699, -205}}, color = {204, 0, 0}, thickness = 1));
        connect(core1R.volumetricPowerOut, pipeCoreToDHRS.PiDecay_Heat) annotation(
          Line(points = {{-659, -205}, {-448, -205}, {-448, 10}, {-411, 10}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, dhrs.pDecay) annotation(
          Line(points = {{-659, -205}, {-448, -205}, {-448, 70}, {-300, 70}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, pipeDHRStoHX.PiDecay_Heat) annotation(
          Line(points = {{-659, -205}, {-659, 98}, {-163, 98}, {-163, 65}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, heatExchanger.P_decay) annotation(
          Line(points = {{-659, -205}, {-659, 114}, {-3, 114}, {-3, 54}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, pipeHXtoCore.PiDecay_Heat) annotation(
          Line(points = {{-659, -205}, {-353.5, -205}, {-353.5, -102}, {-150, -102}}, color = {220, 138, 221}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeHXtoUHX.PiDecay_Heat) annotation(
          Line(points = {{-29, -145}, {-74, -145}, {-74, -52}, {-48, -52}}, color = {220, 138, 221}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeUHXtoHX.PiDecay_Heat) annotation(
          Line(points = {{-29, -145}, {148, -145}, {148, 37}, {113, 37}}, color = {220, 138, 221}, thickness = 1));
        connect(primaryPump.flowFrac, core1R.flowFracIn) annotation(
          Line(points = {{-554, 126}, {-554, 20}, {-699, 20}, {-699, -166}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeCoreToDHRS.flowFrac) annotation(
          Line(points = {{-554, 126}, {-468, 126}, {-468, -10}, {-411, -10}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, dhrs.flowFrac) annotation(
          Line(points = {{-554, 126}, {-328, 126}, {-328, 20}, {-300, 20}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeDHRStoHX.flowFrac) annotation(
          Line(points = {{-554, 126}, {-206, 126}, {-206, 46}, {-163, 46}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, heatExchanger.primaryFF) annotation(
          Line(points = {{-554, 126}, {-554, 92}, {-45, 92}, {-45, 54}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeHXtoCore.flowFrac) annotation(
          Line(points = {{-554, 126}, {-108, 126}, {-108, -83}, {-150, -83}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeUHXtoHX.flowFrac) annotation(
          Line(points = {{175, 96}, {174, 96}, {174, 24}, {113, 24}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, heatExchanger.secondaryFF) annotation(
          Line(points = {{175, 96}, {175, 98}, {174, 98}, {174, -6}, {40, -6}, {40, 11}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, uhx.flowFrac) annotation(
          Line(points = {{175, 96}, {174, 96}, {174, -43}, {23, -43}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeHXtoUHX.flowFrac) annotation(
          Line(points = {{175, 96}, {175, 98}, {176, 98}, {176, -82}, {-48, -82}, {-48, -65}}, color = {245, 121, 0}, thickness = 1));
        connect(externalReact.step, core1R.realIn) annotation(
          Line(points = {{-706, 74}, {-600, 74}, {-600, -106}}, thickness = 1));
        connect(uhxDemand.realOut, uhx.powDemand) annotation(
          Line(points = {{44, -196}, {36, -196}, {36, -98}}, thickness = 1));
        annotation(
          Diagram(coordinateSystem(extent = {{-760, 180}, {220, -280}}), graphics = {Polygon(points = {{108, 32}, {108, 32}})}));
      end R1MSRR2dol;

      model R1MSRR1dol
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotFuel = MSRR_PlantData.PrimaryLoop.vdotFuel;
        parameter SMD_MSR_Modelica.Units.Density rhoFuel = MSRR_PlantData.Materials.rhoFuel;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpFuel = MSRR_PlantData.Materials.cpFuel;
        parameter SMD_MSR_Modelica.Units.Conductivity kFuel = MSRR_PlantData.Materials.kFuel;
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotCoolant = MSRR_PlantData.SecondaryLoop.vdotCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoCoolant = MSRR_PlantData.Materials.rhoCoolant;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpCoolant = MSRR_PlantData.Materials.cpCoolant;
        parameter SMD_MSR_Modelica.Units.Conductivity kCoolant = MSRR_PlantData.Materials.kCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoGrap = MSRR_PlantData.Materials.rhoGrap;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpGrap = MSRR_PlantData.Materials.cpGrap;
        parameter SMD_MSR_Modelica.Units.Density rhoHXtube = MSRR_PlantData.Materials.rhoHXtube;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpHXtube = MSRR_PlantData.Materials.cpHXtube;
        MSRR.Components.HeatExchanger heatExchanger(vol_P = MSRR_PlantData.SecondaryLoop.hxVolP, vol_T = MSRR_PlantData.SecondaryLoop.hxVolT, vol_S = MSRR_PlantData.SecondaryLoop.hxVolS, rhoP = rhoFuel, rhoT = rhoHXtube, rhoS = rhoCoolant, cP_P = scpFuel, cP_T = scpHXtube, cP_S = scpCoolant, VdotPnom = volDotFuel, VdotSnom = volDotCoolant, hApNom = MSRR_PlantData.SecondaryLoop.hApNom, hAsNom = MSRR_PlantData.SecondaryLoop.hAsNom, hAExp = 0.33, EnableRad = false, AcShell = MSRR_PlantData.SecondaryLoop.HX.AcShell, AcTube = MSRR_PlantData.SecondaryLoop.HX.AcTube, ArShell = MSRR_PlantData.SecondaryLoop.HX.ArShell, Kp = kFuel, Ks = kCoolant, L_shell = MSRR_PlantData.SecondaryLoop.HX.L_shell, L_tube = MSRR_PlantData.SecondaryLoop.HX.L_tube, e = MSRR_PlantData.SecondaryLoop.HX.e, Tinf = MSRR_PlantData.SecondaryLoop.HX.Tinf, TpIn_0 = MSRR_PlantData.SecondaryLoop.HX.TpIn_0, TpOut_0 = MSRR_PlantData.SecondaryLoop.HX.TpOut_0, TsIn_0 = MSRR_PlantData.SecondaryLoop.HX.TsIn_0, TsOut_0 = MSRR_PlantData.SecondaryLoop.HX.TsOut_0) annotation(
          Placement(transformation(origin = {-28.2, 32.6}, extent = {{-50.8, -25.4}, {76.2, 25.4}})));
        SMD_MSR_Modelica.HeatTransport.UHX uhx(Tp_0 = MSRR_PlantData.SecondaryLoop.UHX.Tp_0, vDot = volDotCoolant, cP = scpCoolant, rho = rhoCoolant, vol = MSRR_PlantData.SecondaryLoop.uhxVol) annotation(
          Placement(transformation(origin = {77.462, -70.3463}, extent = {{-54.8618, -41.1463}, {27.4309, 41.1463}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoUHX(vol = MSRR_PlantData.SecondaryLoop.pipeHXtoUHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.T_0) annotation(
          Placement(transformation(origin = {-28.8, -81.0667}, extent = {{-27.2, 9.06667}, {18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeUHXtoHX(vol = MSRR_PlantData.SecondaryLoop.pipeUHXtoHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.T_0) annotation(
          Placement(transformation(origin = {93.2, 7.73333}, extent = {{27.2, 9.06667}, {-18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.DHRS dhrs(vol = MSRR_PlantData.PrimaryLoop.volLoop[2], rho = rhoFuel, cP = scpFuel, vDotNom = volDotFuel, DHRS_tK = MSRR_PlantData.PrimaryLoop.dhrsTK, DHRS_MaxP_Rm(displayUnit = "MW") = MSRR_PlantData.PrimaryLoop.dhrsMaxRemove, DHRS_P_Bleed = MSRR_PlantData.PrimaryLoop.dhrsBleed, DHRS_time = MSRR_PlantData.PrimaryLoop.dhrsEngageTime, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.Ac, L = MSRR_PlantData.PrimaryLoop.L, Ar = MSRR_PlantData.PrimaryLoop.Ar, EnableRad = false, e = MSRR_PlantData.PrimaryLoop.e, Tinf = MSRR_PlantData.PrimaryLoop.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.T_0) annotation(
          Placement(transformation(origin = {-267, 32.6667}, extent = {{-49.3333, -24.6667}, {37, 49.3333}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeDHRStoHX(vol = MSRR_PlantData.PrimaryLoop.volLoop[3], volFracNode = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ac, L = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ar, e = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.T_0) annotation(
          Placement(transformation(origin = {-135.6, 23.2}, extent = {{-38.4, 12.8}, {25.6, 51.2}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoCore(vol = MSRR_PlantData.PrimaryLoop.volLoop[5], volFracNode = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ac, L = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ar, e = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.T_0) annotation(
          Placement(transformation(origin = {-176.4, -61.6}, extent = {{-37.2, 12.4}, {24.8, 49.6}}, rotation = 180)));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeCoreToDHRS(vol = MSRR_PlantData.PrimaryLoop.volLoop[1], volFracNode = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ac, L = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ar, e = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.T_0) annotation(
          Placement(transformation(origin = {-382.4, -32.8}, extent = {{-39.6, 13.2}, {26.4, 52.8}})));
        MSRR.Components.MSRR1R core1R(numGroups = MSRR_PlantData.Kinetics.numGroups, EnableRad = false, lambda = MSRR_PlantData.Kinetics.lambda, beta = MSRR_PlantData.Kinetics.beta, LAMBDA = MSRR_PlantData.Kinetics.LAMBDA, a_F = MSRR_PlantData.Kinetics.a_F, a_G = MSRR_PlantData.Kinetics.a_G, n_0 = 1, rho_fuel = rhoFuel, rho_grap = rhoGrap, cP_fuel = scpFuel, cP_grap = scpGrap, volDotFuel = volDotFuel, Tinf = MSRR_PlantData.Core1R.TF1, kFuel = kFuel, TF1_0 = MSRR_PlantData.Core1R.TF1, TF2_0 = MSRR_PlantData.Core1R.TF2, TG_0 = MSRR_PlantData.Core1R.TG) annotation(
          Placement(transformation(origin = {-519.334, -66.0002}, extent = {{-110, -89.9998}, {-49.9999, -29.9999}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
          Placement(transformation(origin = {-27, -161}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.HeatTransport.Pump primaryPump(numRampUp = MSRR_PlantData.Pumps.primaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.primaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.primaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.primaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {-554, 158}, extent = {{-24, -32}, {24, 16}})));
        SMD_MSR_Modelica.HeatTransport.Pump secondaryPump(numRampUp = MSRR_PlantData.Pumps.secondaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.secondaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.secondaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.secondaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {175, 126.667}, extent = {{-23, -30.6667}, {23, 15.3333}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantReal uhxDemand(magnitude = MSRR_PlantData.nominalPower) annotation(
          Placement(transformation(origin = {43, -209}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.Signals.TimeDependent.Stepper externalReact(numSteps = 2, stepTime = {0, 4000}, amplitude = {0, 604.887}) annotation(
          Placement(transformation(origin = {-708.198, 63.8017}, extent = {{-29.7983, -29.7983}, {29.7983, 29.7983}})));
      equation
        connect(heatExchanger.T_out_sFluid, pipeHXtoUHX.PiTemp_IN) annotation(
          Line(points = {{-58, 20}, {-94, 20}, {-94, -58}, {-48, -58}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeHXtoUHX.PiTempOut, uhx.tempIn) annotation(
          Line(points = {{-18, -58}, {23, -58}, {23, -70}}, color = {204, 0, 0}, thickness = 1));
        connect(uhx.tempOut, pipeUHXtoHX.PiTemp_IN) annotation(
          Line(points = {{77, -70}, {140, -70}, {140, 30}, {113, 30}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeUHXtoHX.PiTempOut, heatExchanger.T_in_sFluid) annotation(
          Line(points = {{83, 30}, {60.5, 30}, {60.5, 20}, {52, 20}}, color = {204, 0, 0}, thickness = 1));
        connect(dhrs.tempOut, pipeDHRStoHX.PiTemp_IN) annotation(
          Line(points = {{-247, 45}, {-205.5, 45}, {-205.5, 55}, {-163, 55}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeDHRStoHX.PiTempOut, heatExchanger.T_in_pFluid) annotation(
          Line(points = {{-121, 55}, {-95, 55}, {-95, 45}, {-58, 45}}, color = {204, 0, 0}, thickness = 1));
        connect(heatExchanger.T_out_pFluid, pipeHXtoCore.PiTemp_IN) annotation(
          Line(points = {{52, 45}, {156, 45}, {156, -93}, {-150, -93}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeCoreToDHRS.PiTempOut, dhrs.tempIn) annotation(
          Line(points = {{-367, 0}, {-328.5, 0}, {-328.5, 45}, {-300, 45}}, color = {204, 0, 0}, thickness = 1));
        connect(core1R.tempOut, pipeCoreToDHRS.PiTemp_IN) annotation(
          Line(points = {{-659, -166}, {-409.5, -166}, {-409.5, 0}, {-411, 0}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeHXtoCore.PiTempOut, core1R.tempIn) annotation(
          Line(points = {{-191, -93}, {-390, -93}, {-390, -205}, {-699, -205}}, color = {204, 0, 0}, thickness = 1));
        connect(core1R.volumetricPowerOut, pipeCoreToDHRS.PiDecay_Heat) annotation(
          Line(points = {{-659, -205}, {-448, -205}, {-448, 10}, {-411, 10}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, dhrs.pDecay) annotation(
          Line(points = {{-659, -205}, {-448, -205}, {-448, 70}, {-300, 70}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, pipeDHRStoHX.PiDecay_Heat) annotation(
          Line(points = {{-659, -205}, {-659, 98}, {-163, 98}, {-163, 65}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, heatExchanger.P_decay) annotation(
          Line(points = {{-659, -205}, {-659, 114}, {-3, 114}, {-3, 54}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, pipeHXtoCore.PiDecay_Heat) annotation(
          Line(points = {{-659, -205}, {-353.5, -205}, {-353.5, -102}, {-150, -102}}, color = {220, 138, 221}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeHXtoUHX.PiDecay_Heat) annotation(
          Line(points = {{-29, -145}, {-74, -145}, {-74, -52}, {-48, -52}}, color = {220, 138, 221}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeUHXtoHX.PiDecay_Heat) annotation(
          Line(points = {{-29, -145}, {148, -145}, {148, 37}, {113, 37}}, color = {220, 138, 221}, thickness = 1));
        connect(primaryPump.flowFrac, core1R.flowFracIn) annotation(
          Line(points = {{-554, 126}, {-554, 20}, {-699, 20}, {-699, -166}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeCoreToDHRS.flowFrac) annotation(
          Line(points = {{-554, 126}, {-468, 126}, {-468, -10}, {-411, -10}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, dhrs.flowFrac) annotation(
          Line(points = {{-554, 126}, {-328, 126}, {-328, 20}, {-300, 20}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeDHRStoHX.flowFrac) annotation(
          Line(points = {{-554, 126}, {-206, 126}, {-206, 46}, {-163, 46}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, heatExchanger.primaryFF) annotation(
          Line(points = {{-554, 126}, {-554, 92}, {-45, 92}, {-45, 54}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeHXtoCore.flowFrac) annotation(
          Line(points = {{-554, 126}, {-108, 126}, {-108, -83}, {-150, -83}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeUHXtoHX.flowFrac) annotation(
          Line(points = {{175, 96}, {174, 96}, {174, 24}, {113, 24}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, heatExchanger.secondaryFF) annotation(
          Line(points = {{175, 96}, {175, 98}, {174, 98}, {174, -6}, {40, -6}, {40, 11}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, uhx.flowFrac) annotation(
          Line(points = {{175, 96}, {174, 96}, {174, -43}, {23, -43}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeHXtoUHX.flowFrac) annotation(
          Line(points = {{175, 96}, {175, 98}, {176, 98}, {176, -82}, {-48, -82}, {-48, -65}}, color = {245, 121, 0}, thickness = 1));
        connect(externalReact.step, core1R.realIn) annotation(
          Line(points = {{-706, 74}, {-600, 74}, {-600, -106}}, thickness = 1));
        connect(uhxDemand.realOut, uhx.powDemand) annotation(
          Line(points = {{44, -196}, {36, -196}, {36, -98}}, thickness = 1));
        annotation(
          Diagram(coordinateSystem(extent = {{-760, 180}, {220, -280}}), graphics = {Polygon(points = {{108, 32}, {108, 32}})}));
      end R1MSRR1dol;

      model R1MSRRhalfDol
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotFuel = MSRR_PlantData.PrimaryLoop.vdotFuel;
        parameter SMD_MSR_Modelica.Units.Density rhoFuel = MSRR_PlantData.Materials.rhoFuel;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpFuel = MSRR_PlantData.Materials.cpFuel;
        parameter SMD_MSR_Modelica.Units.Conductivity kFuel = MSRR_PlantData.Materials.kFuel;
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotCoolant = MSRR_PlantData.SecondaryLoop.vdotCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoCoolant = MSRR_PlantData.Materials.rhoCoolant;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpCoolant = MSRR_PlantData.Materials.cpCoolant;
        parameter SMD_MSR_Modelica.Units.Conductivity kCoolant = MSRR_PlantData.Materials.kCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoGrap = MSRR_PlantData.Materials.rhoGrap;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpGrap = MSRR_PlantData.Materials.cpGrap;
        parameter SMD_MSR_Modelica.Units.Density rhoHXtube = MSRR_PlantData.Materials.rhoHXtube;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpHXtube = MSRR_PlantData.Materials.cpHXtube;
        MSRR.Components.HeatExchanger heatExchanger(vol_P = MSRR_PlantData.SecondaryLoop.hxVolP, vol_T = MSRR_PlantData.SecondaryLoop.hxVolT, vol_S = MSRR_PlantData.SecondaryLoop.hxVolS, rhoP = rhoFuel, rhoT = rhoHXtube, rhoS = rhoCoolant, cP_P = scpFuel, cP_T = scpHXtube, cP_S = scpCoolant, VdotPnom = volDotFuel, VdotSnom = volDotCoolant, hApNom = MSRR_PlantData.SecondaryLoop.hApNom, hAsNom = MSRR_PlantData.SecondaryLoop.hAsNom, hAExp = 0.33, EnableRad = false, AcShell = MSRR_PlantData.SecondaryLoop.HX.AcShell, AcTube = MSRR_PlantData.SecondaryLoop.HX.AcTube, ArShell = MSRR_PlantData.SecondaryLoop.HX.ArShell, Kp = kFuel, Ks = kCoolant, L_shell = MSRR_PlantData.SecondaryLoop.HX.L_shell, L_tube = MSRR_PlantData.SecondaryLoop.HX.L_tube, e = MSRR_PlantData.SecondaryLoop.HX.e, Tinf = MSRR_PlantData.SecondaryLoop.HX.Tinf, TpIn_0 = MSRR_PlantData.SecondaryLoop.HX.TpIn_0, TpOut_0 = MSRR_PlantData.SecondaryLoop.HX.TpOut_0, TsIn_0 = MSRR_PlantData.SecondaryLoop.HX.TsIn_0, TsOut_0 = MSRR_PlantData.SecondaryLoop.HX.TsOut_0) annotation(
          Placement(transformation(origin = {-28.2, 32.6}, extent = {{-50.8, -25.4}, {76.2, 25.4}})));
        SMD_MSR_Modelica.HeatTransport.UHX uhx(Tp_0 = MSRR_PlantData.SecondaryLoop.UHX.Tp_0, vDot = volDotCoolant, cP = scpCoolant, rho = rhoCoolant, vol = MSRR_PlantData.SecondaryLoop.uhxVol) annotation(
          Placement(transformation(origin = {77.462, -70.3463}, extent = {{-54.8618, -41.1463}, {27.4309, 41.1463}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoUHX(vol = MSRR_PlantData.SecondaryLoop.pipeHXtoUHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.T_0) annotation(
          Placement(transformation(origin = {-28.8, -81.0667}, extent = {{-27.2, 9.06667}, {18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeUHXtoHX(vol = MSRR_PlantData.SecondaryLoop.pipeUHXtoHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.T_0) annotation(
          Placement(transformation(origin = {93.2, 7.73333}, extent = {{27.2, 9.06667}, {-18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.DHRS dhrs(vol = MSRR_PlantData.PrimaryLoop.volLoop[2], rho = rhoFuel, cP = scpFuel, vDotNom = volDotFuel, DHRS_tK = MSRR_PlantData.PrimaryLoop.dhrsTK, DHRS_MaxP_Rm(displayUnit = "MW") = MSRR_PlantData.PrimaryLoop.dhrsMaxRemove, DHRS_P_Bleed = MSRR_PlantData.PrimaryLoop.dhrsBleed, DHRS_time = MSRR_PlantData.PrimaryLoop.dhrsEngageTime, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.Ac, L = MSRR_PlantData.PrimaryLoop.L, Ar = MSRR_PlantData.PrimaryLoop.Ar, EnableRad = false, e = MSRR_PlantData.PrimaryLoop.e, Tinf = MSRR_PlantData.PrimaryLoop.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.T_0) annotation(
          Placement(transformation(origin = {-267, 32.6667}, extent = {{-49.3333, -24.6667}, {37, 49.3333}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeDHRStoHX(vol = MSRR_PlantData.PrimaryLoop.volLoop[3], volFracNode = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ac, L = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ar, e = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.T_0) annotation(
          Placement(transformation(origin = {-135.6, 23.2}, extent = {{-38.4, 12.8}, {25.6, 51.2}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoCore(vol = MSRR_PlantData.PrimaryLoop.volLoop[5], volFracNode = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ac, L = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ar, e = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.T_0) annotation(
          Placement(transformation(origin = {-176.4, -61.6}, extent = {{-37.2, 12.4}, {24.8, 49.6}}, rotation = 180)));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeCoreToDHRS(vol = MSRR_PlantData.PrimaryLoop.volLoop[1], volFracNode = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ac, L = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ar, e = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.T_0) annotation(
          Placement(transformation(origin = {-382.4, -32.8}, extent = {{-39.6, 13.2}, {26.4, 52.8}})));
        MSRR.Components.MSRR1R core1R(numGroups = MSRR_PlantData.Kinetics.numGroups, EnableRad = false, lambda = MSRR_PlantData.Kinetics.lambda, beta = MSRR_PlantData.Kinetics.beta, LAMBDA = MSRR_PlantData.Kinetics.LAMBDA, a_F = MSRR_PlantData.Kinetics.a_F, a_G = MSRR_PlantData.Kinetics.a_G, n_0 = 1, rho_fuel = rhoFuel, rho_grap = rhoGrap, cP_fuel = scpFuel, cP_grap = scpGrap, volDotFuel = volDotFuel, Tinf = MSRR_PlantData.Core1R.TF1, kFuel = kFuel, TF1_0 = MSRR_PlantData.Core1R.TF1, TF2_0 = MSRR_PlantData.Core1R.TF2, TG_0 = MSRR_PlantData.Core1R.TG) annotation(
          Placement(transformation(origin = {-519.334, -66.0002}, extent = {{-110, -89.9998}, {-49.9999, -29.9999}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
          Placement(transformation(origin = {-27, -161}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.HeatTransport.Pump primaryPump(numRampUp = MSRR_PlantData.Pumps.primaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.primaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.primaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.primaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {-554, 158}, extent = {{-24, -32}, {24, 16}})));
        SMD_MSR_Modelica.HeatTransport.Pump secondaryPump(numRampUp = MSRR_PlantData.Pumps.secondaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.secondaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.secondaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.secondaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {175, 126.667}, extent = {{-23, -30.6667}, {23, 15.3333}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantReal uhxDemand(magnitude = MSRR_PlantData.nominalPower) annotation(
          Placement(transformation(origin = {43, -209}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.Signals.TimeDependent.Stepper externalReact(numSteps = 2, stepTime = {0, 4000}, amplitude = {0, 294.535}) annotation(
          Placement(transformation(origin = {-708.198, 63.8017}, extent = {{-29.7983, -29.7983}, {29.7983, 29.7983}})));
      equation
        connect(heatExchanger.T_out_sFluid, pipeHXtoUHX.PiTemp_IN) annotation(
          Line(points = {{-58, 20}, {-94, 20}, {-94, -58}, {-48, -58}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeHXtoUHX.PiTempOut, uhx.tempIn) annotation(
          Line(points = {{-18, -58}, {23, -58}, {23, -70}}, color = {204, 0, 0}, thickness = 1));
        connect(uhx.tempOut, pipeUHXtoHX.PiTemp_IN) annotation(
          Line(points = {{77, -70}, {140, -70}, {140, 30}, {113, 30}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeUHXtoHX.PiTempOut, heatExchanger.T_in_sFluid) annotation(
          Line(points = {{83, 30}, {60.5, 30}, {60.5, 20}, {52, 20}}, color = {204, 0, 0}, thickness = 1));
        connect(dhrs.tempOut, pipeDHRStoHX.PiTemp_IN) annotation(
          Line(points = {{-247, 45}, {-205.5, 45}, {-205.5, 55}, {-163, 55}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeDHRStoHX.PiTempOut, heatExchanger.T_in_pFluid) annotation(
          Line(points = {{-121, 55}, {-95, 55}, {-95, 45}, {-58, 45}}, color = {204, 0, 0}, thickness = 1));
        connect(heatExchanger.T_out_pFluid, pipeHXtoCore.PiTemp_IN) annotation(
          Line(points = {{52, 45}, {156, 45}, {156, -93}, {-150, -93}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeCoreToDHRS.PiTempOut, dhrs.tempIn) annotation(
          Line(points = {{-367, 0}, {-328.5, 0}, {-328.5, 45}, {-300, 45}}, color = {204, 0, 0}, thickness = 1));
        connect(core1R.tempOut, pipeCoreToDHRS.PiTemp_IN) annotation(
          Line(points = {{-659, -166}, {-409.5, -166}, {-409.5, 0}, {-411, 0}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeHXtoCore.PiTempOut, core1R.tempIn) annotation(
          Line(points = {{-191, -93}, {-390, -93}, {-390, -205}, {-699, -205}}, color = {204, 0, 0}, thickness = 1));
        connect(core1R.volumetricPowerOut, pipeCoreToDHRS.PiDecay_Heat) annotation(
          Line(points = {{-659, -205}, {-448, -205}, {-448, 10}, {-411, 10}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, dhrs.pDecay) annotation(
          Line(points = {{-659, -205}, {-448, -205}, {-448, 70}, {-300, 70}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, pipeDHRStoHX.PiDecay_Heat) annotation(
          Line(points = {{-659, -205}, {-659, 98}, {-163, 98}, {-163, 65}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, heatExchanger.P_decay) annotation(
          Line(points = {{-659, -205}, {-659, 114}, {-3, 114}, {-3, 54}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, pipeHXtoCore.PiDecay_Heat) annotation(
          Line(points = {{-659, -205}, {-353.5, -205}, {-353.5, -102}, {-150, -102}}, color = {220, 138, 221}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeHXtoUHX.PiDecay_Heat) annotation(
          Line(points = {{-29, -145}, {-74, -145}, {-74, -52}, {-48, -52}}, color = {220, 138, 221}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeUHXtoHX.PiDecay_Heat) annotation(
          Line(points = {{-29, -145}, {148, -145}, {148, 37}, {113, 37}}, color = {220, 138, 221}, thickness = 1));
        connect(primaryPump.flowFrac, core1R.flowFracIn) annotation(
          Line(points = {{-554, 126}, {-554, 20}, {-699, 20}, {-699, -166}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeCoreToDHRS.flowFrac) annotation(
          Line(points = {{-554, 126}, {-468, 126}, {-468, -10}, {-411, -10}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, dhrs.flowFrac) annotation(
          Line(points = {{-554, 126}, {-328, 126}, {-328, 20}, {-300, 20}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeDHRStoHX.flowFrac) annotation(
          Line(points = {{-554, 126}, {-206, 126}, {-206, 46}, {-163, 46}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, heatExchanger.primaryFF) annotation(
          Line(points = {{-554, 126}, {-554, 92}, {-45, 92}, {-45, 54}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeHXtoCore.flowFrac) annotation(
          Line(points = {{-554, 126}, {-108, 126}, {-108, -83}, {-150, -83}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeUHXtoHX.flowFrac) annotation(
          Line(points = {{175, 96}, {174, 96}, {174, 24}, {113, 24}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, heatExchanger.secondaryFF) annotation(
          Line(points = {{175, 96}, {175, 98}, {174, 98}, {174, -6}, {40, -6}, {40, 11}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, uhx.flowFrac) annotation(
          Line(points = {{175, 96}, {174, 96}, {174, -43}, {23, -43}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeHXtoUHX.flowFrac) annotation(
          Line(points = {{175, 96}, {175, 98}, {176, 98}, {176, -82}, {-48, -82}, {-48, -65}}, color = {245, 121, 0}, thickness = 1));
        connect(externalReact.step, core1R.realIn) annotation(
          Line(points = {{-706, 74}, {-600, 74}, {-600, -106}}, thickness = 1));
        connect(uhxDemand.realOut, uhx.powDemand) annotation(
          Line(points = {{44, -196}, {36, -196}, {36, -98}}, thickness = 1));
        annotation(
          Diagram(coordinateSystem(extent = {{-760, 180}, {220, -280}}), graphics = {Polygon(points = {{108, 32}, {108, 32}})}));
      end R1MSRRhalfDol;

      model R1MSRRpOneDol
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotFuel = MSRR_PlantData.PrimaryLoop.vdotFuel;
        parameter SMD_MSR_Modelica.Units.Density rhoFuel = MSRR_PlantData.Materials.rhoFuel;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpFuel = MSRR_PlantData.Materials.cpFuel;
        parameter SMD_MSR_Modelica.Units.Conductivity kFuel = MSRR_PlantData.Materials.kFuel;
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotCoolant = MSRR_PlantData.SecondaryLoop.vdotCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoCoolant = MSRR_PlantData.Materials.rhoCoolant;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpCoolant = MSRR_PlantData.Materials.cpCoolant;
        parameter SMD_MSR_Modelica.Units.Conductivity kCoolant = MSRR_PlantData.Materials.kCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoGrap = MSRR_PlantData.Materials.rhoGrap;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpGrap = MSRR_PlantData.Materials.cpGrap;
        parameter SMD_MSR_Modelica.Units.Density rhoHXtube = MSRR_PlantData.Materials.rhoHXtube;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpHXtube = MSRR_PlantData.Materials.cpHXtube;
        MSRR.Components.HeatExchanger heatExchanger(vol_P = MSRR_PlantData.SecondaryLoop.hxVolP, vol_T = MSRR_PlantData.SecondaryLoop.hxVolT, vol_S = MSRR_PlantData.SecondaryLoop.hxVolS, rhoP = rhoFuel, rhoT = rhoHXtube, rhoS = rhoCoolant, cP_P = scpFuel, cP_T = scpHXtube, cP_S = scpCoolant, VdotPnom = volDotFuel, VdotSnom = volDotCoolant, hApNom = MSRR_PlantData.SecondaryLoop.hApNom, hAsNom = MSRR_PlantData.SecondaryLoop.hAsNom, hAExp = 0.33, EnableRad = false, AcShell = MSRR_PlantData.SecondaryLoop.HX.AcShell, AcTube = MSRR_PlantData.SecondaryLoop.HX.AcTube, ArShell = MSRR_PlantData.SecondaryLoop.HX.ArShell, Kp = kFuel, Ks = kCoolant, L_shell = MSRR_PlantData.SecondaryLoop.HX.L_shell, L_tube = MSRR_PlantData.SecondaryLoop.HX.L_tube, e = MSRR_PlantData.SecondaryLoop.HX.e, Tinf = MSRR_PlantData.SecondaryLoop.HX.Tinf, TpIn_0 = MSRR_PlantData.SecondaryLoop.HX.TpIn_0, TpOut_0 = MSRR_PlantData.SecondaryLoop.HX.TpOut_0, TsIn_0 = MSRR_PlantData.SecondaryLoop.HX.TsIn_0, TsOut_0 = MSRR_PlantData.SecondaryLoop.HX.TsOut_0) annotation(
          Placement(transformation(origin = {-28.2, 32.6}, extent = {{-50.8, -25.4}, {76.2, 25.4}})));
        SMD_MSR_Modelica.HeatTransport.UHX uhx(Tp_0 = MSRR_PlantData.SecondaryLoop.UHX.Tp_0, vDot = volDotCoolant, cP = scpCoolant, rho = rhoCoolant, vol = MSRR_PlantData.SecondaryLoop.uhxVol) annotation(
          Placement(transformation(origin = {77.462, -70.3463}, extent = {{-54.8618, -41.1463}, {27.4309, 41.1463}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoUHX(vol = MSRR_PlantData.SecondaryLoop.pipeHXtoUHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.T_0) annotation(
          Placement(transformation(origin = {-28.8, -81.0667}, extent = {{-27.2, 9.06667}, {18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeUHXtoHX(vol = MSRR_PlantData.SecondaryLoop.pipeUHXtoHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.T_0) annotation(
          Placement(transformation(origin = {93.2, 7.73333}, extent = {{27.2, 9.06667}, {-18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.DHRS dhrs(vol = MSRR_PlantData.PrimaryLoop.volLoop[2], rho = rhoFuel, cP = scpFuel, vDotNom = volDotFuel, DHRS_tK = MSRR_PlantData.PrimaryLoop.dhrsTK, DHRS_MaxP_Rm(displayUnit = "MW") = MSRR_PlantData.PrimaryLoop.dhrsMaxRemove, DHRS_P_Bleed = MSRR_PlantData.PrimaryLoop.dhrsBleed, DHRS_time = MSRR_PlantData.PrimaryLoop.dhrsEngageTime, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.Ac, L = MSRR_PlantData.PrimaryLoop.L, Ar = MSRR_PlantData.PrimaryLoop.Ar, EnableRad = false, e = MSRR_PlantData.PrimaryLoop.e, Tinf = MSRR_PlantData.PrimaryLoop.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.T_0) annotation(
          Placement(transformation(origin = {-267, 32.6667}, extent = {{-49.3333, -24.6667}, {37, 49.3333}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeDHRStoHX(vol = MSRR_PlantData.PrimaryLoop.volLoop[3], volFracNode = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ac, L = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ar, e = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.T_0) annotation(
          Placement(transformation(origin = {-135.6, 23.2}, extent = {{-38.4, 12.8}, {25.6, 51.2}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoCore(vol = MSRR_PlantData.PrimaryLoop.volLoop[5], volFracNode = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ac, L = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ar, e = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.T_0) annotation(
          Placement(transformation(origin = {-176.4, -61.6}, extent = {{-37.2, 12.4}, {24.8, 49.6}}, rotation = 180)));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeCoreToDHRS(vol = MSRR_PlantData.PrimaryLoop.volLoop[1], volFracNode = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ac, L = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ar, e = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.T_0) annotation(
          Placement(transformation(origin = {-382.4, -32.8}, extent = {{-39.6, 13.2}, {26.4, 52.8}})));
        MSRR.Components.MSRR1R core1R(numGroups = MSRR_PlantData.Kinetics.numGroups, EnableRad = false, lambda = MSRR_PlantData.Kinetics.lambda, beta = MSRR_PlantData.Kinetics.beta, LAMBDA = MSRR_PlantData.Kinetics.LAMBDA, a_F = MSRR_PlantData.Kinetics.a_F, a_G = MSRR_PlantData.Kinetics.a_G, n_0 = 1, rho_fuel = rhoFuel, rho_grap = rhoGrap, cP_fuel = scpFuel, cP_grap = scpGrap, volDotFuel = volDotFuel, Tinf = MSRR_PlantData.Core1R.TF1, kFuel = kFuel, TF1_0 = MSRR_PlantData.Core1R.TF1, TF2_0 = MSRR_PlantData.Core1R.TF2, TG_0 = MSRR_PlantData.Core1R.TG) annotation(
          Placement(transformation(origin = {-519.334, -66.0002}, extent = {{-110, -89.9998}, {-49.9999, -29.9999}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
          Placement(transformation(origin = {-27, -161}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.HeatTransport.Pump primaryPump(numRampUp = MSRR_PlantData.Pumps.primaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.primaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.primaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.primaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {-554, 158}, extent = {{-24, -32}, {24, 16}})));
        SMD_MSR_Modelica.HeatTransport.Pump secondaryPump(numRampUp = MSRR_PlantData.Pumps.secondaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.secondaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.secondaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.secondaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {175, 126.667}, extent = {{-23, -30.6667}, {23, 15.3333}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantReal uhxDemand(magnitude = MSRR_PlantData.nominalPower) annotation(
          Placement(transformation(origin = {43, -209}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.Signals.TimeDependent.Stepper externalReact(numSteps = 2, stepTime = {0, 4000}, amplitude = {0, 58.907}) annotation(
          Placement(transformation(origin = {-708.198, 63.8017}, extent = {{-29.7983, -29.7983}, {29.7983, 29.7983}})));
      equation
        connect(heatExchanger.T_out_sFluid, pipeHXtoUHX.PiTemp_IN) annotation(
          Line(points = {{-58, 20}, {-94, 20}, {-94, -58}, {-48, -58}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeHXtoUHX.PiTempOut, uhx.tempIn) annotation(
          Line(points = {{-18, -58}, {23, -58}, {23, -70}}, color = {204, 0, 0}, thickness = 1));
        connect(uhx.tempOut, pipeUHXtoHX.PiTemp_IN) annotation(
          Line(points = {{77, -70}, {140, -70}, {140, 30}, {113, 30}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeUHXtoHX.PiTempOut, heatExchanger.T_in_sFluid) annotation(
          Line(points = {{83, 30}, {60.5, 30}, {60.5, 20}, {52, 20}}, color = {204, 0, 0}, thickness = 1));
        connect(dhrs.tempOut, pipeDHRStoHX.PiTemp_IN) annotation(
          Line(points = {{-247, 45}, {-205.5, 45}, {-205.5, 55}, {-163, 55}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeDHRStoHX.PiTempOut, heatExchanger.T_in_pFluid) annotation(
          Line(points = {{-121, 55}, {-95, 55}, {-95, 45}, {-58, 45}}, color = {204, 0, 0}, thickness = 1));
        connect(heatExchanger.T_out_pFluid, pipeHXtoCore.PiTemp_IN) annotation(
          Line(points = {{52, 45}, {156, 45}, {156, -93}, {-150, -93}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeCoreToDHRS.PiTempOut, dhrs.tempIn) annotation(
          Line(points = {{-367, 0}, {-328.5, 0}, {-328.5, 45}, {-300, 45}}, color = {204, 0, 0}, thickness = 1));
        connect(core1R.tempOut, pipeCoreToDHRS.PiTemp_IN) annotation(
          Line(points = {{-659, -166}, {-409.5, -166}, {-409.5, 0}, {-411, 0}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeHXtoCore.PiTempOut, core1R.tempIn) annotation(
          Line(points = {{-191, -93}, {-390, -93}, {-390, -205}, {-699, -205}}, color = {204, 0, 0}, thickness = 1));
        connect(core1R.volumetricPowerOut, pipeCoreToDHRS.PiDecay_Heat) annotation(
          Line(points = {{-659, -205}, {-448, -205}, {-448, 10}, {-411, 10}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, dhrs.pDecay) annotation(
          Line(points = {{-659, -205}, {-448, -205}, {-448, 70}, {-300, 70}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, pipeDHRStoHX.PiDecay_Heat) annotation(
          Line(points = {{-659, -205}, {-659, 98}, {-163, 98}, {-163, 65}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, heatExchanger.P_decay) annotation(
          Line(points = {{-659, -205}, {-659, 114}, {-3, 114}, {-3, 54}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, pipeHXtoCore.PiDecay_Heat) annotation(
          Line(points = {{-659, -205}, {-353.5, -205}, {-353.5, -102}, {-150, -102}}, color = {220, 138, 221}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeHXtoUHX.PiDecay_Heat) annotation(
          Line(points = {{-29, -145}, {-74, -145}, {-74, -52}, {-48, -52}}, color = {220, 138, 221}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeUHXtoHX.PiDecay_Heat) annotation(
          Line(points = {{-29, -145}, {148, -145}, {148, 37}, {113, 37}}, color = {220, 138, 221}, thickness = 1));
        connect(primaryPump.flowFrac, core1R.flowFracIn) annotation(
          Line(points = {{-554, 126}, {-554, 20}, {-699, 20}, {-699, -166}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeCoreToDHRS.flowFrac) annotation(
          Line(points = {{-554, 126}, {-468, 126}, {-468, -10}, {-411, -10}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, dhrs.flowFrac) annotation(
          Line(points = {{-554, 126}, {-328, 126}, {-328, 20}, {-300, 20}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeDHRStoHX.flowFrac) annotation(
          Line(points = {{-554, 126}, {-206, 126}, {-206, 46}, {-163, 46}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, heatExchanger.primaryFF) annotation(
          Line(points = {{-554, 126}, {-554, 92}, {-45, 92}, {-45, 54}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeHXtoCore.flowFrac) annotation(
          Line(points = {{-554, 126}, {-108, 126}, {-108, -83}, {-150, -83}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeUHXtoHX.flowFrac) annotation(
          Line(points = {{175, 96}, {174, 96}, {174, 24}, {113, 24}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, heatExchanger.secondaryFF) annotation(
          Line(points = {{175, 96}, {175, 98}, {174, 98}, {174, -6}, {40, -6}, {40, 11}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, uhx.flowFrac) annotation(
          Line(points = {{175, 96}, {174, 96}, {174, -43}, {23, -43}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeHXtoUHX.flowFrac) annotation(
          Line(points = {{175, 96}, {175, 98}, {176, 98}, {176, -82}, {-48, -82}, {-48, -65}}, color = {245, 121, 0}, thickness = 1));
        connect(externalReact.step, core1R.realIn) annotation(
          Line(points = {{-706, 74}, {-600, 74}, {-600, -106}}, thickness = 1));
        connect(uhxDemand.realOut, uhx.powDemand) annotation(
          Line(points = {{44, -196}, {36, -196}, {36, -98}}, thickness = 1));
        annotation(
          Diagram(coordinateSystem(extent = {{-760, 180}, {220, -280}}), graphics = {Polygon(points = {{108, 32}, {108, 32}})}));
      end R1MSRRpOneDol;

      model R1MSRR100pcm
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotFuel = MSRR_PlantData.PrimaryLoop.vdotFuel;
        parameter SMD_MSR_Modelica.Units.Density rhoFuel = MSRR_PlantData.Materials.rhoFuel;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpFuel = MSRR_PlantData.Materials.cpFuel;
        parameter SMD_MSR_Modelica.Units.Conductivity kFuel = MSRR_PlantData.Materials.kFuel;
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotCoolant = MSRR_PlantData.SecondaryLoop.vdotCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoCoolant = MSRR_PlantData.Materials.rhoCoolant;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpCoolant = MSRR_PlantData.Materials.cpCoolant;
        parameter SMD_MSR_Modelica.Units.Conductivity kCoolant = MSRR_PlantData.Materials.kCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoGrap = MSRR_PlantData.Materials.rhoGrap;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpGrap = MSRR_PlantData.Materials.cpGrap;
        parameter SMD_MSR_Modelica.Units.Density rhoHXtube = MSRR_PlantData.Materials.rhoHXtube;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpHXtube = MSRR_PlantData.Materials.cpHXtube;
        MSRR.Components.HeatExchanger heatExchanger(vol_P = MSRR_PlantData.SecondaryLoop.hxVolP, vol_T = MSRR_PlantData.SecondaryLoop.hxVolT, vol_S = MSRR_PlantData.SecondaryLoop.hxVolS, rhoP = rhoFuel, rhoT = rhoHXtube, rhoS = rhoCoolant, cP_P = scpFuel, cP_T = scpHXtube, cP_S = scpCoolant, VdotPnom = volDotFuel, VdotSnom = volDotCoolant, hApNom = MSRR_PlantData.SecondaryLoop.hApNom, hAsNom = MSRR_PlantData.SecondaryLoop.hAsNom, hAExp = 0.33, EnableRad = false, AcShell = MSRR_PlantData.SecondaryLoop.HX.AcShell, AcTube = MSRR_PlantData.SecondaryLoop.HX.AcTube, ArShell = MSRR_PlantData.SecondaryLoop.HX.ArShell, Kp = kFuel, Ks = kCoolant, L_shell = MSRR_PlantData.SecondaryLoop.HX.L_shell, L_tube = MSRR_PlantData.SecondaryLoop.HX.L_tube, e = MSRR_PlantData.SecondaryLoop.HX.e, Tinf = MSRR_PlantData.SecondaryLoop.HX.Tinf, TpIn_0 = MSRR_PlantData.SecondaryLoop.HX.TpIn_0, TpOut_0 = MSRR_PlantData.SecondaryLoop.HX.TpOut_0, TsIn_0 = MSRR_PlantData.SecondaryLoop.HX.TsIn_0, TsOut_0 = MSRR_PlantData.SecondaryLoop.HX.TsOut_0) annotation(
          Placement(transformation(origin = {-28.2, 32.6}, extent = {{-50.8, -25.4}, {76.2, 25.4}})));
        SMD_MSR_Modelica.HeatTransport.UHX uhx(Tp_0 = MSRR_PlantData.SecondaryLoop.UHX.Tp_0, vDot = volDotCoolant, cP = scpCoolant, rho = rhoCoolant, vol = MSRR_PlantData.SecondaryLoop.uhxVol) annotation(
          Placement(transformation(origin = {77.462, -70.3463}, extent = {{-54.8618, -41.1463}, {27.4309, 41.1463}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoUHX(vol = MSRR_PlantData.SecondaryLoop.pipeHXtoUHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.T_0) annotation(
          Placement(transformation(origin = {-28.8, -81.0667}, extent = {{-27.2, 9.06667}, {18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeUHXtoHX(vol = MSRR_PlantData.SecondaryLoop.pipeUHXtoHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.T_0) annotation(
          Placement(transformation(origin = {93.2, 7.73333}, extent = {{27.2, 9.06667}, {-18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.DHRS dhrs(vol = MSRR_PlantData.PrimaryLoop.volLoop[2], rho = rhoFuel, cP = scpFuel, vDotNom = volDotFuel, DHRS_tK = MSRR_PlantData.PrimaryLoop.dhrsTK, DHRS_MaxP_Rm(displayUnit = "MW") = MSRR_PlantData.PrimaryLoop.dhrsMaxRemove, DHRS_P_Bleed = MSRR_PlantData.PrimaryLoop.dhrsBleed, DHRS_time = MSRR_PlantData.PrimaryLoop.dhrsEngageTime, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.Ac, L = MSRR_PlantData.PrimaryLoop.L, Ar = MSRR_PlantData.PrimaryLoop.Ar, EnableRad = false, e = MSRR_PlantData.PrimaryLoop.e, Tinf = MSRR_PlantData.PrimaryLoop.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.T_0) annotation(
          Placement(transformation(origin = {-267, 32.6667}, extent = {{-49.3333, -24.6667}, {37, 49.3333}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeDHRStoHX(vol = MSRR_PlantData.PrimaryLoop.volLoop[3], volFracNode = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ac, L = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ar, e = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.T_0) annotation(
          Placement(transformation(origin = {-135.6, 23.2}, extent = {{-38.4, 12.8}, {25.6, 51.2}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoCore(vol = MSRR_PlantData.PrimaryLoop.volLoop[5], volFracNode = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ac, L = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ar, e = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.T_0) annotation(
          Placement(transformation(origin = {-176.4, -61.6}, extent = {{-37.2, 12.4}, {24.8, 49.6}}, rotation = 180)));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeCoreToDHRS(vol = MSRR_PlantData.PrimaryLoop.volLoop[1], volFracNode = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ac, L = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ar, e = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.T_0) annotation(
          Placement(transformation(origin = {-382.4, -32.8}, extent = {{-39.6, 13.2}, {26.4, 52.8}})));
        MSRR.Components.MSRR1R core1R(numGroups = MSRR_PlantData.Kinetics.numGroups, EnableRad = false, lambda = MSRR_PlantData.Kinetics.lambda, beta = MSRR_PlantData.Kinetics.beta, LAMBDA = MSRR_PlantData.Kinetics.LAMBDA, a_F = MSRR_PlantData.Kinetics.a_F, a_G = MSRR_PlantData.Kinetics.a_G, n_0 = 1, rho_fuel = rhoFuel, rho_grap = rhoGrap, cP_fuel = scpFuel, cP_grap = scpGrap, volDotFuel = volDotFuel, Tinf = MSRR_PlantData.Core1R.TF1, kFuel = kFuel, TF1_0 = MSRR_PlantData.Core1R.TF1, TF2_0 = MSRR_PlantData.Core1R.TF2, TG_0 = MSRR_PlantData.Core1R.TG) annotation(
          Placement(transformation(origin = {-519.334, -66.0002}, extent = {{-110, -89.9998}, {-49.9999, -29.9999}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
          Placement(transformation(origin = {-27, -161}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.HeatTransport.Pump primaryPump(numRampUp = MSRR_PlantData.Pumps.primaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.primaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.primaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.primaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {-554, 158}, extent = {{-24, -32}, {24, 16}})));
        SMD_MSR_Modelica.HeatTransport.Pump secondaryPump(numRampUp = MSRR_PlantData.Pumps.secondaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.secondaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.secondaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.secondaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {175, 126.667}, extent = {{-23, -30.6667}, {23, 15.3333}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantReal uhxDemand(magnitude = MSRR_PlantData.nominalPower) annotation(
          Placement(transformation(origin = {43, -209}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.Signals.TimeDependent.Stepper externalReact(numSteps = 2, stepTime = {0, 4000}, amplitude = {0, 100}) annotation(
          Placement(transformation(origin = {-708.198, 63.8017}, extent = {{-29.7983, -29.7983}, {29.7983, 29.7983}})));
      equation
        connect(heatExchanger.T_out_sFluid, pipeHXtoUHX.PiTemp_IN) annotation(
          Line(points = {{-58, 20}, {-94, 20}, {-94, -58}, {-48, -58}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeHXtoUHX.PiTempOut, uhx.tempIn) annotation(
          Line(points = {{-18, -58}, {23, -58}, {23, -70}}, color = {204, 0, 0}, thickness = 1));
        connect(uhx.tempOut, pipeUHXtoHX.PiTemp_IN) annotation(
          Line(points = {{77, -70}, {140, -70}, {140, 30}, {113, 30}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeUHXtoHX.PiTempOut, heatExchanger.T_in_sFluid) annotation(
          Line(points = {{83, 30}, {60.5, 30}, {60.5, 20}, {52, 20}}, color = {204, 0, 0}, thickness = 1));
        connect(dhrs.tempOut, pipeDHRStoHX.PiTemp_IN) annotation(
          Line(points = {{-247, 45}, {-205.5, 45}, {-205.5, 55}, {-163, 55}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeDHRStoHX.PiTempOut, heatExchanger.T_in_pFluid) annotation(
          Line(points = {{-121, 55}, {-95, 55}, {-95, 45}, {-58, 45}}, color = {204, 0, 0}, thickness = 1));
        connect(heatExchanger.T_out_pFluid, pipeHXtoCore.PiTemp_IN) annotation(
          Line(points = {{52, 45}, {156, 45}, {156, -93}, {-150, -93}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeCoreToDHRS.PiTempOut, dhrs.tempIn) annotation(
          Line(points = {{-367, 0}, {-328.5, 0}, {-328.5, 45}, {-300, 45}}, color = {204, 0, 0}, thickness = 1));
        connect(core1R.tempOut, pipeCoreToDHRS.PiTemp_IN) annotation(
          Line(points = {{-659, -166}, {-409.5, -166}, {-409.5, 0}, {-411, 0}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeHXtoCore.PiTempOut, core1R.tempIn) annotation(
          Line(points = {{-191, -93}, {-390, -93}, {-390, -205}, {-699, -205}}, color = {204, 0, 0}, thickness = 1));
        connect(core1R.volumetricPowerOut, pipeCoreToDHRS.PiDecay_Heat) annotation(
          Line(points = {{-659, -205}, {-448, -205}, {-448, 10}, {-411, 10}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, dhrs.pDecay) annotation(
          Line(points = {{-659, -205}, {-448, -205}, {-448, 70}, {-300, 70}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, pipeDHRStoHX.PiDecay_Heat) annotation(
          Line(points = {{-659, -205}, {-659, 98}, {-163, 98}, {-163, 65}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, heatExchanger.P_decay) annotation(
          Line(points = {{-659, -205}, {-659, 114}, {-3, 114}, {-3, 54}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, pipeHXtoCore.PiDecay_Heat) annotation(
          Line(points = {{-659, -205}, {-353.5, -205}, {-353.5, -102}, {-150, -102}}, color = {220, 138, 221}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeHXtoUHX.PiDecay_Heat) annotation(
          Line(points = {{-29, -145}, {-74, -145}, {-74, -52}, {-48, -52}}, color = {220, 138, 221}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeUHXtoHX.PiDecay_Heat) annotation(
          Line(points = {{-29, -145}, {148, -145}, {148, 37}, {113, 37}}, color = {220, 138, 221}, thickness = 1));
        connect(primaryPump.flowFrac, core1R.flowFracIn) annotation(
          Line(points = {{-554, 126}, {-554, 20}, {-699, 20}, {-699, -166}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeCoreToDHRS.flowFrac) annotation(
          Line(points = {{-554, 126}, {-468, 126}, {-468, -10}, {-411, -10}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, dhrs.flowFrac) annotation(
          Line(points = {{-554, 126}, {-328, 126}, {-328, 20}, {-300, 20}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeDHRStoHX.flowFrac) annotation(
          Line(points = {{-554, 126}, {-206, 126}, {-206, 46}, {-163, 46}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, heatExchanger.primaryFF) annotation(
          Line(points = {{-554, 126}, {-554, 92}, {-45, 92}, {-45, 54}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeHXtoCore.flowFrac) annotation(
          Line(points = {{-554, 126}, {-108, 126}, {-108, -83}, {-150, -83}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeUHXtoHX.flowFrac) annotation(
          Line(points = {{175, 96}, {174, 96}, {174, 24}, {113, 24}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, heatExchanger.secondaryFF) annotation(
          Line(points = {{175, 96}, {175, 98}, {174, 98}, {174, -6}, {40, -6}, {40, 11}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, uhx.flowFrac) annotation(
          Line(points = {{175, 96}, {174, 96}, {174, -43}, {23, -43}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeHXtoUHX.flowFrac) annotation(
          Line(points = {{175, 96}, {175, 98}, {176, 98}, {176, -82}, {-48, -82}, {-48, -65}}, color = {245, 121, 0}, thickness = 1));
        connect(externalReact.step, core1R.realIn) annotation(
          Line(points = {{-706, 74}, {-600, 74}, {-600, -106}}, thickness = 1));
        connect(uhxDemand.realOut, uhx.powDemand) annotation(
          Line(points = {{44, -196}, {36, -196}, {36, -98}}, thickness = 1));
        annotation(
          Diagram(coordinateSystem(extent = {{-760, 180}, {220, -280}}), graphics = {Polygon(points = {{108, 32}, {108, 32}})}));
      end R1MSRR100pcm;

      model R1MSRR10pcm
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotFuel = MSRR_PlantData.PrimaryLoop.vdotFuel;
        parameter SMD_MSR_Modelica.Units.Density rhoFuel = MSRR_PlantData.Materials.rhoFuel;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpFuel = MSRR_PlantData.Materials.cpFuel;
        parameter SMD_MSR_Modelica.Units.Conductivity kFuel = MSRR_PlantData.Materials.kFuel;
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotCoolant = MSRR_PlantData.SecondaryLoop.vdotCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoCoolant = MSRR_PlantData.Materials.rhoCoolant;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpCoolant = MSRR_PlantData.Materials.cpCoolant;
        parameter SMD_MSR_Modelica.Units.Conductivity kCoolant = MSRR_PlantData.Materials.kCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoGrap = MSRR_PlantData.Materials.rhoGrap;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpGrap = MSRR_PlantData.Materials.cpGrap;
        parameter SMD_MSR_Modelica.Units.Density rhoHXtube = MSRR_PlantData.Materials.rhoHXtube;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpHXtube = MSRR_PlantData.Materials.cpHXtube;
        MSRR.Components.HeatExchanger heatExchanger(vol_P = MSRR_PlantData.SecondaryLoop.hxVolP, vol_T = MSRR_PlantData.SecondaryLoop.hxVolT, vol_S = MSRR_PlantData.SecondaryLoop.hxVolS, rhoP = rhoFuel, rhoT = rhoHXtube, rhoS = rhoCoolant, cP_P = scpFuel, cP_T = scpHXtube, cP_S = scpCoolant, VdotPnom = volDotFuel, VdotSnom = volDotCoolant, hApNom = MSRR_PlantData.SecondaryLoop.hApNom, hAsNom = MSRR_PlantData.SecondaryLoop.hAsNom, hAExp = 0.33, EnableRad = false, AcShell = MSRR_PlantData.SecondaryLoop.HX.AcShell, AcTube = MSRR_PlantData.SecondaryLoop.HX.AcTube, ArShell = MSRR_PlantData.SecondaryLoop.HX.ArShell, Kp = kFuel, Ks = kCoolant, L_shell = MSRR_PlantData.SecondaryLoop.HX.L_shell, L_tube = MSRR_PlantData.SecondaryLoop.HX.L_tube, e = MSRR_PlantData.SecondaryLoop.HX.e, Tinf = MSRR_PlantData.SecondaryLoop.HX.Tinf, TpIn_0 = MSRR_PlantData.SecondaryLoop.HX.TpIn_0, TpOut_0 = MSRR_PlantData.SecondaryLoop.HX.TpOut_0, TsIn_0 = MSRR_PlantData.SecondaryLoop.HX.TsIn_0, TsOut_0 = MSRR_PlantData.SecondaryLoop.HX.TsOut_0) annotation(
          Placement(transformation(origin = {-28.2, 32.6}, extent = {{-50.8, -25.4}, {76.2, 25.4}})));
        SMD_MSR_Modelica.HeatTransport.UHX uhx(Tp_0 = MSRR_PlantData.SecondaryLoop.UHX.Tp_0, vDot = volDotCoolant, cP = scpCoolant, rho = rhoCoolant, vol = MSRR_PlantData.SecondaryLoop.uhxVol) annotation(
          Placement(transformation(origin = {77.462, -70.3463}, extent = {{-54.8618, -41.1463}, {27.4309, 41.1463}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoUHX(vol = MSRR_PlantData.SecondaryLoop.pipeHXtoUHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.T_0) annotation(
          Placement(transformation(origin = {-28.8, -81.0667}, extent = {{-27.2, 9.06667}, {18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeUHXtoHX(vol = MSRR_PlantData.SecondaryLoop.pipeUHXtoHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.T_0) annotation(
          Placement(transformation(origin = {93.2, 7.73333}, extent = {{27.2, 9.06667}, {-18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.DHRS dhrs(vol = MSRR_PlantData.PrimaryLoop.volLoop[2], rho = rhoFuel, cP = scpFuel, vDotNom = volDotFuel, DHRS_tK = MSRR_PlantData.PrimaryLoop.dhrsTK, DHRS_MaxP_Rm(displayUnit = "MW") = MSRR_PlantData.PrimaryLoop.dhrsMaxRemove, DHRS_P_Bleed = MSRR_PlantData.PrimaryLoop.dhrsBleed, DHRS_time = MSRR_PlantData.PrimaryLoop.dhrsEngageTime, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.Ac, L = MSRR_PlantData.PrimaryLoop.L, Ar = MSRR_PlantData.PrimaryLoop.Ar, EnableRad = false, e = MSRR_PlantData.PrimaryLoop.e, Tinf = MSRR_PlantData.PrimaryLoop.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.T_0) annotation(
          Placement(transformation(origin = {-267, 32.6667}, extent = {{-49.3333, -24.6667}, {37, 49.3333}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeDHRStoHX(vol = MSRR_PlantData.PrimaryLoop.volLoop[3], volFracNode = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ac, L = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ar, e = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.T_0) annotation(
          Placement(transformation(origin = {-135.6, 23.2}, extent = {{-38.4, 12.8}, {25.6, 51.2}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoCore(vol = MSRR_PlantData.PrimaryLoop.volLoop[5], volFracNode = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ac, L = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ar, e = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.T_0) annotation(
          Placement(transformation(origin = {-176.4, -61.6}, extent = {{-37.2, 12.4}, {24.8, 49.6}}, rotation = 180)));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeCoreToDHRS(vol = MSRR_PlantData.PrimaryLoop.volLoop[1], volFracNode = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ac, L = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ar, e = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.T_0) annotation(
          Placement(transformation(origin = {-382.4, -32.8}, extent = {{-39.6, 13.2}, {26.4, 52.8}})));
        MSRR.Components.MSRR1R core1R(numGroups = MSRR_PlantData.Kinetics.numGroups, EnableRad = false, lambda = MSRR_PlantData.Kinetics.lambda, beta = MSRR_PlantData.Kinetics.beta, LAMBDA = MSRR_PlantData.Kinetics.LAMBDA, a_F = MSRR_PlantData.Kinetics.a_F, a_G = MSRR_PlantData.Kinetics.a_G, n_0 = 1, rho_fuel = rhoFuel, rho_grap = rhoGrap, cP_fuel = scpFuel, cP_grap = scpGrap, volDotFuel = volDotFuel, Tinf = MSRR_PlantData.Core1R.TF1, kFuel = kFuel, TF1_0 = MSRR_PlantData.Core1R.TF1, TF2_0 = MSRR_PlantData.Core1R.TF2, TG_0 = MSRR_PlantData.Core1R.TG) annotation(
          Placement(transformation(origin = {-519.334, -66.0002}, extent = {{-110, -89.9998}, {-49.9999, -29.9999}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
          Placement(transformation(origin = {-27, -161}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.HeatTransport.Pump primaryPump(numRampUp = MSRR_PlantData.Pumps.primaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.primaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.primaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.primaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {-554, 158}, extent = {{-24, -32}, {24, 16}})));
        SMD_MSR_Modelica.HeatTransport.Pump secondaryPump(numRampUp = MSRR_PlantData.Pumps.secondaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.secondaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.secondaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.secondaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {175, 126.667}, extent = {{-23, -30.6667}, {23, 15.3333}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantReal uhxDemand(magnitude = MSRR_PlantData.nominalPower) annotation(
          Placement(transformation(origin = {43, -209}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.Signals.TimeDependent.Stepper externalReact(numSteps = 2, stepTime = {0, 4000}, amplitude = {0, 10}) annotation(
          Placement(transformation(origin = {-708.198, 63.8017}, extent = {{-29.7983, -29.7983}, {29.7983, 29.7983}})));
      equation
        connect(heatExchanger.T_out_sFluid, pipeHXtoUHX.PiTemp_IN) annotation(
          Line(points = {{-58, 20}, {-94, 20}, {-94, -58}, {-48, -58}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeHXtoUHX.PiTempOut, uhx.tempIn) annotation(
          Line(points = {{-18, -58}, {23, -58}, {23, -70}}, color = {204, 0, 0}, thickness = 1));
        connect(uhx.tempOut, pipeUHXtoHX.PiTemp_IN) annotation(
          Line(points = {{77, -70}, {140, -70}, {140, 30}, {113, 30}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeUHXtoHX.PiTempOut, heatExchanger.T_in_sFluid) annotation(
          Line(points = {{83, 30}, {60.5, 30}, {60.5, 20}, {52, 20}}, color = {204, 0, 0}, thickness = 1));
        connect(dhrs.tempOut, pipeDHRStoHX.PiTemp_IN) annotation(
          Line(points = {{-247, 45}, {-205.5, 45}, {-205.5, 55}, {-163, 55}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeDHRStoHX.PiTempOut, heatExchanger.T_in_pFluid) annotation(
          Line(points = {{-121, 55}, {-95, 55}, {-95, 45}, {-58, 45}}, color = {204, 0, 0}, thickness = 1));
        connect(heatExchanger.T_out_pFluid, pipeHXtoCore.PiTemp_IN) annotation(
          Line(points = {{52, 45}, {156, 45}, {156, -93}, {-150, -93}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeCoreToDHRS.PiTempOut, dhrs.tempIn) annotation(
          Line(points = {{-367, 0}, {-328.5, 0}, {-328.5, 45}, {-300, 45}}, color = {204, 0, 0}, thickness = 1));
        connect(core1R.tempOut, pipeCoreToDHRS.PiTemp_IN) annotation(
          Line(points = {{-659, -166}, {-409.5, -166}, {-409.5, 0}, {-411, 0}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeHXtoCore.PiTempOut, core1R.tempIn) annotation(
          Line(points = {{-191, -93}, {-390, -93}, {-390, -205}, {-699, -205}}, color = {204, 0, 0}, thickness = 1));
        connect(core1R.volumetricPowerOut, pipeCoreToDHRS.PiDecay_Heat) annotation(
          Line(points = {{-659, -205}, {-448, -205}, {-448, 10}, {-411, 10}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, dhrs.pDecay) annotation(
          Line(points = {{-659, -205}, {-448, -205}, {-448, 70}, {-300, 70}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, pipeDHRStoHX.PiDecay_Heat) annotation(
          Line(points = {{-659, -205}, {-659, 98}, {-163, 98}, {-163, 65}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, heatExchanger.P_decay) annotation(
          Line(points = {{-659, -205}, {-659, 114}, {-3, 114}, {-3, 54}}, color = {220, 138, 221}, thickness = 1));
        connect(core1R.volumetricPowerOut, pipeHXtoCore.PiDecay_Heat) annotation(
          Line(points = {{-659, -205}, {-353.5, -205}, {-353.5, -102}, {-150, -102}}, color = {220, 138, 221}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeHXtoUHX.PiDecay_Heat) annotation(
          Line(points = {{-29, -145}, {-74, -145}, {-74, -52}, {-48, -52}}, color = {220, 138, 221}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeUHXtoHX.PiDecay_Heat) annotation(
          Line(points = {{-29, -145}, {148, -145}, {148, 37}, {113, 37}}, color = {220, 138, 221}, thickness = 1));
        connect(primaryPump.flowFrac, core1R.flowFracIn) annotation(
          Line(points = {{-554, 126}, {-554, 20}, {-699, 20}, {-699, -166}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeCoreToDHRS.flowFrac) annotation(
          Line(points = {{-554, 126}, {-468, 126}, {-468, -10}, {-411, -10}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, dhrs.flowFrac) annotation(
          Line(points = {{-554, 126}, {-328, 126}, {-328, 20}, {-300, 20}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeDHRStoHX.flowFrac) annotation(
          Line(points = {{-554, 126}, {-206, 126}, {-206, 46}, {-163, 46}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, heatExchanger.primaryFF) annotation(
          Line(points = {{-554, 126}, {-554, 92}, {-45, 92}, {-45, 54}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeHXtoCore.flowFrac) annotation(
          Line(points = {{-554, 126}, {-108, 126}, {-108, -83}, {-150, -83}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeUHXtoHX.flowFrac) annotation(
          Line(points = {{175, 96}, {174, 96}, {174, 24}, {113, 24}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, heatExchanger.secondaryFF) annotation(
          Line(points = {{175, 96}, {175, 98}, {174, 98}, {174, -6}, {40, -6}, {40, 11}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, uhx.flowFrac) annotation(
          Line(points = {{175, 96}, {174, 96}, {174, -43}, {23, -43}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeHXtoUHX.flowFrac) annotation(
          Line(points = {{175, 96}, {175, 98}, {176, 98}, {176, -82}, {-48, -82}, {-48, -65}}, color = {245, 121, 0}, thickness = 1));
        connect(externalReact.step, core1R.realIn) annotation(
          Line(points = {{-706, 74}, {-600, 74}, {-600, -106}}, thickness = 1));
        connect(uhxDemand.realOut, uhx.powDemand) annotation(
          Line(points = {{44, -196}, {36, -196}, {36, -98}}, thickness = 1));
        annotation(
          Diagram(coordinateSystem(extent = {{-760, 180}, {220, -280}}), graphics = {Polygon(points = {{108, 32}, {108, 32}})}));
      end R1MSRR10pcm;
    end R1fullSteps;

    package R9fullSteps
      model R9MSRR2dol
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotFuel = MSRR_PlantData.PrimaryLoop.vdotFuel;
        parameter SMD_MSR_Modelica.Units.Density rhoFuel = MSRR_PlantData.Materials.rhoFuel;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpFuel = MSRR_PlantData.Materials.cpFuel;
        parameter SMD_MSR_Modelica.Units.Conductivity kFuel = MSRR_PlantData.Materials.kFuel;
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotCoolant = MSRR_PlantData.SecondaryLoop.vdotCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoCoolant = MSRR_PlantData.Materials.rhoCoolant;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpCoolant = MSRR_PlantData.Materials.cpCoolant;
        parameter SMD_MSR_Modelica.Units.Conductivity kCoolant = MSRR_PlantData.Materials.kCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoGrap = MSRR_PlantData.Materials.rhoGrap;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpGrap = MSRR_PlantData.Materials.cpGrap;
        parameter SMD_MSR_Modelica.Units.Density rhoHXtube = MSRR_PlantData.Materials.rhoHXtube;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpHXtube = MSRR_PlantData.Materials.cpHXtube;
        MSRR.Components.HeatExchanger heatExchanger(vol_P = MSRR_PlantData.SecondaryLoop.hxVolP, vol_T = MSRR_PlantData.SecondaryLoop.hxVolT, vol_S = MSRR_PlantData.SecondaryLoop.hxVolS, rhoP = rhoFuel, rhoT = rhoHXtube, rhoS = rhoCoolant, cP_P = scpFuel, cP_T = scpHXtube, cP_S = scpCoolant, VdotPnom = volDotFuel, VdotSnom = volDotCoolant, hApNom = MSRR_PlantData.SecondaryLoop.hApNom, hAsNom = MSRR_PlantData.SecondaryLoop.hAsNom, hAExp = 0.33, EnableRad = false, AcShell = MSRR_PlantData.SecondaryLoop.HX.AcShell, AcTube = MSRR_PlantData.SecondaryLoop.HX.AcTube, ArShell = MSRR_PlantData.SecondaryLoop.HX.ArShell, Kp = kFuel, Ks = kCoolant, L_shell = MSRR_PlantData.SecondaryLoop.HX.L_shell, L_tube = MSRR_PlantData.SecondaryLoop.HX.L_tube, e = MSRR_PlantData.SecondaryLoop.HX.e, Tinf = MSRR_PlantData.SecondaryLoop.HX.Tinf, TpIn_0 = MSRR_PlantData.SecondaryLoop.HX.TpIn_0, TpOut_0 = MSRR_PlantData.SecondaryLoop.HX.TpOut_0, TsIn_0 = MSRR_PlantData.SecondaryLoop.HX.TsIn_0, TsOut_0 = MSRR_PlantData.SecondaryLoop.HX.TsOut_0) annotation(
          Placement(transformation(origin = {-12.2, 90.6}, extent = {{-50.8, -25.4}, {76.2, 25.4}})));
        SMD_MSR_Modelica.HeatTransport.UHX uhx(Tp_0 = MSRR_PlantData.SecondaryLoop.UHX.Tp_0, vDot = volDotCoolant, cP = scpCoolant, rho = rhoCoolant, vol = MSRR_PlantData.SecondaryLoop.uhxVol) annotation(
          Placement(transformation(origin = {103.462, -66.3463}, extent = {{-54.8618, -41.1463}, {27.4309, 41.1463}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoUHX(vol = MSRR_PlantData.SecondaryLoop.pipeHXtoUHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.T_0) annotation(
          Placement(transformation(origin = {-28.8, -55.0667}, extent = {{-27.2, 9.06667}, {18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeUHXtoHX(vol = MSRR_PlantData.SecondaryLoop.pipeUHXtoHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.T_0) annotation(
          Placement(transformation(origin = {95.2, 27.7333}, extent = {{27.2, 9.06667}, {-18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.DHRS dhrs(vol = MSRR_PlantData.PrimaryLoop.volLoop[2], rho = rhoFuel, cP = scpFuel, vDotNom = volDotFuel, DHRS_tK = MSRR_PlantData.PrimaryLoop.dhrsTK, DHRS_MaxP_Rm(displayUnit = "MW") = MSRR_PlantData.PrimaryLoop.dhrsMaxRemove, DHRS_P_Bleed = MSRR_PlantData.PrimaryLoop.dhrsBleed, DHRS_time = MSRR_PlantData.PrimaryLoop.dhrsEngageTime, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.Ac, L = MSRR_PlantData.PrimaryLoop.L, Ar = MSRR_PlantData.PrimaryLoop.Ar, EnableRad = false, e = MSRR_PlantData.PrimaryLoop.e, Tinf = MSRR_PlantData.PrimaryLoop.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.T_0) annotation(
          Placement(transformation(origin = {-249, 30.6667}, extent = {{-49.3333, -24.6667}, {37, 49.3333}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeDHRStoHX(vol = MSRR_PlantData.PrimaryLoop.volLoop[3], volFracNode = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ac, L = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ar, e = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.T_0) annotation(
          Placement(transformation(origin = {-141.6, 11.2}, extent = {{-38.4, 12.8}, {25.6, 51.2}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoCore(vol = MSRR_PlantData.PrimaryLoop.volLoop[5], volFracNode = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ac, L = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ar, e = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.T_0) annotation(
          Placement(transformation(origin = {-158.4, -105.6}, extent = {{-37.2, 12.4}, {24.8, 49.6}}, rotation = 180)));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeCoreToDHRS(vol = MSRR_PlantData.PrimaryLoop.volLoop[1], volFracNode = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ac, L = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ar, e = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.T_0) annotation(
          Placement(transformation(origin = {-374, 6}, extent = {{-39.6, 13.2}, {26.4, 52.8}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
          Placement(transformation(origin = {-37, -175}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.HeatTransport.Pump primaryPump(numRampUp = MSRR_PlantData.Pumps.primaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.primaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.primaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.primaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {-550, 116}, extent = {{-24, -32}, {24, 16}})));
        SMD_MSR_Modelica.HeatTransport.Pump secondaryPump(numRampUp = MSRR_PlantData.Pumps.secondaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.secondaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.secondaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.secondaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {175, 114.667}, extent = {{-23, -30.6667}, {23, 15.3333}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantReal uhxDemand(magnitude = MSRR_PlantData.nominalPower) annotation(
          Placement(transformation(origin = {45, -251}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.Signals.TimeDependent.Stepper externalReact(numSteps = 2, stepTime = {0, 4000}, amplitude = {0, 1178.138}) annotation(
          Placement(transformation(origin = {-596.198, -38.1983}, extent = {{-23.7983, 23.7983}, {23.7983, -23.7983}}, rotation = -0)));
        MSRR.Components.MSRR9R msre9r(numGroups = MSRR_PlantData.Kinetics.numGroups, lambda = MSRR_PlantData.Kinetics.lambda, beta = MSRR_PlantData.Kinetics.beta, LAMBDA = MSRR_PlantData.Kinetics.LAMBDA, n_0 = 1, aF = MSRR_PlantData.Kinetics.a_F, aG = MSRR_PlantData.Kinetics.a_G, volF1 = MSRR_PlantData.Core9R.volF1, volF2 = MSRR_PlantData.Core9R.volF2, volG = MSRR_PlantData.Core9R.volG, hA = MSRR_PlantData.Core9R.hA, kFN1 = MSRR_PlantData.Core9R.kFN1, kFN2 = MSRR_PlantData.Core9R.kFN2, kHT1 = MSRR_PlantData.Core9R.kHT1, kHT2 = MSRR_PlantData.Core9R.kHT2, TF1_0 = MSRR_PlantData.Core9R.TF1_0_regions, TF2_0 = MSRR_PlantData.Core9R.TF2_0_regions, TG_0 = MSRR_PlantData.Core9R.TG_0_regions, Tmix_0 = MSRR_PlantData.Core9R.Tmix_0, IF1 = MSRR_PlantData.Core9R.IF1, IF2 = MSRR_PlantData.Core9R.IF2, IG = MSRR_PlantData.Core9R.IG, flowFracRegions = MSRR_PlantData.Core9R.flowFracRegions, rho_fuel = rhoFuel, cP_fuel = scpFuel, kFuel = kFuel, volDotFuel = volDotFuel, rho_grap = rhoGrap, cP_grap = scpGrap, regionTripTime = MSRR_PlantData.Core9R.regionTripTime, regionCoastDownK = MSRR_PlantData.Core9R.regionCoastDownK, freeConvFF = MSRR_PlantData.Pumps.freeConvFF, EnableRad = false, T_inf = 500, LF1 = MSRR_PlantData.Core9R.LF1, LF2 = MSRR_PlantData.Core9R.LF2, Ac = MSRR_PlantData.Core9R.Ac, ArF1 = MSRR_PlantData.Core9R.ArF1, ArF2 = MSRR_PlantData.Core9R.ArF2, e = MSRR_PlantData.Core9R.e) annotation(
          Placement(transformation(origin = {-258.333, -15}, extent = {{-179.667, -147}, {-81.6667, -49}})));
      equation
        connect(heatExchanger.T_out_sFluid, pipeHXtoUHX.PiTemp_IN) annotation(
          Line(points = {{-42, 78}, {-94, 78}, {-94, -32}, {-51, -32}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeHXtoUHX.PiTempOut, uhx.tempIn) annotation(
          Line(points = {{-15, -32}, {28, -32}, {28, -66}, {49, -66}}, color = {204, 0, 0}, thickness = 1));
        connect(uhx.tempOut, pipeUHXtoHX.PiTemp_IN) annotation(
          Line(points = {{103, -66}, {140, -66}, {140, 50}, {118, 50}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeUHXtoHX.PiTempOut, heatExchanger.T_in_sFluid) annotation(
          Line(points = {{82, 50}, {57.5, 50}, {57.5, 78}, {68, 78}}, color = {204, 0, 0}, thickness = 1));
        connect(dhrs.tempOut, pipeDHRStoHX.PiTemp_IN) annotation(
          Line(points = {{-229, 43}, {-174, 43}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeDHRStoHX.PiTempOut, heatExchanger.T_in_pFluid) annotation(
          Line(points = {{-122, 43}, {-76, 43}, {-76, 103}, {-42, 103}}, color = {204, 0, 0}, thickness = 1));
        connect(heatExchanger.T_out_pFluid, pipeHXtoCore.PiTemp_IN) annotation(
          Line(points = {{68, 103}, {156, 103}, {156, -137}, {-132, -137}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeCoreToDHRS.PiTempOut, dhrs.tempIn) annotation(
          Line(points = {{-359, 39}, {-322.5, 39}, {-322.5, 43}, {-282, 43}}, color = {204, 0, 0}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeHXtoUHX.PiDecay_Heat) annotation(
          Line(points = {{-39, -159}, {-74, -159}, {-74, -23}, {-51, -23}}, color = {220, 138, 221}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeUHXtoHX.PiDecay_Heat) annotation(
          Line(points = {{-39, -159}, {148, -159}, {148, 59}, {118, 59}}, color = {220, 138, 221}, thickness = 1));
        connect(primaryPump.flowFrac, pipeCoreToDHRS.flowFrac) annotation(
          Line(points = {{-550, 92}, {-468, 92}, {-468, 29}, {-403, 29}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, dhrs.flowFrac) annotation(
          Line(points = {{-550, 92}, {-328, 92}, {-328, 18}, {-282, 18}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeDHRStoHX.flowFrac) annotation(
          Line(points = {{-550, 92}, {-206, 92}, {-206, 30}, {-174, 30}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, heatExchanger.primaryFF) annotation(
          Line(points = {{-550, 92}, {-299.5, 92}, {-299.5, 112}, {-29, 112}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeHXtoCore.flowFrac) annotation(
          Line(points = {{-550, 92}, {-108, 92}, {-108, -127}, {-132, -127}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeUHXtoHX.flowFrac) annotation(
          Line(points = {{175, 92}, {174, 92}, {174, 41}, {118, 41}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, heatExchanger.secondaryFF) annotation(
          Line(points = {{175, 92}, {175, 98}, {174, 98}, {174, -6}, {56, -6}, {56, 69}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, uhx.flowFrac) annotation(
          Line(points = {{175, 92}, {174, 92}, {174, -39}, {49, -39}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeHXtoUHX.flowFrac) annotation(
          Line(points = {{175, 92}, {175, 98}, {176, 98}, {176, -82}, {-51, -82}, {-51, -41}}, color = {245, 121, 0}, thickness = 1));
        connect(pipeHXtoCore.PiTempOut, msre9r.tempIn) annotation(
          Line(points = {{-173, -137}, {-173, -242}, {-552, -242}}, color = {204, 0, 0}, thickness = 1));
        connect(msre9r.volumetricPowerOut, pipeHXtoCore.PiDecay_Heat) annotation(
          Line(points = {{-486, -243}, {-319, -243}, {-319, -146}, {-132, -146}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, pipeCoreToDHRS.PiDecay_Heat) annotation(
          Line(points = {{-486, -243}, {-434, -243}, {-434, 49}, {-403, 49}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, dhrs.pDecay) annotation(
          Line(points = {{-486, -243}, {-318, -243}, {-318, 68}, {-282, 68}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, pipeDHRStoHX.PiDecay_Heat) annotation(
          Line(points = {{-486, -243}, {-200, -243}, {-200, 56}, {-174, 56}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, heatExchanger.P_decay) annotation(
          Line(points = {{-486, -243}, {-434, -243}, {-434, 112}, {13, 112}}, color = {220, 138, 221}, thickness = 1));
        connect(primaryPump.flowFrac, msre9r.flowFracIn) annotation(
          Line(points = {{-550, 92}, {-550, -53}, {-552, -53}, {-552, -178}}, color = {245, 121, 0}, thickness = 1));
        connect(msre9r.tempOut, pipeCoreToDHRS.PiTemp_IN) annotation(
          Line(points = {{-487, -178}, {-486.5, -178}, {-486.5, 39}, {-403, 39}}, color = {204, 0, 0}, thickness = 1));
        connect(externalReact.step, msre9r.realIn) annotation(
          Line(points = {{-594, -46}, {-520, -46}, {-520, -178}}, thickness = 1));
        connect(uhxDemand.realOut, uhx.powDemand) annotation(
          Line(points = {{46, -238}, {62, -238}, {62, -94}}, thickness = 1));
        annotation(
          Diagram(coordinateSystem(extent = {{-680, 160}, {220, -320}})));
      end R9MSRR2dol;

      model R9MSRR1dol
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotFuel = MSRR_PlantData.PrimaryLoop.vdotFuel;
        parameter SMD_MSR_Modelica.Units.Density rhoFuel = MSRR_PlantData.Materials.rhoFuel;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpFuel = MSRR_PlantData.Materials.cpFuel;
        parameter SMD_MSR_Modelica.Units.Conductivity kFuel = MSRR_PlantData.Materials.kFuel;
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotCoolant = MSRR_PlantData.SecondaryLoop.vdotCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoCoolant = MSRR_PlantData.Materials.rhoCoolant;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpCoolant = MSRR_PlantData.Materials.cpCoolant;
        parameter SMD_MSR_Modelica.Units.Conductivity kCoolant = MSRR_PlantData.Materials.kCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoGrap = MSRR_PlantData.Materials.rhoGrap;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpGrap = MSRR_PlantData.Materials.cpGrap;
        parameter SMD_MSR_Modelica.Units.Density rhoHXtube = MSRR_PlantData.Materials.rhoHXtube;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpHXtube = MSRR_PlantData.Materials.cpHXtube;
        MSRR.Components.HeatExchanger heatExchanger(vol_P = MSRR_PlantData.SecondaryLoop.hxVolP, vol_T = MSRR_PlantData.SecondaryLoop.hxVolT, vol_S = MSRR_PlantData.SecondaryLoop.hxVolS, rhoP = rhoFuel, rhoT = rhoHXtube, rhoS = rhoCoolant, cP_P = scpFuel, cP_T = scpHXtube, cP_S = scpCoolant, VdotPnom = volDotFuel, VdotSnom = volDotCoolant, hApNom = MSRR_PlantData.SecondaryLoop.hApNom, hAsNom = MSRR_PlantData.SecondaryLoop.hAsNom, hAExp = 0.33, EnableRad = false, AcShell = MSRR_PlantData.SecondaryLoop.HX.AcShell, AcTube = MSRR_PlantData.SecondaryLoop.HX.AcTube, ArShell = MSRR_PlantData.SecondaryLoop.HX.ArShell, Kp = kFuel, Ks = kCoolant, L_shell = MSRR_PlantData.SecondaryLoop.HX.L_shell, L_tube = MSRR_PlantData.SecondaryLoop.HX.L_tube, e = MSRR_PlantData.SecondaryLoop.HX.e, Tinf = MSRR_PlantData.SecondaryLoop.HX.Tinf, TpIn_0 = MSRR_PlantData.SecondaryLoop.HX.TpIn_0, TpOut_0 = MSRR_PlantData.SecondaryLoop.HX.TpOut_0, TsIn_0 = MSRR_PlantData.SecondaryLoop.HX.TsIn_0, TsOut_0 = MSRR_PlantData.SecondaryLoop.HX.TsOut_0) annotation(
          Placement(transformation(origin = {-10.2, 90.6}, extent = {{-50.8, -25.4}, {76.2, 25.4}})));
        SMD_MSR_Modelica.HeatTransport.UHX uhx(Tp_0 = MSRR_PlantData.SecondaryLoop.UHX.Tp_0, vDot = volDotCoolant, cP = scpCoolant, rho = rhoCoolant, vol = MSRR_PlantData.SecondaryLoop.uhxVol) annotation(
          Placement(transformation(origin = {101.462, -66.3463}, extent = {{-54.8618, -41.1463}, {27.4309, 41.1463}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoUHX(vol = MSRR_PlantData.SecondaryLoop.pipeHXtoUHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.T_0) annotation(
          Placement(transformation(origin = {-28.8, -55.0667}, extent = {{-27.2, 9.06667}, {18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeUHXtoHX(vol = MSRR_PlantData.SecondaryLoop.pipeUHXtoHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.T_0) annotation(
          Placement(transformation(origin = {95.2, 27.7333}, extent = {{27.2, 9.06667}, {-18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.DHRS dhrs(vol = MSRR_PlantData.PrimaryLoop.volLoop[2], rho = rhoFuel, cP = scpFuel, vDotNom = volDotFuel, DHRS_tK = MSRR_PlantData.PrimaryLoop.dhrsTK, DHRS_MaxP_Rm(displayUnit = "MW") = MSRR_PlantData.PrimaryLoop.dhrsMaxRemove, DHRS_P_Bleed = MSRR_PlantData.PrimaryLoop.dhrsBleed, DHRS_time = MSRR_PlantData.PrimaryLoop.dhrsEngageTime, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.Ac, L = MSRR_PlantData.PrimaryLoop.L, Ar = MSRR_PlantData.PrimaryLoop.Ar, EnableRad = false, e = MSRR_PlantData.PrimaryLoop.e, Tinf = MSRR_PlantData.PrimaryLoop.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.T_0) annotation(
          Placement(transformation(origin = {-249, 30.6667}, extent = {{-49.3333, -24.6667}, {37, 49.3333}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeDHRStoHX(vol = MSRR_PlantData.PrimaryLoop.volLoop[3], volFracNode = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ac, L = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ar, e = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.T_0) annotation(
          Placement(transformation(origin = {-141.6, 11.2}, extent = {{-38.4, 12.8}, {25.6, 51.2}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoCore(vol = MSRR_PlantData.PrimaryLoop.volLoop[5], volFracNode = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ac, L = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ar, e = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.T_0) annotation(
          Placement(transformation(origin = {-158.4, -105.6}, extent = {{-37.2, 12.4}, {24.8, 49.6}}, rotation = 180)));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeCoreToDHRS(vol = MSRR_PlantData.PrimaryLoop.volLoop[1], volFracNode = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ac, L = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ar, e = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.T_0) annotation(
          Placement(transformation(origin = {-374, 6}, extent = {{-39.6, 13.2}, {26.4, 52.8}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
          Placement(transformation(origin = {-37, -175}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.HeatTransport.Pump primaryPump(numRampUp = MSRR_PlantData.Pumps.primaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.primaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.primaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.primaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {-550, 116}, extent = {{-24, -32}, {24, 16}})));
        SMD_MSR_Modelica.HeatTransport.Pump secondaryPump(numRampUp = MSRR_PlantData.Pumps.secondaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.secondaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.secondaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.secondaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {175, 114.667}, extent = {{-23, -30.6667}, {23, 15.3333}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantReal uhxDemand(magnitude = MSRR_PlantData.nominalPower) annotation(
          Placement(transformation(origin = {43, -251}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.Signals.TimeDependent.Stepper externalReact(numSteps = 2, stepTime = {0, 4000}, amplitude = {0, 604.887}) annotation(
          Placement(transformation(origin = {-596.198, -38.1983}, extent = {{-23.7983, 23.7983}, {23.7983, -23.7983}}, rotation = -0)));
        MSRR.Components.MSRR9R msre9r(numGroups = MSRR_PlantData.Kinetics.numGroups, lambda = MSRR_PlantData.Kinetics.lambda, beta = MSRR_PlantData.Kinetics.beta, LAMBDA = MSRR_PlantData.Kinetics.LAMBDA, n_0 = 1, aF = MSRR_PlantData.Kinetics.a_F, aG = MSRR_PlantData.Kinetics.a_G, volF1 = MSRR_PlantData.Core9R.volF1, volF2 = MSRR_PlantData.Core9R.volF2, volG = MSRR_PlantData.Core9R.volG, hA = MSRR_PlantData.Core9R.hA, kFN1 = MSRR_PlantData.Core9R.kFN1, kFN2 = MSRR_PlantData.Core9R.kFN2, kHT1 = MSRR_PlantData.Core9R.kHT1, kHT2 = MSRR_PlantData.Core9R.kHT2, TF1_0 = MSRR_PlantData.Core9R.TF1_0_regions, TF2_0 = MSRR_PlantData.Core9R.TF2_0_regions, TG_0 = MSRR_PlantData.Core9R.TG_0_regions, Tmix_0 = MSRR_PlantData.Core9R.Tmix_0, IF1 = MSRR_PlantData.Core9R.IF1, IF2 = MSRR_PlantData.Core9R.IF2, IG = MSRR_PlantData.Core9R.IG, flowFracRegions = MSRR_PlantData.Core9R.flowFracRegions, rho_fuel = rhoFuel, cP_fuel = scpFuel, kFuel = kFuel, volDotFuel = volDotFuel, rho_grap = rhoGrap, cP_grap = scpGrap, regionTripTime = MSRR_PlantData.Core9R.regionTripTime, regionCoastDownK = MSRR_PlantData.Core9R.regionCoastDownK, freeConvFF = MSRR_PlantData.Pumps.freeConvFF, EnableRad = false, T_inf = 500, LF1 = MSRR_PlantData.Core9R.LF1, LF2 = MSRR_PlantData.Core9R.LF2, Ac = MSRR_PlantData.Core9R.Ac, ArF1 = MSRR_PlantData.Core9R.ArF1, ArF2 = MSRR_PlantData.Core9R.ArF2, e = MSRR_PlantData.Core9R.e) annotation(
          Placement(transformation(origin = {-260.333, -15}, extent = {{-179.667, -147}, {-81.6667, -49}})));
      equation
        connect(heatExchanger.T_out_sFluid, pipeHXtoUHX.PiTemp_IN) annotation(
          Line(points = {{-40, 78}, {-94, 78}, {-94, -32}, {-51, -32}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeHXtoUHX.PiTempOut, uhx.tempIn) annotation(
          Line(points = {{-15, -32}, {28, -32}, {28, -66}, {47, -66}}, color = {204, 0, 0}, thickness = 1));
        connect(uhx.tempOut, pipeUHXtoHX.PiTemp_IN) annotation(
          Line(points = {{101, -66}, {140, -66}, {140, 50}, {118, 50}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeUHXtoHX.PiTempOut, heatExchanger.T_in_sFluid) annotation(
          Line(points = {{82, 50}, {57.5, 50}, {57.5, 78}, {70, 78}}, color = {204, 0, 0}, thickness = 1));
        connect(dhrs.tempOut, pipeDHRStoHX.PiTemp_IN) annotation(
          Line(points = {{-229, 43}, {-174, 43}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeDHRStoHX.PiTempOut, heatExchanger.T_in_pFluid) annotation(
          Line(points = {{-122, 43}, {-76, 43}, {-76, 103}, {-40, 103}}, color = {204, 0, 0}, thickness = 1));
        connect(heatExchanger.T_out_pFluid, pipeHXtoCore.PiTemp_IN) annotation(
          Line(points = {{70, 103}, {156, 103}, {156, -137}, {-132, -137}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeCoreToDHRS.PiTempOut, dhrs.tempIn) annotation(
          Line(points = {{-359, 39}, {-322.5, 39}, {-322.5, 43}, {-282, 43}}, color = {204, 0, 0}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeHXtoUHX.PiDecay_Heat) annotation(
          Line(points = {{-39, -159}, {-74, -159}, {-74, -23}, {-51, -23}}, color = {220, 138, 221}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeUHXtoHX.PiDecay_Heat) annotation(
          Line(points = {{-39, -159}, {148, -159}, {148, 59}, {118, 59}}, color = {220, 138, 221}, thickness = 1));
        connect(primaryPump.flowFrac, pipeCoreToDHRS.flowFrac) annotation(
          Line(points = {{-550, 92}, {-468, 92}, {-468, 29}, {-403, 29}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, dhrs.flowFrac) annotation(
          Line(points = {{-550, 92}, {-328, 92}, {-328, 18}, {-282, 18}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeDHRStoHX.flowFrac) annotation(
          Line(points = {{-550, 92}, {-206, 92}, {-206, 30}, {-174, 30}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, heatExchanger.primaryFF) annotation(
          Line(points = {{-550, 92}, {-299.5, 92}, {-299.5, 112}, {-27, 112}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeHXtoCore.flowFrac) annotation(
          Line(points = {{-550, 92}, {-108, 92}, {-108, -127}, {-132, -127}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeUHXtoHX.flowFrac) annotation(
          Line(points = {{175, 92}, {174, 92}, {174, 41}, {118, 41}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, heatExchanger.secondaryFF) annotation(
          Line(points = {{175, 92}, {175, 98}, {174, 98}, {174, -6}, {58, -6}, {58, 69}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, uhx.flowFrac) annotation(
          Line(points = {{175, 92}, {174, 92}, {174, -39}, {47, -39}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeHXtoUHX.flowFrac) annotation(
          Line(points = {{175, 92}, {175, 98}, {176, 98}, {176, -82}, {-51, -82}, {-51, -41}}, color = {245, 121, 0}, thickness = 1));
        connect(pipeHXtoCore.PiTempOut, msre9r.tempIn) annotation(
          Line(points = {{-173, -137}, {-175, -242}, {-554, -242}}, color = {204, 0, 0}, thickness = 1));
        connect(msre9r.volumetricPowerOut, pipeHXtoCore.PiDecay_Heat) annotation(
          Line(points = {{-488, -243}, {-319, -243}, {-319, -146}, {-132, -146}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, pipeCoreToDHRS.PiDecay_Heat) annotation(
          Line(points = {{-488, -243}, {-434, -243}, {-434, 49}, {-403, 49}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, dhrs.pDecay) annotation(
          Line(points = {{-488, -243}, {-318, -243}, {-318, 68}, {-282, 68}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, pipeDHRStoHX.PiDecay_Heat) annotation(
          Line(points = {{-488, -243}, {-200, -243}, {-200, 56}, {-174, 56}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, heatExchanger.P_decay) annotation(
          Line(points = {{-488, -243}, {-434, -243}, {-434, 112}, {15, 112}}, color = {220, 138, 221}, thickness = 1));
        connect(primaryPump.flowFrac, msre9r.flowFracIn) annotation(
          Line(points = {{-550, 92}, {-550, -53}, {-554, -53}, {-554, -178}}, color = {245, 121, 0}, thickness = 1));
        connect(msre9r.tempOut, pipeCoreToDHRS.PiTemp_IN) annotation(
          Line(points = {{-489, -178}, {-486.5, -178}, {-486.5, 39}, {-403, 39}}, color = {204, 0, 0}, thickness = 1));
        connect(externalReact.step, msre9r.realIn) annotation(
          Line(points = {{-594, -46}, {-520, -46}, {-522, -178}}, thickness = 1));
        connect(uhxDemand.realOut, uhx.powDemand) annotation(
          Line(points = {{44, -237.5}, {62, -237.5}, {62, -94}, {47, -94}}, thickness = 1));
        annotation(
          Diagram(coordinateSystem(extent = {{-680, 160}, {220, -320}})));
      end R9MSRR1dol;

      model R9MSRRhalfDol
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotFuel = MSRR_PlantData.PrimaryLoop.vdotFuel;
        parameter SMD_MSR_Modelica.Units.Density rhoFuel = MSRR_PlantData.Materials.rhoFuel;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpFuel = MSRR_PlantData.Materials.cpFuel;
        parameter SMD_MSR_Modelica.Units.Conductivity kFuel = MSRR_PlantData.Materials.kFuel;
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotCoolant = MSRR_PlantData.SecondaryLoop.vdotCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoCoolant = MSRR_PlantData.Materials.rhoCoolant;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpCoolant = MSRR_PlantData.Materials.cpCoolant;
        parameter SMD_MSR_Modelica.Units.Conductivity kCoolant = MSRR_PlantData.Materials.kCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoGrap = MSRR_PlantData.Materials.rhoGrap;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpGrap = MSRR_PlantData.Materials.cpGrap;
        parameter SMD_MSR_Modelica.Units.Density rhoHXtube = MSRR_PlantData.Materials.rhoHXtube;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpHXtube = MSRR_PlantData.Materials.cpHXtube;
        MSRR.Components.HeatExchanger heatExchanger(vol_P = MSRR_PlantData.SecondaryLoop.hxVolP, vol_T = MSRR_PlantData.SecondaryLoop.hxVolT, vol_S = MSRR_PlantData.SecondaryLoop.hxVolS, rhoP = rhoFuel, rhoT = rhoHXtube, rhoS = rhoCoolant, cP_P = scpFuel, cP_T = scpHXtube, cP_S = scpCoolant, VdotPnom = volDotFuel, VdotSnom = volDotCoolant, hApNom = MSRR_PlantData.SecondaryLoop.hApNom, hAsNom = MSRR_PlantData.SecondaryLoop.hAsNom, hAExp = 0.33, EnableRad = false, AcShell = MSRR_PlantData.SecondaryLoop.HX.AcShell, AcTube = MSRR_PlantData.SecondaryLoop.HX.AcTube, ArShell = MSRR_PlantData.SecondaryLoop.HX.ArShell, Kp = kFuel, Ks = kCoolant, L_shell = MSRR_PlantData.SecondaryLoop.HX.L_shell, L_tube = MSRR_PlantData.SecondaryLoop.HX.L_tube, e = MSRR_PlantData.SecondaryLoop.HX.e, Tinf = MSRR_PlantData.SecondaryLoop.HX.Tinf, TpIn_0 = MSRR_PlantData.SecondaryLoop.HX.TpIn_0, TpOut_0 = MSRR_PlantData.SecondaryLoop.HX.TpOut_0, TsIn_0 = MSRR_PlantData.SecondaryLoop.HX.TsIn_0, TsOut_0 = MSRR_PlantData.SecondaryLoop.HX.TsOut_0) annotation(
          Placement(transformation(origin = {-10.2, 90.6}, extent = {{-50.8, -25.4}, {76.2, 25.4}})));
        SMD_MSR_Modelica.HeatTransport.UHX uhx(Tp_0 = MSRR_PlantData.SecondaryLoop.UHX.Tp_0, vDot = volDotCoolant, cP = scpCoolant, rho = rhoCoolant, vol = MSRR_PlantData.SecondaryLoop.uhxVol) annotation(
          Placement(transformation(origin = {101.462, -66.3463}, extent = {{-54.8618, -41.1463}, {27.4309, 41.1463}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoUHX(vol = MSRR_PlantData.SecondaryLoop.pipeHXtoUHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.T_0) annotation(
          Placement(transformation(origin = {-28.8, -55.0667}, extent = {{-27.2, 9.06667}, {18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeUHXtoHX(vol = MSRR_PlantData.SecondaryLoop.pipeUHXtoHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.T_0) annotation(
          Placement(transformation(origin = {95.2, 27.7333}, extent = {{27.2, 9.06667}, {-18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.DHRS dhrs(vol = MSRR_PlantData.PrimaryLoop.volLoop[2], rho = rhoFuel, cP = scpFuel, vDotNom = volDotFuel, DHRS_tK = MSRR_PlantData.PrimaryLoop.dhrsTK, DHRS_MaxP_Rm(displayUnit = "MW") = MSRR_PlantData.PrimaryLoop.dhrsMaxRemove, DHRS_P_Bleed = MSRR_PlantData.PrimaryLoop.dhrsBleed, DHRS_time = MSRR_PlantData.PrimaryLoop.dhrsEngageTime, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.Ac, L = MSRR_PlantData.PrimaryLoop.L, Ar = MSRR_PlantData.PrimaryLoop.Ar, EnableRad = false, e = MSRR_PlantData.PrimaryLoop.e, Tinf = MSRR_PlantData.PrimaryLoop.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.T_0) annotation(
          Placement(transformation(origin = {-249, 30.6667}, extent = {{-49.3333, -24.6667}, {37, 49.3333}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeDHRStoHX(vol = MSRR_PlantData.PrimaryLoop.volLoop[3], volFracNode = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ac, L = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ar, e = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.T_0) annotation(
          Placement(transformation(origin = {-141.6, 11.2}, extent = {{-38.4, 12.8}, {25.6, 51.2}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoCore(vol = MSRR_PlantData.PrimaryLoop.volLoop[5], volFracNode = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ac, L = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ar, e = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.T_0) annotation(
          Placement(transformation(origin = {-158.4, -105.6}, extent = {{-37.2, 12.4}, {24.8, 49.6}}, rotation = 180)));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeCoreToDHRS(vol = MSRR_PlantData.PrimaryLoop.volLoop[1], volFracNode = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ac, L = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ar, e = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.T_0) annotation(
          Placement(transformation(origin = {-374, 6}, extent = {{-39.6, 13.2}, {26.4, 52.8}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
          Placement(transformation(origin = {-37, -175}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.HeatTransport.Pump primaryPump(numRampUp = MSRR_PlantData.Pumps.primaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.primaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.primaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.primaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {-550, 116}, extent = {{-24, -32}, {24, 16}})));
        SMD_MSR_Modelica.HeatTransport.Pump secondaryPump(numRampUp = MSRR_PlantData.Pumps.secondaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.secondaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.secondaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.secondaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {175, 114.667}, extent = {{-23, -30.6667}, {23, 15.3333}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantReal uhxDemand(magnitude = MSRR_PlantData.nominalPower) annotation(
          Placement(transformation(origin = {43, -251}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.Signals.TimeDependent.Stepper externalReact(numSteps = 2, stepTime = {0, 4000}, amplitude = {0, 294.535}) annotation(
          Placement(transformation(origin = {-596.198, -38.1983}, extent = {{-23.7983, 23.7983}, {23.7983, -23.7983}}, rotation = -0)));
        MSRR.Components.MSRR9R msre9r(numGroups = MSRR_PlantData.Kinetics.numGroups, lambda = MSRR_PlantData.Kinetics.lambda, beta = MSRR_PlantData.Kinetics.beta, LAMBDA = MSRR_PlantData.Kinetics.LAMBDA, n_0 = 1, aF = MSRR_PlantData.Kinetics.a_F, aG = MSRR_PlantData.Kinetics.a_G, volF1 = MSRR_PlantData.Core9R.volF1, volF2 = MSRR_PlantData.Core9R.volF2, volG = MSRR_PlantData.Core9R.volG, hA = MSRR_PlantData.Core9R.hA, kFN1 = MSRR_PlantData.Core9R.kFN1, kFN2 = MSRR_PlantData.Core9R.kFN2, kHT1 = MSRR_PlantData.Core9R.kHT1, kHT2 = MSRR_PlantData.Core9R.kHT2, TF1_0 = MSRR_PlantData.Core9R.TF1_0_regions, TF2_0 = MSRR_PlantData.Core9R.TF2_0_regions, TG_0 = MSRR_PlantData.Core9R.TG_0_regions, Tmix_0 = MSRR_PlantData.Core9R.Tmix_0, IF1 = MSRR_PlantData.Core9R.IF1, IF2 = MSRR_PlantData.Core9R.IF2, IG = MSRR_PlantData.Core9R.IG, flowFracRegions = MSRR_PlantData.Core9R.flowFracRegions, rho_fuel = rhoFuel, cP_fuel = scpFuel, kFuel = kFuel, volDotFuel = volDotFuel, rho_grap = rhoGrap, cP_grap = scpGrap, regionTripTime = MSRR_PlantData.Core9R.regionTripTime, regionCoastDownK = MSRR_PlantData.Core9R.regionCoastDownK, freeConvFF = MSRR_PlantData.Pumps.freeConvFF, EnableRad = false, T_inf = 500, LF1 = MSRR_PlantData.Core9R.LF1, LF2 = MSRR_PlantData.Core9R.LF2, Ac = MSRR_PlantData.Core9R.Ac, ArF1 = MSRR_PlantData.Core9R.ArF1, ArF2 = MSRR_PlantData.Core9R.ArF2, e = MSRR_PlantData.Core9R.e) annotation(
          Placement(transformation(origin = {-260.333, -15}, extent = {{-179.667, -147}, {-81.6667, -49}})));
      equation
        connect(heatExchanger.T_out_sFluid, pipeHXtoUHX.PiTemp_IN) annotation(
          Line(points = {{-40, 78}, {-94, 78}, {-94, -32}, {-51, -32}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeHXtoUHX.PiTempOut, uhx.tempIn) annotation(
          Line(points = {{-15, -32}, {28, -32}, {28, -66}, {47, -66}}, color = {204, 0, 0}, thickness = 1));
        connect(uhx.tempOut, pipeUHXtoHX.PiTemp_IN) annotation(
          Line(points = {{101, -66}, {140, -66}, {140, 50}, {118, 50}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeUHXtoHX.PiTempOut, heatExchanger.T_in_sFluid) annotation(
          Line(points = {{82, 50}, {57.5, 50}, {57.5, 78}, {70, 78}}, color = {204, 0, 0}, thickness = 1));
        connect(dhrs.tempOut, pipeDHRStoHX.PiTemp_IN) annotation(
          Line(points = {{-229, 43}, {-174, 43}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeDHRStoHX.PiTempOut, heatExchanger.T_in_pFluid) annotation(
          Line(points = {{-122, 43}, {-76, 43}, {-76, 103}, {-40, 103}}, color = {204, 0, 0}, thickness = 1));
        connect(heatExchanger.T_out_pFluid, pipeHXtoCore.PiTemp_IN) annotation(
          Line(points = {{70, 103}, {156, 103}, {156, -137}, {-132, -137}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeCoreToDHRS.PiTempOut, dhrs.tempIn) annotation(
          Line(points = {{-359, 39}, {-322.5, 39}, {-322.5, 43}, {-282, 43}}, color = {204, 0, 0}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeHXtoUHX.PiDecay_Heat) annotation(
          Line(points = {{-39, -159}, {-74, -159}, {-74, -23}, {-51, -23}}, color = {220, 138, 221}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeUHXtoHX.PiDecay_Heat) annotation(
          Line(points = {{-39, -159}, {148, -159}, {148, 59}, {118, 59}}, color = {220, 138, 221}, thickness = 1));
        connect(primaryPump.flowFrac, pipeCoreToDHRS.flowFrac) annotation(
          Line(points = {{-550, 92}, {-468, 92}, {-468, 29}, {-403, 29}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, dhrs.flowFrac) annotation(
          Line(points = {{-550, 92}, {-328, 92}, {-328, 18}, {-282, 18}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeDHRStoHX.flowFrac) annotation(
          Line(points = {{-550, 92}, {-206, 92}, {-206, 30}, {-174, 30}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, heatExchanger.primaryFF) annotation(
          Line(points = {{-550, 92}, {-299.5, 92}, {-299.5, 112}, {-27, 112}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeHXtoCore.flowFrac) annotation(
          Line(points = {{-550, 92}, {-108, 92}, {-108, -127}, {-132, -127}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeUHXtoHX.flowFrac) annotation(
          Line(points = {{175, 92}, {174, 92}, {174, 41}, {118, 41}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, heatExchanger.secondaryFF) annotation(
          Line(points = {{175, 92}, {175, 98}, {174, 98}, {174, -6}, {58, -6}, {58, 69}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, uhx.flowFrac) annotation(
          Line(points = {{175, 92}, {174, 92}, {174, -39}, {47, -39}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeHXtoUHX.flowFrac) annotation(
          Line(points = {{175, 92}, {175, 98}, {176, 98}, {176, -82}, {-51, -82}, {-51, -41}}, color = {245, 121, 0}, thickness = 1));
        connect(pipeHXtoCore.PiTempOut, msre9r.tempIn) annotation(
          Line(points = {{-173, -137}, {-175, -242}, {-554, -242}}, color = {204, 0, 0}, thickness = 1));
        connect(msre9r.volumetricPowerOut, pipeHXtoCore.PiDecay_Heat) annotation(
          Line(points = {{-488, -243}, {-319, -243}, {-319, -146}, {-132, -146}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, pipeCoreToDHRS.PiDecay_Heat) annotation(
          Line(points = {{-488, -243}, {-434, -243}, {-434, 49}, {-403, 49}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, dhrs.pDecay) annotation(
          Line(points = {{-488, -243}, {-318, -243}, {-318, 68}, {-282, 68}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, pipeDHRStoHX.PiDecay_Heat) annotation(
          Line(points = {{-488, -243}, {-200, -243}, {-200, 56}, {-174, 56}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, heatExchanger.P_decay) annotation(
          Line(points = {{-488, -243}, {-434, -243}, {-434, 112}, {15, 112}}, color = {220, 138, 221}, thickness = 1));
        connect(primaryPump.flowFrac, msre9r.flowFracIn) annotation(
          Line(points = {{-550, 92}, {-550, -53}, {-554, -53}, {-554, -178}}, color = {245, 121, 0}, thickness = 1));
        connect(msre9r.tempOut, pipeCoreToDHRS.PiTemp_IN) annotation(
          Line(points = {{-489, -178}, {-486.5, -178}, {-486.5, 39}, {-403, 39}}, color = {204, 0, 0}, thickness = 1));
        connect(externalReact.step, msre9r.realIn) annotation(
          Line(points = {{-594, -46}, {-520, -46}, {-522, -178}}, thickness = 1));
        connect(uhxDemand.realOut, uhx.powDemand) annotation(
          Line(points = {{44, -237.5}, {62, -237.5}, {62, -94}, {47, -94}}, thickness = 1));
        annotation(
          Diagram(coordinateSystem(extent = {{-680, 160}, {220, -320}})));
      end R9MSRRhalfDol;

      model R9MSRRpOneDol
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotFuel = MSRR_PlantData.PrimaryLoop.vdotFuel;
        parameter SMD_MSR_Modelica.Units.Density rhoFuel = MSRR_PlantData.Materials.rhoFuel;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpFuel = MSRR_PlantData.Materials.cpFuel;
        parameter SMD_MSR_Modelica.Units.Conductivity kFuel = MSRR_PlantData.Materials.kFuel;
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotCoolant = MSRR_PlantData.SecondaryLoop.vdotCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoCoolant = MSRR_PlantData.Materials.rhoCoolant;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpCoolant = MSRR_PlantData.Materials.cpCoolant;
        parameter SMD_MSR_Modelica.Units.Conductivity kCoolant = MSRR_PlantData.Materials.kCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoGrap = MSRR_PlantData.Materials.rhoGrap;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpGrap = MSRR_PlantData.Materials.cpGrap;
        parameter SMD_MSR_Modelica.Units.Density rhoHXtube = MSRR_PlantData.Materials.rhoHXtube;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpHXtube = MSRR_PlantData.Materials.cpHXtube;
        MSRR.Components.HeatExchanger heatExchanger(vol_P = MSRR_PlantData.SecondaryLoop.hxVolP, vol_T = MSRR_PlantData.SecondaryLoop.hxVolT, vol_S = MSRR_PlantData.SecondaryLoop.hxVolS, rhoP = rhoFuel, rhoT = rhoHXtube, rhoS = rhoCoolant, cP_P = scpFuel, cP_T = scpHXtube, cP_S = scpCoolant, VdotPnom = volDotFuel, VdotSnom = volDotCoolant, hApNom = MSRR_PlantData.SecondaryLoop.hApNom, hAsNom = MSRR_PlantData.SecondaryLoop.hAsNom, hAExp = 0.33, EnableRad = false, AcShell = MSRR_PlantData.SecondaryLoop.HX.AcShell, AcTube = MSRR_PlantData.SecondaryLoop.HX.AcTube, ArShell = MSRR_PlantData.SecondaryLoop.HX.ArShell, Kp = kFuel, Ks = kCoolant, L_shell = MSRR_PlantData.SecondaryLoop.HX.L_shell, L_tube = MSRR_PlantData.SecondaryLoop.HX.L_tube, e = MSRR_PlantData.SecondaryLoop.HX.e, Tinf = MSRR_PlantData.SecondaryLoop.HX.Tinf, TpIn_0 = MSRR_PlantData.SecondaryLoop.HX.TpIn_0, TpOut_0 = MSRR_PlantData.SecondaryLoop.HX.TpOut_0, TsIn_0 = MSRR_PlantData.SecondaryLoop.HX.TsIn_0, TsOut_0 = MSRR_PlantData.SecondaryLoop.HX.TsOut_0) annotation(
          Placement(transformation(origin = {-10.2, 90.6}, extent = {{-50.8, -25.4}, {76.2, 25.4}})));
        SMD_MSR_Modelica.HeatTransport.UHX uhx(Tp_0 = MSRR_PlantData.SecondaryLoop.UHX.Tp_0, vDot = volDotCoolant, cP = scpCoolant, rho = rhoCoolant, vol = MSRR_PlantData.SecondaryLoop.uhxVol) annotation(
          Placement(transformation(origin = {101.462, -66.3463}, extent = {{-54.8618, -41.1463}, {27.4309, 41.1463}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoUHX(vol = MSRR_PlantData.SecondaryLoop.pipeHXtoUHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.T_0) annotation(
          Placement(transformation(origin = {-28.8, -55.0667}, extent = {{-27.2, 9.06667}, {18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeUHXtoHX(vol = MSRR_PlantData.SecondaryLoop.pipeUHXtoHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.T_0) annotation(
          Placement(transformation(origin = {95.2, 27.7333}, extent = {{27.2, 9.06667}, {-18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.DHRS dhrs(vol = MSRR_PlantData.PrimaryLoop.volLoop[2], rho = rhoFuel, cP = scpFuel, vDotNom = volDotFuel, DHRS_tK = MSRR_PlantData.PrimaryLoop.dhrsTK, DHRS_MaxP_Rm(displayUnit = "MW") = MSRR_PlantData.PrimaryLoop.dhrsMaxRemove, DHRS_P_Bleed = MSRR_PlantData.PrimaryLoop.dhrsBleed, DHRS_time = MSRR_PlantData.PrimaryLoop.dhrsEngageTime, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.Ac, L = MSRR_PlantData.PrimaryLoop.L, Ar = MSRR_PlantData.PrimaryLoop.Ar, EnableRad = false, e = MSRR_PlantData.PrimaryLoop.e, Tinf = MSRR_PlantData.PrimaryLoop.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.T_0) annotation(
          Placement(transformation(origin = {-249, 30.6667}, extent = {{-49.3333, -24.6667}, {37, 49.3333}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeDHRStoHX(vol = MSRR_PlantData.PrimaryLoop.volLoop[3], volFracNode = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ac, L = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ar, e = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.T_0) annotation(
          Placement(transformation(origin = {-141.6, 11.2}, extent = {{-38.4, 12.8}, {25.6, 51.2}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoCore(vol = MSRR_PlantData.PrimaryLoop.volLoop[5], volFracNode = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ac, L = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ar, e = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.T_0) annotation(
          Placement(transformation(origin = {-158.4, -105.6}, extent = {{-37.2, 12.4}, {24.8, 49.6}}, rotation = 180)));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeCoreToDHRS(vol = MSRR_PlantData.PrimaryLoop.volLoop[1], volFracNode = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ac, L = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ar, e = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.T_0) annotation(
          Placement(transformation(origin = {-374, 6}, extent = {{-39.6, 13.2}, {26.4, 52.8}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
          Placement(transformation(origin = {-37, -175}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.HeatTransport.Pump primaryPump(numRampUp = MSRR_PlantData.Pumps.primaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.primaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.primaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.primaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {-550, 116}, extent = {{-24, -32}, {24, 16}})));
        SMD_MSR_Modelica.HeatTransport.Pump secondaryPump(numRampUp = MSRR_PlantData.Pumps.secondaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.secondaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.secondaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.secondaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {175, 114.667}, extent = {{-23, -30.6667}, {23, 15.3333}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantReal uhxDemand(magnitude = MSRR_PlantData.nominalPower) annotation(
          Placement(transformation(origin = {43, -251}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.Signals.TimeDependent.Stepper externalReact(numSteps = 2, stepTime = {0, 4000}, amplitude = {0, 58.907}) annotation(
          Placement(transformation(origin = {-596.198, -38.1983}, extent = {{-23.7983, 23.7983}, {23.7983, -23.7983}}, rotation = -0)));
        MSRR.Components.MSRR9R msre9r(numGroups = MSRR_PlantData.Kinetics.numGroups, lambda = MSRR_PlantData.Kinetics.lambda, beta = MSRR_PlantData.Kinetics.beta, LAMBDA = MSRR_PlantData.Kinetics.LAMBDA, n_0 = 1, aF = MSRR_PlantData.Kinetics.a_F, aG = MSRR_PlantData.Kinetics.a_G, volF1 = MSRR_PlantData.Core9R.volF1, volF2 = MSRR_PlantData.Core9R.volF2, volG = MSRR_PlantData.Core9R.volG, hA = MSRR_PlantData.Core9R.hA, kFN1 = MSRR_PlantData.Core9R.kFN1, kFN2 = MSRR_PlantData.Core9R.kFN2, kHT1 = MSRR_PlantData.Core9R.kHT1, kHT2 = MSRR_PlantData.Core9R.kHT2, TF1_0 = MSRR_PlantData.Core9R.TF1_0_regions, TF2_0 = MSRR_PlantData.Core9R.TF2_0_regions, TG_0 = MSRR_PlantData.Core9R.TG_0_regions, Tmix_0 = MSRR_PlantData.Core9R.Tmix_0, IF1 = MSRR_PlantData.Core9R.IF1, IF2 = MSRR_PlantData.Core9R.IF2, IG = MSRR_PlantData.Core9R.IG, flowFracRegions = MSRR_PlantData.Core9R.flowFracRegions, rho_fuel = rhoFuel, cP_fuel = scpFuel, kFuel = kFuel, volDotFuel = volDotFuel, rho_grap = rhoGrap, cP_grap = scpGrap, regionTripTime = MSRR_PlantData.Core9R.regionTripTime, regionCoastDownK = MSRR_PlantData.Core9R.regionCoastDownK, freeConvFF = MSRR_PlantData.Pumps.freeConvFF, EnableRad = false, T_inf = 500, LF1 = MSRR_PlantData.Core9R.LF1, LF2 = MSRR_PlantData.Core9R.LF2, Ac = MSRR_PlantData.Core9R.Ac, ArF1 = MSRR_PlantData.Core9R.ArF1, ArF2 = MSRR_PlantData.Core9R.ArF2, e = MSRR_PlantData.Core9R.e) annotation(
          Placement(transformation(origin = {-260.333, -15}, extent = {{-179.667, -147}, {-81.6667, -49}})));
      equation
        connect(heatExchanger.T_out_sFluid, pipeHXtoUHX.PiTemp_IN) annotation(
          Line(points = {{-40, 78}, {-94, 78}, {-94, -32}, {-51, -32}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeHXtoUHX.PiTempOut, uhx.tempIn) annotation(
          Line(points = {{-15, -32}, {28, -32}, {28, -66}, {47, -66}}, color = {204, 0, 0}, thickness = 1));
        connect(uhx.tempOut, pipeUHXtoHX.PiTemp_IN) annotation(
          Line(points = {{101, -66}, {140, -66}, {140, 50}, {118, 50}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeUHXtoHX.PiTempOut, heatExchanger.T_in_sFluid) annotation(
          Line(points = {{82, 50}, {57.5, 50}, {57.5, 78}, {70, 78}}, color = {204, 0, 0}, thickness = 1));
        connect(dhrs.tempOut, pipeDHRStoHX.PiTemp_IN) annotation(
          Line(points = {{-229, 43}, {-174, 43}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeDHRStoHX.PiTempOut, heatExchanger.T_in_pFluid) annotation(
          Line(points = {{-122, 43}, {-76, 43}, {-76, 103}, {-40, 103}}, color = {204, 0, 0}, thickness = 1));
        connect(heatExchanger.T_out_pFluid, pipeHXtoCore.PiTemp_IN) annotation(
          Line(points = {{70, 103}, {156, 103}, {156, -137}, {-132, -137}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeCoreToDHRS.PiTempOut, dhrs.tempIn) annotation(
          Line(points = {{-359, 39}, {-322.5, 39}, {-322.5, 43}, {-282, 43}}, color = {204, 0, 0}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeHXtoUHX.PiDecay_Heat) annotation(
          Line(points = {{-39, -159}, {-74, -159}, {-74, -23}, {-51, -23}}, color = {220, 138, 221}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeUHXtoHX.PiDecay_Heat) annotation(
          Line(points = {{-39, -159}, {148, -159}, {148, 59}, {118, 59}}, color = {220, 138, 221}, thickness = 1));
        connect(primaryPump.flowFrac, pipeCoreToDHRS.flowFrac) annotation(
          Line(points = {{-550, 92}, {-468, 92}, {-468, 29}, {-403, 29}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, dhrs.flowFrac) annotation(
          Line(points = {{-550, 92}, {-328, 92}, {-328, 18}, {-282, 18}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeDHRStoHX.flowFrac) annotation(
          Line(points = {{-550, 92}, {-206, 92}, {-206, 30}, {-174, 30}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, heatExchanger.primaryFF) annotation(
          Line(points = {{-550, 92}, {-299.5, 92}, {-299.5, 112}, {-27, 112}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeHXtoCore.flowFrac) annotation(
          Line(points = {{-550, 92}, {-108, 92}, {-108, -127}, {-132, -127}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeUHXtoHX.flowFrac) annotation(
          Line(points = {{175, 92}, {174, 92}, {174, 41}, {118, 41}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, heatExchanger.secondaryFF) annotation(
          Line(points = {{175, 92}, {175, 98}, {174, 98}, {174, -6}, {58, -6}, {58, 69}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, uhx.flowFrac) annotation(
          Line(points = {{175, 92}, {174, 92}, {174, -39}, {47, -39}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeHXtoUHX.flowFrac) annotation(
          Line(points = {{175, 92}, {175, 98}, {176, 98}, {176, -82}, {-51, -82}, {-51, -41}}, color = {245, 121, 0}, thickness = 1));
        connect(pipeHXtoCore.PiTempOut, msre9r.tempIn) annotation(
          Line(points = {{-173, -137}, {-175, -242}, {-554, -242}}, color = {204, 0, 0}, thickness = 1));
        connect(msre9r.volumetricPowerOut, pipeHXtoCore.PiDecay_Heat) annotation(
          Line(points = {{-488, -243}, {-319, -243}, {-319, -146}, {-132, -146}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, pipeCoreToDHRS.PiDecay_Heat) annotation(
          Line(points = {{-488, -243}, {-434, -243}, {-434, 49}, {-403, 49}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, dhrs.pDecay) annotation(
          Line(points = {{-488, -243}, {-318, -243}, {-318, 68}, {-282, 68}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, pipeDHRStoHX.PiDecay_Heat) annotation(
          Line(points = {{-488, -243}, {-200, -243}, {-200, 56}, {-174, 56}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, heatExchanger.P_decay) annotation(
          Line(points = {{-488, -243}, {-434, -243}, {-434, 112}, {15, 112}}, color = {220, 138, 221}, thickness = 1));
        connect(primaryPump.flowFrac, msre9r.flowFracIn) annotation(
          Line(points = {{-550, 92}, {-550, -53}, {-554, -53}, {-554, -178}}, color = {245, 121, 0}, thickness = 1));
        connect(msre9r.tempOut, pipeCoreToDHRS.PiTemp_IN) annotation(
          Line(points = {{-489, -178}, {-486.5, -178}, {-486.5, 39}, {-403, 39}}, color = {204, 0, 0}, thickness = 1));
        connect(externalReact.step, msre9r.realIn) annotation(
          Line(points = {{-594, -46}, {-520, -46}, {-522, -178}}, thickness = 1));
        connect(uhxDemand.realOut, uhx.powDemand) annotation(
          Line(points = {{44, -237.5}, {62, -237.5}, {62, -94}, {47, -94}}, thickness = 1));
        annotation(
          Diagram(coordinateSystem(extent = {{-680, 160}, {220, -320}})));
      end R9MSRRpOneDol;

      model R9MSRR100pcm
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotFuel = MSRR_PlantData.PrimaryLoop.vdotFuel;
        parameter SMD_MSR_Modelica.Units.Density rhoFuel = MSRR_PlantData.Materials.rhoFuel;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpFuel = MSRR_PlantData.Materials.cpFuel;
        parameter SMD_MSR_Modelica.Units.Conductivity kFuel = MSRR_PlantData.Materials.kFuel;
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotCoolant = MSRR_PlantData.SecondaryLoop.vdotCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoCoolant = MSRR_PlantData.Materials.rhoCoolant;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpCoolant = MSRR_PlantData.Materials.cpCoolant;
        parameter SMD_MSR_Modelica.Units.Conductivity kCoolant = MSRR_PlantData.Materials.kCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoGrap = MSRR_PlantData.Materials.rhoGrap;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpGrap = MSRR_PlantData.Materials.cpGrap;
        parameter SMD_MSR_Modelica.Units.Density rhoHXtube = MSRR_PlantData.Materials.rhoHXtube;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpHXtube = MSRR_PlantData.Materials.cpHXtube;
        MSRR.Components.HeatExchanger heatExchanger(vol_P = MSRR_PlantData.SecondaryLoop.hxVolP, vol_T = MSRR_PlantData.SecondaryLoop.hxVolT, vol_S = MSRR_PlantData.SecondaryLoop.hxVolS, rhoP = rhoFuel, rhoT = rhoHXtube, rhoS = rhoCoolant, cP_P = scpFuel, cP_T = scpHXtube, cP_S = scpCoolant, VdotPnom = volDotFuel, VdotSnom = volDotCoolant, hApNom = MSRR_PlantData.SecondaryLoop.hApNom, hAsNom = MSRR_PlantData.SecondaryLoop.hAsNom, hAExp = 0.33, EnableRad = false, AcShell = MSRR_PlantData.SecondaryLoop.HX.AcShell, AcTube = MSRR_PlantData.SecondaryLoop.HX.AcTube, ArShell = MSRR_PlantData.SecondaryLoop.HX.ArShell, Kp = kFuel, Ks = kCoolant, L_shell = MSRR_PlantData.SecondaryLoop.HX.L_shell, L_tube = MSRR_PlantData.SecondaryLoop.HX.L_tube, e = MSRR_PlantData.SecondaryLoop.HX.e, Tinf = MSRR_PlantData.SecondaryLoop.HX.Tinf, TpIn_0 = MSRR_PlantData.SecondaryLoop.HX.TpIn_0, TpOut_0 = MSRR_PlantData.SecondaryLoop.HX.TpOut_0, TsIn_0 = MSRR_PlantData.SecondaryLoop.HX.TsIn_0, TsOut_0 = MSRR_PlantData.SecondaryLoop.HX.TsOut_0) annotation(
          Placement(transformation(origin = {-10.2, 90.6}, extent = {{-50.8, -25.4}, {76.2, 25.4}})));
        SMD_MSR_Modelica.HeatTransport.UHX uhx(Tp_0 = MSRR_PlantData.SecondaryLoop.UHX.Tp_0, vDot = volDotCoolant, cP = scpCoolant, rho = rhoCoolant, vol = MSRR_PlantData.SecondaryLoop.uhxVol) annotation(
          Placement(transformation(origin = {101.462, -66.3463}, extent = {{-54.8618, -41.1463}, {27.4309, 41.1463}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoUHX(vol = MSRR_PlantData.SecondaryLoop.pipeHXtoUHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.T_0) annotation(
          Placement(transformation(origin = {-28.8, -55.0667}, extent = {{-27.2, 9.06667}, {18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeUHXtoHX(vol = MSRR_PlantData.SecondaryLoop.pipeUHXtoHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.T_0) annotation(
          Placement(transformation(origin = {95.2, 27.7333}, extent = {{27.2, 9.06667}, {-18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.DHRS dhrs(vol = MSRR_PlantData.PrimaryLoop.volLoop[2], rho = rhoFuel, cP = scpFuel, vDotNom = volDotFuel, DHRS_tK = MSRR_PlantData.PrimaryLoop.dhrsTK, DHRS_MaxP_Rm(displayUnit = "MW") = MSRR_PlantData.PrimaryLoop.dhrsMaxRemove, DHRS_P_Bleed = MSRR_PlantData.PrimaryLoop.dhrsBleed, DHRS_time = MSRR_PlantData.PrimaryLoop.dhrsEngageTime, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.Ac, L = MSRR_PlantData.PrimaryLoop.L, Ar = MSRR_PlantData.PrimaryLoop.Ar, EnableRad = false, e = MSRR_PlantData.PrimaryLoop.e, Tinf = MSRR_PlantData.PrimaryLoop.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.T_0) annotation(
          Placement(transformation(origin = {-249, 30.6667}, extent = {{-49.3333, -24.6667}, {37, 49.3333}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeDHRStoHX(vol = MSRR_PlantData.PrimaryLoop.volLoop[3], volFracNode = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ac, L = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ar, e = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.T_0) annotation(
          Placement(transformation(origin = {-141.6, 11.2}, extent = {{-38.4, 12.8}, {25.6, 51.2}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoCore(vol = MSRR_PlantData.PrimaryLoop.volLoop[5], volFracNode = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ac, L = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ar, e = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.T_0) annotation(
          Placement(transformation(origin = {-158.4, -105.6}, extent = {{-37.2, 12.4}, {24.8, 49.6}}, rotation = 180)));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeCoreToDHRS(vol = MSRR_PlantData.PrimaryLoop.volLoop[1], volFracNode = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ac, L = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ar, e = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.T_0) annotation(
          Placement(transformation(origin = {-374, 6}, extent = {{-39.6, 13.2}, {26.4, 52.8}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
          Placement(transformation(origin = {-37, -175}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.HeatTransport.Pump primaryPump(numRampUp = MSRR_PlantData.Pumps.primaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.primaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.primaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.primaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {-550, 116}, extent = {{-24, -32}, {24, 16}})));
        SMD_MSR_Modelica.HeatTransport.Pump secondaryPump(numRampUp = MSRR_PlantData.Pumps.secondaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.secondaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.secondaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.secondaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {175, 114.667}, extent = {{-23, -30.6667}, {23, 15.3333}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantReal uhxDemand(magnitude = MSRR_PlantData.nominalPower) annotation(
          Placement(transformation(origin = {43, -251}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.Signals.TimeDependent.Stepper externalReact(numSteps = 2, stepTime = {0, 4000}, amplitude = {0, 100}) annotation(
          Placement(transformation(origin = {-596.198, -38.1983}, extent = {{-23.7983, 23.7983}, {23.7983, -23.7983}}, rotation = -0)));
        MSRR.Components.MSRR9R msre9r(numGroups = MSRR_PlantData.Kinetics.numGroups, lambda = MSRR_PlantData.Kinetics.lambda, beta = MSRR_PlantData.Kinetics.beta, LAMBDA = MSRR_PlantData.Kinetics.LAMBDA, n_0 = 1, aF = MSRR_PlantData.Kinetics.a_F, aG = MSRR_PlantData.Kinetics.a_G, volF1 = MSRR_PlantData.Core9R.volF1, volF2 = MSRR_PlantData.Core9R.volF2, volG = MSRR_PlantData.Core9R.volG, hA = MSRR_PlantData.Core9R.hA, kFN1 = MSRR_PlantData.Core9R.kFN1, kFN2 = MSRR_PlantData.Core9R.kFN2, kHT1 = MSRR_PlantData.Core9R.kHT1, kHT2 = MSRR_PlantData.Core9R.kHT2, TF1_0 = MSRR_PlantData.Core9R.TF1_0_regions, TF2_0 = MSRR_PlantData.Core9R.TF2_0_regions, TG_0 = MSRR_PlantData.Core9R.TG_0_regions, Tmix_0 = MSRR_PlantData.Core9R.Tmix_0, IF1 = MSRR_PlantData.Core9R.IF1, IF2 = MSRR_PlantData.Core9R.IF2, IG = MSRR_PlantData.Core9R.IG, flowFracRegions = MSRR_PlantData.Core9R.flowFracRegions, rho_fuel = rhoFuel, cP_fuel = scpFuel, kFuel = kFuel, volDotFuel = volDotFuel, rho_grap = rhoGrap, cP_grap = scpGrap, regionTripTime = MSRR_PlantData.Core9R.regionTripTime, regionCoastDownK = MSRR_PlantData.Core9R.regionCoastDownK, freeConvFF = MSRR_PlantData.Pumps.freeConvFF, EnableRad = false, T_inf = 500, LF1 = MSRR_PlantData.Core9R.LF1, LF2 = MSRR_PlantData.Core9R.LF2, Ac = MSRR_PlantData.Core9R.Ac, ArF1 = MSRR_PlantData.Core9R.ArF1, ArF2 = MSRR_PlantData.Core9R.ArF2, e = MSRR_PlantData.Core9R.e) annotation(
          Placement(transformation(origin = {-260.333, -15}, extent = {{-179.667, -147}, {-81.6667, -49}})));
      equation
        connect(heatExchanger.T_out_sFluid, pipeHXtoUHX.PiTemp_IN) annotation(
          Line(points = {{-40, 78}, {-94, 78}, {-94, -32}, {-51, -32}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeHXtoUHX.PiTempOut, uhx.tempIn) annotation(
          Line(points = {{-15, -32}, {28, -32}, {28, -66}, {47, -66}}, color = {204, 0, 0}, thickness = 1));
        connect(uhx.tempOut, pipeUHXtoHX.PiTemp_IN) annotation(
          Line(points = {{101, -66}, {140, -66}, {140, 50}, {118, 50}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeUHXtoHX.PiTempOut, heatExchanger.T_in_sFluid) annotation(
          Line(points = {{82, 50}, {57.5, 50}, {57.5, 78}, {70, 78}}, color = {204, 0, 0}, thickness = 1));
        connect(dhrs.tempOut, pipeDHRStoHX.PiTemp_IN) annotation(
          Line(points = {{-229, 43}, {-174, 43}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeDHRStoHX.PiTempOut, heatExchanger.T_in_pFluid) annotation(
          Line(points = {{-122, 43}, {-76, 43}, {-76, 103}, {-40, 103}}, color = {204, 0, 0}, thickness = 1));
        connect(heatExchanger.T_out_pFluid, pipeHXtoCore.PiTemp_IN) annotation(
          Line(points = {{70, 103}, {156, 103}, {156, -137}, {-132, -137}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeCoreToDHRS.PiTempOut, dhrs.tempIn) annotation(
          Line(points = {{-359, 39}, {-322.5, 39}, {-322.5, 43}, {-282, 43}}, color = {204, 0, 0}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeHXtoUHX.PiDecay_Heat) annotation(
          Line(points = {{-39, -159}, {-74, -159}, {-74, -23}, {-51, -23}}, color = {220, 138, 221}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeUHXtoHX.PiDecay_Heat) annotation(
          Line(points = {{-39, -159}, {148, -159}, {148, 59}, {118, 59}}, color = {220, 138, 221}, thickness = 1));
        connect(primaryPump.flowFrac, pipeCoreToDHRS.flowFrac) annotation(
          Line(points = {{-550, 92}, {-468, 92}, {-468, 29}, {-403, 29}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, dhrs.flowFrac) annotation(
          Line(points = {{-550, 92}, {-328, 92}, {-328, 18}, {-282, 18}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeDHRStoHX.flowFrac) annotation(
          Line(points = {{-550, 92}, {-206, 92}, {-206, 30}, {-174, 30}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, heatExchanger.primaryFF) annotation(
          Line(points = {{-550, 92}, {-299.5, 92}, {-299.5, 112}, {-27, 112}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeHXtoCore.flowFrac) annotation(
          Line(points = {{-550, 92}, {-108, 92}, {-108, -127}, {-132, -127}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeUHXtoHX.flowFrac) annotation(
          Line(points = {{175, 92}, {174, 92}, {174, 41}, {118, 41}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, heatExchanger.secondaryFF) annotation(
          Line(points = {{175, 92}, {175, 98}, {174, 98}, {174, -6}, {58, -6}, {58, 69}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, uhx.flowFrac) annotation(
          Line(points = {{175, 92}, {174, 92}, {174, -39}, {47, -39}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeHXtoUHX.flowFrac) annotation(
          Line(points = {{175, 92}, {175, 98}, {176, 98}, {176, -82}, {-51, -82}, {-51, -41}}, color = {245, 121, 0}, thickness = 1));
        connect(pipeHXtoCore.PiTempOut, msre9r.tempIn) annotation(
          Line(points = {{-173, -137}, {-175, -242}, {-554, -242}}, color = {204, 0, 0}, thickness = 1));
        connect(msre9r.volumetricPowerOut, pipeHXtoCore.PiDecay_Heat) annotation(
          Line(points = {{-488, -243}, {-319, -243}, {-319, -146}, {-132, -146}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, pipeCoreToDHRS.PiDecay_Heat) annotation(
          Line(points = {{-488, -243}, {-434, -243}, {-434, 49}, {-403, 49}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, dhrs.pDecay) annotation(
          Line(points = {{-488, -243}, {-318, -243}, {-318, 68}, {-282, 68}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, pipeDHRStoHX.PiDecay_Heat) annotation(
          Line(points = {{-488, -243}, {-200, -243}, {-200, 56}, {-174, 56}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, heatExchanger.P_decay) annotation(
          Line(points = {{-488, -243}, {-434, -243}, {-434, 112}, {15, 112}}, color = {220, 138, 221}, thickness = 1));
        connect(primaryPump.flowFrac, msre9r.flowFracIn) annotation(
          Line(points = {{-550, 92}, {-550, -53}, {-554, -53}, {-554, -178}}, color = {245, 121, 0}, thickness = 1));
        connect(msre9r.tempOut, pipeCoreToDHRS.PiTemp_IN) annotation(
          Line(points = {{-489, -178}, {-486.5, -178}, {-486.5, 39}, {-403, 39}}, color = {204, 0, 0}, thickness = 1));
        connect(externalReact.step, msre9r.realIn) annotation(
          Line(points = {{-594, -46}, {-520, -46}, {-522, -178}}, thickness = 1));
        connect(uhxDemand.realOut, uhx.powDemand) annotation(
          Line(points = {{44, -237.5}, {62, -237.5}, {62, -94}, {47, -94}}, thickness = 1));
        annotation(
          Diagram(coordinateSystem(extent = {{-680, 160}, {220, -320}})));
      end R9MSRR100pcm;

      model R9MSRR10pcm
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotFuel = MSRR_PlantData.PrimaryLoop.vdotFuel;
        parameter SMD_MSR_Modelica.Units.Density rhoFuel = MSRR_PlantData.Materials.rhoFuel;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpFuel = MSRR_PlantData.Materials.cpFuel;
        parameter SMD_MSR_Modelica.Units.Conductivity kFuel = MSRR_PlantData.Materials.kFuel;
        parameter SMD_MSR_Modelica.Units.VolumetricFlowRate volDotCoolant = MSRR_PlantData.SecondaryLoop.vdotCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoCoolant = MSRR_PlantData.Materials.rhoCoolant;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpCoolant = MSRR_PlantData.Materials.cpCoolant;
        parameter SMD_MSR_Modelica.Units.Conductivity kCoolant = MSRR_PlantData.Materials.kCoolant;
        parameter SMD_MSR_Modelica.Units.Density rhoGrap = MSRR_PlantData.Materials.rhoGrap;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpGrap = MSRR_PlantData.Materials.cpGrap;
        parameter SMD_MSR_Modelica.Units.Density rhoHXtube = MSRR_PlantData.Materials.rhoHXtube;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity scpHXtube = MSRR_PlantData.Materials.cpHXtube;
        MSRR.Components.HeatExchanger heatExchanger(vol_P = MSRR_PlantData.SecondaryLoop.hxVolP, vol_T = MSRR_PlantData.SecondaryLoop.hxVolT, vol_S = MSRR_PlantData.SecondaryLoop.hxVolS, rhoP = rhoFuel, rhoT = rhoHXtube, rhoS = rhoCoolant, cP_P = scpFuel, cP_T = scpHXtube, cP_S = scpCoolant, VdotPnom = volDotFuel, VdotSnom = volDotCoolant, hApNom = MSRR_PlantData.SecondaryLoop.hApNom, hAsNom = MSRR_PlantData.SecondaryLoop.hAsNom, hAExp = 0.33, EnableRad = false, AcShell = MSRR_PlantData.SecondaryLoop.HX.AcShell, AcTube = MSRR_PlantData.SecondaryLoop.HX.AcTube, ArShell = MSRR_PlantData.SecondaryLoop.HX.ArShell, Kp = kFuel, Ks = kCoolant, L_shell = MSRR_PlantData.SecondaryLoop.HX.L_shell, L_tube = MSRR_PlantData.SecondaryLoop.HX.L_tube, e = MSRR_PlantData.SecondaryLoop.HX.e, Tinf = MSRR_PlantData.SecondaryLoop.HX.Tinf, TpIn_0 = MSRR_PlantData.SecondaryLoop.HX.TpIn_0, TpOut_0 = MSRR_PlantData.SecondaryLoop.HX.TpOut_0, TsIn_0 = MSRR_PlantData.SecondaryLoop.HX.TsIn_0, TsOut_0 = MSRR_PlantData.SecondaryLoop.HX.TsOut_0) annotation(
          Placement(transformation(origin = {-10.2, 90.6}, extent = {{-50.8, -25.4}, {76.2, 25.4}})));
        SMD_MSR_Modelica.HeatTransport.UHX uhx(Tp_0 = MSRR_PlantData.SecondaryLoop.UHX.Tp_0, vDot = volDotCoolant, cP = scpCoolant, rho = rhoCoolant, vol = MSRR_PlantData.SecondaryLoop.uhxVol) annotation(
          Placement(transformation(origin = {101.462, -66.3463}, extent = {{-54.8618, -41.1463}, {27.4309, 41.1463}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoUHX(vol = MSRR_PlantData.SecondaryLoop.pipeHXtoUHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeHXtoUHX.T_0) annotation(
          Placement(transformation(origin = {-28.8, -55.0667}, extent = {{-27.2, 9.06667}, {18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeUHXtoHX(vol = MSRR_PlantData.SecondaryLoop.pipeUHXtoHXvol, volFracNode = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.volFracNode, vDotNom = volDotCoolant, rho = rhoCoolant, cP = scpCoolant, K = kCoolant, Ac = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ac, L = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.L, EnableRad = false, Ar = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Ar, e = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.e, Tinf = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.Tinf, T_0 = MSRR_PlantData.SecondaryLoop.PipeUHXtoHX.T_0) annotation(
          Placement(transformation(origin = {95.2, 27.7333}, extent = {{27.2, 9.06667}, {-18.1333, 36.2667}})));
        SMD_MSR_Modelica.HeatTransport.DHRS dhrs(vol = MSRR_PlantData.PrimaryLoop.volLoop[2], rho = rhoFuel, cP = scpFuel, vDotNom = volDotFuel, DHRS_tK = MSRR_PlantData.PrimaryLoop.dhrsTK, DHRS_MaxP_Rm(displayUnit = "MW") = MSRR_PlantData.PrimaryLoop.dhrsMaxRemove, DHRS_P_Bleed = MSRR_PlantData.PrimaryLoop.dhrsBleed, DHRS_time = MSRR_PlantData.PrimaryLoop.dhrsEngageTime, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.Ac, L = MSRR_PlantData.PrimaryLoop.L, Ar = MSRR_PlantData.PrimaryLoop.Ar, EnableRad = false, e = MSRR_PlantData.PrimaryLoop.e, Tinf = MSRR_PlantData.PrimaryLoop.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.T_0) annotation(
          Placement(transformation(origin = {-249, 30.6667}, extent = {{-49.3333, -24.6667}, {37, 49.3333}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeDHRStoHX(vol = MSRR_PlantData.PrimaryLoop.volLoop[3], volFracNode = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ac, L = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Ar, e = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeDHRStoHX.T_0) annotation(
          Placement(transformation(origin = {-141.6, 11.2}, extent = {{-38.4, 12.8}, {25.6, 51.2}})));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeHXtoCore(vol = MSRR_PlantData.PrimaryLoop.volLoop[5], volFracNode = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ac, L = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Ar, e = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeHXtoCore.T_0) annotation(
          Placement(transformation(origin = {-158.4, -105.6}, extent = {{-37.2, 12.4}, {24.8, 49.6}}, rotation = 180)));
        SMD_MSR_Modelica.HeatTransport.Pipe pipeCoreToDHRS(vol = MSRR_PlantData.PrimaryLoop.volLoop[1], volFracNode = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.volFracNode, vDotNom = volDotFuel, rho = rhoFuel, cP = scpFuel, K = kFuel, Ac = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ac, L = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.L, EnableRad = false, Ar = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Ar, e = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.e, Tinf = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.Tinf, T_0 = MSRR_PlantData.PrimaryLoop.PipeCoreToDHRS.T_0) annotation(
          Placement(transformation(origin = {-374, 6}, extent = {{-39.6, 13.2}, {26.4, 52.8}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
          Placement(transformation(origin = {-37, -175}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.HeatTransport.Pump primaryPump(numRampUp = MSRR_PlantData.Pumps.primaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.primaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.primaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.primaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {-550, 116}, extent = {{-24, -32}, {24, 16}})));
        SMD_MSR_Modelica.HeatTransport.Pump secondaryPump(numRampUp = MSRR_PlantData.Pumps.secondaryNumRampUp, rampUpK = MSRR_PlantData.Pumps.secondaryRampUpK, rampUpTo = MSRR_PlantData.Pumps.secondaryRampUpTo, rampUpTime = MSRR_PlantData.Pumps.secondaryRampUpTime, tripK = MSRR_PlantData.Pumps.tripK, tripTime = MSRR_PlantData.Pumps.tripTime, freeConvFF = MSRR_PlantData.Pumps.freeConvFF) annotation(
          Placement(transformation(origin = {175, 114.667}, extent = {{-23, -30.6667}, {23, 15.3333}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantReal uhxDemand(magnitude = MSRR_PlantData.nominalPower) annotation(
          Placement(transformation(origin = {43, -251}, extent = {{-27, -27}, {27, 27}})));
        SMD_MSR_Modelica.Signals.TimeDependent.Stepper externalReact(numSteps = 2, stepTime = {0, 4000}, amplitude = {0, 10}) annotation(
          Placement(transformation(origin = {-596.198, -38.1983}, extent = {{-23.7983, 23.7983}, {23.7983, -23.7983}}, rotation = -0)));
        MSRR.Components.MSRR9R msre9r(numGroups = MSRR_PlantData.Kinetics.numGroups, lambda = MSRR_PlantData.Kinetics.lambda, beta = MSRR_PlantData.Kinetics.beta, LAMBDA = MSRR_PlantData.Kinetics.LAMBDA, n_0 = 1, aF = MSRR_PlantData.Kinetics.a_F, aG = MSRR_PlantData.Kinetics.a_G, volF1 = MSRR_PlantData.Core9R.volF1, volF2 = MSRR_PlantData.Core9R.volF2, volG = MSRR_PlantData.Core9R.volG, hA = MSRR_PlantData.Core9R.hA, kFN1 = MSRR_PlantData.Core9R.kFN1, kFN2 = MSRR_PlantData.Core9R.kFN2, kHT1 = MSRR_PlantData.Core9R.kHT1, kHT2 = MSRR_PlantData.Core9R.kHT2, TF1_0 = MSRR_PlantData.Core9R.TF1_0_regions, TF2_0 = MSRR_PlantData.Core9R.TF2_0_regions, TG_0 = MSRR_PlantData.Core9R.TG_0_regions, Tmix_0 = MSRR_PlantData.Core9R.Tmix_0, IF1 = MSRR_PlantData.Core9R.IF1, IF2 = MSRR_PlantData.Core9R.IF2, IG = MSRR_PlantData.Core9R.IG, flowFracRegions = MSRR_PlantData.Core9R.flowFracRegions, rho_fuel = rhoFuel, cP_fuel = scpFuel, kFuel = kFuel, volDotFuel = volDotFuel, rho_grap = rhoGrap, cP_grap = scpGrap, regionTripTime = MSRR_PlantData.Core9R.regionTripTime, regionCoastDownK = MSRR_PlantData.Core9R.regionCoastDownK, freeConvFF = MSRR_PlantData.Pumps.freeConvFF, EnableRad = false, T_inf = 500, LF1 = MSRR_PlantData.Core9R.LF1, LF2 = MSRR_PlantData.Core9R.LF2, Ac = MSRR_PlantData.Core9R.Ac, ArF1 = MSRR_PlantData.Core9R.ArF1, ArF2 = MSRR_PlantData.Core9R.ArF2, e = MSRR_PlantData.Core9R.e) annotation(
          Placement(transformation(origin = {-368.333, -141}, extent = {{-179.667, -147}, {-81.6667, -49}})));
      equation
        connect(heatExchanger.T_out_sFluid, pipeHXtoUHX.PiTemp_IN) annotation(
          Line(points = {{-40, 78}, {-94, 78}, {-94, -32}, {-51, -32}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeHXtoUHX.PiTempOut, uhx.tempIn) annotation(
          Line(points = {{-15, -32}, {28, -32}, {28, -66}, {47, -66}}, color = {204, 0, 0}, thickness = 1));
        connect(uhx.tempOut, pipeUHXtoHX.PiTemp_IN) annotation(
          Line(points = {{101, -66}, {140, -66}, {140, 50}, {118, 50}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeUHXtoHX.PiTempOut, heatExchanger.T_in_sFluid) annotation(
          Line(points = {{82, 50}, {57.5, 50}, {57.5, 78}, {70, 78}}, color = {204, 0, 0}, thickness = 1));
        connect(dhrs.tempOut, pipeDHRStoHX.PiTemp_IN) annotation(
          Line(points = {{-229, 43}, {-174, 43}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeDHRStoHX.PiTempOut, heatExchanger.T_in_pFluid) annotation(
          Line(points = {{-122, 43}, {-76, 43}, {-76, 103}, {-40, 103}}, color = {204, 0, 0}, thickness = 1));
        connect(heatExchanger.T_out_pFluid, pipeHXtoCore.PiTemp_IN) annotation(
          Line(points = {{70, 103}, {156, 103}, {156, -137}, {-132, -137}}, color = {204, 0, 0}, thickness = 1));
        connect(pipeCoreToDHRS.PiTempOut, dhrs.tempIn) annotation(
          Line(points = {{-359, 39}, {-322.5, 39}, {-322.5, 43}, {-282, 43}}, color = {204, 0, 0}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeHXtoUHX.PiDecay_Heat) annotation(
          Line(points = {{-39, -159}, {-74, -159}, {-74, -23}, {-51, -23}}, color = {220, 138, 221}, thickness = 1));
        connect(constantVolumetricPower.volPow, pipeUHXtoHX.PiDecay_Heat) annotation(
          Line(points = {{-39, -159}, {148, -159}, {148, 59}, {118, 59}}, color = {220, 138, 221}, thickness = 1));
        connect(primaryPump.flowFrac, pipeCoreToDHRS.flowFrac) annotation(
          Line(points = {{-550, 92}, {-468, 92}, {-468, 29}, {-403, 29}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, dhrs.flowFrac) annotation(
          Line(points = {{-550, 92}, {-328, 92}, {-328, 18}, {-282, 18}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeDHRStoHX.flowFrac) annotation(
          Line(points = {{-550, 92}, {-206, 92}, {-206, 30}, {-174, 30}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, heatExchanger.primaryFF) annotation(
          Line(points = {{-550, 92}, {-299.5, 92}, {-299.5, 112}, {-27, 112}}, color = {245, 121, 0}, thickness = 1));
        connect(primaryPump.flowFrac, pipeHXtoCore.flowFrac) annotation(
          Line(points = {{-550, 92}, {-108, 92}, {-108, -127}, {-132, -127}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeUHXtoHX.flowFrac) annotation(
          Line(points = {{175, 92}, {174, 92}, {174, 41}, {118, 41}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, heatExchanger.secondaryFF) annotation(
          Line(points = {{175, 92}, {175, 98}, {174, 98}, {174, -6}, {58, -6}, {58, 69}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, uhx.flowFrac) annotation(
          Line(points = {{175, 92}, {174, 92}, {174, -39}, {47, -39}}, color = {245, 121, 0}, thickness = 1));
        connect(secondaryPump.flowFrac, pipeHXtoUHX.flowFrac) annotation(
          Line(points = {{175, 92}, {175, 98}, {176, 98}, {176, -82}, {-51, -82}, {-51, -41}}, color = {245, 121, 0}, thickness = 1));
        connect(pipeHXtoCore.PiTempOut, msre9r.tempIn) annotation(
          Line(points = {{-173, -137}, {-173, -368}, {-662, -368}}, color = {204, 0, 0}, thickness = 1));
        connect(msre9r.volumetricPowerOut, pipeHXtoCore.PiDecay_Heat) annotation(
          Line(points = {{-596, -369}, {-319, -369}, {-319, -146}, {-132, -146}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, pipeCoreToDHRS.PiDecay_Heat) annotation(
          Line(points = {{-596, -369}, {-596, 49}, {-403, 49}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, dhrs.pDecay) annotation(
          Line(points = {{-596, -369}, {-318, -369}, {-318, 68}, {-282, 68}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, pipeDHRStoHX.PiDecay_Heat) annotation(
          Line(points = {{-596, -369}, {-200, -369}, {-200, 56}, {-174, 56}}, color = {220, 138, 221}, thickness = 1));
        connect(msre9r.volumetricPowerOut, heatExchanger.P_decay) annotation(
          Line(points = {{-596, -369}, {-596, 112}, {15, 112}}, color = {220, 138, 221}, thickness = 1));
        connect(primaryPump.flowFrac, msre9r.flowFracIn) annotation(
          Line(points = {{-550, 92}, {-550, -53}, {-662, -53}, {-662, -304}}, color = {245, 121, 0}, thickness = 1));
        connect(msre9r.tempOut, pipeCoreToDHRS.PiTemp_IN) annotation(
          Line(points = {{-597, -304}, {-486.5, -304}, {-486.5, 39}, {-403, 39}}, color = {204, 0, 0}, thickness = 1));
        connect(externalReact.step, msre9r.realIn) annotation(
          Line(points = {{-594, -46}, {-520, -46}, {-520, -304}, {-630, -304}}, thickness = 1));
        connect(uhxDemand.realOut, uhx.powDemand) annotation(
          Line(points = {{44, -237.5}, {62, -237.5}, {62, -94}, {47, -94}}, thickness = 1));
        annotation(
          Diagram(coordinateSystem(extent = {{-680, 160}, {220, -320}})));
      end R9MSRR10pcm;
    end R9fullSteps;
  end Transients;
end MSRR;
