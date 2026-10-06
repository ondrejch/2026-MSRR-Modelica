# 1R reactivity-step sensitivity (hA exponent + linked properties)

Step: +100 pcm at t = 2000 s; model `MSRR.MSRRuhxNominalTrimThermalSS`; horizon 10000 s.

At nominal prescribed flow (FF = 1) the hA power law is normalized (`FF^hAExp = 1`), so the exponent has no effect by construction; the reduced-flow rows quantify the actual sensitivity. `kFuel = 0` in the production model, so its scaled rows are exactly inert. The `rho_cp_fuel` rows scale each of rho_f and c_p,f by the listed factor, so rho_f*c_p,f moves by its square (scale 0.9 / 1.1: rho*c_p x0.81 / x1.21).

dT_out is the outlet-temperature rise (tail mean minus pre-step mean) net of a matched no-step twin run over the same windows: every case starts from the FF = 1 nominal-hA setpoint row and is still drifting when the step fires. dT_out raw is the uncorrected step-run difference and no-step dT the twin's drift (dT_out = raw - no-step). In sensitivity_metrics.csv, pre_steady_tout_C and post_steady_tout_C are the step run's uncorrected window means, so their difference is dT_out raw, not dT_out. The decay ratio is the overshoot above the post-step steady power of the first local maximum after the first local minimum that follows the peak (at t_peak2), relative to the first overshoot.

| case | hAExp | FF | property set | scale | peak [MW] | t_peak [s] | steady [MW] | overshoot | decay ratio | dT_out [C] | hA [W/K] | dT_out raw [C] | no-step dT [C] | t_peak2 [s] |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gridA_exp0_ff1 | 0 | 1 | nominal | 1 | 1.945 | 2032 | 1 | 0.945 | 0.09648 | 8.757 | 4565 | 8.757 | 3.187e-06 | 2332 |
| gridA_exp0p25_ff1 | 0.25 | 1 | nominal | 1 | 1.945 | 2032 | 1 | 0.945 | 0.09648 | 8.757 | 4565 | 8.757 | 3.187e-06 | 2332 |
| gridA_exp0p33_ff1 | 0.33 | 1 | nominal | 1 | 1.945 | 2032 | 1 | 0.945 | 0.09648 | 8.757 | 4565 | 8.757 | 3.187e-06 | 2332 |
| gridA_exp0p5_ff1 | 0.5 | 1 | nominal | 1 | 1.945 | 2032 | 1 | 0.945 | 0.09648 | 8.757 | 4565 | 8.757 | 3.187e-06 | 2332 |
| gridA_exp0p8_ff1 | 0.8 | 1 | nominal | 1 | 1.945 | 2032 | 1 | 0.945 | 0.09648 | 8.757 | 4565 | 8.757 | 3.187e-06 | 2332 |
| gridA_exp0_ff0p66 | 0 | 0.66 | nominal | 1 | 1.915 | 2032 | 1 | 0.9146 | 0.103 | 8.757 | 4565 | 8.732 | -0.02521 | 2330 |
| gridA_exp0p25_ff0p66 | 0.25 | 0.66 | nominal | 1 | 1.913 | 2032 | 1 | 0.9132 | 0.1046 | 8.758 | 4115 | 8.659 | -0.09838 | 2332 |
| gridA_exp0p33_ff0p66 | 0.33 | 0.66 | nominal | 1 | 1.913 | 2032 | 1 | 0.9127 | 0.1051 | 8.758 | 3980 | 8.627 | -0.1306 | 2333 |
| gridA_exp0p5_ff0p66 | 0.5 | 0.66 | nominal | 1 | 1.912 | 2032 | 1 | 0.9118 | 0.106 | 8.758 | 3709 | 8.542 | -0.2163 | 2334 |
| gridA_exp0p8_ff0p66 | 0.8 | 0.66 | nominal | 1 | 1.91 | 2032 | 1 | 0.9101 | 0.1075 | 8.76 | 3274 | 8.323 | -0.4366 | 2336 |
| gridB_rho_cp_fuel_x0p9 | 0.33 | 1 | rho_cp_fuel | 0.9 | 1.841 | 2028 | 1 | 0.8414 | 0.1275 | 8.757 | 4565 | 8.765 | 0.008005 | 2274 |
| gridB_rho_cp_fuel_x1p1 | 0.33 | 1 | rho_cp_fuel | 1.1 | 2.052 | 2036 | 1 | 1.052 | 0.0779 | 8.757 | 4565 | 8.751 | -0.006468 | 2384 |
| gridB_kFuel_x0p9 | 0.33 | 1 | kFuel | 0.9 | 1.945 | 2032 | 1 | 0.945 | 0.09648 | 8.757 | 4565 | 8.757 | 3.187e-06 | 2332 |
| gridB_kFuel_x1p1 | 0.33 | 1 | kFuel | 1.1 | 1.945 | 2032 | 1 | 0.945 | 0.09648 | 8.757 | 4565 | 8.757 | 3.187e-06 | 2332 |
| gridB_hAnom_x0p9 | 0.33 | 1 | hAnom | 0.9 | 1.943 | 2032 | 1 | 0.9434 | 0.09847 | 8.758 | 4108 | 8.69 | -0.06723 | 2334 |
| gridB_hAnom_x1p1 | 0.33 | 1 | hAnom | 1.1 | 1.946 | 2032 | 1 | 0.9464 | 0.09447 | 8.757 | 5022 | 8.79 | 0.03274 | 2329 |
