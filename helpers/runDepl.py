# -*- coding: utf-8 -*-
"""
Project: SMD-MSR_Modelica
Author: Visura Pathirana
Advisor: Dr. Ondrej Chvala
"""

import numpy as np
import os
import subprocess
from shutil import copyfile
from scipy import interpolate

simBasePath = os.getcwd()
script_dir = os.path.dirname(os.path.abspath(__file__))
kin_dyn_path = os.path.join(script_dir, "..", "freq", "kin_dyn_edit.txt")

# Import depletion data from .txt file
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

for depletionPoint in depletion_range:

    workPath = f"../depl{depletionPoint:.0f}"
    os.makedirs(workPath, exist_ok=True)

    betaG = [
        beta1Intpl(depletionPoint), beta2Intpl(depletionPoint), beta3Intpl(depletionPoint),
        beta4Intpl(depletionPoint), beta5Intpl(depletionPoint), beta6Intpl(depletionPoint)
    ]
    beta = 'beta = {' + ','.join(map(str, betaG)) + '}'
    LAM = f"Lam = {ngtIntpl(depletionPoint)}"
    a_F = f"a_F = {alphaFuelIntpl(depletionPoint)}"
    a_G = f"a_G = {alphaGrapIntpl(depletionPoint)}"

    omega = "omega = 1"
    sin_mag = "sin_mag = 0"

    # Write depl script
    modelFileText = ""
    with open("MSRE.mo", "r") as reading_file:
        for line in reading_file:
            searchLine = line.strip()
            # Replaced flawed regex replacements with safe string replacements
            newLine = searchLine.replace('beta = {1,2,3,4,5,6}', beta)
            newLine = newLine.replace('Lam = 1', LAM)
            newLine = newLine.replace('a_F = 1', a_F)
            newLine = newLine.replace('a_G = 1', a_G)
            newLine = newLine.replace('omega = 1', omega)
            newLine = newLine.replace('sin_mag = 1', sin_mag)
            modelFileText += newLine + "\n"

    modelFileName = f"MSRE_depl{depletionPoint:.0f}.mo"
    with open(os.path.join(workPath, modelFileName), 'w') as modelFile:
        modelFile.write(modelFileText)

    runDeplSim = f'//SMD-MSR_Modelica \n' \
                 f'loadFile("SMD_MSR_Modelica.mo"); \n' \
                 f'loadFile("{modelFileName}"); \n' \
                 f'simulate(MSRE,startTime=0,stopTime=10000,numberOfIntervals=100000,tolerance=1E-6,method=dassl,outputFormat = "csv",fileNamePrefix ="depl{depletionPoint:.0f}"); \n'

    # Save depl script in work dir
    runDeplName = "runModelica.mos"
    with open(os.path.join(workPath, runDeplName), 'w') as runDepl:
        runDepl.write(runDeplSim)

    # Copy SMD_Modelica Library
    modelicaLibrary = os.path.join(workPath, "SMD_MSR_Modelica.mo")
    copyfile("SMD_MSR_Modelica.mo", modelicaLibrary)

    # Properly execute and check subprocess outputs
    subprocess.run(["chmod", "+x", workPath], check=True)
    subprocess.run(["omc", "runModelica.mos"], cwd=workPath, check=True)
