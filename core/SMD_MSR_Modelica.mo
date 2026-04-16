/* MSRR simulation library.
   Contains sub-packages: HeatTransport (lumped T-H components), Nuclear
   (point kinetics, decay heat, reactivity feedback, power block), Signals
   (time-dependent sources, signal operations, test constants), Units (type
   definitions), and PortsConnectors (connector interfaces).
   All temperatures throughout this library are in degrees Celsius (degC);
   Stefan-Boltzmann radiation uses absolute temperature (T + 273.15) K. */
package SMD_MSR_Modelica
  /* Lumped-parameter thermal-hydraulic components for the primary loop,
     secondary loop, DHRS, radiator/UHX, pipe segments, and pumps. */
  package HeatTransport
    /* Counter-flow shell-and-tube heat exchanger: 4 primary (P) nodes,
       2 tube-wall (T) nodes, and 4 secondary (S) nodes arranged in
       counter-flow. Per-node hA scales with flow fraction via a 6th-order
       polynomial fit of Nu ~ Re^0.8 Pr^0.4 (Dittus-Boelter) correlation. */
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
      parameter SMD_MSR_Modelica.Units.VolumeticFlowRate VdotPnom;
      parameter SMD_MSR_Modelica.Units.VolumeticFlowRate VdotSnom;
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
      parameter SMD_MSR_Modelica.Units.Temperature Tinf;
      parameter SMD_MSR_Modelica.Units.Temperature TpIn_0;
      parameter SMD_MSR_Modelica.Units.Temperature TpOut_0;
      parameter SMD_MSR_Modelica.Units.Temperature TsIn_0;
      parameter SMD_MSR_Modelica.Units.Temperature TsOut_0;
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
        Placement(transformation(origin = {-132, 30}, extent = {{-16, -16}, {16, 16}}), iconTransformation(origin = {-136, 30}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.TempIn T_in_sFluid annotation(
        Placement(transformation(origin = {137, -31}, extent = {{-17, -17}, {17, 17}}), iconTransformation(origin = {136, -30}, extent = {{-10, -10}, {10, 10}})));
      output SMD_MSR_Modelica.PortsConnectors.TempOut T_out_sFluid annotation(
        Placement(transformation(origin = {-134, -36}, extent = {{-14, -14}, {14, 14}}), iconTransformation(origin = {-136, -30}, extent = {{-10, -10}, {10, 10}})));
      output SMD_MSR_Modelica.PortsConnectors.TempOut T_out_pFluid annotation(
        Placement(transformation(origin = {134, 28}, extent = {{-18, -18}, {18, 18}}), iconTransformation(origin = {136, 30}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.FlowFractionIn primaryFF annotation(
        Placement(transformation(origin = {-90, 49}, extent = {{-9, -9}, {9, 9}}), iconTransformation(origin = {-90, 50}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.FlowFractionIn secondaryFF annotation(
        Placement(transformation(origin = {100, -47}, extent = {{-11, -11}, {11, 11}}), iconTransformation(origin = {90, -50}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.VolumetircPowerIn P_decay annotation(
        Placement(transformation(origin = {10, 50}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {0, 50}, extent = {{-10, -10}, {10, 10}})));
    initial equation
      // FixedStart: primary nodes linearly interpolated between TpIn_0 and TpOut_0;
      // secondary nodes between TpIn_0 and TsOut_0 (counter-flow ordering);
      // tube-wall nodes initialized to the hA-weighted average of adjacent fluid nodes.
      // SteadyState: all derivatives set to zero; solver finds equilibrium.
      if initMode == SMD_MSR_Modelica.Units.InitMode.FixedStart then
        T_PN1 = TpIn_0 - (TpIn_0 - TpOut_0)/4;
        T_PN2 = TpIn_0 - 2*(TpIn_0 - TpOut_0)/4;
        T_PN3 = TpIn_0 - 3*(TpIn_0 - TpOut_0)/4;
        T_out_pFluid.T = TpOut_0;
        T_TN1 = (T_PN1*hApn + T_SN3*hAsn)/(hApn + hAsn);
        T_TN2 = (T_PN3*hApn + T_SN1*hAsn)/(hApn + hAsn);
        T_SN1 = TpIn_0 + (TsOut_0 - TpIn_0);
        T_SN2 = TpIn_0 + 2*(TsOut_0 - TpIn_0);
        T_SN3 = TpIn_0 + 3*(TsOut_0 - TpIn_0);
        T_out_sFluid.T = TsOut_0;
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
      mDotP = VdotPnom*rhoP*primaryFF.FF;
      mDotS = VdotSnom*rhoS*secondaryFF.FF;
      // Per-node hA = (nominal total hA / 4) x P6(FF), where P6 is a 6th-order
      // polynomial fit so that hA(FF=1) = hApNom/4 and hA degrades at low flow.
      hApn = (hApNom/4)*(0.8215*primaryFF.FF^6 - 4.108*primaryFF.FF^5 + 7.848*primaryFF.FF^4 - 7.165*primaryFF.FF^3 + 3.004*primaryFF.FF^2 + 0.5903*primaryFF.FF + 0.008537);
      hAsn = (hAsNom/4)*(0.8215*secondaryFF.FF^6 - 4.108*secondaryFF.FF^5 + 7.848*secondaryFF.FF^4 - 7.165*secondaryFF.FF^3 + 3.004*secondaryFF.FF^2 + 0.5903*secondaryFF.FF + 0.008537);
//hApn = (hApNom/4);
//hAsn = (hAsNom/4);
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
      condPowPN1 = ((Kp*AcShell)/LpN)*(T_in_pFluid.T - T_PN1);
      condPowPN2 = ((Kp*AcShell)/LpN)*(T_PN1 - T_PN2);
      condPowPN3 = ((Kp*AcShell)/LpN)*(T_PN2 - T_PN3);
      condPowPN4 = ((Kp*AcShell)/LpN)*(T_PN3 - T_out_pFluid.T);
      condPowSN1 = ((Ks*AcTube)/LsN)*(T_in_sFluid.T - T_SN1);
      condPowSN2 = ((Ks*AcTube)/LsN)*(T_SN1 - T_SN2);
      condPowSN3 = ((Ks*AcTube)/LsN)*(T_SN2 - T_SN3);
      condPowSN4 = ((Ks*AcTube)/LsN)*(T_SN3 - T_out_sFluid.T);
      // Counter-flow coupling: P nodes 1-2 exchange heat with tube node TN1;
      // P nodes 3-4 exchange with TN2. Secondary flow opposes primary (TN2->S1,S2; TN1->S3,S4).
      convPowPN1 = hApn*(T_PN1 - T_TN1);
      convPowPN2 = hApn*(T_PN2 - T_TN1);
      convPowPN3 = hApn*(T_PN3 - T_TN2);
      convPowPN4 = hApn*(T_out_pFluid.T - T_TN2);
      convPowSN1 = hAsn*(T_TN2 - T_SN1);
      convPowSN2 = hAsn*(T_TN2 - T_SN2);
      convPowSN3 = hAsn*(T_TN1 - T_SN3);
      convPowSN4 = hAsn*(T_TN1 - T_out_sFluid.T);

    end HeatExchanger;

    /* Air-cooled single-node radiator (primary fluid node + air node).
       hA scales with primary flow fraction via the same 6th-order polynomial
       used for the HX. Used as the ultimate heat sink in configurations
       employing a forced-air radiator rather than a liquid-cooled UHX. */
    model Radiator
      parameter SMD_MSR_Modelica.Units.Volume vol_P;
      parameter SMD_MSR_Modelica.Units.Volume vol_S;
      parameter SMD_MSR_Modelica.Units.Density rhoP;
      parameter SMD_MSR_Modelica.Units.Density rhoS;
      parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP_P;
      parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP_S;
      parameter SMD_MSR_Modelica.Units.VolumeticFlowRate VdotPnom;
      parameter SMD_MSR_Modelica.Units.VolumeticFlowRate VdotSnom;
      parameter SMD_MSR_Modelica.Units.Convection hAnom;
      parameter SMD_MSR_Modelica.Units.Conductivity Kp;
      parameter SMD_MSR_Modelica.Units.Area Ac_P;
      parameter SMD_MSR_Modelica.Units.Length Lp;
      parameter Boolean EnableRad;
      parameter SMD_MSR_Modelica.Units.Emissivity e;
      parameter SMD_MSR_Modelica.Units.Area Ar_P;
      parameter SMD_MSR_Modelica.Units.Temperature Tinf;
      parameter SMD_MSR_Modelica.Units.Temperature Tp_0;
      parameter SMD_MSR_Modelica.Units.Temperature Ts_0;
      constant SMD_MSR_Modelica.Units.Temperature realToTemp = 1;
      SMD_MSR_Modelica.Units.Power powP;
      SMD_MSR_Modelica.Units.Power powS;
      SMD_MSR_Modelica.Units.Power powPflow;
      SMD_MSR_Modelica.Units.Power powSflow;
      SMD_MSR_Modelica.Units.Power powPSconv;
      SMD_MSR_Modelica.Units.Power powPcond;
      SMD_MSR_Modelica.Units.Power powP_rad;
      SMD_MSR_Modelica.Units.Convection hA;
      SMD_MSR_Modelica.Units.Temperature tempInAir;
      SMD_MSR_Modelica.Units.MassFlowRate mDotP;
      SMD_MSR_Modelica.Units.MassFlowRate mDotS;
      SMD_MSR_Modelica.Units.Mass mP;
      SMD_MSR_Modelica.Units.Mass mS;
      SMD_MSR_Modelica.Units.Temperature tempOutS;
      input SMD_MSR_Modelica.PortsConnectors.TempIn tempInP annotation(
        Placement(transformation(origin = {6, 30}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-50, 6}, extent = {{-10, -10}, {10, 10}})));
      output SMD_MSR_Modelica.PortsConnectors.TempOut tempOutP annotation(
        Placement(transformation(origin = {-10, -40}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {48, 6}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.FlowFractionIn flowFracP annotation(
        Placement(transformation(origin = {30, 30}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-50, 26}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.RealIn tempInS annotation(
        Placement(transformation(origin = {-50, 30}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-48, -24}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.RealIn flowFracS annotation(
        Placement(transformation(origin = {-22, 30}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-8, -24}, extent = {{-10, -10}, {10, 10}})));
    initial equation
      tempOutP.T = Tp_0;
      tempOutS = Ts_0;
    equation
      tempInAir = tempInS.R*realToTemp;
      mDotP = VdotPnom*rhoP*flowFracP.FF;
      mDotS = VdotSnom*rhoS*flowFracS.R;
      mP = vol_P*rhoP;
      mS = vol_S*rhoS;
      // hA uses the same 6th-order polynomial as the HX (not the FF^0.33 power law).
      hA = hAnom*(0.8215*flowFracP.FF^6 - 4.108*flowFracP.FF^5 + 7.848*flowFracP.FF^4 - 7.165*flowFracP.FF^3 + 3.004*flowFracP.FF^2 + 0.5903*flowFracP.FF + 0.008537);
      powP = der(tempOutP.T)*mP*cP_P;
      powS = der(tempOutS)*mS*cP_S;
      powPflow = mDotP*cP_P*(tempInP.T - tempOutP.T);
      powSflow = mDotS*cP_S*(tempInAir - tempOutS);
      powPSconv = hA*(tempOutP.T - tempOutS);
      powPcond = ((Kp*Ac_P)/Lp)*(tempInP.T - tempOutP.T);
      if EnableRad == true then
        powP_rad = e*SMD_MSR_Modelica.Constants.SigSBK*Ar_P*((Tinf + 273.15)^4 - (tempInP.T + 273.15)^4);
      else
        powP_rad = 0;
      end if;
      powP = powPflow + powPcond + powP_rad - powPSconv;
      powS = powSflow + powPSconv;
      annotation(
        Diagram(graphics = {Rectangle(origin = {-10, -10}, lineThickness = 2, extent = {{-50, 50}, {50, -50}}), Text(origin = {7, 17}, extent = {{-15, 5}, {15, -5}}, textString = "TpIN", fontSize = 20), Text(origin = {23, -44}, extent = {{-13, 8}, {13, -8}}, textString = "Rad"), Text(origin = {-9, -51}, extent = {{-15, 5}, {15, -5}}, textString = "TpOUT", fontSize = 20), Text(origin = {31, 17}, extent = {{-15, 5}, {15, -5}}, textString = "FFp", fontSize = 20), Text(origin = {-49, 17}, extent = {{-15, 5}, {15, -5}}, textString = "TsIN", fontSize = 20), Text(origin = {-21, 17}, extent = {{-15, 5}, {15, -5}}, textString = "FFs", fontSize = 20), Rectangle(origin = {-32, -10}, lineColor = {224, 27, 36}, fillColor = {224, 27, 36}, fillPattern = FillPattern.Solid, lineThickness = 1, extent = {{-18, 10}, {18, -10}}), Rectangle(origin = {12, -10}, lineColor = {143, 240, 164}, fillColor = {143, 240, 164}, fillPattern = FillPattern.Solid, lineThickness = 1, extent = {{-18, 10}, {18, -10}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
        Icon(graphics = {Text(origin = {-43, -7}, extent = {{-15, 5}, {15, -5}}, textString = "Temp IN"), Text(origin = {28, 23}, extent = {{-30, 7}, {30, -7}}, textString = "Radiator"), Rectangle(origin = {0.6, 0.79}, lineThickness = 2, extent = {{-63.26, 33.16}, {63.26, -33.16}}), Text(origin = {43, -5}, extent = {{-15, 5}, {15, -5}}, textString = "Temp OUT")}, coordinateSystem(extent = {{-60, 40}, {60, -40}})));
    end Radiator;

    /* Decay Heat Removal System: single lumped fluid node. A sigmoid function
       ramps the removed power (powRm) from DHRS_P_Bleed to DHRS_MaxP_Rm over
       time constant DHRS_tK, activating at DHRS_time after reactor shutdown. */
    model DHRS
      parameter SMD_MSR_Modelica.Units.Volume vol;
      parameter SMD_MSR_Modelica.Units.Density rho;
      parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP;
      parameter SMD_MSR_Modelica.Units.VolumeticFlowRate vDotNom;
      parameter SMD_MSR_Modelica.Units.TimeConstant DHRS_tK;
      parameter SMD_MSR_Modelica.Units.Power DHRS_MaxP_Rm;
      parameter SMD_MSR_Modelica.Units.Power DHRS_P_Bleed;
      parameter SMD_MSR_Modelica.Units.InitiationTime DHRS_time;
      parameter SMD_MSR_Modelica.Units.Conductivity K;
      parameter SMD_MSR_Modelica.Units.Area Ac;
      parameter SMD_MSR_Modelica.Units.Length L;
      parameter SMD_MSR_Modelica.Units.Area Ar;
      parameter Boolean EnableRad;
      parameter SMD_MSR_Modelica.Units.Emissivity e;
      parameter SMD_MSR_Modelica.Units.Temperature Tinf;
      parameter SMD_MSR_Modelica.Units.Temperature T_0;
      parameter SMD_MSR_Modelica.Units.InitMode initMode = SMD_MSR_Modelica.Units.InitMode.FixedStart;
      SMD_MSR_Modelica.Units.Mass m;
      SMD_MSR_Modelica.Units.MassFlowRate mDot;
      SMD_MSR_Modelica.Units.Power powRm;
      SMD_MSR_Modelica.Units.Power pow;
      SMD_MSR_Modelica.Units.Power powFlow;
      SMD_MSR_Modelica.Units.Power powCond;
      SMD_MSR_Modelica.Units.Power powRad;
      SMD_MSR_Modelica.Units.Power powDecay;
      input SMD_MSR_Modelica.PortsConnectors.VolumetircPowerIn pDecay annotation(
        Placement(transformation(origin = {-50, 48}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-50, 40}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.FlowFractionIn flowFrac annotation(
        Placement(transformation(origin = {-50, -30}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-50, -40}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.TempIn tempIn annotation(
        Placement(transformation(origin = {-50, 10}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-50, 0}, extent = {{-10, -10}, {10, 10}})));
      output SMD_MSR_Modelica.PortsConnectors.TempOut tempOut annotation(
        Placement(transformation(origin = {50, 0}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {48, 0}, extent = {{-10, -10}, {10, 10}})));
    initial equation
      if initMode == SMD_MSR_Modelica.Units.InitMode.FixedStart then
        tempOut.T = T_0;
      else
        der(tempOut.T) = 0;
      end if;
    equation
      m = vol*rho;
      mDot = vDotNom*rho*flowFrac.FF;
      // Sigmoid ramp-up of removed power from DHRS_P_Bleed to DHRS_MaxP_Rm,
      // centred at DHRS_time with characteristic time DHRS_tK.
      powRm = (DHRS_MaxP_Rm - DHRS_P_Bleed)/(1 + exp(log(1/1E-3 - 1)*(1 - (time - DHRS_time)/DHRS_tK))) + DHRS_P_Bleed;
      pow = m*cP*der(tempOut.T);
      powCond = ((K*Ac)/L)*(tempIn.T - tempOut.T);
      if EnableRad == true then
        powRad = e*SMD_MSR_Modelica.Constants.SigSBK*Ar*((Tinf + 273.15)^4 - (tempOut.T + 273.15)^4);
      else
        powRad = 0;
      end if;
      powFlow = mDot*cP*(tempIn.T - tempOut.T);
      powDecay = pDecay.Q*vol;
      pow = powFlow + powCond + powRad + powDecay - powRm;
      annotation(
        Diagram(coordinateSystem(extent = {{-80, 60}, {80, -60}}), graphics = {Rectangle(lineThickness = 1, extent = {{-70, 60}, {70, -60}}), Text(origin = {53, -13}, extent = {{-15, 11}, {15, -11}}, textString = "Tout", fontSize = 20), Text(origin = {-49, -9}, extent = {{-15, 11}, {15, -11}}, textString = "Tin", fontSize = 20), Text(origin = {33, 44}, extent = {{-31, 12}, {31, -12}}, textString = "DHRS"), Text(origin = {-49, -47}, extent = {{-15, 11}, {15, -11}}, textString = "FF", fontSize = 20), Text(extent = {{-13, 8}, {13, -8}}, textString = "DH", fontSize = 20)}),
        Icon(coordinateSystem(extent = {{-80, 60}, {80, -60}}), graphics = {Rectangle(lineThickness = 1, extent = {{-70, 60}, {70, -60}}), Text(origin = {27, 38}, extent = {{-31, 12}, {31, -12}}, textString = "DHRS")}));
    end DHRS;

    /* Single-node pipe segment with plug-flow transit-time delay.
       The total fluid volume is split into a well-mixed representative node
       (fraction volFracNode) and a delay volume whose transit time scales
       inversely with flow fraction (clamped to MaxTau at near-zero flow).
       The outlet temperature is a time-delayed copy of the node temperature. */
    model Pipe
      parameter SMD_MSR_Modelica.Units.Volume vol;
      parameter Real volFracNode;
      parameter SMD_MSR_Modelica.Units.VolumeticFlowRate vDotNom;
      parameter SMD_MSR_Modelica.Units.Density rho;
      parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP;
      parameter SMD_MSR_Modelica.Units.Conductivity K;
      parameter SMD_MSR_Modelica.Units.Area Ac;
      parameter SMD_MSR_Modelica.Units.Length L;
      parameter Boolean EnableRad;
      parameter SMD_MSR_Modelica.Units.Area Ar;
      parameter SMD_MSR_Modelica.Units.Emissivity e;
      parameter SMD_MSR_Modelica.Units.Temperature Tinf;
      parameter SMD_MSR_Modelica.Units.Temperature T_0;
      parameter SMD_MSR_Modelica.Units.InitMode initMode = SMD_MSR_Modelica.Units.InitMode.FixedStart;
      SMD_MSR_Modelica.Units.Mass mPi;
      SMD_MSR_Modelica.Units.Mass mPiRep;
      SMD_MSR_Modelica.Units.Mass mPiDelay;
      SMD_MSR_Modelica.Units.MassFlowRate mDotNom;
      SMD_MSR_Modelica.Units.MassFlowRate mDot;
      SMD_MSR_Modelica.Units.Power powPi;
      SMD_MSR_Modelica.Units.Power powFlow;
      SMD_MSR_Modelica.Units.Power powCondIn;
      SMD_MSR_Modelica.Units.Power powCondOut;
      SMD_MSR_Modelica.Units.Power powRad;
      SMD_MSR_Modelica.Units.Power powDecay;
      SMD_MSR_Modelica.Units.ResidentTime tauPiDelay;
      SMD_MSR_Modelica.Units.ResidentTime varTauPi;
      SMD_MSR_Modelica.Units.Temperature tempPi;
      SMD_MSR_Modelica.Units.Temperature tempPiIn;
      input SMD_MSR_Modelica.PortsConnectors.TempIn PiTemp_IN annotation(
        Placement(transformation(origin = {-36, 2}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-40, 0}, extent = {{-10, -10}, {10, 10}})));
      output SMD_MSR_Modelica.PortsConnectors.TempOut PiTempOut annotation(
        Placement(transformation(origin = {40, 2}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {40, 0}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.VolumetircPowerIn PiDecay_Heat annotation(
        Placement(transformation(origin = {-36, 20}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-40, 20}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.FlowFractionIn flowFrac annotation(
        Placement(transformation(origin = {-36, -18}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-40, -20}, extent = {{-10, -10}, {10, 10}})));
    initial equation
      if initMode == SMD_MSR_Modelica.Units.InitMode.FixedStart then
        tempPi = T_0;
      else
        der(tempPi) = 0;
      end if;
    equation
      mPi = rho*vol;
      mDotNom = rho*vDotNom;
      mDot = rho*vDotNom*flowFrac.FF;
      mPiRep = mPi*volFracNode;   // mass of the well-mixed representative node
      mPiDelay = mPi*(1 - volFracNode); // mass in the plug-flow delay section
      tauPiDelay = mPiDelay/(2*mDotNom); // nominal one-way transit time of delay volume
      // varTauPi: actual transit time, clamped to MaxTau at zero flow to avoid singularity.
      varTauPi = if flowFrac.FF > SMD_MSR_Modelica.Constants.MinTau then tauPiDelay/flowFrac.FF else SMD_MSR_Modelica.Constants.MaxTau;
      tempPiIn = delay(PiTemp_IN.T, varTauPi, SMD_MSR_Modelica.Constants.MaxTau);
      powPi = mPiRep*cP*der(tempPi);
      powFlow = mDot*cP*(tempPiIn - tempPi);
      powCondIn = ((2*K*Ac)/L)*(PiTemp_IN.T - tempPi);
      powCondOut = ((2*K*Ac)/L)*(tempPi - PiTempOut.T);
      if EnableRad == true then
        powRad = e*SMD_MSR_Modelica.Constants.SigSBK*Ar*((Tinf + 273.15)^4 - (tempPi + 273.15)^4);
      else
        powRad = 0;
      end if;
      powDecay = vol*PiDecay_Heat.Q;
      powPi = powFlow + powCondIn - powCondOut + powRad + powDecay;
      PiTempOut.T = delay(tempPi, varTauPi, SMD_MSR_Modelica.Constants.MaxTau);
      annotation(
        Icon(coordinateSystem(extent = {{-60, 40}, {60, -40}}), graphics = {Rectangle(lineThickness = 1, extent = {{-60, 40}, {60, -40}}), Text(origin = {1, 1}, extent = {{-19, 9}, {19, -9}}, textString = "Pipe")}));
    end Pipe;

    model PrimaryPump
      parameter SMD_MSR_Modelica.Units.FlowFraction freeConvectionFF;
      parameter SMD_MSR_Modelica.Units.PumpConstant primaryPumpK;
      parameter SMD_MSR_Modelica.Units.InitiationTime tripPrimaryPump;
      output SMD_MSR_Modelica.PortsConnectors.FlowFractionOut primaryFlowFrac annotation(
        Placement(visible = true, transformation(origin = {-1.77636e-15, -40}, extent = {{-20, -20}, {20, 20}}, rotation = 0), iconTransformation(origin = {1.77636e-15, -60}, extent = {{-20, -20}, {20, 20}}, rotation = 0)));
    initial equation
      primaryFlowFrac.FF = 1;
    equation
      primaryFlowFrac.FF = (1 - freeConvectionFF)*exp(-(1/primaryPumpK)*delay(time, tripPrimaryPump)) + freeConvectionFF;
      annotation(
        Diagram(graphics = {Rectangle(lineColor = {245, 121, 0}, lineThickness = 1, extent = {{-60, 60}, {60, -60}}), Text(origin = {2, 6}, extent = {{-42, 16}, {42, -16}}, textString = "Primary Pump")}),
        Icon(graphics = {Rectangle(origin = {0, -20}, lineColor = {245, 121, 0}, lineThickness = 1, extent = {{-60, 60}, {60, -60}}), Text(origin = {4, -20}, extent = {{-42, 16}, {42, -16}}, textString = "Primary Pump")}));
    end PrimaryPump;

    /* Ultimate Heat Exchanger (UHX): passive single-node heat sink.
       powDemand is the demanded extraction rate from the secondary coolant;
       at nominal operation this equals reactor power. No hA correlation is
       needed -- the extraction is treated as a direct power withdrawal. */
    model UHX
      parameter SMD_MSR_Modelica.Units.Volume vol;
      parameter SMD_MSR_Modelica.Units.Density rho;
      parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP;
      parameter SMD_MSR_Modelica.Units.VolumeticFlowRate vDot;
      parameter SMD_MSR_Modelica.Units.Temperature Tp_0;
      parameter SMD_MSR_Modelica.Units.InitMode initMode = SMD_MSR_Modelica.Units.InitMode.FixedStart;
      constant SMD_MSR_Modelica.Units.Power realToPow = 1;
      SMD_MSR_Modelica.Units.Mass m;
      SMD_MSR_Modelica.Units.MassFlowRate mDot;
      SMD_MSR_Modelica.Units.Power powRm;
      SMD_MSR_Modelica.Units.Power pow;
      SMD_MSR_Modelica.Units.Power powFlow;
      input SMD_MSR_Modelica.PortsConnectors.FlowFractionIn flowFrac annotation(
        Placement(visible = true, transformation(origin = {-60, 40}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {-60, 40}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
      output SMD_MSR_Modelica.PortsConnectors.TempOut tempOut annotation(
        Placement(visible = true, transformation(origin = {20, 0}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {20, 0}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
      input SMD_MSR_Modelica.PortsConnectors.TempIn tempIn annotation(
        Placement(visible = true, transformation(origin = {-60, 0}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {-60, 0}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
      input SMD_MSR_Modelica.PortsConnectors.RealIn powDemand annotation(
        Placement(visible = true, transformation(origin = {-60, -40}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {-60, -40}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
    initial equation
      if initMode == SMD_MSR_Modelica.Units.InitMode.FixedStart then
        tempOut.T = Tp_0;
      else
        der(tempOut.T) = 0;
      end if;
    equation
      m = vol*rho;
      mDot = vDot*rho;
      pow = m*cP*der(tempOut.T);
      powFlow = mDot*cP*(tempIn.T - tempOut.T);
      powRm = powDemand.R*realToPow;
      pow = powFlow - powRm;
      annotation(
        Diagram(graphics = {Rectangle(origin = {-20, 0}, lineThickness = 1, extent = {{-60, 60}, {60, -60}}), Text(origin = {-60, -14}, extent = {{-14, 12}, {14, -12}}, textString = "Tin", fontSize = 20), Text(origin = {8, 44}, extent = {{-34, 18}, {34, -18}}, textString = "UHX"), Text(origin = {-60, 26}, extent = {{-14, 12}, {14, -12}}, textString = "FF", fontSize = 20), Text(origin = {-60, -52}, extent = {{-14, 12}, {14, -12}}, textString = "Qrm", fontSize = 20), Text(origin = {22, -14}, extent = {{-16, 12}, {16, -12}}, textString = "Tout", fontSize = 20)}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
        Icon(coordinateSystem(extent = {{-80, 60}, {40, -60}}), graphics = {Rectangle(origin = {-20, 0}, lineThickness = 1, extent = {{-60, 60}, {60, -60}}), Text(origin = {8, 44}, extent = {{-34, 18}, {34, -18}}, textString = "UHX")}));
    end UHX;

    /* Lumped mixing plenum with numStreams inflow streams.
       Each stream contributes mDotIn[i]*Cp*T_in[i] to the enthalpy inflow.
       The mixed node temperature T satisfies the total enthalpy balance;
       temp_Out.T = T is broadcast to downstream components. */
    model MixingPot
      parameter Integer numStreams;
      parameter SMD_MSR_Modelica.Units.Volume vol;
      parameter SMD_MSR_Modelica.Units.VolumeticFlowRate VdotNom;
      parameter SMD_MSR_Modelica.Units.FlowFraction flowFractionsNom[numStreams];
      parameter SMD_MSR_Modelica.Units.Density rho;
      parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity Cp;
      parameter SMD_MSR_Modelica.Units.Conductivity K;
      parameter SMD_MSR_Modelica.Units.Area Ac;
      parameter SMD_MSR_Modelica.Units.Length L;
      parameter Boolean EnableRad;
      parameter SMD_MSR_Modelica.Units.Area Ar;
      parameter SMD_MSR_Modelica.Units.Emissivity e;
      parameter SMD_MSR_Modelica.Units.Temperature T_0;
      parameter SMD_MSR_Modelica.Units.Temperature Tinf;
      SMD_MSR_Modelica.Units.Mass m;
      SMD_MSR_Modelica.Units.MassFlowRate mDotIn[numStreams];
      SMD_MSR_Modelica.Units.MassFlowRate mDotOut;
      SMD_MSR_Modelica.Units.Temperature T;
      SMD_MSR_Modelica.Units.Power powM;
      SMD_MSR_Modelica.Units.Power powRad;
      SMD_MSR_Modelica.Units.Power powCond;
      SMD_MSR_Modelica.Units.Power powFlowOut;
      SMD_MSR_Modelica.Units.Power powFlowIn[numStreams];
      SMD_MSR_Modelica.Units.Power powDecay;
      output SMD_MSR_Modelica.PortsConnectors.TempOut temp_Out annotation(
        Placement(transformation(origin = {60, 0}, extent = {{-20, -20}, {20, 20}}), iconTransformation(origin = {18, 0}, extent = {{-20, -20}, {20, 20}})));
      input SMD_MSR_Modelica.PortsConnectors.FlowFractionIn flowFraction[numStreams] annotation(
        Placement(transformation(origin = {-58, -60}, extent = {{-20, -20}, {20, 20}}), iconTransformation(origin = {-60, -42}, extent = {{-20, -20}, {20, 20}})));
      input SMD_MSR_Modelica.PortsConnectors.TempIn temp_In[numStreams] annotation(
        Placement(visible = true, transformation(origin = {-60, 0}, extent = {{-20, -20}, {20, 20}}, rotation = 0), iconTransformation(origin = {-60, -2}, extent = {{-20, -20}, {20, 20}}, rotation = 0)));
      input SMD_MSR_Modelica.PortsConnectors.VolumetircPowerIn decayHeat annotation(
        Placement(transformation(origin = {-58, 58}, extent = {{-22, -22}, {22, 22}}), iconTransformation(origin = {-60, 40}, extent = {{-20, -20}, {20, 20}})));
    initial equation
      T = T_0;
    equation
      for i in 1:numStreams loop
        mDotIn[i] = VdotNom*flowFraction[i].FF*flowFractionsNom[i]*rho;
        powFlowIn[i] = mDotIn[i]*Cp*temp_In[i].T;
      end for;
      mDotOut = sum(mDotIn);
      m = vol*rho;
      powM = m*Cp*der(T);
      if EnableRad == true then
        powRad = e*SMD_MSR_Modelica.Constants.SigSBK*Ar*((Tinf + 273.15)^4 - (T + 273.15)^4);
      else
        powRad = 0;
      end if;
      powCond = ((K*Ac)/L)*((sum(temp_In.T)/numStreams) - T);
      powFlowOut = mDotOut*Cp*T;
      powDecay = decayHeat.Q*vol;
      powM = sum(powFlowIn) - powFlowOut + powCond + powRad + powDecay;
      temp_Out.T = T;
      annotation(
        Diagram(graphics = {Rectangle(origin = {0, -6}, lineThickness = 2, extent = {{-80, 86}, {80, -86}}), Text(origin = {50, 69}, extent = {{-26, 15}, {26, -15}}, textString = "Mixing Pot"), Text(origin = {-58, -20}, extent = {{-16, 6}, {16, -6}}, textString = "T_In"), Text(origin = {-58, -80}, extent = {{-16, 6}, {16, -6}}, textString = "FF"), Text(origin = {-58, 38}, extent = {{-16, 6}, {16, -6}}, textString = "DH"), Text(origin = {62, -18}, extent = {{-16, 6}, {16, -6}}, textString = "T_Out")}, coordinateSystem(extent = {{-80, 80}, {80, -100}})),
        Icon(graphics = {Text(origin = {-8, 43}, extent = {{-26, 15}, {26, -15}}, textString = "Mixing Pot"), Rectangle(origin = {-20, 0}, lineThickness = 1, extent = {{-60, 60}, {60, -60}})}, coordinateSystem(extent = {{-80, 60}, {40, -60}})));
    end MixingPot;

    /* Flow distributor: splits a single pump FF signal to numOutput regions,
       each with an optional per-region trip. After a region trip the local FF
       undergoes exponential coast-down toward freeConvectionFF with decay
       constant coastDownK (independent of pump FF). */
    model FlowDistributor
      parameter Integer numOutput;
      parameter SMD_MSR_Modelica.Units.FlowFraction freeConvectionFF;
      parameter SMD_MSR_Modelica.Units.PumpConstant coastDownK;
      parameter SMD_MSR_Modelica.Units.InitiationTime regionTripTime[numOutput];
      output SMD_MSR_Modelica.PortsConnectors.FlowFractionOut flowFracOut[numOutput] annotation(
        Placement(transformation(origin = {39, -39}, extent = {{-17, -17}, {17, 17}}), iconTransformation(origin = {19, -39}, extent = {{-17, -17}, {17, 17}})));
      input SMD_MSR_Modelica.PortsConnectors.FlowFractionIn flowFracIn annotation(
        Placement(transformation(origin = {-41, -39}, extent = {{-15, -15}, {15, 15}}), iconTransformation(origin = {19, 3}, extent = {{-15, -15}, {15, 15}})));
    equation
      for i in 1:numOutput loop
        flowFracOut[i].FF = (flowFracIn.FF*(1 - freeConvectionFF)*exp(-coastDownK*delay(time, regionTripTime[i], regionTripTime[i]))) + freeConvectionFF;
      end for;
      annotation(
        Diagram(graphics = {Rectangle(origin = {0, -30}, lineColor = {230, 97, 0}, lineThickness = 1.5, extent = {{-60, 30}, {60, -30}}), Text(origin = {16, -16}, extent = {{-42, 16}, {42, -16}}, textString = "FlowDistributor")}, coordinateSystem(extent = {{-60, 20}, {60, -60}})),
        Icon(graphics = {Text(origin = {21, -18}, extent = {{-35, 14}, {35, -14}}, textString = "FlowDistributor"), Rectangle(origin = {20, -20}, lineColor = {230, 97, 0}, lineThickness = 1.5, extent = {{-40, 40}, {40, -40}})}, coordinateSystem(extent = {{-20, 20}, {60, -60}})));
    end FlowDistributor;

    /* Pump model with multi-stage sigmoid ramp-up and exponential trip
       coast-down. Each stage i adds a sigmoid increment toward rampUpTo[i],
       activating at rampUpTime[i] with time constant rampUpK[i]. After
       tripTime the total FF decays exponentially toward freeConvFF with
       time constant tripK. */
    model Pump
      parameter Integer numRampUp = 1;
      parameter SMD_MSR_Modelica.Units.PumpConstant rampUpK[numRampUp];
      parameter SMD_MSR_Modelica.Units.FlowFraction rampUpTo[numRampUp];
      parameter SMD_MSR_Modelica.Units.InitiationTime rampUpTime[numRampUp];
      parameter SMD_MSR_Modelica.Units.PumpConstant tripK;
      parameter SMD_MSR_Modelica.Units.InitiationTime tripTime;
      parameter SMD_MSR_Modelica.Units.FlowFraction freeConvFF;
      constant Real epsilon = 1E-4;
      SMD_MSR_Modelica.Units.FlowFraction rampUp[numRampUp];
      SMD_MSR_Modelica.Units.FlowFraction rampDown;
      output SMD_MSR_Modelica.PortsConnectors.FlowFractionOut flowFrac annotation(
        Placement(visible = true, transformation(origin = {-1.77636e-15, -40}, extent = {{-20, -20}, {20, 20}}, rotation = 0), iconTransformation(origin = {1.77636e-15, -60}, extent = {{-20, -20}, {20, 20}}, rotation = 0)));
    initial equation
// primaryFlowFrac.FF = 0;
    equation
      // Each rampUp[i] is a sigmoid increment toward its target FF; stages are additive.
      for i in 1:numRampUp loop
        if i == 1 then
          rampUp[i] = (rampUpTo[i] - freeConvFF)/(1 + exp(log(1/epsilon - 1)*(1 - (delay(time, rampUpTime[i], rampUpTime[i]))/rampUpK[i])));
        else
          rampUp[i] = (rampUpTo[i] - rampUpTo[i - 1])/(1 + exp(log(1/epsilon - 1)*(1 - (delay(time, rampUpTime[i], rampUpTime[i]))/rampUpK[i])));
        end if;
      end for;
      // rampDown: exponential coast-down of the accumulated FF after tripTime.
      if time >= tripTime then
        rampDown = (-sum(rampUp))*exp(-(1/tripK)*delay(time, tripTime, tripTime)) + sum(rampUp);
      else
        rampDown = 0;
      end if;
      if time >= tripTime then
        flowFrac.FF = sum(rampUp) - rampDown + freeConvFF;
      elseif time >= rampUpTime[1] then
        flowFrac.FF = sum(rampUp) + freeConvFF;
      else
        flowFrac.FF = freeConvFF;
      end if;
      annotation(
        Diagram(graphics = {Rectangle(lineColor = {245, 121, 0}, lineThickness = 2, extent = {{-60, 60}, {60, -60}}), Text(origin = {-2, 6}, extent = {{-42, 16}, {42, -16}}, textString = "Pump")}, coordinateSystem(extent = {{-60, 60}, {60, -60}})),
        Icon(graphics = {Rectangle(origin = {0, -20}, lineColor = {245, 121, 0}, lineThickness = 1, extent = {{-60, 60}, {60, -60}}), Text(origin = {1, -17}, extent = {{-29, 9}, {29, -9}}, textString = "Pump")}, coordinateSystem(extent = {{-60, 40}, {60, -80}})));
    end Pump;

    model RTMSlayer
      //first and last layer experiance radiation heat transfer
      //first node generate heat to emulate an embedded heating element
      //geometry is setup for a can with a hemispherical bottom and characterized by height of cylinder and radius of hemisphere
      //geometry can be changed by changing equations for A0, A, V0 and V
      parameter Integer numLayers1 = 3;//steel jacket
      parameter Integer numLayers2 = 6;//insulating jacket
      parameter SMD_MSR_Modelica.Units.Density rho1 = 8238;//steel jacket
      parameter SMD_MSR_Modelica.Units.Density rho2 = 430;//insulating jacket https://www.rockwool.com/group/advice-and-inspiration/why-stone-wool/thermal-properties/
      parameter SMD_MSR_Modelica.Units.HeatCapacity Cp1 = 468;//steel jacket
      parameter SMD_MSR_Modelica.Units.HeatCapacity Cp2 = 0.1;//insulating jacket
      parameter SMD_MSR_Modelica.Units.Length hCylinder = 2.0260;
      parameter SMD_MSR_Modelica.Units.Length radius = 0.8720;
      parameter SMD_MSR_Modelica.Units.Length thickness1 = 0.01;//steel jacket
      parameter SMD_MSR_Modelica.Units.Length thickness2 = 0.1920;//insulating jacket
      parameter SMD_MSR_Modelica.Units.Conductivity k1 = 13.4;//steel jacket
      parameter SMD_MSR_Modelica.Units.Conductivity k2 = 0.071;//insulating jacket
      parameter SMD_MSR_Modelica.Units.Emissivity eIn = 0.8;
      parameter SMD_MSR_Modelica.Units.Emissivity eOut = 0.1;

      constant SMD_MSR_Modelica.Units.Power real2pow = 1;

      SMD_MSR_Modelica.Units.Length L1;//discritazised layer thickness of steel
      SMD_MSR_Modelica.Units.Length L2;//discritazised layer thickness of insulation
      SMD_MSR_Modelica.Units.Length L[numLayers1 + numLayers2];//layer thickness vector
      SMD_MSR_Modelica.Units.Length cL[numLayers1 + numLayers2];//cummalative thickness
      SMD_MSR_Modelica.Units.Area A0;//inner area
      SMD_MSR_Modelica.Units.Area A[numLayers1 + numLayers2];//node area
      SMD_MSR_Modelica.Units.Volume V0;//inner volume
      SMD_MSR_Modelica.Units.Volume V[numLayers1 + numLayers2];//node volume
      SMD_MSR_Modelica.Units.Volume cV[numLayers1 + numLayers2];//cummalative volume
      SMD_MSR_Modelica.Units.Density rho[numLayers1 + numLayers2];
      SMD_MSR_Modelica.Units.Conductivity k[numLayers1 + numLayers2];
      SMD_MSR_Modelica.Units.Temperature T[numLayers1 + numLayers2];
      SMD_MSR_Modelica.Units.SpecificHeatCapacity Cp[numLayers1 + numLayers2];
      SMD_MSR_Modelica.Units.Power pow[numLayers1 + numLayers2];
      SMD_MSR_Modelica.Units.Power cond[numLayers1 + numLayers2 - 1];
      SMD_MSR_Modelica.Units.Power radIn;
      SMD_MSR_Modelica.Units.Power radOut;
      SMD_MSR_Modelica.Units.Power powGen;


      input SMD_MSR_Modelica.PortsConnectors.TempIn Tin annotation(
        Placement(transformation(origin = {80, 0}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {80, 0}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.RealIn heaterPower annotation(
        Placement(transformation(origin = {-80, -82}, extent = {{-6, -5}, {6, 5}}), iconTransformation(origin = {-80, -80}, extent = {{-10, -10}, {10, 10}})));
      parameter SMD_MSR_Modelica.Units.Temperature Tamb = 25;

    //parameter SMD_MSR_Modelica.Units.Temperature Tin;
      /*//output SMD_MSR_Modelica.PortsConnectors.TempOut Tamb annotation(
        Placement(transformation(origin = {-80, 0}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-80, 0}, extent = {{-10, -10}, {10, 10}})));
    */
    initial equation
      der(T) = fill(0, numLayers1 + numLayers2);

    equation
      L1 = thickness1/numLayers1;//steel jacket dl
      L2 = thickness2/numLayers2;//insulation jacket dl

    //set up vectors for easy handling
      L[1:numLayers1] = fill(L1, numLayers1);
      L[numLayers1 + 1:numLayers1 + numLayers2] = fill(L2, numLayers1 + numLayers2);
      rho[1:numLayers1] = fill(rho1, numLayers1);
      rho[numLayers1 + 1:numLayers1 + numLayers2] = fill(rho2, numLayers1 + numLayers2);
      Cp[1:numLayers1] = fill(Cp1, numLayers1);
      Cp[numLayers1 + 1:numLayers1 + numLayers2] = fill(Cp2, numLayers1 + numLayers2);
      k[1:numLayers1] = fill(k1, numLayers1);
      k[numLayers1 + 1:numLayers1 + numLayers2] = fill(k2, numLayers1 + numLayers2);

      A0 = 2*(22/7)*radius*hCylinder + (22/7)*radius^2 + 2*(22/7)*radius^2;//inner surface area A_cylinder + A_lid + A_hemishpere
      V0 = (22/7)*radius^2*hCylinder + (2/3)*(22/7)*radius^2;//inner volume V_cylinder + V_hemisphere

      cL[1] = L[1];
      V[1] = cV[1] - V0;//first node volume
      for i in 2:numLayers1 + numLayers2 loop
        cL[i] = cL[i - 1] + L[i];//cummalative length
        V[i] = cV[i] - cV[i - 1];//node volume
      end for;

      for i in 1:numLayers1 + numLayers2 loop
        A[i] = 2*(22/7)*(radius + cL[i])*hCylinder + (22/7)*(radius + cL[i])^2 + 2*(22/7)*(radius + cL[i])^2;//inner surface area A_cylinder + A_lid + A_hemishpere
        cV[i] = (22/7)*(radius + cL[i])^2*hCylinder + (22/7)*cL[i]*(radius + cL[i])^2 + (2/3)*(22/7)*(radius + cL[i])^2;//cummalative volume V_cylinder + V_hemisphere + V_lid
      end for;

      radIn = eIn*SMD_MSR_Modelica.Constants.SigSBK*A0*((Tin.T + 273.15)^4 - (T[1] + 273.15)^4 );//radiation in to node 1
      radOut = eOut*SMD_MSR_Modelica.Constants.SigSBK*A[end]*((T[end] + 273.15)^4 - (Tamb + 273.15)^4);//radiation out from end node

      for i in 1:numLayers1 + numLayers2 - 1 loop
        cond[i] = (k[i]*A[i]/L[i])*(T[i] - T[i + 1]);//set up conduction terms
      end for;
      powGen = (real2pow);
      pow = rho.*V.*Cp.*der(T);//node energy

      pow[1] = radIn - cond[1] + powGen;//node 1 energy balance
      pow[end] = cond[end] - radOut;//end node energy balance

      //pow[1]= eIn*SMD_MSR_Modelica.Constants.SigSBK*A0*((Tin.T + 273.15)^4 - (T[1] + 273.15)^4 ) - cond[1];
      //pow[end]= cond[end] - eOut*SMD_MSR_Modelica.Constants.SigSBK*A[end]*((T[end] + 273.15)^4 - (Tamb + 273.15)^4);

      for i in 2:numLayers1 + numLayers2-1  loop
        pow[i] = cond[i-1] - cond[i];//internal node energy balance
      end for;

      annotation(
        Diagram(graphics = {Rectangle(lineThickness = 1, extent = {{-100, 120}, {100, -120}}), Rectangle(origin = {0, 30}, lineColor = {240, 74, 121}, fillColor = {240, 74, 121}, pattern = LinePattern.None, fillPattern = FillPattern.Solid, extent = {{-60, 70}, {60, -70}}), Ellipse(origin = {0, -43}, fillColor = {240, 74, 121}, pattern = LinePattern.None, fillPattern = FillPattern.Solid, extent = {{-60, -57}, {60, 57}}), Rectangle(origin = {0, 27}, pattern = LinePattern.None, fillPattern = FillPattern.Solid, extent = {{-50, 62}, {50, -62}}), Ellipse(origin = {0, -36}, pattern = LinePattern.None, fillPattern = FillPattern.Solid, extent = {{-50, -51}, {50, 51}}), Rectangle(origin = {0, 24}, fillColor = {26, 95, 180}, pattern = LinePattern.None, fillPattern = FillPattern.Solid, extent = {{-46, 61}, {46, -61}}), Ellipse(origin = {0, -36}, fillColor = {26, 95, 180}, pattern = LinePattern.None, fillPattern = FillPattern.Solid, extent = {{-46, -47}, {46, 47}}), Text(origin = {0, 6}, extent = {{-18, -12}, {18, 12}}, textString = "RTMS")}, coordinateSystem(extent = {{-100, 120}, {100, -120}})),
    Icon(graphics = {Rectangle(lineThickness = 1, extent = {{-100, 120}, {100, -120}}), Rectangle(origin = {0, 30}, lineColor = {240, 74, 121}, fillColor = {240, 74, 121}, pattern = LinePattern.None, fillPattern = FillPattern.Solid, extent = {{-60, 70}, {60, -70}}), Ellipse(origin = {0, -43}, fillColor = {240, 74, 121}, pattern = LinePattern.None, fillPattern = FillPattern.Solid, extent = {{-60, -57}, {60, 57}}), Rectangle(origin = {0, 27}, pattern = LinePattern.None, fillPattern = FillPattern.Solid, extent = {{-50, 62}, {50, -62}}), Ellipse(origin = {0, -36}, pattern = LinePattern.None, fillPattern = FillPattern.Solid, extent = {{-50, -51}, {50, 51}}), Rectangle(origin = {0, 24}, fillColor = {26, 95, 180}, pattern = LinePattern.None, fillPattern = FillPattern.Solid, extent = {{-46, 61}, {46, -61}}), Ellipse(origin = {0, -36}, fillColor = {26, 95, 180}, pattern = LinePattern.None, fillPattern = FillPattern.Solid, extent = {{-46, -47}, {46, 47}}), Text(origin = {0, 6}, extent = {{-18, -12}, {18, 12}}, textString = "RTMS")}, coordinateSystem(extent = {{-100, 120}, {100, -120}})));
    end RTMSlayer;

    model HeatExchangerwithHeat
      parameter SMD_MSR_Modelica.Units.Volume vol_P;
      parameter SMD_MSR_Modelica.Units.Volume vol_T;
      parameter SMD_MSR_Modelica.Units.Volume vol_S;
      parameter SMD_MSR_Modelica.Units.Density rhoP;
      parameter SMD_MSR_Modelica.Units.Density rhoT;
      parameter SMD_MSR_Modelica.Units.Density rhoS;
      parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP_P;
      parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP_T;
      parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP_S;
      parameter SMD_MSR_Modelica.Units.VolumeticFlowRate VdotPnom;
      parameter SMD_MSR_Modelica.Units.VolumeticFlowRate VdotSnom;
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
      parameter SMD_MSR_Modelica.Units.Temperature Tinf;
      parameter SMD_MSR_Modelica.Units.Temperature TpIn_0;
      parameter SMD_MSR_Modelica.Units.Temperature TpOut_0;
      parameter SMD_MSR_Modelica.Units.Temperature TsIn_0;
      parameter SMD_MSR_Modelica.Units.Temperature TsOut_0;
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
        Placement(transformation(origin = {-132, 30}, extent = {{-16, -16}, {16, 16}}), iconTransformation(origin = {-136, 30}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.TempIn T_in_sFluid annotation(
        Placement(transformation(origin = {137, -31}, extent = {{-17, -17}, {17, 17}}), iconTransformation(origin = {136, -30}, extent = {{-10, -10}, {10, 10}})));
      output SMD_MSR_Modelica.PortsConnectors.TempOut T_out_sFluid annotation(
        Placement(transformation(origin = {-134, -36}, extent = {{-14, -14}, {14, 14}}), iconTransformation(origin = {-136, -30}, extent = {{-10, -10}, {10, 10}})));
      output SMD_MSR_Modelica.PortsConnectors.TempOut T_out_pFluid annotation(
        Placement(transformation(origin = {134, 28}, extent = {{-18, -18}, {18, 18}}), iconTransformation(origin = {136, 30}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.FlowFractionIn primaryFF annotation(
        Placement(transformation(origin = {-90, 49}, extent = {{-9, -9}, {9, 9}}), iconTransformation(origin = {-90, 50}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.FlowFractionIn secondaryFF annotation(
        Placement(transformation(origin = {100, -47}, extent = {{-11, -11}, {11, 11}}), iconTransformation(origin = {90, -50}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.VolumetircPowerIn P_decay annotation(
        Placement(transformation(origin = {10, 50}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {0, 50}, extent = {{-10, -10}, {10, 10}})));
    initial equation
      T_PN1 = TpIn_0 - (TpIn_0 - TpOut_0)/4;
      T_PN2 = TpIn_0 - 2*(TpIn_0 - TpOut_0)/4;
      T_PN3 = TpIn_0 - 3*(TpIn_0 - TpOut_0)/4;
      T_out_pFluid.T = TpOut_0;
      T_TN1 = (T_PN1*hApn + T_SN3*hAsn)/(hApn + hAsn);
      T_TN2 = (T_PN3*hApn + T_SN1*hAsn)/(hApn + hAsn);
      T_SN1 = TpIn_0 + (TsOut_0 - TpIn_0);
      T_SN2 = TpIn_0 + 2*(TsOut_0 - TpIn_0);
      T_SN3 = TpIn_0 + 3*(TsOut_0 - TpIn_0);
      T_out_sFluid.T = TsOut_0;
    equation
      mDotP = VdotPnom*rhoP*primaryFF.FF;
      mDotS = VdotSnom*rhoS*secondaryFF.FF;
      hApn = (hApNom/4)*(0.8215*primaryFF.FF^6 - 4.108*primaryFF.FF^5 + 7.848*primaryFF.FF^4 - 7.165*primaryFF.FF^3 + 3.004*primaryFF.FF^2 + 0.5903*primaryFF.FF + 0.008537);
      hAsn = (hAsNom/4)*(0.8215*secondaryFF.FF^6 - 4.108*secondaryFF.FF^5 + 7.848*secondaryFF.FF^4 - 7.165*secondaryFF.FF^3 + 3.004*secondaryFF.FF^2 + 0.5903*secondaryFF.FF + 0.008537);
    //hApn = (hApNom/4);
    //hAsn = (hAsNom/4);
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
      condPowPN1 = ((Kp*AcShell)/LpN)*(T_in_pFluid.T - T_PN1);
      condPowPN2 = ((Kp*AcShell)/LpN)*(T_PN1 - T_PN2);
      condPowPN3 = ((Kp*AcShell)/LpN)*(T_PN2 - T_PN3);
      condPowPN4 = ((Kp*AcShell)/LpN)*(T_PN3 - T_out_pFluid.T);
      condPowSN1 = ((Ks*AcTube)/LsN)*(T_in_sFluid.T - T_SN1);
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
      powPN1 = flowPowPN1 + condPowPN1 - convPowPN1 + radPowPN1 + decayPowPN1;
      powPN2 = flowPowPN2 + condPowPN2 - convPowPN2 + radPowPN2 + decayPowPN2;
      powPN3 = flowPowPN3 + condPowPN3 - convPowPN3 + radPowPN3 + decayPowPN3;
      powPN4 = flowPowPN4 + condPowPN4 - convPowPN4 + radPowPN4 + decayPowPN4;
      powTN1 = convPowPN1 + convPowPN2 - convPowSN3 - convPowSN4;
      powTN2 = convPowPN3 + convPowPN4 - convPowSN1 - convPowSN2;
      powSN1 = flowPowSN1 + convPowSN1;
      powSN2 = flowPowSN2 + convPowSN2;
      powSN3 = flowPowSN3 + convPowSN3;
      powSN4 = flowPowSN4 + convPowSN4;
      annotation(
        Diagram(graphics = {Rectangle(extent = {{-149.242, 59.993}, {149.242, -59.993}}), Rectangle(origin = {-80, 30}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 10}, {20, -10}}), Rectangle(origin = {100, 30}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 10}, {20, -10}}), Rectangle(origin = {-20, 30}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 10}, {20, -10}}), Rectangle(origin = {-80.95, -29.61}, lineColor = {239, 41, 41}, fillColor = {239, 41, 41}, fillPattern = FillPattern.Solid, extent = {{-19.63, -9.61}, {19.63, 9.61}}), Rectangle(origin = {-20.12, -29.82}, lineColor = {239, 41, 41}, fillColor = {239, 41, 41}, fillPattern = FillPattern.Solid, extent = {{-19.88, 9.82}, {19.88, -9.82}}), Rectangle(origin = {40.8, -29.85}, lineColor = {239, 41, 41}, fillColor = {239, 41, 41}, fillPattern = FillPattern.Solid, extent = {{-19.8, 9.85}, {19.8, -9.85}}), Rectangle(origin = {100.85, -29.89}, lineColor = {239, 41, 41}, fillColor = {239, 41, 41}, fillPattern = FillPattern.Solid, extent = {{-19.85, 9.89}, {19.85, -9.89}}), Rectangle(origin = {70.92, -1.05}, lineColor = {136, 138, 133}, fillColor = {136, 138, 133}, fillPattern = FillPattern.Solid, extent = {{-49.92, 8.83}, {49.92, -8.83}}), Rectangle(origin = {40, 30}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 10}, {20, -10}}), Rectangle(origin = {-50.08, -1.05}, lineColor = {136, 138, 133}, fillColor = {136, 138, 133}, fillPattern = FillPattern.Solid, extent = {{-49.92, 8.83}, {49.92, -8.83}}), Text(origin = {120, 49}, extent = {{-28, 9}, {28, -9}}, textString = "HX")}, coordinateSystem(extent = {{-160, 60}, {160, -60}})),
        Icon(graphics = {Rectangle(lineThickness = 1, extent = {{-149.24, 59.99}, {149.24, -59.99}}), Rectangle(origin = {-60.08, -1.05}, lineColor = {136, 138, 133}, fillColor = {136, 138, 133}, fillPattern = FillPattern.Solid, extent = {{-49.92, 8.83}, {49.92, -8.83}}), Rectangle(origin = {30, 30}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 10}, {20, -10}}), Rectangle(origin = {60.92, -1.05}, lineColor = {136, 138, 133}, fillColor = {136, 138, 133}, fillPattern = FillPattern.Solid, extent = {{-49.92, 8.83}, {49.92, -8.83}}), Rectangle(origin = {-30.12, -30.82}, lineColor = {239, 41, 41}, fillColor = {239, 41, 41}, fillPattern = FillPattern.Solid, extent = {{-19.88, 9.82}, {19.88, -9.82}}), Rectangle(origin = {-30, 30}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 10}, {20, -10}}), Rectangle(origin = {-90, 30}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 10}, {20, -10}}), Rectangle(origin = {90, 30}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 10}, {20, -10}}), Rectangle(origin = {30.8, -30.85}, lineColor = {239, 41, 41}, fillColor = {239, 41, 41}, fillPattern = FillPattern.Solid, extent = {{-19.8, 9.85}, {19.8, -9.85}}), Rectangle(origin = {90.85, -30.89}, lineColor = {239, 41, 41}, fillColor = {239, 41, 41}, fillPattern = FillPattern.Solid, extent = {{-19.85, 9.89}, {19.85, -9.89}}), Rectangle(origin = {-90.95, -30.61}, lineColor = {239, 41, 41}, fillColor = {239, 41, 41}, fillPattern = FillPattern.Solid, extent = {{-19.63, -9.61}, {19.63, 9.61}}), Text(origin = {118, 49}, extent = {{-28, 9}, {28, -9}}, textString = "HX")}, coordinateSystem(extent = {{-160, 60}, {160, -60}})));
    end HeatExchangerwithHeat;
  end HeatTransport;

  /* Point kinetics + neutronics sub-models for the MSRR simulation.
     Includes: PKE (fixed-fuel), mPKE (circulating-fuel), FuelChannel,
     DecayHeat, ReactivityFeedback, PowerBlock, and SumReactivity. */
  package Nuclear
    /* Standard 6-group point kinetics equations for a fixed-fuel reactor.
       ReactivityIn.R is in pcm and is multiplied by 1e-5 to obtain Delta k/k.
       The external neutron source S is normalised to 1.58x10^20 n/s (~ full-power flux). */
    model PKE
      //Parameter declaration
      parameter Integer numGroups = 6;
      parameter SMD_MSR_Modelica.Units.DecayConstant lambda[numGroups];
      parameter SMD_MSR_Modelica.Units.DelayedNeutronFrac beta[numGroups];
      parameter SMD_MSR_Modelica.Units.NeutronGenerationTime LAMBDA;
      parameter SMD_MSR_Modelica.Units.NominalNeutronPopulation n_0;
      parameter SMD_MSR_Modelica.Units.NominalNeutronPopulation nFloor = 1e-9
      "Numerical floor for normalized neutron population during low-power solves";
      parameter SMD_MSR_Modelica.Units.NominalNeutronPopulation nFloorDuringForcing = 1e-9
      "Optional floor applied after nFloorSwitchTime (frequency forcing window)"
      annotation(Evaluate = false);
      parameter SMD_MSR_Modelica.Units.InitiationTime nFloorSwitchTime = 1e100
      "Time to switch from nFloor to nFloorDuringForcing"
      annotation(Evaluate = false);
      parameter SMD_MSR_Modelica.Units.InitMode initMode = SMD_MSR_Modelica.Units.InitMode.FixedStart;

      SMD_MSR_Modelica.Units.DelayedNeutronFrac sumBeta;
      SMD_MSR_Modelica.Units.PrecursorConc CG[numGroups];
      SMD_MSR_Modelica.Units.PrecursorDecayRate CGDecay[numGroups];
      SMD_MSR_Modelica.Units.Reactivity reactivity;
      SMD_MSR_Modelica.Units.Reactivity externalReactivityIn;
      SMD_MSR_Modelica.Units.NomalizedNeutronEmissionRate nomS;
      SMD_MSR_Modelica.Units.NominalNeutronPopulation nFloorActive;
      SMD_MSR_Modelica.Units.NominalNeutronPopulation nClamped;
      Real nDotRaw;
      input SMD_MSR_Modelica.PortsConnectors.ReactivityIn feedback annotation(
        Placement(transformation(origin = {40, 38}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {20, 20}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.NeutronEmsRateIn S annotation(
        Placement(transformation(origin = {0, 38}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-8, 20}, extent = {{-10, -10}, {10, 10}})));
      output SMD_MSR_Modelica.PortsConnectors.NomNeutronPopOut n_population annotation(
        Placement(visible = true, transformation(origin = {0, -38}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {-10, -40}, extent = {{-12, -12}, {12, 12}}, rotation = 0)));
      input SMD_MSR_Modelica.PortsConnectors.RealIn ReactivityIn annotation(
        Placement(transformation(origin = {-36, 38}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-40, 20}, extent = {{-10, -10}, {10, 10}})));
    initial equation
      if initMode == SMD_MSR_Modelica.Units.InitMode.FixedStart then
        n_population.n = max(n_0, nFloor);
        for i in 1:numGroups loop
          der(CG[i]) = 0;
        end for;
      else
        der(n_population.n) = 0;
        for i in 1:numGroups loop
          der(CG[i]) = 0;
        end for;
      end if;
    equation
      // PKE neutron balance: prompt production + delayed precursor decay + external source.
      // nFloor is a numerical lower bound, distinct from the physical external source term nomS.
      nFloorActive = if time >= nFloorSwitchTime then nFloorDuringForcing else nFloor;
      nClamped = noEvent(max(n_population.n, nFloorActive));
      nDotRaw = ((reactivity - sum(beta))/LAMBDA)*nClamped + sum(CGDecay) + nomS;
      der(n_population.n) = if n_population.n <= nFloorActive and nDotRaw < 0 then 0 else nDotRaw;
      reactivity = feedback.rho + externalReactivityIn;
      sumBeta = sum(beta);
      externalReactivityIn = ReactivityIn.R*1E-5; // convert pcm -> Delta k/k
      nomS = S.nDot/1.58E20; // normalise source to unit neutron population
      for i in 1:numGroups loop
        CGDecay[i] = lambda[i]*CG[i];
        der(CG[i]) = (beta[i]*nClamped)/LAMBDA - CGDecay[i];
      end for;
      when n_population.n < nFloorActive then
        reinit(n_population.n, nFloorActive);
      end when;
      annotation(
        Diagram(graphics = {Rectangle(origin = {2, 0}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, lineThickness = 2, extent = {{-50, 50}, {50, -50}}), Text(origin = {3, 1}, extent = {{37, -27}, {-37, 27}}, textString = "PKE")}, coordinateSystem(extent = {{-60, 60}, {60, -60}})),
        Icon(graphics = {Rectangle(origin = {-10, -10}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, lineThickness = 1.5, extent = {{-50, 50}, {50, -50}}), Text(origin = {-11, -11}, extent = {{37, -27}, {-37, 27}}, textString = "PKE")}, coordinateSystem(extent = {{-60, 40}, {40, -60}})));
    end PKE;

    /* Modified PKE for circulating-fuel MSR (Dulla et al. formulation).
       Delayed-neutron precursors are swept out of the core during out-of-core
       transit (varTauLoop) and return with radioactive decay applied (CGReturn).
       rho_0sta / rho_0dyn correct the steady-state bias caused by precursor
       drift: rho_0 = beta - sum(CG_0), where CG_0 depends on transit times. */
    model mPKE
      //Parameter declaration
      parameter Integer numGroups;
      parameter SMD_MSR_Modelica.Units.DecayConstant lambda[numGroups];
      parameter SMD_MSR_Modelica.Units.DelayedNeutronFrac beta[numGroups];
      parameter SMD_MSR_Modelica.Units.NeutronGenerationTime LAMBDA;
      parameter SMD_MSR_Modelica.Units.ResidentTime nomTauCore;
      parameter SMD_MSR_Modelica.Units.ResidentTime nomTauLoop;
      parameter SMD_MSR_Modelica.Units.NominalNeutronPopulation n_0;
      parameter SMD_MSR_Modelica.Units.NominalNeutronPopulation nFloor = 1e-9
      "Numerical floor for normalized neutron population during low-power solves";
      parameter SMD_MSR_Modelica.Units.NominalNeutronPopulation nFloorDuringForcing = 1e-9
      "Optional floor applied after nFloorSwitchTime (frequency forcing window)"
      annotation(Evaluate = false);
      parameter SMD_MSR_Modelica.Units.InitiationTime nFloorSwitchTime = 1e100
      "Time to switch from nFloor to nFloorDuringForcing"
      annotation(Evaluate = false);
      parameter SMD_MSR_Modelica.Units.InitMode initMode = SMD_MSR_Modelica.Units.InitMode.FixedStart;
      //Variable decleration
      SMD_MSR_Modelica.Units.DelayedNeutronFrac sumBeta;
      SMD_MSR_Modelica.Units.DelayedNeutronFrac sumCG_0dyn;
      SMD_MSR_Modelica.Units.DelayedNeutronFrac rho_0dyn;
      SMD_MSR_Modelica.Units.DelayedNeutronFrac rho_0sta;
      //SMD_MSR_Modelica.Units.DelayedNeutronFrac beta_eff;
      SMD_MSR_Modelica.Units.ResidentTime varTauCore;
      SMD_MSR_Modelica.Units.ResidentTime varTauLoop;
      SMD_MSR_Modelica.Units.PrecursorConc CG[numGroups];
      SMD_MSR_Modelica.Units.PrecursorConc CG_0dyn[numGroups];
      SMD_MSR_Modelica.Units.PrecursorConc CG_0sta[numGroups];
      SMD_MSR_Modelica.Units.PrecursorReturnRate CGReturn[numGroups];
      SMD_MSR_Modelica.Units.PrecursorDecayRate CGDecay[numGroups];
      SMD_MSR_Modelica.Units.Reactivity reactivity;
      SMD_MSR_Modelica.Units.Reactivity externalReactivityIn;
      SMD_MSR_Modelica.Units.NomalizedNeutronEmissionRate nomS;
      SMD_MSR_Modelica.Units.NominalNeutronPopulation nFloorActive;
      SMD_MSR_Modelica.Units.NominalNeutronPopulation nClamped;
      Real nDotRaw;
      input SMD_MSR_Modelica.PortsConnectors.ReactivityIn feedback annotation(
        Placement(visible = true, transformation(origin = {18, 40}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {0, 20}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
      input SMD_MSR_Modelica.PortsConnectors.FlowFractionIn fuelFlowFrac annotation(
        Placement(visible = true, transformation(origin = {42, 40}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {20, 20}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
      input SMD_MSR_Modelica.PortsConnectors.NeutronEmsRateIn S annotation(
        Placement(visible = true, transformation(origin = {-6, 40}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {-20, 20}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
      output SMD_MSR_Modelica.PortsConnectors.NomNeutronPopOut n_population annotation(
        Placement(visible = true, transformation(origin = {0, -38}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {-10, -40}, extent = {{-12, -12}, {12, 12}}, rotation = 0)));
      input SMD_MSR_Modelica.PortsConnectors.RealIn ReactivityIn annotation(
        Placement(visible = true, transformation(origin = {-36, 40}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {-40, 20}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
    initial equation
      if initMode == SMD_MSR_Modelica.Units.InitMode.FixedStart then
        n_population.n = max(n_0, nFloor);
        for i in 1:numGroups loop
          der(CG[i]) = 0;
        end for;
      else
        der(n_population.n) = 0;
        for i in 1:numGroups loop
          der(CG[i]) = 0;
        end for;
      end if;
    equation
      // Core and loop transit times scale inversely with FF; clamped to MaxTau at zero flow.
      varTauCore = if fuelFlowFrac.FF > SMD_MSR_Modelica.Constants.MinTau then nomTauCore/fuelFlowFrac.FF else SMD_MSR_Modelica.Constants.MaxTau;
      varTauLoop = if fuelFlowFrac.FF > SMD_MSR_Modelica.Constants.MinTau then nomTauLoop/fuelFlowFrac.FF else SMD_MSR_Modelica.Constants.MaxTau;
      // nFloor is a numerical lower bound, distinct from the physical external source term nomS.
      nFloorActive = if time >= nFloorSwitchTime then nFloorDuringForcing else nFloor;
      nClamped = noEvent(max(n_population.n, nFloorActive));
      nDotRaw = ((reactivity - sum(beta))/LAMBDA)*nClamped + sum(CGDecay) + nomS;
      der(n_population.n) = if n_population.n <= nFloorActive and nDotRaw < 0 then 0 else nDotRaw;
      // Apply flow-dependent circulating-fuel compensation only when the core loop is flowing.
      // Use rho_0dyn (current FF) so the compensation tracks instantaneous flow reactivity loss.
      reactivity = feedback.rho + externalReactivityIn
        + (if fuelFlowFrac.FF > SMD_MSR_Modelica.Constants.MinTau then rho_0dyn else 0);
      sumBeta = sum(beta);
      sumCG_0dyn = sum(CG_0dyn);
      externalReactivityIn = ReactivityIn.R*1E-5;
      nomS = S.nDot/1.58E20;
      rho_0sta = sumBeta - sum(CG_0sta);
      rho_0dyn = sumBeta - sumCG_0dyn;
      for i in 1:numGroups loop
        der(CG[i]) = (beta[i]*nClamped)/LAMBDA - CGDecay[i] - (CG[i]/varTauCore) + CGReturn[i];
        CGDecay[i] = lambda[i]*CG[i];
        // Precursors re-enter the core after one loop transit with radioactive decay applied.
        CGReturn[i] = (delay(CG[i], varTauLoop, SMD_MSR_Modelica.Constants.MaxTau)*exp(-lambda[i]*varTauLoop))/varTauCore;
        // Static (nominal-FF) steady-state CG used for circulating-fuel bias correction.
        CG_0sta[i] = beta[i]/(1 + (1/(lambda[i]*nomTauCore))*(1 - exp(-lambda[i]*nomTauLoop)));
        // Dynamic (current-FF) steady-state CG; used to track rho_0 at varying flow.
        CG_0dyn[i] = beta[i]/(1 + (1/(lambda[i]*varTauCore))*(1 - exp(-lambda[i]*varTauLoop)));
      end for;
      when n_population.n < nFloorActive then
        reinit(n_population.n, nFloorActive);
      end when;
      annotation(
        Diagram(graphics = {Rectangle(origin = {2, 0}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, lineThickness = 2, extent = {{-50, 50}, {50, -50}}), Text(origin = {3, 1}, extent = {{37, -27}, {-37, 27}}, textString = "mPKE")}, coordinateSystem(extent = {{-60, 60}, {60, -60}})),
        Icon(graphics = {Rectangle(origin = {-10, -10}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, lineThickness = 1, extent = {{-50, 50}, {50, -50}}), Text(origin = {-11, -11}, extent = {{37, -27}, {-37, 27}}, textString = "mPKE")}, coordinateSystem(extent = {{-60, 40}, {40, -60}})));
    end mPKE;

    /* Lumped two-node fuel-salt + one-node graphite fuel channel (SMD library version).
       Uses the same 6th-order polynomial hA(FF) as the HeatExchanger (rather than
       the FF^0.33 power law used in MSRR.Components.FuelChannel). */
    model FuelChannel
      parameter SMD_MSR_Modelica.Units.Volume vol_FN1;
      parameter SMD_MSR_Modelica.Units.Volume vol_FN2;
      parameter SMD_MSR_Modelica.Units.Volume vol_GN;
      parameter SMD_MSR_Modelica.Units.Density rho_fuel;
      parameter SMD_MSR_Modelica.Units.Density rho_grap;
      parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP_fuel;
      parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP_grap;
      parameter SMD_MSR_Modelica.Units.VolumeticFlowRate Vdot_fuelNom;
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
      parameter SMD_MSR_Modelica.Units.Temperature Tinf;
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
      input SMD_MSR_Modelica.PortsConnectors.VolumetircPowerIn decayHeat annotation(
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
    initial equation
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
      hA = hAnom*(0.8215*fuelFlowFraction.FF^6 - 4.108*fuelFlowFraction.FF^5 + 7.848*fuelFlowFraction.FF^4 - 7.165*fuelFlowFraction.FF^3 + 3.004*fuelFlowFraction.FF^2 + 0.5903*fuelFlowFraction.FF + 0.008537);
//hA = (hAnom);
      //hA = hAnom*fuelFlowFraction.FF^(0.33);
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
      convPowFN2 = hA*(kHT_FN2/(kHT_FN1 + kHT_FN2))*(fuelNode1.T - grapNode.T);
      if OuterRegion == true then
        radPowFN1 = e*SMD_MSR_Modelica.Constants.SigSBK*ArF1*((Tinf + 273.15)^4 - (fuelNode1.T + 273.15)^4);
        radPowFN2 = e*SMD_MSR_Modelica.Constants.SigSBK*ArF2*((Tinf + 273.15)^4 - (fuelNode2.T + 273.15)^4);
      else
        radPowFN1 = 0;
        radPowFN2 = 0;
      end if;
      fissPowFN1 = kFN1*fissionPower.P;
      fissPowFN2 = kFN2*fissionPower.P;
      fissPowGN = kG*fissionPower.P;
      decayPowFN1 = decayHeat.Q*vol_FN1;
      decayPowFN2 = decayHeat.Q*vol_FN2;
      powFN1 = flowPowFN1 + condPowFN1 - condPowFN2 - convPowFN1 + radPowFN1 + fissPowFN1 + decayPowFN1;
      powFN2 = flowPowFN2 + condPowFN2 - convPowFN2 + radPowFN2 + fissPowFN2 + decayPowFN2;
      powGN = convPowFN1 + convPowFN2 + fissPowGN;
      annotation(
        Diagram(coordinateSystem(extent = {{-100, 80}, {60, -80}}), graphics = {Rectangle(origin = {-19, 0}, lineThickness = 1, extent = {{-80, 80}, {80, -80}}), Rectangle(origin = {-40, 33}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 27}, {20, -27}}), Rectangle(origin = {5, 0}, lineColor = {136, 138, 133}, fillColor = {136, 138, 133}, fillPattern = FillPattern.Solid, extent = {{-15, 60}, {15, -60}}), Rectangle(origin = {-40, -33}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 27}, {20, -27}})}),
        Icon(coordinateSystem(extent = {{-100, 80}, {60, -80}}), graphics = {Rectangle(origin = {-40, 34}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 26}, {20, -26}}), Rectangle(origin = {5, 0}, lineColor = {136, 138, 133}, fillColor = {136, 138, 133}, fillPattern = FillPattern.Solid, extent = {{-15, 60}, {15, -60}}), Rectangle(origin = {-20, 0}, lineThickness = 1, extent = {{-79, 80}, {79, -80}}), Rectangle(origin = {-40, -34}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-20, 26}, {20, -26}})}));
    end FuelChannel;

    /* Three-group (Keepin) decay heat model.
       Each group i satisfies: d(DHG[i])/dt = DHYG[i]*n - DHlamG[i]*DHG[i],
       where DHYG[i] is the yield and DHlamG[i] is the decay constant.
       decayHeat_Out.DH = sum(DHG[i]) is the normalized total decay heat fraction. */
    model DecayHeat
      parameter Integer numGroups = 3;
      parameter SMD_MSR_Modelica.Units.DecayHeatYield DHYG[numGroups];
      parameter SMD_MSR_Modelica.Units.DecayConstant DHlamG[numGroups];
      SMD_MSR_Modelica.Units.HeatTransferFraction DHG[numGroups];
      input SMD_MSR_Modelica.PortsConnectors.NomNeutronPopIn nPop annotation(
        Placement(visible = true, transformation(origin = {1, 33}, extent = {{-19, -19}, {19, 19}}, rotation = 0), iconTransformation(origin = {9, 41}, extent = {{-13, -13}, {13, 13}}, rotation = 0)));
      output SMD_MSR_Modelica.PortsConnectors.DecayHeatOut decayHeat_Out annotation(
        Placement(visible = true, transformation(origin = {-4.44089e-16, -30}, extent = {{-20, -20}, {20, 20}}, rotation = 0), iconTransformation(origin = {10, -20}, extent = {{-14, -14}, {14, 14}}, rotation = 0)));
    initial equation
      for i in 1:numGroups loop
        der(DHG[i]) = 0;
      end for;
    equation
      for i in 1:numGroups loop
        der(DHG[i]) = nPop.n*DHYG[i] - DHlamG[i]*DHG[i];
      end for;
      decayHeat_Out.DH = sum(DHG);
      annotation(
        Diagram(graphics = {Rectangle(origin = {2, 0}, lineColor = {0, 225, 255}, lineThickness = 0.75, extent = {{-50, 50}, {50, -50}}), Text(origin = {3, 0}, extent = {{-45, 46}, {45, -46}}, textString = "Decay Heat"), Text(origin = {1, 17}, extent = {{9, -9}, {-9, 9}}, textString = "n", fontSize = 20), Text(origin = {0, -45}, extent = {{12, -11}, {-12, 11}}, textString = "DH", fontSize = 20)}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
        Icon(graphics = {Text(origin = {9, 9}, extent = {{-43, 25}, {43, -25}}, textString = "DH"), Rectangle(origin = {10, 10}, lineColor = {0, 225, 255}, lineThickness = 1, extent = {{-50, 50}, {50, -50}}), Text(origin = {1, 36}, extent = {{1, 2}, {-1, -2}}, textString = "")}, coordinateSystem(extent = {{-40, 60}, {60, -40}})));
    end DecayHeat;

    /* Linear reactivity feedback from fuel-salt and graphite temperatures.
       rho = a_F*(IF1*(TF1 - TF1sp) + IF2*(TF2 - TF2sp)) + a_G*IG*(TG - TGsp),
       where IF1, IF2, IG are neutron importance weights for each node and
       a_F, a_G are the fuel-salt and graphite temperature reactivity coefficients. */
    model ReactivityFeedback
      parameter SMD_MSR_Modelica.Units.TemperatureReactivityCoef a_F;
      parameter SMD_MSR_Modelica.Units.TemperatureReactivityCoef a_G;
      parameter Real IF1;
      parameter Real IF2;
      parameter Real IG;
      parameter SMD_MSR_Modelica.Units.Temperature FuelTempSetPointNode1;
      parameter SMD_MSR_Modelica.Units.Temperature FuelTempSetPointNode2;
      parameter SMD_MSR_Modelica.Units.Temperature GrapTempSetPoint;
      SMD_MSR_Modelica.Units.Reactivity FuelTempFeedbackNode1;
      SMD_MSR_Modelica.Units.Reactivity FuelTempFeedbackNode2;
      SMD_MSR_Modelica.Units.Reactivity GrapTempFeedback;
      SMD_MSR_Modelica.Units.Reactivity TotalTempFeedback;
      input SMD_MSR_Modelica.PortsConnectors.TempIn fuelNode1 annotation(
        Placement(visible = true, transformation(origin = {-40, 34}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {-40, 40}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
      input SMD_MSR_Modelica.PortsConnectors.TempIn fuelNode2 annotation(
        Placement(transformation(origin = {-40, 0}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {20, 40}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.TempIn grapNode annotation(
        Placement(transformation(origin = {-40, -30}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-10, 40}, extent = {{-10, -10}, {10, 10}})));
      output SMD_MSR_Modelica.PortsConnectors.ReactivityOut feedback annotation(
        Placement(visible = true, transformation(origin = {32, 18}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {-10, -20}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
    equation
      FuelTempFeedbackNode1 = (fuelNode1.T - FuelTempSetPointNode1)*IF1*a_F;
      FuelTempFeedbackNode2 = (fuelNode2.T - FuelTempSetPointNode2)*IF2*a_F;
      GrapTempFeedback = (grapNode.T - GrapTempSetPoint)*a_G*IG;
      TotalTempFeedback = FuelTempFeedbackNode1 + FuelTempFeedbackNode2 + GrapTempFeedback;
      feedback.rho = TotalTempFeedback;
      annotation(
        Diagram(graphics = {Rectangle(origin = {0, 2}, extent = {{-50, 50}, {50, -50}}), Text(origin = {13, 40}, extent = {{-31, 24}, {31, -24}}, textString = "Feedback"), Text(origin = {-32, 22}, extent = {{-16, 6}, {16, -6}}, textString = "Temp_FuelN1"), Text(origin = {-32, -12}, extent = {{-16, 6}, {16, -6}}, textString = "Temp_FuelN2"), Text(origin = {-32, -40}, extent = {{-16, 6}, {16, -6}}, textString = "Temp_GrapN1"), Text(origin = {24, 4}, extent = {{-16, 6}, {16, -6}}, textString = "Temp_Feedback")}, coordinateSystem(extent = {{-60, 60}, {60, -60}})),
        Icon(graphics = {Text(origin = {-40, 50}, extent = {{-16, 6}, {16, -6}}, textString = "F1"), Text(origin = {-8, 6}, extent = {{-32, 24}, {32, -24}}, textString = "TRFB"), Text(origin = {20, 50}, extent = {{-16, 6}, {16, -6}}, textString = "F2"), Rectangle(origin = {-10, 10}, lineColor = {11, 200, 36}, lineThickness = 1, extent = {{-50, 50}, {50, -50}}), Text(origin = {-10, 50}, extent = {{-16, 6}, {16, -6}}, textString = "G")}, coordinateSystem(extent = {{-60, 60}, {40, -40}})));
    end ReactivityFeedback;

    model Poisons
      parameter SMD_MSR_Modelica.Units.IsotopicDecayConstant Te135_lam = 1;
      parameter SMD_MSR_Modelica.Units.IsotopicDecayConstant I135_lam = 1;
      parameter SMD_MSR_Modelica.Units.IsotopicDecayConstant Xe135_lam = 1;
      parameter SMD_MSR_Modelica.Units.IsotopicFissYield Pm149_lam = 1;
      parameter SMD_MSR_Modelica.Units.IsotopicDecayConstant Sm149_lam = 1;
      parameter SMD_MSR_Modelica.Units.IsotopicFissYield Te135_gamma = 1;
      parameter SMD_MSR_Modelica.Units.IsotopicFissYield I135_gamma = 1;
      parameter SMD_MSR_Modelica.Units.IsotopicFissYield Xe135_gamma = 1;
      parameter SMD_MSR_Modelica.Units.IsotopicFissYield Pm149_gamma = 1;
      SMD_MSR_Modelica.Units.IsotopicConc Te135_Conc;
      SMD_MSR_Modelica.Units.IsotopicConc I135_Conc;
      SMD_MSR_Modelica.Units.IsotopicConc Xe135_Conc;
      SMD_MSR_Modleica.Units.IsotopicConc Pm149_Conc;
      SMD_MSR_Modelica.Units.IsotopicConc Sm149_Conc;
    initial equation

    equation
      der(Te135_Conc) = Te135_gamma*Sig_fission*phi0;
    end Poisons;

    /* Converts normalized neutron population and decay heat groups to physical powers.
       Fission power = n*(1 - 0.068)*P, where 0.068 is the total delayed/decay fraction.
       Decay power per unit volume = decayNP.DH*P / TotalFuelVol, broadcast to all nodes. */
    model PowerBlock
      parameter SMD_MSR_Modelica.Units.Power P;
      parameter SMD_MSR_Modelica.Units.Volume TotalFuelVol;
      SMD_MSR_Modelica.Units.NominalPower nomFissionPower;
      SMD_MSR_Modelica.Units.NominalPower nomReactorPower;
      SMD_MSR_Modelica.Units.Power decayPower;
      SMD_MSR_Modelica.Units.Power reactorPower;
      input SMD_MSR_Modelica.PortsConnectors.NomNeutronPopIn nPop annotation(
        Placement(transformation(origin = {-60, -40}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-20, 0}, extent = {{-10, -10}, {10, 10}})));
      input SMD_MSR_Modelica.PortsConnectors.DecayHeatIn decayNP annotation(
        Placement(transformation(origin = {40, -40}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {40, 0}, extent = {{-10, -10}, {10, 10}})));
      output SMD_MSR_Modelica.PortsConnectors.PowerOut fissionPower annotation(
        Placement(transformation(origin = {-60, 60}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {-20, -60}, extent = {{-10, -10}, {10, 10}})));
      output SMD_MSR_Modelica.PortsConnectors.VolumetircPowerOut decayPowerM annotation(
        Placement(transformation(origin = {40, 60}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {40, -60}, extent = {{-10, -10}, {10, 10}})));
    equation
      // 0.068 = total recoverable decay-heat fraction; remainder is prompt fission power.
      nomFissionPower = nPop.n*(1 - 0.068);
//nomFissionPower = nPop.n;
      fissionPower.P = nomFissionPower*P;
      decayPower = decayNP.DH*P;
//decayPower = 0;
      decayPowerM.Q = decayPower/TotalFuelVol;
      nomReactorPower = nomFissionPower + decayNP.DH;
      reactorPower = fissionPower.P + decayPower;
      annotation(
        Diagram(graphics = {Rectangle(origin = {-10, 10}, lineColor = {224, 27, 36}, extent = {{-70, 70}, {70, -70}}), Text(origin = {-11, 10}, extent = {{61, 46}, {-61, -46}}, textString = "Power Block")}, coordinateSystem(extent = {{-80, 80}, {60, -60}})),
        Icon(graphics = {Rectangle(origin = {10, -30}, lineColor = {224, 27, 36}, lineThickness = 1, extent = {{-50, 50}, {50, -50}}), Text(origin = {11, -31}, extent = {{33, 21}, {-33, -21}}, textString = "PB"), Text(origin = {-19, -72}, extent = {{-9, 6}, {9, -6}}, textString = "FP"), Text(origin = {41, -72}, extent = {{-9, 6}, {9, -6}}, textString = "DP")}, coordinateSystem(extent = {{-40, 20}, {60, -80}})));
    end PowerBlock;

    /* Sums numInput individual region reactivity feedbacks (Delta k/k) into a
       single total feedback reactivity for input to PKE or mPKE. */
    model SumReactivity
      parameter Integer numInput;
      input SMD_MSR_Modelica.PortsConnectors.ReactivityIn reactivityIn[numInput] annotation(
        Placement(visible = true, transformation(origin = {20, 20}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {8, 20}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
      output SMD_MSR_Modelica.PortsConnectors.ReactivityOut reactivityOut annotation(
        Placement(visible = true, transformation(origin = {-20, 20}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {10, -40}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
    equation
      reactivityOut.rho = sum(reactivityIn.rho);
      annotation(
        Diagram(graphics = {Rectangle(origin = {0, 20}, lineColor = {51, 209, 122}, extent = {{-40, 20}, {40, -20}}), Text(origin = {11, 33}, extent = {{-29, 7}, {29, -7}}, textString = "Sum React"), Text(origin = {21, 9}, extent = {{-9, 5}, {9, -5}}, textString = "In"), Text(origin = {-19, 9}, extent = {{-9, 7}, {9, -7}}, textString = "Out")}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
        Icon(graphics = {Text(origin = {10, -10}, extent = {{-40, 16}, {40, -16}}, textString = "SumR"), Rectangle(origin = {10, -10}, lineColor = {11, 200, 36}, lineThickness = 2, extent = {{-50, 50}, {50, -50}})}, coordinateSystem(extent = {{-40, 40}, {60, -60}})));
    end SumReactivity;
  end Nuclear;

  // All Temperature quantities are in degrees Celsius (degC).
  // Stefan-Boltzmann radiation in all components uses (T + 273.15) K.
  package Units
    type Area = Real(unit = "m2", min = 0);
    type Amount = Real(unit = "mol", min = 0);
    type AngularFrequency = Real(unit = "rad/s");
    type AtomicDensity = Real(unit = "1/m3", min = 0);
    type AvagadroNumber = Real(unit = "1/mol");
    type CrossSection = Real(unit = "m2", min = 0);
    type Current = Real(unit = "A");
    type Conductivity = Real(unit = "W/(m.C)", min = 0);
    type Convection = Real(unit = "J/(s.C)", min = 0);
    type Density = Real(unit = "kg/m3", min = 0);
    type DecayConstant = Real(unit = "1/s", min = 0);
    type DecayHeatPrecursorDecayConstant = Real(unit = "1/s", min = 0);
    type DecayHeatYield = Real(unit = "1/s", min = 0);
    type DecayHeatFraction = Real(unit = "1", min = 0);
    type Demand = Real(unit = "1", min = 0);
    type DelayedNeutronFrac = Real(unit = "1", min = 0);
    type Energy = Real(unit = "J");
    type Emissivity = Real(unit = "1", min = 0);
    type Enrichment = Real(unit = "1", min = 0);
    type EnergyPerFission = Real(unit = "J", min = 0);
    type Frequency = Real(unit = "1/s", min = 0);
    type InitMode = enumeration(
      FixedStart "Use explicit initial values",
      SteadyState "Solve zero-derivative initialization"
    );
    type FlowFraction = Real(unit = "1", min = 0);
    type HeatTransferFraction = Real(unit = "1", min = -1e-8);
    type HeatCapacity = Real(unit = "J/C");
    type IsotopicConc = Real(unit = "1/m^3", min = 0);
    type IsotopicDecayConstant = Real(unit = "1/s", min = 0);
    type IsotopicFissYield = Real(unit = "1", min = 0);
    type InitiationTime = Real(unit = "s", min = 0);
    type Length = Real(unit = "m", min = 0);
    type MassFlowRate = Real(unit = "kg/s", min = 0);
    type Mass = Real(unit = "kg", min = 0);
    type MolarMass = Real(unit = "kg/mol", min = 0);
    type MoleFraction = Real(unit = "1", min = 0);
    type MultipicationFactor = Real(unit = "1", min = 0);
    type NeutronDensity = Real(unit = "1/m3", min = 0);
    type NeutronPopulation = Real(unit = "1", min = 0);
    type NeutronEmissionRate = Real(unit = "1/s", min = 0);
    type NomalizedNeutronEmissionRate = Real(unit = "1/s", min = 0);
    type NeutronGenerationTime = Real(unit = "s", min = 0);
    type NeutronFlux = Real(unit = "n/(cm2.s)");
    type NominalPower = Real(unit = "1", min = -1e-8);
    type NominalNeutronPopulation = Real(unit = "1", min = 0);
    type PrecursorConc = Real(unit = "1", min = 0);
    type PrecursorDecayRate = Real(unit = "1/s", min = 0);
    type PrecursorReturnRate = Real(unit = "1/s", min = 0);
    type PumpConstant = Real(unit = "1/s", min = 0);
    type Power = Real(unit = "W");
    type Reactivity = Real(unit = "1");
    type ResidentTime = Real(unit = "s", min = 0, max = 1E6);
    type Speed = Real(unit = "m/s", min = 0);
    type StefanBoltzmannConstant = Real(unit = "W/(m2.K4)");
    type SpecificHeatCapacity = Real(unit = "J/(kg.C)", min = 0);
    type TemperatureReactivityCoef = Real(unit = "1/C");
    type Temperature = Real(unit = "C");
    type TimeConstant = Real(unit = "1/s", min = 0);
    type Volume = Real(unit = "m3", min = 0);
    type VolumeticFlowRate = Real(unit = "m3/s", min = 0);
    type Velocity = Real(unit = "m/s");
    type VolumeImportance = Real(unit = "1", min = 0);
    type VolumetircPower = Real(unit = "W/m3");
  end Units;

  package PortsConnectors
    connector TempIn
      SMD_MSR_Modelica.Units.Temperature T;
      annotation(
        Diagram(graphics = {Ellipse(origin = {1, -1}, lineColor = {204, 0, 0}, fillColor = {204, 0, 0}, fillPattern = FillPattern.Solid, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
        Icon(graphics = {Ellipse(origin = {1, -1}, lineColor = {204, 0, 0}, fillColor = {204, 0, 0}, fillPattern = FillPattern.Solid, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})));
    end TempIn;

    connector TempOut
      SMD_MSR_Modelica.Units.Temperature T;
      annotation(
        Icon(graphics = {Ellipse(origin = {1, -1}, lineColor = {204, 0, 0}, lineThickness = 0.5, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
        Diagram(graphics = {Ellipse(origin = {1, -1}, lineColor = {204, 0, 0}, lineThickness = 0.5, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})));
    end TempOut;

    connector DecayHeatIn
      SMD_MSR_Modelica.Units.NominalPower DH;
      annotation(
        Diagram(graphics = {Ellipse(origin = {1, -1}, lineColor = {0, 225, 255}, fillColor = {0, 225, 255}, fillPattern = FillPattern.Solid, lineThickness = 0.5, extent = {{-51, 47}, {51, -47}}, endAngle = 360)}),
        Icon(graphics = {Ellipse(origin = {1, -1}, lineColor = {0, 225, 255}, fillColor = {0, 225, 255}, fillPattern = FillPattern.Solid, lineThickness = 0.5, extent = {{-51, 47}, {51, -47}}, endAngle = 360)}));
    end DecayHeatIn;

    connector DecayHeatOut
      SMD_MSR_Modelica.Units.NominalPower DH;
      annotation(
        Icon(graphics = {Ellipse(origin = {1, -1}, lineColor = {0, 225, 255}, fillColor = {0, 225, 255}, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
        Diagram(graphics = {Ellipse(origin = {1, -1}, lineColor = {0, 225, 255}, fillColor = {0, 225, 255}, lineThickness = 0.5, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})));
    end DecayHeatOut;

    connector NomNeutronPopIn
      SMD_MSR_Modelica.Units.NominalNeutronPopulation n;
      annotation(
        Diagram(graphics = {Ellipse(origin = {1, -1}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
        Icon(graphics = {Ellipse(origin = {1, -1}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, lineThickness = 0.5, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})));
    end NomNeutronPopIn;

    connector ReactivityOut
      SMD_MSR_Modelica.Units.Reactivity rho;
      annotation(
        Diagram(graphics = {Ellipse(origin = {1, -1}, lineColor = {78, 154, 6}, fillColor = {78, 154, 6}, lineThickness = 0.5, extent = {{-51, 47}, {51, -47}}, endAngle = 360)}),
        Icon(graphics = {Ellipse(origin = {1, -1}, lineColor = {78, 154, 6}, fillColor = {78, 154, 6}, lineThickness = 0.5, extent = {{-51, 47}, {51, -47}}, endAngle = 360)}));
    end ReactivityOut;

    connector ReactivityIn
      SMD_MSR_Modelica.Units.Reactivity rho;
      annotation(
        Diagram(graphics = {Ellipse(origin = {1, -1}, lineColor = {78, 154, 6}, fillColor = {78, 154, 6}, fillPattern = FillPattern.Solid, lineThickness = 0.5, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
        Icon(graphics = {Ellipse(origin = {1, -1}, lineColor = {78, 154, 6}, fillColor = {78, 154, 6}, fillPattern = FillPattern.Solid, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})));
    end ReactivityIn;

    connector FlowFractionIn
      SMD_MSR_Modelica.Units.FlowFraction FF;
      annotation(
        Diagram(graphics = {Ellipse(origin = {1, -1}, lineColor = {255, 120, 0}, fillColor = {245, 121, 0}, fillPattern = FillPattern.Solid, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
        Icon(graphics = {Ellipse(origin = {1, -1}, lineColor = {255, 120, 0}, fillColor = {245, 121, 0}, fillPattern = FillPattern.Solid, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})));
    end FlowFractionIn;

    connector FlowFractionOut
      SMD_MSR_Modelica.Units.FlowFraction FF;
      annotation(
        Diagram(graphics = {Ellipse(origin = {1, -1}, lineColor = {245, 121, 0}, fillColor = {255, 255, 255}, fillPattern = FillPattern.Solid, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}),
        Icon(graphics = {Ellipse(origin = {1, -1}, lineColor = {245, 121, 0}, fillColor = {255, 255, 255}, fillPattern = FillPattern.Solid, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}));
    end FlowFractionOut;

    connector NeutronEmsRateIn
      SMD_MSR_Modelica.Units.NeutronEmissionRate nDot;
      annotation(
        Diagram(graphics = {Ellipse(origin = {1, -1}, lineColor = {191, 64, 188}, fillColor = {191, 64, 188}, fillPattern = FillPattern.Solid, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}),
        Icon(graphics = {Ellipse(origin = {1, -1}, lineColor = {191, 64, 188}, fillColor = {191, 64, 188}, fillPattern = FillPattern.Solid, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}));
    end NeutronEmsRateIn;

    connector NomNeutronPopOut
      SMD_MSR_Modelica.Units.NominalNeutronPopulation n;
      annotation(
        Diagram(graphics = {Ellipse(origin = {1, -1}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
        Icon(coordinateSystem(extent = {{-100, -100}, {100, 100}}), graphics = {Ellipse(origin = {1, -1}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}));
    end NomNeutronPopOut;

    connector NeutronEmsRateOut
      SMD_MSR_Modelica.Units.NeutronEmissionRate nDot;
      annotation(
        Diagram(graphics = {Ellipse(origin = {1, -1}, lineColor = {191, 64, 188}, fillColor = {191, 64, 188}, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
        Icon(graphics = {Ellipse(origin = {1, -1}, lineColor = {191, 64, 188}, fillColor = {191, 64, 188}, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})));
    end NeutronEmsRateOut;

    connector RealIn
      Real R;
      annotation(
        Diagram(graphics = {Ellipse(origin = {1, 5}, fillPattern = FillPattern.Solid, lineThickness = 1, extent = {{-51, 47}, {51, -47}}), Text(extent = {{2, -10}, {2, -10}})}, coordinateSystem(extent = {{-60, 60}, {60, -40}})),
        Icon(graphics = {Ellipse(origin = {1, 3}, fillPattern = FillPattern.Solid, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})));
    end RealIn;

    connector RealOut
      Real R;
      annotation(
        Icon(graphics = {Ellipse(origin = {1, -1}, lineThickness = 0.5, extent = {{-51, 47}, {51, -47}}, endAngle = 360)}),
        Diagram(graphics = {Ellipse(origin = {1, -1}, lineThickness = 0.5, extent = {{-51, 47}, {51, -47}}, endAngle = 360)}));
    end RealOut;

    connector PowerIn
      SMD_MSR_Modelica.Units.Power P;
      annotation(
        Diagram(graphics = {Ellipse(origin = {1, -1}, lineColor = {129, 61, 156}, fillColor = {129, 61, 156}, fillPattern = FillPattern.Solid, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
        Icon(graphics = {Ellipse(origin = {-1, -1}, lineColor = {129, 61, 156}, fillColor = {129, 61, 156}, fillPattern = FillPattern.Solid, lineThickness = 0.5, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})));
    end PowerIn;

    connector PowerOut
      SMD_MSR_Modelica.Units.Power P;
      annotation(
        Diagram(graphics = {Ellipse(origin = {1, -1}, lineColor = {145, 65, 172}, fillColor = {145, 65, 172}, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
        Icon(graphics = {Ellipse(origin = {-1, -1}, lineColor = {129, 61, 156}, fillColor = {129, 61, 156}, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})));
    end PowerOut;

    connector VolumetircPowerIn
      SMD_MSR_Modelica.Units.VolumetircPower Q;
      annotation(
        Diagram(graphics = {Ellipse(origin = {1, -1}, lineColor = {220, 138, 221}, fillColor = {220, 138, 221}, fillPattern = FillPattern.Solid, lineThickness = 0.5, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
        Icon(graphics = {Ellipse(origin = {1, -1}, lineColor = {220, 138, 221}, fillColor = {220, 138, 221}, fillPattern = FillPattern.Solid, lineThickness = 0.5, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})));
    end VolumetircPowerIn;

    connector VolumetircPowerOut
      SMD_MSR_Modelica.Units.VolumetircPower Q;
      annotation(
        Icon(graphics = {Ellipse(origin = {1, -1}, lineColor = {220, 138, 221}, fillColor = {0, 225, 255}, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
        Diagram(graphics = {Ellipse(origin = {1, -1}, lineColor = {220, 138, 221}, fillColor = {0, 225, 255}, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-60, 60}, {60, -60}})));
    end VolumetircPowerOut;

    connector HeatPortIn
      SMD_MSR_Modelica.Units.Temperature T;
      flow SMD_MSR_Modelica.Units.Power Q;
      annotation(
        Diagram(graphics = {Ellipse(origin = {1, -1}, lineColor = {204, 0, 0}, fillColor = {204, 0, 0}, fillPattern = FillPattern.Solid, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
        Icon(graphics = {Ellipse(origin = {1, -1}, lineColor = {204, 0, 0}, fillColor = {204, 0, 0}, fillPattern = FillPattern.Solid, lineThickness = 1, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})));
    end HeatPortIn;

    connector HeatPortOut
      SMD_MSR_Modelica.Units.Temperature T;
      flow SMD_MSR_Modelica.Units.Power Q;
      annotation(
        Icon(graphics = {Ellipse(origin = {1, -1}, lineColor = {204, 0, 0}, lineThickness = 0.5, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
        Diagram(graphics = {Ellipse(origin = {1, -1}, lineColor = {204, 0, 0}, lineThickness = 0.5, extent = {{-51, 47}, {51, -47}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})));
    end HeatPortOut;
  end PortsConnectors;

  package QAtoolBox
    package TestPKE
      model nOneTest
        SMD_MSR_Modelica.Nuclear.PKE pke(LAMBDA = 2.400E-04, beta = {0.000223, 0.001457, 0.001307, 0.002628, 0.000766, 0.00023}, lambda = {1.240E-02, 3.05E-02, 1.11E-01, 3.01E-01, 1.140E+00, 3.014E+00}, n_0 = 1, numGroups = 6) annotation(
          Placement(transformation(origin = {-2, 1.42109e-14}, extent = {{-56, -56}, {56, 56}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantReactivity constantReactivity(Reactivity = 0) annotation(
          Placement(transformation(origin = {160, 100}, extent = {{-42, -42}, {42, 42}}, rotation = 180)));
        SMD_MSR_Modelica.Signals.Constants.ConstantNeutronSource constantNeutronSource(S = 0) annotation(
          Placement(transformation(origin = {-1, 105}, extent = {{-33, -33}, {33, 33}}, rotation = 180)));
        SMD_MSR_Modelica.Signals.Constants.ConstantReal constantReal(magnitude = 0) annotation(
          Placement(transformation(origin = {-129, 113}, extent = {{-39, -39}, {39, 39}}, rotation = 180)));
      equation
        connect(constantNeutronSource.neutronEmsRateOut, pke.S) annotation(
          Line(points = {{0, 94}, {0, 34}}, color = {191, 64, 188}));
        connect(constantReactivity.reactivity, pke.feedback) annotation(
          Line(points = {{160, 85}, {32, 85}, {32, 34}}, color = {78, 154, 6}));
        connect(constantReal.realOut, pke.ReactivityIn) annotation(
          Line(points = {{-130, 106}, {-36, 106}, {-36, 34}}));
        annotation(
          Diagram(coordinateSystem(extent = {{-100, 140}, {200, -100}})));
      end nOneTest;

      model nHalfTest
        SMD_MSR_Modelica.Nuclear.PKE pke(LAMBDA = 2.400E-04, beta = {0.000223, 0.001457, 0.001307, 0.002628, 0.000766, 0.00023}, lambda = {1.240E-02, 3.05E-02, 1.11E-01, 3.01E-01, 1.140E+00, 3.014E+00}, n_0 = 0.5, numGroups = 6) annotation(
          Placement(transformation(origin = {-2, 1.42109e-14}, extent = {{-56, -56}, {56, 56}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantReactivity constantReactivity(Reactivity = 0) annotation(
          Placement(transformation(origin = {160, 100}, extent = {{-42, -42}, {42, 42}}, rotation = 180)));
        SMD_MSR_Modelica.Signals.Constants.ConstantNeutronSource constantNeutronSource(S = 0) annotation(
          Placement(transformation(origin = {-1, 105}, extent = {{-33, -33}, {33, 33}}, rotation = 180)));
        SMD_MSR_Modelica.Signals.Constants.ConstantReal constantReal(magnitude = 0) annotation(
          Placement(transformation(origin = {-129, 113}, extent = {{-39, -39}, {39, 39}}, rotation = 180)));
      equation
        connect(constantNeutronSource.neutronEmsRateOut, pke.S) annotation(
          Line(points = {{0, 94}, {0, 34}}, color = {191, 64, 188}));
        connect(constantReactivity.reactivity, pke.feedback) annotation(
          Line(points = {{160, 85}, {32, 85}, {32, 34}}, color = {78, 154, 6}));
        connect(constantReal.realOut, pke.ReactivityIn) annotation(
          Line(points = {{-130, 106}, {-36, 106}, {-36, 34}}));
        annotation(
          Diagram(coordinateSystem(extent = {{-100, 140}, {200, -100}})));
      end nHalfTest;

      model nZeroTest
        SMD_MSR_Modelica.Nuclear.PKE pke(LAMBDA = 2.400E-04, beta = {0.000223, 0.001457, 0.001307, 0.002628, 0.000766, 0.00023}, lambda = {1.240E-02, 3.05E-02, 1.11E-01, 3.01E-01, 1.140E+00, 3.014E+00}, n_0 = 0, numGroups = 6) annotation(
          Placement(transformation(origin = {-2, 1.42109e-14}, extent = {{-56, -56}, {56, 56}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantReactivity constantReactivity(Reactivity = 0) annotation(
          Placement(transformation(origin = {160, 100}, extent = {{-42, -42}, {42, 42}}, rotation = 180)));
        SMD_MSR_Modelica.Signals.Constants.ConstantNeutronSource constantNeutronSource(S = 0) annotation(
          Placement(transformation(origin = {-1, 105}, extent = {{-33, -33}, {33, 33}}, rotation = 180)));
        SMD_MSR_Modelica.Signals.Constants.ConstantReal constantReal(magnitude = 0) annotation(
          Placement(transformation(origin = {-129, 113}, extent = {{-39, -39}, {39, 39}}, rotation = 180)));
      equation
        connect(constantNeutronSource.neutronEmsRateOut, pke.S) annotation(
          Line(points = {{0, 94}, {0, 34}}, color = {191, 64, 188}));
        connect(constantReactivity.reactivity, pke.feedback) annotation(
          Line(points = {{160, 85}, {32, 85}, {32, 34}}, color = {78, 154, 6}));
        connect(constantReal.realOut, pke.ReactivityIn) annotation(
          Line(points = {{-130, 106}, {-36, 106}, {-36, 34}}));
        annotation(
          Diagram(coordinateSystem(extent = {{-100, 140}, {200, -100}})));
      end nZeroTest;
    end TestPKE;

    package Test_mPKE
      model nOneTest
        Nuclear.mPKE mpke(numGroups = 6, lambda = {1.240E-02, 3.05E-02, 1.11E-01, 3.01E-01, 1.140E+00, 3.014E+00}, beta = {0.000223, 0.001457, 0.001307, 0.002628, 0.000766, 0.00023}, LAMBDA = 2.400E-04, n_0 = 1, nomTauLoop = 16.73, nomTauCore = 8.46) annotation(
          Placement(transformation(origin = {3, -57}, extent = {{-39, -39}, {39, 39}})));
        Signals.Constants.ConstantNeutronSource constantNeutronSource(S = 0) annotation(
          Placement(transformation(origin = {-31, -7}, extent = {{-19, -19}, {19, 19}}, rotation = 180)));
        Signals.Constants.ConstantFlowFrac constantFlowFrac(ConstantFlowFrac = 1) annotation(
          Placement(transformation(origin = {93, -7}, extent = {{-21, -21}, {21, 21}}, rotation = 180)));
        Signals.Constants.ConstantReal constantReal(magnitude = 0) annotation(
          Placement(transformation(origin = {-130, -6}, extent = {{-28, -28}, {28, 28}}, rotation = 180)));
        Signals.Constants.ConstantReactivity constantReactivity(Reactivity = 0) annotation(
          Placement(transformation(origin = {26, -8}, extent = {{-20, -20}, {20, 20}}, rotation = 180)));
      equation
        connect(constantReal.realOut, mpke.ReactivityIn) annotation(
          Line(points = {{-130, -12}, {-130, -50}, {-12, -50}}));
        connect(constantNeutronSource.neutronEmsRateOut, mpke.S) annotation(
          Line(points = {{-30, -14}, {-30, -28}, {-4, -28}, {-4, -50}}, color = {191, 64, 188}));
        connect(constantReactivity.reactivity, mpke.feedback) annotation(
          Line(points = {{26, -16}, {26, -28}, {4, -28}, {4, -50}}, color = {78, 154, 6}));
        connect(constantFlowFrac.flowFraction_Out, mpke.fuelFlowFrac) annotation(
          Line(points = {{94, -14}, {92, -14}, {92, -50}, {10, -50}}, color = {245, 121, 0}));
        annotation(
          Diagram(coordinateSystem(extent = {{-160, 20}, {120, -100}})));
      end nOneTest;

      model nHalfTest
        Nuclear.mPKE mpke(numGroups = 6, lambda = {1.240E-02, 3.05E-02, 1.11E-01, 3.01E-01, 1.140E+00, 3.014E+00}, beta = {0.000223, 0.001457, 0.001307, 0.002628, 0.000766, 0.00023}, LAMBDA = 2.400E-04, n_0 = 0.5, nomTauLoop = 16.73, nomTauCore = 8.46) annotation(
          Placement(transformation(origin = {3, -57}, extent = {{-39, -39}, {39, 39}})));
        Signals.Constants.ConstantNeutronSource constantNeutronSource(S = 0) annotation(
          Placement(transformation(origin = {-31, -7}, extent = {{-19, -19}, {19, 19}}, rotation = 180)));
        Signals.Constants.ConstantFlowFrac constantFlowFrac(ConstantFlowFrac = 1) annotation(
          Placement(transformation(origin = {93, -7}, extent = {{-21, -21}, {21, 21}}, rotation = 180)));
        Signals.Constants.ConstantReal constantReal(magnitude = 0) annotation(
          Placement(transformation(origin = {-130, -6}, extent = {{-28, -28}, {28, 28}}, rotation = 180)));
        Signals.Constants.ConstantReactivity constantReactivity(Reactivity = 0) annotation(
          Placement(transformation(origin = {26, -8}, extent = {{-20, -20}, {20, 20}}, rotation = 180)));
      equation
        connect(constantReal.realOut, mpke.ReactivityIn) annotation(
          Line(points = {{-130, -12}, {-130, -50}, {-12, -50}}));
        connect(constantNeutronSource.neutronEmsRateOut, mpke.S) annotation(
          Line(points = {{-30, -14}, {-30, -28}, {-4, -28}, {-4, -50}}, color = {191, 64, 188}));
        connect(constantReactivity.reactivity, mpke.feedback) annotation(
          Line(points = {{26, -16}, {26, -28}, {4, -28}, {4, -50}}, color = {78, 154, 6}));
        connect(constantFlowFrac.flowFraction_Out, mpke.fuelFlowFrac) annotation(
          Line(points = {{94, -14}, {92, -14}, {92, -50}, {10, -50}}, color = {245, 121, 0}));
        annotation(
          Diagram(coordinateSystem(extent = {{-160, 20}, {120, -100}})));
      end nHalfTest;

      model nZeroTest
        Nuclear.mPKE mpke(numGroups = 6, lambda = {1.240E-02, 3.05E-02, 1.11E-01, 3.01E-01, 1.140E+00, 3.014E+00}, beta = {0.000223, 0.001457, 0.001307, 0.002628, 0.000766, 0.00023}, LAMBDA = 2.400E-04, n_0 = 0, nomTauLoop = 16.73, nomTauCore = 8.46) annotation(
          Placement(transformation(origin = {3, -57}, extent = {{-39, -39}, {39, 39}})));
        Signals.Constants.ConstantNeutronSource constantNeutronSource(S = 0) annotation(
          Placement(transformation(origin = {-31, -7}, extent = {{-19, -19}, {19, 19}}, rotation = 180)));
        Signals.Constants.ConstantFlowFrac constantFlowFrac(ConstantFlowFrac = 0) annotation(
          Placement(transformation(origin = {93, -7}, extent = {{-21, -21}, {21, 21}}, rotation = 180)));
        Signals.Constants.ConstantReal constantReal(magnitude = 0) annotation(
          Placement(transformation(origin = {-130, -6}, extent = {{-28, -28}, {28, 28}}, rotation = 180)));
        Signals.Constants.ConstantReactivity constantReactivity(Reactivity = 0) annotation(
          Placement(transformation(origin = {26, -8}, extent = {{-20, -20}, {20, 20}}, rotation = 180)));
      equation
        connect(constantReal.realOut, mpke.ReactivityIn) annotation(
          Line(points = {{-130, -12}, {-130, -50}, {-12, -50}}));
        connect(constantNeutronSource.neutronEmsRateOut, mpke.S) annotation(
          Line(points = {{-30, -14}, {-30, -28}, {-4, -28}, {-4, -50}}, color = {191, 64, 188}));
        connect(constantReactivity.reactivity, mpke.feedback) annotation(
          Line(points = {{26, -16}, {26, -28}, {4, -28}, {4, -50}}, color = {78, 154, 6}));
        connect(constantFlowFrac.flowFraction_Out, mpke.fuelFlowFrac) annotation(
          Line(points = {{94, -14}, {92, -14}, {92, -50}, {10, -50}}, color = {245, 121, 0}));
        annotation(
          Diagram(coordinateSystem(extent = {{-160, 20}, {120, -100}})));
      end nZeroTest;
    end Test_mPKE;

    package TestFuelChannel
      model powerTest
        Nuclear.FuelChannel fuelChannel(rho_fuel = 2146.5, rho_grap = 1860, cP_fuel = 1966.5, cP_grap = 1773, Vdot_fuelNom = 7.5708E-02, kFN1 = 0.4650, kFN2 = 0.4650, kG = 0.07, kHT_FN1 = 0.5, kHT_FN2 = 0.5, TF1_0 = 644.5, TF2_0 = 657, TG_0 = 675.8561, regionFlowFrac = 1, KF = 0, Ac = 0, LF1 = 1, LF2 = 1, OuterRegion = false, ArF1 = 0, ArF2 = 0, e = 0, Tinf = 100, vol_FN1 = 0.3202, vol_FN2 = 0.3202, vol_GN = 1.9539, hAnom = 36000) annotation(
          Placement(transformation(origin = {-33.5833, -27.4444}, extent = {{-49.3056, -49.3056}, {29.5833, 39.4444}})));
        Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
          Placement(transformation(origin = {-207, -25}, extent = {{-23, -23}, {23, 23}})));
        Signals.Constants.ConstantPower constantPower(P(displayUnit = "MW") = 8000000) annotation(
          Placement(transformation(origin = {-206, 22}, extent = {{-24, -24}, {24, 24}})));
        Signals.Constants.ConstantFlowFrac constantFlowFrac(ConstantFlowFrac = 1) annotation(
          Placement(transformation(origin = {-206, -80}, extent = {{-20, -20}, {20, 20}})));
        Signals.Constants.ConstantHeatRemoval constantHeatRemoval(rho = 2146.5, cP = 1966.5, vol = 0.3202*2, V_dotNom = 7.5708E-02, powRm(displayUnit = "MW") = 8000000, inletTemp = 658) annotation(
          Placement(transformation(origin = {73, -7}, extent = {{33, -33}, {-33, 33}}, rotation = -180)));
      equation
        connect(constantVolumetricPower.volPow, fuelChannel.decayHeat) annotation(
          Line(points = {{-208, -16}, {-70, -16}, {-70, -22}}, color = {220, 138, 221}));
        connect(constantPower.pow, fuelChannel.fissionPower) annotation(
          Line(points = {{-206, 30}, {-70, 30}, {-70, 0}}, color = {129, 61, 156}));
        connect(constantFlowFrac.flowFraction_Out, fuelChannel.fuelFlowFraction) annotation(
          Line(points = {{-206, -72}, {-206, -42}, {-68, -42}}, color = {245, 121, 0}));
        connect(fuelChannel.fuelNode2, constantHeatRemoval.temp_In) annotation(
          Line(points = {{-14, -6}, {60, -6}, {60, -7}}, color = {204, 0, 0}));
        connect(constantHeatRemoval.temp_Out, fuelChannel.temp_In) annotation(
          Line(points = {{86, -7}, {108, -7}, {108, -98}, {-70, -98}, {-70, -62}}, color = {204, 0, 0}));
        annotation(
          Diagram(coordinateSystem(extent = {{-240, 60}, {120, -100}})));
      end powerTest;

      model fullTest
        Nuclear.FuelChannel fuelChannel(rho_fuel = 2146.5, rho_grap = 1860, cP_fuel = 1966.5, cP_grap = 1773, Vdot_fuelNom = 7.5708E-02, kFN1 = 0.4650, kFN2 = 0.4650, kG = 0.07, kHT_FN1 = 0.5, kHT_FN2 = 0.5, TF1_0 = 644.5, TF2_0 = 657, TG_0 = 675.8561, regionFlowFrac = 1, KF = 4.76, Ac = 1.5882, LF1 = 0.7875, LF2 = 0.7875, OuterRegion = true, ArF1 = 3.5172, ArF2 = 3.5172, e = 0.080000, Tinf = 500, vol_FN1 = 0.3202, vol_FN2 = 0.3202, vol_GN = 1.9539, hAnom = 36000) annotation(
          Placement(transformation(origin = {4.08333, 3.44444}, extent = {{-43.1944, -43.1944}, {25.9167, 34.5556}})));
        Nuclear.mPKE m_PKE(numGroups = 6, lambda = {1.240E-02, 3.05E-02, 1.11E-01, 3.01E-01, 1.140E+00, 3.014E+00}, beta = {0.000223, 0.001457, 0.001307, 0.002628, 0.000766, 0.00023}, LAMBDA = 2.400E-04, n_0 = 1, nomTauLoop = 16.73, nomTauCore = 8.46) annotation(
          Placement(transformation(origin = {-210, 60}, extent = {{-38, -38}, {38, 38}})));
        Nuclear.DecayHeat decayHeat(numGroups = 3, DHYG = {2.3751e-03, 8.7763e-05, 1.9596e-06}, DHlamG = {0.09453, 0.004420, 8.6098E-5}) annotation(
          Placement(transformation(origin = {-196, -66}, extent = {{-34, -34}, {34, 34}})));
        Nuclear.PowerBlock powerBlock(P(displayUnit = "MW") = 8000000, TotalFuelVol = 0.3202*2) annotation(
          Placement(transformation(origin = {-107, -7}, extent = {{-29, -29}, {29, 29}}, rotation = 90)));
        Signals.Constants.ConstantFlowFrac constantFlowFrac(ConstantFlowFrac = 1) annotation(
          Placement(transformation(origin = {-140, 118}, extent = {{-22, 22}, {22, -22}}, rotation = -0)));
        Nuclear.ReactivityFeedback reactivityFeedback(FuelTempSetPointNode1 = 644.5, FuelTempSetPointNode2 = 657.2700, GrapTempSetPoint = 675.8561, IF1 = 0.5, IF2 = 0.5, IG = 1, a_F = -8.71E-05, a_G = -6.66E-05) annotation(
          Placement(transformation(origin = {89, 1}, extent = {{-29, -29}, {29, 29}}, rotation = 90)));
        Signals.Constants.ConstantNeutronSource constantNeutronSource(S = 0) annotation(
          Placement(transformation(origin = {-196, 118}, extent = {{-10, 10}, {10, -10}}, rotation = -0)));
        Signals.Constants.ConstantReal constantReal(magnitude = 0) annotation(
          Placement(transformation(origin = {-258, 104}, extent = {{-10, -10}, {10, 10}})));
        Signals.Constants.ConstantHeatRemoval constantHeatRemoval(rho = 2146.5, cP = 1966.5, vol = 0.3202*2, V_dotNom = 7.5708E-02, powRm(displayUnit = "MW") = 8000000) annotation(
          Placement(transformation(origin = {-15, -77}, extent = {{-23, -23}, {23, 23}}, rotation = 180)));
      equation
        connect(m_PKE.n_population, decayHeat.nPop) annotation(
          Line(points = {{-210, 37}, {-210, -45}, {-197, -45}}, color = {20, 36, 248}, thickness = 1));
        connect(m_PKE.n_population, powerBlock.nPop) annotation(
          Line(points = {{-210, 37}, {-210, -24}, {-124, -24}}, color = {20, 36, 248}, thickness = 1));
        connect(decayHeat.decayHeat_Out, powerBlock.decayNP) annotation(
          Line(points = {{-196, -86}, {-134, -86}, {-134, 10}, {-124, 10}}, color = {0, 225, 255}, thickness = 1));
        connect(powerBlock.decayPowerM, fuelChannel.decayHeat) annotation(
          Line(points = {{-90, 10}, {-64, 10}, {-64, 9}, {-27, 9}}, color = {220, 138, 221}, thickness = 1));
        connect(powerBlock.fissionPower, fuelChannel.fissionPower) annotation(
          Line(points = {{-90, -24}, {-58, -24}, {-58, 28}, {-27, 28}}, color = {129, 61, 156}, thickness = 1));
        connect(constantFlowFrac.flowFraction_Out, fuelChannel.fuelFlowFraction) annotation(
          Line(points = {{-140, 110}, {-66, 110}, {-66, -10}, {-26, -10}}, color = {245, 121, 0}, thickness = 1));
        connect(constantFlowFrac.flowFraction_Out, m_PKE.fuelFlowFrac) annotation(
          Line(points = {{-140, 110}, {-140, 83}, {-187, 83}}, color = {245, 121, 0}, thickness = 1));
        connect(fuelChannel.fuelNode2, reactivityFeedback.fuelNode2) annotation(
          Line(points = {{21, 22}, {38, 22}, {38, 1}, {72, 1}}, color = {204, 0, 0}, thickness = 1));
        connect(fuelChannel.fuelNode1, reactivityFeedback.fuelNode1) annotation(
          Line(points = {{21, -22}, {50, -22}, {50, -16}, {72, -16}}, color = {204, 0, 0}, thickness = 1));
        connect(fuelChannel.grapNode, reactivityFeedback.grapNode) annotation(
          Line(points = {{21, -1}, {42, -1}, {42, 18}, {72, 18}}, color = {204, 0, 0}, thickness = 1));
        connect(reactivityFeedback.feedback, m_PKE.feedback) annotation(
          Line(points = {{106, 1}, {100, 1}, {100, 83}, {-202, 83}}, color = {78, 154, 6}, thickness = 1));
        connect(constantNeutronSource.neutronEmsRateOut, m_PKE.S) annotation(
          Line(points = {{-196, 114}, {-196, 99.5}, {-218, 99.5}, {-218, 83}}, color = {191, 64, 188}, thickness = 1));
        connect(constantReal.realOut, m_PKE.ReactivityIn) annotation(
          Line(points = {{-258, 106}, {-258, 83}, {-233, 83}}, thickness = 1));
        connect(constantHeatRemoval.temp_Out, fuelChannel.temp_In) annotation(
          Line(points = {{-24, -76}, {-62, -76}, {-62, -28}, {-27, -28}}, color = {204, 0, 0}, thickness = 1));
        connect(fuelChannel.fuelNode2, constantHeatRemoval.temp_In) annotation(
          Line(points = {{21, 22}, {38, 22}, {38, -76}, {-6, -76}}, color = {204, 0, 0}, thickness = 1));
        annotation(
          Diagram(coordinateSystem(extent = {{-260, 140}, {120, -100}})));
      end fullTest;
    end TestFuelChannel;

    package TestHX
      model unitTest
        SMD_MSR_Modelica.HeatTransport.HeatExchanger heatExchanger(vol_P = 0.1623, vol_T = 0.023059, vol_S = 0.051863, rhoP = 2146.5, rhoT = 8774.5, rhoS = 1922, cP_P = 1966.5, cP_T = 577.80, cP_S = 2390, VdotPnom = 0.075708, VdotSnom = 0.053627, hApNom = 324000, hAsNom = 153000, EnableRad = false, AcShell = 1, AcTube = 1, ArShell = 1, Kp = 4.76, Ks = 4.76, L_shell = 1.575, L_tube = 1.575, e = 0.01, Tinf = 500, TpIn_0 = 657, TpOut_0 = 632, TsIn_0 = 546.4, TsOut_0 = 579.3) annotation(
          Placement(transformation(origin = {-49.8, 14.6}, extent = {{-41.2, -20.6}, {61.8, 20.6}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
          Placement(transformation(origin = {-32, 50}, extent = {{-10, 10}, {10, -10}}, rotation = -0)));
        SMD_MSR_Modelica.Signals.Constants.ConstantFlowFrac constantFlowFrac(ConstantFlowFrac = 1) annotation(
          Placement(transformation(origin = {-120, 28}, extent = {{-10, -10}, {10, 10}})));
      equation
        connect(heatExchanger.T_in_pFluid, heatExchanger.T_out_pFluid) annotation(
          Line(points = {{-84, 24}, {6, 24}}, color = {204, 0, 0}));
        connect(heatExchanger.T_in_sFluid, heatExchanger.T_out_sFluid) annotation(
          Line(points = {{6, 4}, {-84, 4}}, color = {204, 0, 0}));
        connect(constantVolumetricPower.volPow, heatExchanger.P_decay) annotation(
          Line(points = {{-32, 46}, {-40, 46}, {-40, 32}}, color = {220, 138, 221}));
        connect(constantFlowFrac.flowFraction_Out, heatExchanger.primaryFF) annotation(
          Line(points = {{-120, 32}, {-74, 32}}, color = {245, 121, 0}));
        connect(constantFlowFrac.flowFraction_Out, heatExchanger.secondaryFF) annotation(
          Line(points = {{-120, 32}, {-98, 32}, {-98, -16}, {-6, -16}, {-6, -2}}, color = {245, 121, 0}));
        annotation(
          Diagram(coordinateSystem(extent = {{-100, 60}, {20, -20}})));
      end unitTest;

      model heatTest
        SMD_MSR_Modelica.HeatTransport.HeatExchanger heatExchanger(vol_P = 0.1623, vol_T = 0.023059, vol_S = 0.051863, rhoP = 2146.5, rhoT = 8774.5, rhoS = 1922, cP_P = 1966.5, cP_T = 577.80, cP_S = 2390, VdotPnom = 0.075708, VdotSnom = 0.053627, hApNom = 324000, hAsNom = 153000, EnableRad = false, AcShell = 1, AcTube = 1, ArShell = 1, Kp = 4.76, Ks = 4.76, L_shell = 1.575, L_tube = 1.575, e = 0.01, Tinf = 500, TpIn_0 = 657, TpOut_0 = 632, TsIn_0 = 546.4, TsOut_0 = 579.3) annotation(
          Placement(transformation(origin = {-37, -13}, extent = {{-58, -29}, {87, 29}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantHeatRemoval constantHeatRemoval(rho = 1922, cP = 2390, vol = 0.051863, V_dotNom = 0.053627, powRm(displayUnit = "MW") = 8000000) annotation(
          Placement(transformation(origin = {-30, -96}, extent = {{22, -22}, {-22, 22}}, rotation = -180)));
        SMD_MSR_Modelica.Signals.Constants.ConstantFlowFrac constantFlowFrac(ConstantFlowFrac = 1) annotation(
          Placement(transformation(origin = {-106, 36}, extent = {{-10, -10}, {10, 10}})));
        SMD_MSR_Modelica.Signals.Constants.ConstantHeatProduction constantHeatProduction(rho = 2146.5, cP = 1966.5, vol = 0.1623, V_dotNom = 7.5708E-02, powPr(displayUnit = "MW") = 8000000) annotation(
          Placement(transformation(origin = {-31, 85}, extent = {{27, -27}, {-27, 27}}, rotation = -0)));
        SMD_MSR_Modelica.Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
          Placement(transformation(origin = {-22, 30}, extent = {{-10, 10}, {10, -10}}, rotation = -0)));
      equation
        connect(constantFlowFrac.flowFraction_Out, heatExchanger.primaryFF) annotation(
          Line(points = {{-106, 40}, {-70, 40}, {-70, 12}}, color = {245, 121, 0}));
        connect(constantFlowFrac.flowFraction_Out, heatExchanger.secondaryFF) annotation(
          Line(points = {{-106, 40}, {58, 40}, {58, -50}, {26, -50}, {26, -38}}, color = {245, 121, 0}));
        connect(constantVolumetricPower.volPow, heatExchanger.P_decay) annotation(
          Line(points = {{-22, 26}, {-22, 12}}, color = {220, 138, 221}));
        connect(heatExchanger.T_in_pFluid, constantHeatProduction.temp_Out) annotation(
          Line(points = {{-86, 2}, {-144, 2}, {-144, 86}, {-42, 86}}, color = {204, 0, 0}));
        connect(constantHeatProduction.temp_In, heatExchanger.T_out_pFluid) annotation(
          Line(points = {{-20, 86}, {92, 86}, {92, 2}, {40, 2}}, color = {204, 0, 0}));
        connect(heatExchanger.T_in_sFluid, constantHeatRemoval.temp_Out) annotation(
          Line(points = {{40, -28}, {94, -28}, {94, -96}, {-22, -96}}, color = {204, 0, 0}));
        connect(constantHeatRemoval.temp_In, heatExchanger.T_out_sFluid) annotation(
          Line(points = {{-38, -96}, {-124, -96}, {-124, -28}, {-86, -28}}, color = {204, 0, 0}));
        annotation(
          Diagram(coordinateSystem(extent = {{-120, 60}, {120, -180}})));
      end heatTest;
    end TestHX;

    package TestPipe
      model unitTest
        HeatTransport.Pipe pipe(vol = 0.1, volFracNode = 0.1, vDotNom = 0.01, rho = 1000, cP = 100, K = 1, Ac = 0.01, L = 100, EnableRad = false, Ar = 0.1, e = 0.01, T_0 = 100, Tinf = 100) annotation(
          Placement(transformation(origin = {-116, -86}, extent = {{-112, -112}, {112, 112}})));
        Signals.Constants.ConstantFlowFrac constantFlowFrac(ConstantFlowFrac = 1) annotation(
          Placement(transformation(origin = {-321, -107}, extent = {{-25, -25}, {25, 25}})));
        Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
          Placement(transformation(origin = {-305, -9}, extent = {{-25, -25}, {25, 25}})));
      equation
        connect(constantFlowFrac.flowFraction_Out, pipe.flowFrac) annotation(
          Line(points = {{-320, -98}, {-160, -98}, {-160, -90}}, color = {245, 121, 0}));
        connect(constantVolumetricPower.volPow, pipe.PiDecay_Heat) annotation(
          Line(points = {{-306, 0}, {-160, 0}, {-160, -18}}, color = {220, 138, 221}));
        connect(pipe.PiTemp_IN, pipe.PiTempOut) annotation(
          Line(points = {{-160, -56}, {-84, -56}}, color = {204, 0, 0}));
        annotation(
          Diagram(coordinateSystem(extent = {{-240, 40}, {0, -200}})));
      end unitTest;

      model heatTest
        HeatTransport.Pipe pipe(vol = 0.1, volFracNode = 0.1, vDotNom = 0.01, rho = 1000, cP = 100, K = 1, Ac = 0.01, L = 100, EnableRad = false, Ar = 0.1, e = 0.01, T_0 = 100, Tinf = 100) annotation(
          Placement(transformation(origin = {-116, -86}, extent = {{-112, -112}, {112, 112}})));
        Signals.Constants.ConstantHeatRemoval constantHeatRemoval(vol = 0.1, rho = 1000, cP = 100, V_dotNom = 0.01, powRm = 1) annotation(
          Placement(transformation(origin = {-128, -158}, extent = {{42, -42}, {-42, 42}}, rotation = -0)));
        Signals.Constants.ConstantFlowFrac constantFlowFrac(ConstantFlowFrac = 1) annotation(
          Placement(transformation(origin = {-321, -107}, extent = {{-25, -25}, {25, 25}})));
        Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 10) annotation(
          Placement(transformation(origin = {-305, -9}, extent = {{-25, -25}, {25, 25}})));
      equation
        connect(pipe.PiTempOut, constantHeatRemoval.temp_In) annotation(
          Line(points = {{-84, -56}, {-34, -56}, {-34, -158}, {-112, -158}}, color = {204, 0, 0}));
        connect(constantHeatRemoval.temp_Out, pipe.PiTemp_IN) annotation(
          Line(points = {{-144, -158}, {-228, -158}, {-228, -56}, {-160, -56}}, color = {204, 0, 0}));
        connect(constantFlowFrac.flowFraction_Out, pipe.flowFrac) annotation(
          Line(points = {{-320, -98}, {-160, -98}, {-160, -90}}, color = {245, 121, 0}));
        connect(constantVolumetricPower.volPow, pipe.PiDecay_Heat) annotation(
          Line(points = {{-306, 0}, {-160, 0}, {-160, -18}}, color = {220, 138, 221}));
        annotation(
          Diagram(coordinateSystem(extent = {{-240, 40}, {0, -200}})));
      end heatTest;
    end TestPipe;

    package TestRadiator
      model unitTest
        HeatTransport.Radiator radiator(vol_P = 0.204183209633634, vol_S = 3.159098197920001, rhoP = 1922, rhoS = 1.1237, cP_P = 2390, cP_S = 1008.5, VdotPnom = 5.36265E-02, VdotSnom = 94.389, hAnom = 8521.3, Kp = 1, Ac_P = 0.01, Lp = 1, e = 0.01, Ar_P = 0.1, Tp_0 = 579.39, Ts_0 = 148.9, Tinf = 100, EnableRad = false) annotation(
          Placement(transformation(origin = {5, 7}, extent = {{-75, -75}, {75, 75}})));
        Signals.Constants.ConstantReal constantReal(magnitude = 1) annotation(
          Placement(transformation(origin = {-11, -67}, extent = {{-31, -31}, {31, 31}})));
        Signals.Constants.ConstantReal constantReal1(magnitude = 37.78) annotation(
          Placement(transformation(origin = {-118, -74}, extent = {{-26, -26}, {26, 26}})));
        Signals.Constants.ConstantFlowFrac constantFlowFrac(ConstantFlowFrac = 1) annotation(
          Placement(transformation(origin = {-181, 31}, extent = {{-21, -21}, {21, 21}})));
      equation
        connect(constantReal1.realOut, radiator.tempInS) annotation(
          Line(points = {{-118, -69}, {-46, -69}, {-46, 0}}));
        connect(constantReal.realOut, radiator.flowFracS) annotation(
          Line(points = {{-11, -61}, {-14, -61}, {-14, 0}, {-16, 0}}));
        connect(constantFlowFrac.flowFraction_Out, radiator.flowFracP) annotation(
          Line(points = {{-181, 39}, {-48, 39}, {-48, 38}}, color = {245, 121, 0}));
        connect(radiator.tempOutP, radiator.tempInP) annotation(
          Line(points = {{26, 22}, {-48, 22}}, color = {204, 0, 0}));
      end unitTest;

      model heatTest
        HeatTransport.Radiator radiator(vol_P = 0.204183209633634, vol_S = 3.159098197920001, rhoP = 1922, rhoS = 1.1237, cP_P = 2390, cP_S = 1008.5, VdotPnom = 5.36265E-02, VdotSnom = 94.389, hAnom = 8521.3, Kp = 1, Ac_P = 0.01, Lp = 1, e = 0.01, Ar_P = 0.1, Tp_0 = 579.39, Ts_0 = 148.9, Tinf = 100, EnableRad = false) annotation(
          Placement(transformation(origin = {5, 7}, extent = {{-75, -75}, {75, 75}})));
        Signals.Constants.ConstantHeatProduction constantHeatProduction(vol = 0.204183209633634, rho = 1922, cP = 2390, V_dotNom = 5.36265E-02, powPr(displayUnit = "MW") = 6000000) annotation(
          Placement(transformation(origin = {-11, -59}, extent = {{-27, -27}, {27, 27}}, rotation = 180)));
        Signals.Constants.ConstantReal constantReal(magnitude = 1) annotation(
          Placement(transformation(origin = {-181, -39}, extent = {{-31, -31}, {31, 31}})));
        Signals.Constants.ConstantReal constantReal1(magnitude = 37.78) annotation(
          Placement(transformation(origin = {-118, -74}, extent = {{-26, -26}, {26, 26}})));
        Signals.Constants.ConstantFlowFrac constantFlowFrac(ConstantFlowFrac = 1) annotation(
          Placement(transformation(origin = {-181, 31}, extent = {{-21, -21}, {21, 21}})));
      equation
        connect(constantHeatProduction.temp_In, radiator.tempOutP) annotation(
          Line(points = {{0, -59}, {82, -59}, {82, 22}, {26, 22}}, color = {204, 0, 0}));
        connect(radiator.tempInP, constantHeatProduction.temp_Out) annotation(
          Line(points = {{-48, 22}, {-92, 22}, {-92, -59}, {-22, -59}}, color = {204, 0, 0}));
        connect(constantReal1.realOut, radiator.tempInS) annotation(
          Line(points = {{-118, -69}, {-46, -69}, {-46, 0}}));
        connect(constantReal.realOut, radiator.flowFracS) annotation(
          Line(points = {{-181, -33}, {-16, -33}, {-16, 0}}));
        connect(constantFlowFrac.flowFraction_Out, radiator.flowFracP) annotation(
          Line(points = {{-181, 39}, {-48, 39}, {-48, 38}}, color = {245, 121, 0}));
      end heatTest;
    end TestRadiator;

    package TestDHRS
      model unitTest
        HeatTransport.DHRS dhrs(vol = 0.1, rho = 2146.5, cP = 1966.5, vDotNom = 0.075708, DHRS_tK = 10, DHRS_MaxP_Rm(displayUnit = "W") = 8000, DHRS_P_Bleed = 8000, DHRS_time = 2000, K = 1, Ac = 0.01, L = 1, Ar = 0.1, EnableRad = false, e = 0.01, Tinf = 100, T_0 = 100) annotation(
          Placement(transformation(origin = {-12, -20.6667}, extent = {{-74.6667, -37.3333}, {56, 74.6667}})));
        Signals.Constants.ConstantFlowFrac constantFlowFrac(ConstantFlowFrac = 1) annotation(
          Placement(transformation(origin = {-171, -89}, extent = {{-27, -27}, {27, 27}})));
        Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
          Placement(transformation(origin = {-168, 34}, extent = {{-18, -18}, {18, 18}}, rotation = -90)));
      equation
        connect(constantFlowFrac.flowFraction_Out, dhrs.flowFrac) annotation(
          Line(points = {{-170, -78}, {-170, -40}, {-68, -40}}, color = {245, 121, 0}));
        connect(constantVolumetricPower.volPow, dhrs.pDecay) annotation(
          Line(points = {{-162, 35}, {-68, 35}, {-68, 36}}, color = {220, 138, 221}));
        connect(dhrs.tempIn, dhrs.tempOut) annotation(
          Line(points = {{-68, -2}, {26, -2}}, color = {204, 0, 0}));
        annotation(
          Diagram(coordinateSystem(extent = {{-200, 80}, {80, -120}})));
      end unitTest;

      model heatTest
        HeatTransport.DHRS dhrs(vol = 0.1, rho = 2146.5, cP = 1966.5, vDotNom = 0.075708, DHRS_tK = 10, DHRS_MaxP_Rm(displayUnit = "W") = 8000, DHRS_P_Bleed = 8000, DHRS_time = 2000, K = 1, Ac = 0.01, L = 1, Ar = 0.1, EnableRad = false, e = 0.01, Tinf = 100, T_0 = 100) annotation(
          Placement(transformation(origin = {-12, -20.6667}, extent = {{-74.6667, -37.3333}, {56, 74.6667}})));
        Signals.Constants.ConstantFlowFrac constantFlowFrac(ConstantFlowFrac = 1) annotation(
          Placement(transformation(origin = {-171, -89}, extent = {{-27, -27}, {27, 27}})));
        Signals.Constants.ConstantHeatProduction constantHeatProduction(vol = 0.1, rho = 2146.5, cP = 1966.5, V_dotNom = 0.075708, powPr(displayUnit = "W") = 8000) annotation(
          Placement(transformation(origin = {-32, -102}, extent = {{30, -30}, {-30, 30}}, rotation = -0)));
        Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
          Placement(transformation(origin = {-168, 34}, extent = {{-18, -18}, {18, 18}}, rotation = -90)));
      equation
        connect(constantFlowFrac.flowFraction_Out, dhrs.flowFrac) annotation(
          Line(points = {{-170, -78}, {-170, -40}, {-68, -40}}, color = {245, 121, 0}));
        connect(dhrs.tempOut, constantHeatProduction.temp_In) annotation(
          Line(points = {{26, -2}, {82, -2}, {82, -102}, {-20, -102}}, color = {204, 0, 0}));
        connect(constantHeatProduction.temp_Out, dhrs.tempIn) annotation(
          Line(points = {{-44, -102}, {-108, -102}, {-108, -2}, {-68, -2}}, color = {204, 0, 0}));
        connect(constantVolumetricPower.volPow, dhrs.pDecay) annotation(
          Line(points = {{-162, 35}, {-68, 35}, {-68, 36}}, color = {220, 138, 221}));
        annotation(
          Diagram(coordinateSystem(extent = {{-200, 80}, {80, -120}})));
      end heatTest;
    end TestDHRS;

    package TestPump
      model testPump
        HeatTransport.Pump pump(numRampUp = 3, rampUpK = {10, 100, 500}, rampUpTo = {0.25, 0.5, 1}, rampUpTime = {500, 1000, 2000}, tripK = 50, tripTime = 3500, freeConvFF = 0) annotation(
          Placement(transformation(origin = {-10, 32}, extent = {{-32, -32}, {32, 32}})));
      equation

        annotation(
          Diagram(coordinateSystem(extent = {{-40, 60}, {20, -20}})));
      end testPump;
    end TestPump;

    package TestMixing
      model testMixing
        HeatTransport.MixingPot mixingPot(numStreams = 3, vol = 1, VdotNom = 1, flowFractionsNom = {1, 1, 1}, rho = 1, Cp = 1, K = 1, Ac = 1, L = 1, EnableRad = false, Ar = 1, e = 0, T_0 = 1, Tinf = 1) annotation(
          Placement(transformation(origin = {-23, 9.33333}, extent = {{-31, -31}, {31, 31}}, rotation = 90)));
        Signals.Constants.ConstantVolumetricPower constantVolumetricPower(Q_volumetric = 0) annotation(
          Placement(transformation(origin = {-160, -28}, extent = {{-18, -18}, {18, 18}})));
        Signals.Constants.ConstantTempIn constantTempIn(TestTempIn = 2) annotation(
          Placement(transformation(origin = {-56, -60}, extent = {{-10, -10}, {10, 10}})));
        Signals.Constants.ConstantTempIn constantTempIn1(TestTempIn = 2) annotation(
          Placement(transformation(origin = {-28, -80}, extent = {{-10, -10}, {10, 10}})));
        Signals.Constants.ConstantTempIn constantTempIn2(TestTempIn = 2) annotation(
          Placement(transformation(origin = {12, -78}, extent = {{-10, -10}, {10, 10}})));
        Signals.Constants.ConstantFlowFrac constantFlowFrac(ConstantFlowFrac = 1) annotation(
          Placement(transformation(origin = {40, -62}, extent = {{-10, -10}, {10, 10}})));
        Signals.Constants.ConstantFlowFrac constantFlowFrac1(ConstantFlowFrac = 1) annotation(
          Placement(transformation(origin = {76, -62}, extent = {{-10, -10}, {10, 10}})));
        Signals.Constants.ConstantFlowFrac constantFlowFrac2(ConstantFlowFrac = 1) annotation(
          Placement(transformation(origin = {106, -64}, extent = {{-10, -10}, {10, 10}})));
      equation
        connect(constantVolumetricPower.volPow, mixingPot.decayHeat) annotation(
          Line(points = {{-162, -18}, {-44, -18}, {-44, -22}}, color = {220, 138, 221}));
        connect(constantTempIn.temp_Out, mixingPot.temp_In[1]) annotation(
          Line(points = {{-56, -56}, {-22, -56}, {-22, -22}}, color = {204, 0, 0}));
        connect(constantTempIn1.temp_Out, mixingPot.temp_In[2]) annotation(
          Line(points = {{-28, -76}, {-22, -76}, {-22, -22}}, color = {204, 0, 0}));
        connect(constantTempIn2.temp_Out, mixingPot.temp_In[3]) annotation(
          Line(points = {{12, -74}, {-22, -74}, {-22, -22}}, color = {204, 0, 0}));
        connect(constantFlowFrac.flowFraction_Out, mixingPot.flowFraction[1]) annotation(
          Line(points = {{40, -58}, {-2, -58}, {-2, -22}}, color = {245, 121, 0}));
        connect(constantFlowFrac1.flowFraction_Out, mixingPot.flowFraction[2]) annotation(
          Line(points = {{76, -58}, {-2, -58}, {-2, -22}}, color = {245, 121, 0}));
        connect(constantFlowFrac2.flowFraction_Out, mixingPot.flowFraction[3]) annotation(
          Line(points = {{106, -60}, {-2, -60}, {-2, -22}}, color = {245, 121, 0}));
      end testMixing;
    end TestMixing;

    package TestRTMS
  model test1
  HeatTransport.RTMS rtms(Cp2 = 300)  annotation(
          Placement(transformation(origin = {-31.8333, -28.2}, extent = {{-31.8333, -38.2}, {31.8333, 38.2}})));
  Signals.Constants.ConstantReal constantReal(magnitude = 100)  annotation(
          Placement(transformation(origin = {-153, -59}, extent = {{-9, -9}, {9, 9}})));
  Signals.Constants.ConstantTempIn constantTempIn annotation(
          Placement(transformation(origin = {49, -35}, extent = {{-15, -15}, {15, 15}})));
      equation
  connect(constantReal.realOut, rtms.heaterPower) annotation(
          Line(points = {{-153, -54.5}, {-58, -54.5}, {-58, -54}}));
  connect(constantTempIn.temp_Out, rtms.Tin) annotation(
          Line(points = {{50, -28}, {-6, -28}}, color = {204, 0, 0}));
      end test1;
    end TestRTMS;
  end QAtoolBox;

  package Signals
    package TimeDependent
      model Sigmoid
        parameter Real signalMax;
        parameter Real signalMin;
        parameter Real epsilon = 1E-3;
        parameter SMD_MSR_Modelica.Units.InitiationTime activationTime;
        parameter SMD_MSR_Modelica.Units.TimeConstant timeConstant;
        output SMD_MSR_Modelica.PortsConnectors.RealOut signal annotation(
          Placement(visible = true, transformation(origin = {0, 0}, extent = {{-24, -24}, {24, 24}}, rotation = 0), iconTransformation(origin = {1, -3}, extent = {{-27, -27}, {27, 27}}, rotation = 0)));
      equation
        signal.R = (signalMax - signalMin)/(1 + exp(log(1/epsilon - 1)*(1 - (time - activationTime)/timeConstant))) + signalMin;
        annotation(
          Diagram(graphics = {Rectangle(lineThickness = 1.5, extent = {{-60, 60}, {60, -60}})}));
      end Sigmoid;

      /* Piecewise-constant step signal. Each step at stepTime[i] adds
         (amplitude[i] - amplitude[i-1]); the output is the running total.
         Use to drive reactivity insertions, UHX demand changes, etc. */
      model Stepper
        parameter Integer numSteps;
        parameter SMD_MSR_Modelica.Units.InitiationTime stepTime[numSteps];
        parameter Real amplitude[numSteps];
        Real amp[numSteps];
        output SMD_MSR_Modelica.PortsConnectors.RealOut step annotation(
          Placement(visible = true, transformation(origin = {0, 22}, extent = {{-20, -20}, {20, 20}}, rotation = 0), iconTransformation(origin = {4, 22}, extent = {{-24, -24}, {24, 24}}, rotation = 0)));
      equation
        for i in 1:numSteps loop
          if time > stepTime[i] and i == 1 then
            amp[i] = amplitude[i];
          elseif time > stepTime[i] and i > 1 then
            amp[i] = amplitude[i] - amplitude[i - 1];
          else
            amp[i] = 0;
          end if;
        end for;
        step.R = sum(amp);
        annotation(
          Diagram(graphics = {Rectangle(origin = {3, -3}, lineThickness = 1, extent = {{-59, 57}, {59, -57}})}, coordinateSystem(extent = {{-60, 60}, {60, -60}})),
          Icon(graphics = {Rectangle(origin = {3, -3}, lineThickness = 1, extent = {{-59, 57}, {59, -57}})}, coordinateSystem(extent = {{-60, 60}, {60, -60}})));
      end Stepper;

      /* Sinusoidal source: amplitude*sin(omega*(t - activationTime)) for
         t >= activationTime, zero before. Used for frequency-response (Bode)
         perturbation studies. */
      model SineSource
        parameter Real amplitude;
        parameter SMD_MSR_Modelica.Units.AngularFrequency omega;
        parameter SMD_MSR_Modelica.Units.InitiationTime activationTime;
        output SMD_MSR_Modelica.PortsConnectors.RealOut signal annotation(
          Placement(visible = true, transformation(origin = {0, 22}, extent = {{-20, -20}, {20, 20}}, rotation = 0), iconTransformation(origin = {4, 22}, extent = {{-24, -24}, {24, 24}}, rotation = 0)));
      equation
        if time >= activationTime then
          signal.R = amplitude*sin(omega*(time - activationTime));
        else
          signal.R = 0;
        end if;
        annotation(
          Diagram(graphics = {Rectangle(origin = {3, -3}, lineThickness = 1, extent = {{-59, 57}, {59, -57}})},
            coordinateSystem(extent = {{-60, 60}, {60, -60}})),
          Icon(graphics = {Rectangle(origin = {3, -3}, lineThickness = 1, extent = {{-59, 57}, {59, -57}})},
            coordinateSystem(extent = {{-60, 60}, {60, -60}})));
      end SineSource;

      /* Piecewise-linear ramp signal with saturation cap.
         Each segment adds a rate increment at rampTime[i]; output is clamped
         to maxValue. Used for gradual pump ramp-up or load-following profiles. */
      model Ramper
        parameter Integer numRamps;
        parameter SMD_MSR_Modelica.Units.InitiationTime rampTime[numRamps];
        parameter Real rate[numRamps];
        parameter Real offset;
        parameter Real maxValue;
        Real mag[numRamps];
        output SMD_MSR_Modelica.PortsConnectors.RealOut ramp annotation(
          Placement(visible = true, transformation(origin = {0, 22}, extent = {{-20, -20}, {20, 20}}, rotation = 0), iconTransformation(origin = {4, 22}, extent = {{-24, -24}, {24, 24}}, rotation = 0)));
      equation
        for i in 1:numRamps loop
          if time > rampTime[i] and i == 1 then
            mag[i] = rate[i]*(time - rampTime[i]);
          elseif time > rampTime[i] and i > 1 then
            mag[i] = (rate[i] - rate[i - 1])*(time - rampTime[i]);
          else
            mag[i] = 0;
          end if;
        end for;
        if maxValue > sum(mag) then
          ramp.R = sum(mag) + offset;
        else
          ramp.R = maxValue;
        end if;
        annotation(
          Diagram(graphics = {Rectangle(origin = {3, -3}, lineThickness = 1, extent = {{-59, 57}, {59, -57}})}),
          Icon(graphics = {Rectangle(origin = {3, -3}, lineThickness = 1, extent = {{-59, 57}, {59, -57}})}));
      end Ramper;
    end TimeDependent;

    package Operations
      model SumSignals
        parameter Integer numInput;
        input SMD_MSR_Modelica.PortsConnectors.RealIn realIn[numInput] annotation(
          Placement(visible = true, transformation(origin = {20, 20}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {20, 20}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
        output SMD_MSR_Modelica.PortsConnectors.RealOut realOut annotation(
          Placement(visible = true, transformation(origin = {-18, 22}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {-18, 22}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
      equation
        realOut.R = sum(realIn.R);
        annotation(
          Diagram(graphics = {Rectangle(origin = {0, 20}, extent = {{-40, 20}, {40, -20}}), Text(origin = {11, 33}, extent = {{-29, 7}, {29, -7}}, textString = "Sum"), Text(origin = {21, 9}, extent = {{-9, 5}, {9, -5}}, textString = "In"), Text(origin = {-19, 9}, extent = {{-9, 7}, {9, -7}}, textString = "Out")}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
          Icon(coordinateSystem(extent = {{-100, -100}, {100, 100}}), graphics = {Text(origin = {-19, 9}, extent = {{-9, 7}, {9, -7}}, textString = "Out"), Text(origin = {21, 9}, extent = {{-9, 5}, {9, -5}}, textString = "In"), Text(origin = {11, 33}, extent = {{-29, 7}, {29, -7}}, textString = "Sum"), Rectangle(origin = {0, 20}, extent = {{-40, 20}, {40, -20}})}));
      end SumSignals;
    end Operations;

    package Constants
      model NeutronPopulation
        parameter SMD_MSR_Modelica.Units.NominalNeutronPopulation relNpop;
        output SMD_MSR_Modelica.PortsConnectors.NominalNeutronPopulation n_population annotation(
          Placement(visible = true, transformation(origin = {-1, 29}, extent = {{-29, -29}, {29, 29}}, rotation = 0), iconTransformation(origin = {4, 38}, extent = {{-28, -28}, {28, 28}}, rotation = 0)));
      initial equation
        n_population.n = 0.999;
      equation
        n_population.n = 0.999 + (if time < 8000 then 0 else 0.1*sin(0.01*time));
        annotation(
          Diagram(graphics = {Rectangle(origin = {0, -2}, fillColor = {20, 36, 248}, fillPattern = FillPattern.Solid, extent = {{-60, 60}, {60, -60}}), Text(origin = {3, -28}, extent = {{-51, 16}, {51, -16}}, textString = "n")}),
          Icon(graphics = {Text(origin = {7, -35}, extent = {{-53, 21}, {53, -21}}, textString = "n"), Rectangle(origin = {4, -6}, lineColor = {20, 36, 248}, fillColor = {20, 36, 248}, lineThickness = 2, extent = {{-76, 72}, {76, -72}})}));
      end NeutronPopulation;

      model ConstantReactivity
        parameter SMD_MSR_Modelica.Units.Reactivity Reactivity;
        output SMD_MSR_Modelica.PortsConnectors.ReactivityOut reactivity annotation(
          Placement(visible = true, transformation(origin = {0, 36}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
      equation
        reactivity.rho = Reactivity;
        annotation(
          Diagram(graphics = {Rectangle(lineColor = {78, 154, 6}, lineThickness = 2, extent = {{-60, 60}, {60, -60}}), Text(origin = {2, -36}, extent = {{-40, 20}, {40, -20}}, textString = "rho")}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
          Icon(coordinateSystem(extent = {{-100, -100}, {100, 100}}), graphics = {Text(origin = {2, -36}, extent = {{-40, 20}, {40, -20}}, textString = "rho"), Rectangle(lineColor = {78, 154, 6}, lineThickness = 2, extent = {{-60, 60}, {60, -60}})}));
      end ConstantReactivity;

      model ConstantTempIn
        parameter SMD_MSR_Modelica.Units.Temperature TestTempIn;
        output SMD_MSR_Modelica.PortsConnectors.TempOut temp_Out annotation(
          Placement(visible = true, transformation(origin = {-1.77636e-15, 40}, extent = {{-20, -20}, {20, 20}}, rotation = 0), iconTransformation(origin = {-1.77636e-15, 40}, extent = {{-20, -20}, {20, 20}}, rotation = 0)));
      equation
        temp_Out.T = TestTempIn;
        annotation(
          Diagram(graphics = {Rectangle(lineColor = {204, 0, 0}, lineThickness = 2, extent = {{-60, 60}, {60, -60}}), Text(origin = {1, -31}, extent = {{-25, 13}, {25, -13}}, textString = "T")}),
          Icon(graphics = {Rectangle(lineColor = {204, 0, 0}, lineThickness = 2, extent = {{-60, 60}, {60, -60}}), Text(origin = {1, -31}, extent = {{-25, 13}, {25, -13}}, textString = "T")}));
      end ConstantTempIn;

      model ConstantTempOut
        parameter SMD_MSR_Modelica.Units.Temperature ConstantTempOut;
      input SMD_MSR_Modelica.PortsConnectors.TempIn tempIn annotation(
          Placement(transformation(origin = {0, 40}, extent = {{-10, -10}, {10, 10}}), iconTransformation(origin = {0, 40}, extent = {{-10, -10}, {10, 10}})));
      equation
        tempIn.T = ConstantTempOut;
        annotation(
          Diagram(graphics = {Rectangle(lineColor = {204, 0, 0}, lineThickness = 2, extent = {{-60, 60}, {60, -60}})}),
          Icon(graphics = {Rectangle(lineColor = {204, 0, 0}, lineThickness = 2, extent = {{-60, 60}, {60, -60}})}));
      end ConstantTempOut;

      model ConstantFlowFrac
        parameter SMD_MSR_Modelica.Units.FlowFraction ConstantFlowFrac;
        output SMD_MSR_Modelica.PortsConnectors.FlowFractionOut flowFraction_Out annotation(
          Placement(visible = true, transformation(origin = {0, 40}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {0, 38}, extent = {{-24, -24}, {24, 24}}, rotation = 0)));
      equation
        flowFraction_Out.FF = ConstantFlowFrac;
        annotation(
          Diagram(graphics = {Rectangle(lineColor = {245, 121, 0}, lineThickness = 2, extent = {{-60, 60}, {60, -60}}), Text(origin = {3, -32}, extent = {{-45, 10}, {45, -10}}, textString = "FlowFrac")}),
          Icon(graphics = {Rectangle(lineColor = {245, 121, 0}, lineThickness = 2, extent = {{-60, 60}, {60, -60}}), Text(origin = {3, -32}, extent = {{-45, 10}, {45, -10}}, textString = "FlowFrac")}));
      end ConstantFlowFrac;

      model ConstantVolumetricPower
        parameter SMD_MSR_Modelica.Units.VolumetircPower Q_volumetric;
        output SMD_MSR_Modelica.PortsConnectors.VolumetircPowerOut volPow annotation(
          Placement(visible = true, transformation(origin = {-4, 36}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {-4, 36}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
      equation
        volPow.Q = Q_volumetric;
        annotation(
          Diagram(graphics = {Rectangle(lineColor = {220, 138, 221}, lineThickness = 2, extent = {{-60, 60}, {60, -60}})}, coordinateSystem(extent = {{-60, 60}, {60, -60}})),
          Icon(graphics = {Rectangle(lineColor = {220, 138, 221}, lineThickness = 2, extent = {{-60, 60}, {60, -60}})}, coordinateSystem(extent = {{-60, 60}, {60, -60}})));
      end ConstantVolumetricPower;

      model ConstantHeatProduction
        parameter SMD_MSR_Modelica.Units.Volume vol;
        parameter SMD_MSR_Modelica.Units.Density rho;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP;
        parameter SMD_MSR_Modelica.Units.VolumeticFlowRate V_dotNom;
        parameter SMD_MSR_Modelica.Units.Power powPr;
        SMD_MSR_Modelica.Units.Mass m;
        SMD_MSR_Modelica.Units.MassFlowRate mDot;
        input SMD_MSR_Modelica.PortsConnectors.TempIn temp_In annotation(
          Placement(visible = true, transformation(origin = {-40, -1.77636e-15}, extent = {{-20, -20}, {20, 20}}, rotation = 0), iconTransformation(origin = {-40, -1.9984e-15}, extent = {{-20, -20}, {20, 20}}, rotation = 0)));
        output SMD_MSR_Modelica.PortsConnectors.TempOut temp_Out annotation(
          Placement(visible = true, transformation(origin = {40, -1.77636e-15}, extent = {{-20, -20}, {20, 20}}, rotation = 0), iconTransformation(origin = {40, -1.77636e-15}, extent = {{-20, -20}, {20, 20}}, rotation = 0)));
      initial equation
        der(temp_Out.T) = 0;
      equation
        m = vol*rho;
        mDot = V_dotNom*rho;
        m*cP*der(temp_Out.T) = mDot*cP*(temp_In.T - temp_Out.T) + powPr;
        annotation(
          Diagram(graphics = {Rectangle(lineColor = {204, 0, 0}, lineThickness = 2, extent = {{-60, 60}, {60, -60}}), Text(origin = {0, -42}, extent = {{-48, -30}, {48, 30}}, textString = "Q Production")}),
          Icon(graphics = {Text(origin = {0, -42}, extent = {{-48, -30}, {48, 30}}, textString = "Q Production"), Rectangle(lineColor = {204, 0, 0}, lineThickness = 2, extent = {{-60, 60}, {60, -60}})}));
      end ConstantHeatProduction;

      model ConstantHeatRemoval
        parameter SMD_MSR_Modelica.Units.Volume vol;
        parameter SMD_MSR_Modelica.Units.Density rho;
        parameter SMD_MSR_Modelica.Units.SpecificHeatCapacity cP;
        parameter SMD_MSR_Modelica.Units.VolumeticFlowRate V_dotNom;
        parameter SMD_MSR_Modelica.Units.Power powRm;
        SMD_MSR_Modelica.Units.Mass m;
        SMD_MSR_Modelica.Units.MassFlowRate mDot;
        input SMD_MSR_Modelica.PortsConnectors.TempIn temp_In annotation(
          Placement(visible = true, transformation(origin = {-40, -1.77636e-15}, extent = {{-20, -20}, {20, 20}}, rotation = 0), iconTransformation(origin = {-40, -1.9984e-15}, extent = {{-20, -20}, {20, 20}}, rotation = 0)));
        output SMD_MSR_Modelica.PortsConnectors.TempOut temp_Out annotation(
          Placement(visible = true, transformation(origin = {40, -1.77636e-15}, extent = {{-20, -20}, {20, 20}}, rotation = 0), iconTransformation(origin = {40, -1.77636e-15}, extent = {{-20, -20}, {20, 20}}, rotation = 0)));
      initial equation
        der(temp_Out.T) = 0;
      equation
        m = vol*rho;
        mDot = V_dotNom*rho;
        m*cP*der(temp_Out.T) = mDot*cP*(temp_In.T - temp_Out.T) - powRm;
        annotation(
          Diagram(graphics = {Rectangle(lineColor = {204, 0, 0}, lineThickness = 2, extent = {{-60, 60}, {60, -60}}), Text(origin = {0, -42}, extent = {{-48, -30}, {48, 30}}, textString = "Q Remove")}),
          Icon(graphics = {Text(origin = {0, -42}, extent = {{-48, -30}, {48, 30}}, textString = "Q Remove"), Rectangle(lineColor = {204, 0, 0}, lineThickness = 2, extent = {{-60, 60}, {60, -60}})}));
      end ConstantHeatRemoval;

      model ConstantNeutronSource
        parameter SMD_MSR_Modelica.Units.NeutronEmissionRate S;
        output SMD_MSR_Modelica.PortsConnectors.NeutronEmsRateOut neutronEmsRateOut annotation(
          Placement(visible = true, transformation(origin = {-2, 34}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {-2, 34}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
      equation
        neutronEmsRateOut.nDot = S;
        annotation(
          Diagram(graphics = {Rectangle(lineColor = {191, 64, 188}, lineThickness = 2, extent = {{-60, 60}, {60, -60}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
          Icon(graphics = {Rectangle(lineColor = {191, 64, 188}, lineThickness = 2, extent = {{-60, 60}, {60, -60}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})));
      end ConstantNeutronSource;

      model ConstantReal
        parameter Real magnitude;
        output SMD_MSR_Modelica.PortsConnectors.RealOut realOut annotation(
          Placement(transformation(origin = {1, 20}, extent = {{-20, -20}, {20, 20}}), iconTransformation(origin = {1, 20}, extent = {{-20, -20}, {20, 20}})));
      equation
        realOut.R = magnitude;
        annotation(
          Diagram(graphics = {Rectangle(lineThickness = 2, extent = {{-40, 40}, {40, -40}})}, coordinateSystem(extent = {{-40, 40}, {40, -40}})),
          Icon(graphics = {Rectangle(lineThickness = 2, extent = {{-40, 40}, {40, -40}})}, coordinateSystem(extent = {{-40, 40}, {40, -40}})));
      end ConstantReal;

      model ConstantPower
        parameter SMD_MSR_Modelica.Units.Power P;
        output SMD_MSR_Modelica.PortsConnectors.PowerOut pow annotation(
          Placement(visible = true, transformation(origin = {-4, 36}, extent = {{-10, -10}, {10, 10}}, rotation = 0), iconTransformation(origin = {-4, 36}, extent = {{-10, -10}, {10, 10}}, rotation = 0)));
      equation
        pow.P = P;
        annotation(
          Diagram(graphics = {Rectangle(lineColor = {220, 138, 221}, lineThickness = 2, extent = {{-60, 60}, {60, -60}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})),
          Icon(graphics = {Rectangle(lineColor = {220, 138, 221}, lineThickness = 2, extent = {{-60, 60}, {60, -60}})}, coordinateSystem(extent = {{-100, -100}, {100, 100}})));
      end ConstantPower;
    end Constants;
    annotation(
      Diagram);
  end Signals;

  package Constants
    final constant Real SigSBK = 5.670374419E-8;
    //Stefan-Boltzmann constant W/(m2K4)
    final constant Real Large = 1E6;
    //Just a referance large number to solve singularity issues
    final constant SMD_MSR_Modelica.Units.ResidentTime MaxTau = 1E10;
    final constant SMD_MSR_Modelica.Units.ResidentTime MinTau = 1E-6;
  end Constants;
end SMD_MSR_Modelica;
