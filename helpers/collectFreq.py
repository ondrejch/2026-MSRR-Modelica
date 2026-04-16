# -*- coding: utf-8 -*-
"""
Project: SMD-MSR_Modelica
Author: Visura Pathirana
Advisor: Dr. Ondrej Chvala

The script performs following,
1. Open individual dir with results
2. Grab each results and load it to pandas dataframe
3. Change table headers of the dataframe so that conflicting characters are removed
4. Calculate the gain and the phase shift after 2000s of steady state
5. Write gain data and phase shift data in a .m file for easy plotting using Matlab
"""

import re
import numpy as np
import pandas as pd
import os
from scipy import interpolate
from scipy.optimize import curve_fit

def sine_wave_fit(x, a, b, c, d):
    # x is time (should be off by a 1000)
    # a is amplitude (k)
    # b is the frequency (rad/sec)
    # c is the phase shift (rad)
    # d is the steday state values before hand
    result = a*np.sin(b*(x)+c)+d
    return result

simBasePath = os.getcwd()
script_dir = os.path.dirname(os.path.abspath(__file__))
kin_dyn_path = os.path.join(script_dir, "kin_dyn_edit.txt")

# Import depletion data from .txt file
# First line of the text file is coloumn headings and coloumns are divided by tab delimeter
deplTime,beta1,beta2,beta3,beta4,beta5,beta6,ngt,alphaFuel,alphaGrap = np.loadtxt(
    kin_dyn_path, skiprows=1, delimiter='\t', unpack=True
)

# Interpolate data
beta1Intpl = interpolate.InterpolatedUnivariateSpline(deplTime,beta1)
beta2Intpl = interpolate.InterpolatedUnivariateSpline(deplTime,beta2)
beta3Intpl = interpolate.InterpolatedUnivariateSpline(deplTime,beta3)
beta4Intpl = interpolate.InterpolatedUnivariateSpline(deplTime,beta4)
beta5Intpl = interpolate.InterpolatedUnivariateSpline(deplTime,beta5)
beta6Intpl = interpolate.InterpolatedUnivariateSpline(deplTime,beta6)
ngtIntpl = interpolate.InterpolatedUnivariateSpline(deplTime,ngt)
alphaFuelIntpl = interpolate.InterpolatedUnivariateSpline(deplTime,alphaFuel)
alphaGrapIntpl = interpolate.InterpolatedUnivariateSpline(deplTime,alphaGrap)

# Depletion range
depletion_range = np.linspace(deplTime[0],deplTime[len(deplTime)-1],num=1)
freq_space = np.logspace(-2, 1, num=100)

freq = []
gain  = []
phase = []

for depletionPoint in depletion_range:
    for freqPoint in freq_space:
        # Read results file from each run
        workPath = f"../depl{depletionPoint:.0f}/freq{freqPoint:.5f}"
        dataFileName = f"/MSRE_depl{depletionPoint:.0f}freq{freqPoint:.5f}_res.csv"

        simData = pd.read_csv(f"{workPath}{dataFileName}")

        # simData headers contain conflicting characters that need removing
        simDataHeader = str(list(simData.columns))

        chars_to_remove = ['[' , ']' , '.' , '(' , ')' , '_', '\'']
        rx = '[' + re.escape(''.join(chars_to_remove)) + ']'
        newSimDataHeader = re.sub(rx, '', simDataHeader)

        newSimDataHeader = newSimDataHeader.replace(" ", "").split(',')
        simData.columns = newSimDataHeader

        # Data analysis
        time = simData['time']
        power = simData.FuelChannelNomPower

        # Safely find the closest index to 2000 to avoid floating point mismatch
        index = (simData['time'] - 2000).abs().idxmin()

        # Fit the sine wave
        p0 = [300e-5, freqPoint, 0, 1] # first guess
        bottom_bound = freqPoint - 1e-10 # this is the frequncy bound, cant pass a single value so the bounds are for >1/1000%
        top_bound = freqPoint + 1e-10
        # fit values
        popt, pcov = curve_fit(sine_wave_fit, time.loc[index:], power.loc[index:], p0, bounds=((1e-5, bottom_bound, -1.5708, 0.995), (1000e-5, top_bound, 1.5708, 1.015)))

        gain.append(popt[0]/(1e-5)) # store gain is relative to input terms
        phase.append(popt[2]*180/np.pi) # store pahse in terms of degree
        freq.append(freqPoint)

# Write timeResults as a .m file in proper format to plot using matlab
with open("FrqResNew.m", "w") as text_file:
    print(f"freq = {freq};", file=text_file)
    print(f"gain = {gain};", file=text_file)
    print(f"phase = {phase};", file=text_file)
