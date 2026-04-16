# -*- coding: utf-8 -*-
"""
Project: SMD-MSR_Modelica
Author: Visura Pathirana
Advisor: Dr. Ondrej Chvala
"""

import os
import subprocess
import random
from shutil import copyfile

simBasePath = os.getcwd()

numberOfRuns = 1000
rndRangeLow = 30
rndRangeHigh = 200

run = []
CpCoefFuelVal = []
HTCcoefCoreVal = []
HTCcoefPHXVal = []

for runNumber in range(1, numberOfRuns+1):

    CpCoefFuel = (random.randint(rndRangeLow, rndRangeHigh)) / 100
    HTCcoefCore = (random.randint(rndRangeLow, rndRangeHigh)) / 100
    HTCcoefPHX = (random.randint(rndRangeLow, rndRangeHigh)) / 100

    CpFuel = f"parameter Real CpCoefFuel = {CpCoefFuel}"
    HTCcore = f"parameter Real HTCcoefCore = {HTCcoefCore}"
    HTCphx = f"parameter Real HTCcoefPHX = {HTCcoefPHX}"

    workPath = f"../run{runNumber:.0f}"
    os.makedirs(workPath, exist_ok=True)

    modelFileText = ""
    with open("MSRE.mo", "r") as reading_file:
        for line in reading_file:
            searchLine = line.strip()
            newLine = searchLine.replace('parameter Real CpCoefFuel = 1', CpFuel)
            newLine = newLine.replace('parameter Real HTCcoefCore = 1', HTCcore)
            newLine = newLine.replace('parameter Real HTCcoefPHX = 1', HTCphx)
            modelFileText += newLine + "\n"

    modelFileName = os.path.join(workPath, "MSRE.mo")
    with open(modelFileName, 'w') as modelFile:
        modelFile.write(modelFileText)

    # Write depl script
    runSim = f'//SMD-MSR_Modelica \n' \
             f'loadFile("SMD_MSR_Modelica.mo"); \n' \
             f'loadFile("MSRE.mo"); \n' \
             f'simulate(MSRE,startTime=0,stopTime=10000,numberOfIntervals=10000,tolerance=1E-6,method=dassl,outputFormat = "csv",fileNamePrefix ="run{runNumber:.0f}"); \n'

    runSimFileName = os.path.join(workPath, "runMSRE.mos")
    with open(runSimFileName, 'w') as deplFile:
        deplFile.write(runSim)

    modelicaLibrary = os.path.join(workPath, "SMD_MSR_Modelica.mo")
    copyfile("SMD_MSR_Modelica.mo", modelicaLibrary)

    subprocess.run(["chmod", "+x", workPath], check=True)

    # Properly check output for failure instead of swallowing silently
    subprocess.run(['time', '-p', 'omc', 'runMSRE.mos'], cwd=workPath, capture_output=True, check=True)

    run.append(runNumber)
    CpCoefFuelVal.append(CpCoefFuel)
    HTCcoefCoreVal.append(HTCcoefCore)
    HTCcoefPHXVal.append(HTCcoefPHX)

with open("senResultsX.m", "w") as text_file:
    print(f"Run = {run};", file=text_file)
    print(f"CpCoefFuel = {CpCoefFuelVal};", file=text_file)
    print(f"HTCcoefCore = {HTCcoefCoreVal};", file=text_file)
    print(f"HTCcoefPHX = {HTCcoefPHXVal};", file=text_file)

