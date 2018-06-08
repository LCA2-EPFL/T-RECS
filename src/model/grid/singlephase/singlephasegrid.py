#!/usr/bin/env python3

# The MIT License (MIT)
#
# Copyright (c) 2018 École Polytechnique Fédérale de Lausanne (EPFL)
# Author: Jagdish P. Achara
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from numpy import delete, zeros, ones, dot, append, subtract, insert, add, array, put, full, vstack, divide, diag, inf, conjugate
from numpy.linalg import inv, solve, norm
from operator import itemgetter
from scipy.linalg import lu


class SinglePhaseGrid:
    def __init__(self, data, ALG = 'CW', voltage_tolerance = 1e-15, api_path = None):
        self.lines = tuple((line['from'], line['to'], line['R'], line['X'], line['B'], ) for line in data['lines'])
        self.no_lines = len(self.lines)
        self.no_buses = max(self.lines, key = itemgetter(1))[1] + 1 # +1 because the bus indices start from 0

        self.baseV = data['base_quantities']['V']
        self.baseS = data['base_quantities']['S']
        self.Ybase = self.baseS / (self.baseV * self.baseV)

        self.tolerance = voltage_tolerance

        #Compute the admittance matrix.
        self.__admittance_matrix()

        self.siY = self.__puY * self.Ybase
        self.algorithm = ALG

        if self.algorithm == 'CW':
            self.__puYll = delete(delete(self.__puY, 0, 0), 0, 1)
            self.__puP, self.__puL, self.__puU = lu(self.__puYll)
            self.__puYllInv = inv(self.__puYll)

        if api_path is not None:
            from snippets import load_api, dump_api
            api = load_api(api_path, check_readiness = False)
            api.base_quantities = data['base_quantities']
            dump_api(api, api_path)


    def __admittance_matrix(self):
        A = zeros((self.no_buses, self.no_lines), dtype = complex)
        for i in range(self.no_lines):
            A[self.lines[i][0], i] = 1
            A[self.lines[i][1], i] = -1

        L = zeros((self.no_lines), dtype = complex)
        for i in range(self.no_lines):
            L[i] = 1 / (complex(self.lines[i][2], self.lines[i][3]) * self.Ybase)

        B = zeros((self.no_lines, 1), dtype = complex)
        for i in range(self.no_lines):
            B[i, 0] = complex(0, self.lines[i][4] / self.Ybase / 2)

        self.__puY = add(dot(A, dot(diag(L), A.T)), diag(dot(abs(A), B).flatten()))


    def __Jacob(self, VrVi0):
        u = self.no_buses - 1 # u is the number of PQ buses
        J = zeros((2 * u, 2 * u))

        for i in range(1, self.no_buses):
            for j in range(1, self.no_buses):
                if i != j:
                    J[i - 1, j - 1] = self.__puY[i, j].real * VrVi0[i] + self.__puY[i, j].imag * VrVi0[self.no_buses + i]
                    J[i - 1, j - 1 + u] = -self.__puY[i, j].imag * VrVi0[i] + self.__puY[i, j].real * VrVi0[self.no_buses + i]
                    J[i - 1 + u, j - 1] = J[i - 1, j - 1 + u]
                    J[i - 1 + u, j - 1 + u] = - J[i - 1, j - 1]
                else:
                    sumPR = sumPX = sumQR = sumQX = 0
                    for k in range(self.no_buses):
                        if k != i:
                            a = self.__puY[i, k].real * VrVi0[k] - self.__puY[i, k].imag * VrVi0[self.no_buses + k]
                            b = self.__puY[i, k].imag * VrVi0[k] + self.__puY[i, k].real * VrVi0[self.no_buses + k]
                            sumPR += a
                            sumPX += b
                            sumQR -= b
                            sumQX += a
                    J[i - 1, j - 1] = sumPR + 2 * self.__puY[i, j].real * VrVi0[i]
                    J[i - 1, j - 1 + u] = sumPX + 2 * self.__puY[i, j].real * VrVi0[i + self.no_buses]
                    J[i - 1 + u, j - 1] = sumQR - 2 * self.__puY[i, j].imag * VrVi0[i]
                    J[i - 1 + u, j - 1 + u] = sumQX - 2 * self.__puY[i, j].imag * VrVi0[i + self.no_buses]

        return J


    def __calculPQ(self, VrVi0):
        PQ0 = zeros(2 * (self.no_buses - 1))

        for i in range (1, self.no_buses):
            sum1 = sum2 = sum3 = sum4 = 0
            for k in range (self.no_buses):
                a = self.__puY[i, k].real * VrVi0[k] - self.__puY[i, k].imag * VrVi0[k + self.no_buses]
                b = self.__puY[i, k].imag * VrVi0[k] + self.__puY[i, k].real * VrVi0[k + self.no_buses]
                sum1 += a
                sum2 += b
                sum3 += b
                sum4 += a
            PQ0[i - 1] = sum1 * VrVi0[i] + sum2 * VrVi0[i + self.no_buses]
            PQ0[i - 1 + self.no_buses - 1] = -sum3 * VrVi0[i] + sum4 * VrVi0[i + self.no_buses]

        return PQ0


    def __solveCW(self):
        deltaVpu = full((self.no_buses - 1, 1), 1)
        while norm(deltaVpu, inf) > self.tolerance:
            puC = vstack(divide(conjugate(self.__puS), conjugate(self.__puVk).flatten()))
            puX = solve(self.__puL, puC)
            puVkplus1 = solve(self.__puU, puX) + self.__puW
            deltaVpu = puVkplus1 - self.__puVk
            self.__puVk = puVkplus1


    def __solveNR(self):
        puVRVXinitial = append(self.__puVR, self.__puVX)
        puPQtarget = append(self.__puP, self.__puQ)

        deltaVRVX = ones(self.no_buses)

        while norm(deltaVRVX, inf) > self.tolerance:
            J = self.__Jacob(puVRVXinitial)
            puPQinitial = self.__calculPQ(puVRVXinitial)
            deltaPQ = subtract(puPQtarget, puPQinitial)

            deltaVRVX = dot(inv(J), deltaPQ)
            deltaVRVX = insert(deltaVRVX, [0, self.no_buses - 1], 0)

            puVRVXinitial = add(puVRVXinitial, deltaVRVX)

        self.__puVR = puVRVXinitial[:self.no_buses]
        self.__puVX = puVRVXinitial[self.no_buses:]


    def update(self, listP, listQ, slackVR, slackVX):
        self.pqBusesP = listP
        self.pqBusesQ = listQ

        if self.algorithm == 'CW':
            self.__puW = - dot(self.__puYllInv, vstack(self.__puY[:, 0][1:])) * complex(slackVR, slackVX) / self.baseV
            self.__puS = (array(listP) + 1j * array(listQ)) / self.baseS

            self.__puVk = self.__puW
            self.__solveCW()

            self.__puVR = append([slackVR / self.baseV], self.__puVk.real)
            self.__puVX = append([slackVX / self.baseV], self.__puVk.imag)

        elif self.algorithm == 'NR':
            #Initialize the values of P, Q, Vreal, Vimg in p.u.
            self.__puP = array(listP) / self.baseS
            self.__puQ = array(listQ) / self.baseS
            self.__puVR = full(self.no_buses, slackVR / self.baseV)
            self.__puVX = full(self.no_buses, slackVX / self.baseV)

	        # Load-flow analysis.
            self.__solveNR()

        self.realV =  self.__puVR * self.baseV
        self.imagV =  self.__puVX * self.baseV


    def updatebus(self, index, newP, newQ):
        if self.algorithm == 'CW':
            put(self.__puS, index - 1, complex(newP, newQ) / self.baseS)

            self.__solveCW()

        elif self.algorithm == 'NR':
            put(self.__puP, index - 1, newP / self.baseS)
            put(self.__puQ, index - 1, newQ / self.baseS)

            self.__solveNR()


    def computeSlackPower(self):
        I = 0
        for i in range(self.no_buses):
            I += self.__puY[0, i] * complex(self.__puVR[i], self.__puVX[i])
        S = complex(self.__puVR[0], self.__puVX[0]) * conjugate(I) * self.baseS
        self.slackPower = (S.real, S.imag)


    def computeCurrents(self):
        VRVX = self.realV + 1j * self.imagV
        self.busCurrents = dot(self.siY, vstack(VRVX))

        self.forwardCurrents = zeros(self.no_lines, dtype = complex)
        self.backwardCurrents = zeros(self.no_lines, dtype = complex)
        for i in range(self.no_lines):
            src = self.lines[i][0]
            dst = self.lines[i][1]
            self.forwardCurrents[i] = -self.siY[src, dst] * (VRVX[src] - VRVX[dst]) + VRVX[src] * complex(0, 0.5) * self.lines[i][4]
            self.backwardCurrents[i] = -self.siY[dst, src] * (VRVX[dst] - VRVX[src]) + VRVX[dst] * 0.5j * self.lines[i][4]
