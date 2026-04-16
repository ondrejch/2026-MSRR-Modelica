#!/bin/bash
# Run frequency simulations - examples

# Serial, default settings
python3.12 -m freq.runFreqNominal --core_model 1r

# Serial, custom settings
python3.12 -m freq.runFreqNominal --core_model 1r --power 1.0 --freq_min 0.001 --freq_max 10 --num_freq 50 --base_dir 00runs/freq/1r/power_1

# Parallel, default settings (uses all CPU cores)
python3.12 -m freq.runFreqNominalParallel --core_model 1r

# Parallel, custom settings
python3.12 -m freq.runFreqNominalParallel --core_model 9r --power 1.0 --freq_min 0.001 --freq_max 10 --num_freq 50 --n_jobs 8 --base_dir 00runs/freq/9r/power_1

# After simulations complete, collect and analyze results
python3.12 -m freq.collectFreqNominal --core_model 1r --power 1.0 --plot
