# -*- coding: utf-8 -*-

import re
import numpy as np
import pandas as pd
import os

simBasePath = os.getcwd()

numberOfRuns = 1000

run = []
maxPower, minPower = [], []
maxDerPower, maxDerFuelTemp1, maxDerFuelTemp2, maxDerGrapTemp = [], [], [], []
maxInletTemp, maxOutletTemp = [], []

SSPower, SSDerPower = [], []
SSDerFuelTemp1, SSDerFuelTemp2, SSDerGrapTemp = [], [], []
SSInletTemp, SSOutletTemp = [], []

for runNumber in range(1, numberOfRuns+1):

    workPath = f"../run{runNumber:.0f}"
    dataFileName = f"/run{runNumber:.0f}_res.csv"

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

    # Safely find indices avoiding float comparison
    indexSS = (simData['time'] - 1990).abs().idxmin()
    index = (simData['time'] - 2000).abs().idxmin()

    steadyState = simData.loc[indexSS]
    selectData = simData.loc[index:]

    run.append(runNumber)

    maxPower.append(max(selectData.FuelChannelNomPower))
    minPower.append(min(selectData.FuelChannelNomPower))
    maxDerPower.append(max(selectData.dermPKEnpopulationn))
    maxDerFuelTemp1.append(max(selectData.derFuelChannelfuelNode1T))
    maxDerFuelTemp2.append(max(selectData.derFuelChannelfuelNode2T))
    maxDerGrapTemp.append(max(selectData.derFuelChannelgrapNodeT))
    maxInletTemp.append(max(selectData.FuelChanneltempInT))
    maxOutletTemp.append(max(selectData.FuelChanneltempOutT))

    SSPower.append(steadyState.FuelChannelNomPower)
    SSDerPower.append(steadyState.dermPKEnpopulationn)
    SSDerFuelTemp1.append(steadyState.derFuelChannelfuelNode1T)
    SSDerFuelTemp2.append(steadyState.derFuelChannelfuelNode2T)
    SSDerGrapTemp.append(steadyState.derFuelChannelgrapNodeT)
    SSInletTemp.append(steadyState.FuelChanneltempInT)
    SSOutletTemp.append(steadyState.FuelChanneltempOutT)

with open("senResultsY.m", "w") as text_file:
    print(f"run = {run};", file=text_file)
    print(f"maxPower = {maxPower};", file=text_file)
    print(f"minPower = {minPower};", file=text_file)
    print(f"maxDerPower = {maxDerPower};", file=text_file)
    print(f"maxDerFuelTemp1 = {maxDerFuelTemp1};", file=text_file)
    print(f"maxDerFuelTemp2 = {maxDerFuelTemp2};", file=text_file)
    print(f"maxDerGrapTemp = {maxDerGrapTemp};", file=text_file)
    print(f"maxInletTemp = {maxInletTemp};", file=text_file)
    print(f"maxOutletTemp = {maxOutletTemp};", file=text_file)
    print(f"SSPower = {SSPower};", file=text_file)
    print(f"SSDerPower = {SSDerPower};", file=text_file)
    print(f"SSDerFuelTemp1 = {SSDerFuelTemp1};", file=text_file)
    print(f"SSDerFuelTemp2 = {SSDerFuelTemp2};", file=text_file)
    print(f"SSDerGrapTemp = {SSDerGrapTemp};", file=text_file)
    print(f"SSInletTemp = {SSInletTemp};", file=text_file)
    print(f"SSOutletTemp = {SSOutletTemp};", file=text_file)

