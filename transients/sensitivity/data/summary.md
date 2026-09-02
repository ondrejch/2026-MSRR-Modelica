# 1R reactivity-step sensitivity (hA exponent + linked properties)

Step: +100 pcm at t = 2000 s; model `MSRR.MSRRuhxNominalTrimThermalSS`; horizon 10000 s.

At nominal prescribed flow (FF = 1) the hA power law is normalized (`FF^hAExp = 1`), so the exponent has no effect by construction; the reduced-flow rows quantify the actual sensitivity. `kFuel = 0` in the production model, so its scaled rows are exactly inert.

| case | hAExp | FF | property set | scale | peak [MW] | t_peak [s] | steady [MW] | overshoot | decay ratio | dT_out [C] | hA [W/K] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| gridA_exp0_ff1 | 0 | 1 | nominal | 1 | 1.99 | 2036 | 1 | 0.9904 | 0.01492 | 8.758 | 2.492e+04 |
| gridA_exp0p25_ff1 | 0.25 | 1 | nominal | 1 | 1.99 | 2036 | 1 | 0.9904 | 0.01492 | 8.758 | 2.492e+04 |
| gridA_exp0p33_ff1 | 0.33 | 1 | nominal | 1 | 1.99 | 2036 | 1 | 0.9904 | 0.01492 | 8.758 | 2.492e+04 |
| gridA_exp0p5_ff1 | 0.5 | 1 | nominal | 1 | 1.99 | 2036 | 1 | 0.9904 | 0.01492 | 8.758 | 2.492e+04 |
| gridA_exp0p8_ff1 | 0.8 | 1 | nominal | 1 | 1.99 | 2036 | 1 | 0.9904 | 0.01492 | 8.758 | 2.492e+04 |
| gridA_exp0_ff0p66 | 0 | 0.66 | nominal | 1 | 1.946 | 2035 | 1 | 0.9464 | 0.007036 | 8.757 | 2.492e+04 |
| gridA_exp0p25_ff0p66 | 0.25 | 0.66 | nominal | 1 | 1.941 | 2034 | 1 | 0.9409 | 0.005255 | 8.757 | 2.246e+04 |
| gridA_exp0p33_ff0p66 | 0.33 | 0.66 | nominal | 1 | 1.939 | 2034 | 1 | 0.9393 | 0.004721 | 8.757 | 2.172e+04 |
| gridA_exp0p5_ff0p66 | 0.5 | 0.66 | nominal | 1 | 1.936 | 2034 | 1 | 0.9358 | 0.003653 | 8.756 | 2.024e+04 |
| gridA_exp0p8_ff0p66 | 0.8 | 0.66 | nominal | 1 | 1.93 | 2033 | 1 | 0.9303 | 0.001998 | 8.756 | 1.787e+04 |
| gridB_rho_cp_fuel_x0p9 | 0.33 | 1 | rho_cp_fuel | 0.9 | 1.889 | 2033 | 1 | 0.8889 | 0.008522 | 8.757 | 2.492e+04 |
| gridB_rho_cp_fuel_x1p1 | 0.33 | 1 | rho_cp_fuel | 1.1 | 2.094 | 2039 | 1 | 1.094 | 0.02367 | 8.759 | 2.492e+04 |
| gridB_kFuel_x0p9 | 0.33 | 1 | kFuel | 0.9 | 1.99 | 2036 | 1 | 0.9904 | 0.01492 | 8.758 | 2.492e+04 |
| gridB_kFuel_x1p1 | 0.33 | 1 | kFuel | 1.1 | 1.99 | 2036 | 1 | 0.9904 | 0.01492 | 8.758 | 2.492e+04 |
| gridB_hAnom_x0p9 | 0.33 | 1 | hAnom | 0.9 | 1.984 | 2036 | 1 | 0.9843 | 0.01231 | 8.758 | 2.242e+04 |
| gridB_hAnom_x1p1 | 0.33 | 1 | hAnom | 1.1 | 1.996 | 2037 | 1 | 0.9963 | 0.01736 | 8.758 | 2.741e+04 |
